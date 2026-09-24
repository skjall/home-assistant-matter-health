import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

import pytest
from aiohttp import ClientResponse, web
from conftest import T0, Clock, at, make_engine
from pytest_aiohttp import AiohttpClient

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Finding, Link, Role, Severity
from matter_health.store import Store
from matter_health.web import app as web_app
from matter_health.web import create_app


@pytest.fixture
def static(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The real static files are a build output and may not exist.
    directory = tmp_path / "static"
    directory.mkdir()
    (directory / "index.html").write_text("<h1>Matter Health</h1>", encoding="utf-8")
    (directory / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(web_app, "STATIC", directory)
    return directory


@pytest.fixture
def translations(tmp_path: Path) -> Path:
    directory = tmp_path / "ui"
    directory.mkdir()
    for language, title in (("en", "Findings"), ("de", "Befunde")):
        (directory / f"{language}.json").write_text(
            json.dumps({"nav": {"findings": title}}), encoding="utf-8"
        )
    (directory / "notes.txt").write_text("not a language", encoding="utf-8")
    return directory


def build(
    engine: Engine,
    translations: Path,
    *,
    remote: str | None = "172.30.32.2",
    trust_all: bool = False,
) -> web.Application:
    """The app, seen from ``remote`` as the ingress proxy would reach it."""
    app = create_app(engine, trust_all=trust_all, translations=translations)
    if remote is not None:

        @web.middleware
        async def from_remote(
            request: web.Request,
            handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
        ) -> web.StreamResponse:
            return await handler(request.clone(remote=remote))

        app.middlewares.insert(0, from_remote)
    return app


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx)


def finding(key: str, severity: Severity, minutes: float, ended: bool) -> Finding:
    return Finding(
        key=key,
        rule="test",
        severity=severity,
        title="finding.test.title",
        started_at=at(minutes),
        ended_at=at(minutes + 1) if ended else None,
        chain=[Link(Role.CAUSE, "link.cause_unknown")],
        subjects=["node:7", "node:8"],
    )


@pytest.mark.parametrize(
    ("remote", "trust_all", "status"),
    [
        ("172.30.32.2", False, 200),
        ("172.30.33.254", False, 200),
        ("127.0.0.1", False, 403),
        ("192.0.2.10", False, 403),
        ("", False, 403),
        (None, False, 403),
        ("192.0.2.10", True, 200),
        (None, True, 200),
    ],
)
async def test_only_ingress_may_connect(
    aiohttp_client: AiohttpClient,
    engine: Engine,
    translations: Path,
    static: Path,
    remote: str | None,
    trust_all: bool,
    status: int,
) -> None:
    client = await aiohttp_client(
        build(engine, translations, remote=remote, trust_all=trust_all)
    )

    response = await client.get("/api/languages")

    assert response.status == status
    if status == 403:
        assert "Home Assistant" in await response.text()


async def test_page_and_static_files(
    aiohttp_client: AiohttpClient, engine: Engine, translations: Path, static: Path
) -> None:
    client = await aiohttp_client(build(engine, translations))

    page = await client.get("/")
    script = await client.get("/static/app.js")

    assert await page.text() == "<h1>Matter Health</h1>"
    assert await script.text() == "console.log(1)"
    # Cached copies are checked again, so an update reaches every viewer.
    assert page.headers["Cache-Control"] == "no-cache"
    assert script.headers["Cache-Control"] == "no-cache"
    api = await client.get("/api/languages")
    assert "Cache-Control" not in api.headers


async def test_without_static_files_there_is_no_static_route(
    aiohttp_client: AiohttpClient,
    engine: Engine,
    translations: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_app, "STATIC", tmp_path / "absent")
    client = await aiohttp_client(build(engine, translations))

    assert (await client.get("/static/app.js")).status == 404


async def test_overview(
    aiohttp_client: AiohttpClient,
    ctx: Context,
    store: Store,
    engine: Engine,
    translations: Path,
) -> None:
    ctx.names.set("node:7", "Living Room Plug")
    await engine.set_status("otbr", ok=False, detail="refused")
    await store.set_state("matter.nodes", {"total": 3, "unavailable": ["node:7"]})
    await store.set_state("otbr.node", {"role": "leader"})
    await store.set_state("border_routers", [{"name": "Living Room TV"}])
    await store.put_finding(finding("a", Severity.PROBLEM, 0, ended=False), T0)
    await store.put_finding(finding("b", Severity.WARNING, 0, ended=False), T0)
    await store.put_finding(finding("c", Severity.WARNING, 0, ended=True), T0)
    client = await aiohttp_client(build(engine, translations))

    body = await (await client.get("/api/overview")).json()

    assert body == {
        "sources": {
            "otbr": {"ok": False, "since": T0.isoformat(), "detail": "refused"}
        },
        "thread": {"role": "leader"},
        "border_routers": [{"name": "Living Room TV"}],
        "devices": {
            "total": 3,
            "unavailable": [
                {
                    "subject": "node:7",
                    "name": "Living Room Plug",
                    "usual": False,
                    "known": False,
                    "comes_and_goes": None,
                }
            ],
        },
        "open": {"problem": 1, "warning": 1, "info": 0},
        "now": T0.isoformat(),
        "build": web_app.page_version(),
    }


async def test_overview_before_anything_is_known(
    aiohttp_client: AiohttpClient, engine: Engine, translations: Path
) -> None:
    client = await aiohttp_client(build(engine, translations))

    body = await (await client.get("/api/overview")).json()

    assert body["thread"] is None
    assert body["border_routers"] == []
    assert body["devices"] == {"total": None, "unavailable": []}
    assert body["open"] == {"problem": 0, "warning": 0, "info": 0}


async def test_findings_of_the_last_days_with_current_names(
    aiohttp_client: AiohttpClient,
    ctx: Context,
    clock: Clock,
    store: Store,
    engine: Engine,
    translations: Path,
) -> None:
    ctx.names.set("node:7", "Living Room Plug")
    days = 24 * 60
    await store.put_finding(finding("old", Severity.WARNING, -3 * days, True), T0)
    await store.put_finding(finding("new", Severity.WARNING, -1, True), T0)
    client = await aiohttp_client(build(engine, translations))

    async def keys(query: str) -> list[str]:
        found = await (await client.get(f"/api/findings{query}")).json()
        return [item["key"] for item in found]

    assert await keys("") == ["new", "old"]
    assert await keys("?days=1") == ["new"]
    assert await keys("?days=0") == ["new"]  # at least one day
    assert await keys("?days=soon") == ["new", "old"]

    found = await (await client.get("/api/findings")).json()
    assert found[0]["names"] == {"node:7": "Living Room Plug"}
    assert found[0]["chain"][0]["key"] == "link.cause_unknown"


async def test_timeline_events_newest_first(
    aiohttp_client: AiohttpClient,
    ctx: Context,
    clock: Clock,
    engine: Engine,
    translations: Path,
) -> None:
    ctx.names.set("node:7", "Living Room Plug")
    await ctx.emit(kinds.MATTER_NODE_UNAVAILABLE, "test", "node:7", name="Old Name")
    clock.advance(minutes=1)
    await ctx.emit(kinds.THREAD_STATE, "test", role="leader")
    clock.advance(minutes=1)
    await ctx.emit(
        kinds.HA_POWER_OFF, "test", "entity:switch.media_plug", name="Media Plug"
    )
    client = await aiohttp_client(build(engine, translations))

    found = await (await client.get("/api/events")).json()
    newest = await (await client.get("/api/events?limit=1")).json()

    assert [(item["kind"], item["name"]) for item in found] == [
        (kinds.HA_POWER_OFF, "Media Plug"),
        (kinds.MATTER_NODE_UNAVAILABLE, "Living Room Plug"),
    ]
    assert found[0]["at"] == (T0 + timedelta(minutes=2)).isoformat()
    assert [item["kind"] for item in newest] == [kinds.HA_POWER_OFF]


async def test_languages_and_translations(
    aiohttp_client: AiohttpClient, engine: Engine, translations: Path
) -> None:
    client = await aiohttp_client(build(engine, translations))

    assert await (await client.get("/api/languages")).json() == ["de", "en"]
    german = await client.get("/api/i18n/de")
    assert json.loads(await german.text()) == {"nav": {"findings": "Befunde"}}
    for language in ("fr", "e1", "toolong", "..%2Fen"):
        assert (await client.get(f"/api/i18n/{language}")).status == 404


async def read_message(response: ClientResponse) -> str:
    lines: list[str] = []
    while True:
        line = (await asyncio.wait_for(response.content.readline(), 5)).decode()
        if line == "\n":
            return "".join(lines)
        lines.append(line)


async def test_stream_sends_findings_and_timeline_events(
    aiohttp_client: AiohttpClient,
    ctx: Context,
    engine: Engine,
    translations: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_app, "KEEPALIVE_S", 0.05)
    ctx.names.set("node:7", "Living Room Plug")
    client = await aiohttp_client(build(engine, translations))

    response = await client.get("/api/stream")
    assert response.headers["Content-Type"] == "text/event-stream"
    assert await read_message(response) == ": connected\n"
    assert await read_message(response) == ": keep-alive\n"

    await ctx.emit(kinds.THREAD_STATE, "test", role="leader")  # not on the timeline
    await ctx.emit(kinds.MATTER_NODE_UNAVAILABLE, "test", "node:7")
    await ctx.publish(finding("a", Severity.WARNING, 0, ended=False))

    messages = [await read_message(response) for _ in range(2)]
    topic, data = messages[0].split("\n", 1)
    assert topic == "event: event"
    assert json.loads(data.removeprefix("data: "))["kind"] == (
        kinds.MATTER_NODE_UNAVAILABLE
    )
    topic, data = messages[1].split("\n", 1)
    assert topic == "event: finding"
    assert json.loads(data.removeprefix("data: "))["names"] == {
        "node:7": "Living Room Plug"
    }

    # Once the page is gone, the stream stops listening.
    response.close()
    for _ in range(100):
        if not engine._listeners:
            break
        await asyncio.sleep(0.02)
    assert engine._listeners == []


async def test_dismissing_a_finding(
    aiohttp_client: AiohttpClient,
    store: Store,
    engine: Engine,
    translations: Path,
) -> None:
    await store.put_finding(finding("a", Severity.PROBLEM, 0, ended=False), T0)
    await store.put_finding(finding("b", Severity.WARNING, 0, ended=False), T0)
    await store.set_state("dismissed", {"gone": "whenever"})
    client = await aiohttp_client(build(engine, translations))

    async def state() -> tuple[dict[str, int], dict[str, bool]]:
        overview = await (await client.get("/api/overview")).json()
        found = await (await client.get("/api/findings")).json()
        return overview["open"], {f["key"]: f["dismissed"] for f in found}

    response = await client.post("/api/dismiss", json={"key": "a"})
    assert await response.json() == {"keys": ["a"], "dismissed": True}
    assert await state() == (
        {"problem": 0, "warning": 1, "info": 0},
        {"a": True, "b": False},
    )
    # Entries of findings that no longer exist are dropped on the way.
    assert await store.get_state("dismissed") == {"a": at(0).isoformat()}

    # The same situation coming back later is a new occurrence.
    await store.put_finding(finding("a", Severity.PROBLEM, 30, ended=False), T0)
    assert (await state())[1]["a"] is False

    await client.post("/api/dismiss", json={"key": "a"})
    await client.post("/api/dismiss", json={"key": "a", "dismissed": False})
    assert (await state())[1]["a"] is False


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ("not json", 400),
        ({"dismissed": True}, 400),
        (["a"], 400),
        ({"keys": 3}, 400),
        ({"key": "x"}, 404),
    ],
)
async def test_dismissing_needs_a_known_key(
    aiohttp_client: AiohttpClient,
    engine: Engine,
    translations: Path,
    body: object,
    status: int,
) -> None:
    client = await aiohttp_client(build(engine, translations))

    if isinstance(body, str):
        response = await client.post("/api/dismiss", data=body)
    else:
        response = await client.post("/api/dismiss", json=body)

    assert response.status == status


async def test_acknowledging_several_earlier_findings_at_once(
    aiohttp_client: AiohttpClient,
    store: Store,
    engine: Engine,
    translations: Path,
) -> None:
    for key in ("old", "older"):
        await store.put_finding(finding(key, Severity.WARNING, -60, ended=True), T0)
    client = await aiohttp_client(build(engine, translations))

    response = await client.post("/api/dismiss", json={"keys": ["old", "older"]})
    assert response.status == 200

    found = await (await client.get("/api/findings")).json()
    assert {f["key"]: f["dismissed"] for f in found} == {"old": True, "older": True}

    missing = await client.post("/api/dismiss", json={"keys": ["old", "nope"]})
    assert missing.status == 404


def offline_finding(subject: str, minutes: float, ended: bool = False) -> Finding:
    return Finding(
        key=f"offline:{subject}:{at(minutes).isoformat()}",
        rule="offline",
        severity=Severity.WARNING,
        title="finding.device_unreachable.title",
        started_at=at(minutes),
        ended_at=at(minutes + 1) if ended else None,
        chain=[Link(Role.CAUSE, "link.cause_unknown")],
        subjects=[subject],
    )


async def test_overview_tells_expected_and_known_absences_apart(
    aiohttp_client: AiohttpClient,
    store: Store,
    engine: Engine,
    translations: Path,
) -> None:
    await store.set_state(
        "matter.nodes", {"total": 5, "unavailable": ["node:1", "node:2", "node:3"]}
    )
    await store.set_state(
        "offline.away",
        {"node:1": {"since": at(0).isoformat(), "expected": True}},
    )
    await store.set_state("devices.habits", {"node:1": True, "node:3": False})
    known = offline_finding("node:2", 0)
    await store.put_finding(known, T0)
    await store.put_finding(offline_finding("node:3", 0), T0)
    await store.set_state("dismissed", {known.key: known.started_at.isoformat()})
    client = await aiohttp_client(build(engine, translations))

    body = await (await client.get("/api/overview")).json()

    flags = {
        item["subject"]: (item["usual"], item["known"], item["comes_and_goes"])
        for item in body["devices"]["unavailable"]
    }
    assert flags == {
        "node:1": (True, False, True),
        "node:2": (False, True, None),
        "node:3": (False, False, False),
    }
    assert body["open"] == {"problem": 0, "warning": 1, "info": 0}


async def test_marking_a_device_as_coming_and_going(
    aiohttp_client: AiohttpClient,
    store: Store,
    engine: Engine,
    translations: Path,
) -> None:
    current = offline_finding("node:4", 0)
    earlier = offline_finding("node:4", -60, ended=True)
    other = offline_finding("node:5", 0)
    for item in (current, earlier, other):
        await store.put_finding(item, T0)
    client = await aiohttp_client(build(engine, translations))

    response = await client.post(
        "/api/habit", json={"subject": "node:4", "comes_and_goes": True}
    )

    assert await response.json() == {"subject": "node:4", "comes_and_goes": True}
    assert await store.get_state("devices.habits") == {"node:4": True}
    # Only the absence going on now is taken as known.
    assert await store.get_state("dismissed") == {
        current.key: current.started_at.isoformat()
    }

    await client.post("/api/habit", json={"subject": "node:5", "comes_and_goes": False})
    assert await store.get_state("devices.habits") == {"node:4": True, "node:5": False}
    assert other.key not in (await store.get_state("dismissed"))

    await client.post("/api/habit", json={"subject": "node:4", "comes_and_goes": None})
    assert await store.get_state("devices.habits") == {"node:5": False}


@pytest.mark.parametrize(
    "body",
    [
        {"comes_and_goes": True},
        {"subject": "node:4", "comes_and_goes": "yes"},
        [],
        "not json",
    ],
)
async def test_marking_needs_a_subject_and_a_yes_or_no(
    aiohttp_client: AiohttpClient,
    engine: Engine,
    translations: Path,
    body: object,
) -> None:
    client = await aiohttp_client(build(engine, translations))

    if body == "not json":
        response = await client.post("/api/habit", data=b"{")
    else:
        response = await client.post("/api/habit", json=body)

    assert response.status == 400


def test_page_version_names_the_bundle(tmp_path: Path) -> None:
    assert web_app.page_version(tmp_path) is None

    (tmp_path / "app.js").write_bytes(b"console.log(1)")

    assert (
        web_app.page_version(tmp_path)
        == (hashlib.sha256(b"console.log(1)").hexdigest()[:12])
    )
