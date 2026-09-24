"""HTTP server behind Home Assistant's ingress.

Only the Supervisor's ingress proxy may talk to the add-on: the page shows the
names of devices and people in the home, and ingress is where Home Assistant
checks that the viewer is logged in. Requests from anywhere else are refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import bridges, kinds
from ..engine import Engine, event_payload
from ..model import Finding
from ..rules.habits import HABITS
from ..stories import stories
from ..transports import ROOT, TRANSPORTS

_LOGGER = logging.getLogger(__name__)

#: The Supervisor's ingress proxy connects from this network.
INGRESS_NETWORK = ipaddress.ip_network("172.30.32.0/23")

STATIC = Path(__file__).parent / "static"
TRANSLATIONS = Path(__file__).resolve().parents[3] / "translations" / "ui"

#: What the timeline shows; the rest is raw material for the rules.
TIMELINE_KINDS: tuple[str, ...] = (
    kinds.BORDER_ROUTER_APPEARED,
    kinds.BORDER_ROUTER_GONE,
    kinds.MATTER_NODE_ADDED,
    kinds.MATTER_NODE_REMOVED,
    kinds.MATTER_NODE_AVAILABLE,
    kinds.MATTER_NODE_UNAVAILABLE,
    kinds.HA_POWER_OFF,
    kinds.HA_POWER_ON,
    kinds.THREAD_LEADER_LOST,
    kinds.THREAD_LEADER_CHANGED,
    kinds.THREAD_FOREIGN_PARTITION,
    kinds.THREAD_PARTITION_CHANGED,
    kinds.COMMISSIONING_CONTACT,
    kinds.COMMISSIONING_COMPLETED,
    kinds.COMMISSIONING_FAILED,
    kinds.SOURCE_STATUS,
    kinds.SYSTEM_UPDATED,
    kinds.MATTER_NODES_LOST,
)

ENGINE: web.AppKey[Engine] = web.AppKey("engine", Engine)
TRUST_ALL: web.AppKey[bool] = web.AppKey("trust_all", bool)
TRANSLATION_DIR: web.AppKey[Path] = web.AppKey("translations", Path)
BUILD: web.AppKey[str | None] = web.AppKey("build")

#: Findings the user marked as dealt with, by key, with the start of the
#: occurrence they dismissed. A situation that ends and comes back under the
#: same key starts anew and is shown again.
DISMISSED = "dismissed"

#: Proxies drop a connection that stays silent; a comment line keeps it open.
KEEPALIVE_S = 25.0

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@web.middleware
async def ingress_only(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Refuse every request that does not come through ingress."""
    if not request.app[TRUST_ALL]:
        peer = request.remote or ""
        try:
            allowed = ipaddress.ip_address(peer) in INGRESS_NETWORK
        except ValueError:
            allowed = False
        if not allowed:
            raise web.HTTPForbidden(text="Open Matter Health from Home Assistant.")
    return await handler(request)


@web.middleware
async def revalidate(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Make browsers ask again before reusing the page or its files.

    The companion apps keep cached files across updates of the add-on
    otherwise and go on showing the old page. The files are small and the
    answer to a repeated question is a cheap "not modified".
    """
    response = await handler(request)
    if not request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def with_names(engine: Engine, finding: dict[str, Any]) -> dict[str, Any]:
    """Add the current names of a finding's subjects.

    A finding stores the names known when it was written. A device added a
    moment ago gets its name in Home Assistant only afterwards, so the page
    also receives today's names and prefers them.
    """
    subjects = finding.get("subjects", [])
    book = engine.ctx.names
    names = {s: book.get(s) for s in subjects if book.get(s)}
    devices = {s: book.device(s) for s in subjects if book.device(s)}
    return {**finding, "names": names, "devices": devices}


async def index(request: web.Request) -> web.StreamResponse:
    """Serve the single page; its assets are relative to the ingress path."""
    return web.FileResponse(STATIC / "index.html")


def is_dismissed(finding: Finding, dismissed: dict[str, str]) -> bool:
    """Whether the user dismissed this very occurrence of the finding."""
    return dismissed.get(finding.key) == finding.started_at.isoformat()


async def absences(
    store: Any, findings: list[Finding], dismissed: dict[str, str]
) -> tuple[set[str], set[str]]:
    """Devices away as they usually are, and those whose absence the user knows."""
    away = await store.get_state("offline.away") or {}
    usual = {s for s, entry in away.items() if entry.get("expected")}
    known = {
        subject
        for f in findings
        if f.rule == "offline" and f.ended_at is None and is_dismissed(f, dismissed)
        for subject in f.subjects
    }
    return usual, known


async def overview(request: web.Request) -> web.Response:
    """Everything the header of the page needs in one answer."""
    engine = request.app[ENGINE]
    store = engine.ctx.store
    findings = await store.findings()
    dismissed = await store.get_state(DISMISSED) or {}
    open_findings = [
        f for f in findings if f.ended_at is None and not is_dismissed(f, dismissed)
    ]
    nodes = await store.get_state("matter.nodes") or {}
    habits = await store.get_state(HABITS) or {}
    usual, known = await absences(store, findings, dismissed)
    unavailable = [
        {
            "subject": s,
            "name": engine.ctx.names.get(s),
            "device_id": engine.ctx.names.device(s),
            "usual": s in usual,
            "known": s in known,
            "comes_and_goes": habits.get(s),
        }
        for s in nodes.get("unavailable", [])
    ]
    return web.json_response(
        {
            "sources": engine.status,
            "devices": {"total": nodes.get("total"), "unavailable": unavailable},
            "transports": await transport_summaries(engine),
            "open": {
                severity: sum(1 for f in open_findings if f.severity.value == severity)
                for severity in ("problem", "warning", "info")
            },
            "now": engine.ctx.now().isoformat(),
            "build": request.app[BUILD],
        }
    )


async def transport_summaries(engine: Engine) -> list[dict[str, Any]]:
    """Return what each transport in use says about itself, most devices first.

    A transport counts as in use when a device uses it or it has gateways,
    such as border routers of a Thread network nothing is paired to yet.
    """
    mine: dict[str, str] = await engine.ctx.store.get_state("matter.transports") or {}
    summaries = []
    for name in TRANSPORTS.names():
        devices = sum(1 for transport in mine.values() if transport == name)
        summary = await TRANSPORTS.get(name)(engine.ctx).summary(devices)
        if devices or summary.get("gateways"):
            summaries.append({"name": name, "devices": devices, **summary})
    return sorted(summaries, key=lambda s: -int(s["devices"]))


async def topology(request: web.Request) -> web.Response:
    """Return every transport's part of the network, with names and who is away.

    Ids are made unique across transports by prefixing the transport; the
    home network every transport hangs on keeps its id.
    """
    engine = request.app[ENGINE]
    store = engine.ctx.store
    names = engine.ctx.names
    nodes = await store.get_state("matter.nodes") or {}
    away = set(nodes.get("unavailable", []))
    dismissed = await store.get_state(DISMISSED) or {}
    usual, known = await absences(store, await store.findings(), dismissed)
    # Devices behind a bridge hang on the bridge, not on a transport.
    nodes_away = {s for s in away if not bridges.is_bridged(s)}
    entries = []
    for name in TRANSPORTS.names():
        for raw in await TRANSPORTS.get(name)(engine.ctx).picture(nodes_away):
            entry = dict(raw)
            entry["transport"] = name
            entry["id"] = f"{name}:{raw['id']}"
            parent = raw.get("parent")
            entry["parent"] = parent if parent in (ROOT, None) else f"{name}:{parent}"
            entries.append(entry)
    entries += await bridges.picture(store, entries)
    for entry in entries:
        subject = entry.get("subject")
        entry["name"] = names.get(subject)
        entry["device_id"] = names.device(subject)
        entry["available"] = subject not in away
        # Away, but as expected or as the user knows: no alarm in the picture.
        entry["resting"] = subject in away and (subject in usual or subject in known)
    return web.json_response({"nodes": entries})


async def findings(request: web.Request) -> web.Response:
    """Return findings of the last days, open ones always included."""
    engine = request.app[ENGINE]
    days = _int(request.query.get("days"), 7, 1, 365)
    since = engine.ctx.now() - timedelta(days=days)
    found = await engine.ctx.store.findings(since)
    belongs = stories(found, (type(rule) for rule in engine.rules), engine.ctx.now())
    dismissed = await engine.ctx.store.get_state(DISMISSED) or {}
    return web.json_response(
        [
            with_names(
                engine,
                {
                    **f.as_dict(),
                    "part_of": belongs.get(f.key),
                    "dismissed": is_dismissed(f, dismissed),
                },
            )
            for f in found
        ]
    )


async def dismiss(request: web.Request) -> web.Response:
    """Mark findings as dealt with, or show them again.

    Takes one ``key`` or a list of ``keys``, so a whole list of earlier
    findings can be acknowledged at once.
    """
    engine = request.app[ENGINE]
    store = engine.ctx.store
    try:
        body = await request.json()
        keys = [str(k) for k in body["keys"]] if "keys" in body else [str(body["key"])]
        wanted = bool(body.get("dismissed", True))
    except ValueError, KeyError, TypeError:
        raise web.HTTPBadRequest(text="expected {key or keys, dismissed}") from None
    current = {f.key: f for f in await store.findings()}
    for key in keys:
        if key not in current:
            raise web.HTTPNotFound(text=f"no finding {key}")
    # Entries for findings that have since been removed are dropped here.
    dismissed = {
        k: v
        for k, v in (await store.get_state(DISMISSED) or {}).items()
        if k in current
    }
    for key in keys:
        if wanted:
            dismissed[key] = current[key].started_at.isoformat()
        else:
            dismissed.pop(key, None)
    await store.set_state(DISMISSED, dismissed)
    return web.json_response({"keys": keys, "dismissed": wanted})


async def habit(request: web.Request) -> web.Response:
    """Say whether a device comes and goes by habit, or forget what was said.

    Takes ``{subject, comes_and_goes}`` with true, false or null. A device
    marked as coming and going is reported only once it stays away longer
    than usual, so its current absence is taken as known.
    """
    engine = request.app[ENGINE]
    store = engine.ctx.store
    wrong = web.HTTPBadRequest(
        text="expected {subject, comes_and_goes: true, false or null}"
    )
    try:
        body = await request.json()
        subject = str(body["subject"])
        value = body.get("comes_and_goes")
    except ValueError, KeyError, TypeError:
        raise wrong from None
    if value is not None and not isinstance(value, bool):
        raise wrong
    habits = dict(await store.get_state(HABITS) or {})
    if value is None:
        habits.pop(subject, None)
    else:
        habits[subject] = value
    await store.set_state(HABITS, habits)
    if value:
        dismissed = dict(await store.get_state(DISMISSED) or {})
        for finding in await store.findings():
            if (
                finding.rule == "offline"
                and finding.ended_at is None
                and subject in finding.subjects
            ):
                dismissed[finding.key] = finding.started_at.isoformat()
        await store.set_state(DISMISSED, dismissed)
    return web.json_response({"subject": subject, "comes_and_goes": value})


async def events(request: web.Request) -> web.Response:
    """Return the timeline: who came, who went, what was switched."""
    engine = request.app[ENGINE]
    limit = _int(request.query.get("limit"), 200, 1, 1000)
    found = await engine.ctx.store.events(TIMELINE_KINDS, limit=limit)
    payload = []
    for event in reversed(found):
        item = event_payload(event)
        item["name"] = engine.ctx.names.get(event.subject) or event.data.get("name")
        payload.append(item)
    return web.json_response(payload)


async def languages(request: web.Request) -> web.Response:
    """Return the languages the page is translated into."""
    directory = request.app[TRANSLATION_DIR]
    return web.json_response(sorted(p.stem for p in directory.glob("*.json")))


async def translation(request: web.Request) -> web.StreamResponse:
    """Return the words of the page in one language."""
    language = request.match_info["language"]
    if not language.isalpha() or len(language) > 5:
        raise web.HTTPNotFound()
    path = request.app[TRANSLATION_DIR] / f"{language}.json"
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def stream(request: web.Request) -> web.StreamResponse:
    """Server-sent events: new findings and timeline entries as they happen."""
    engine = request.app[ENGINE]
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=200)

    async def listener(topic: str, payload: dict[str, Any]) -> None:
        if topic == "event" and payload.get("kind") not in TIMELINE_KINDS:
            return
        if topic == "finding":
            payload = with_names(engine, payload)
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait((topic, payload))

    unsubscribe = engine.subscribe(listener)
    try:
        await response.write(b": connected\n\n")
        while True:
            try:
                topic, payload = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
            except TimeoutError:
                await response.write(b": keep-alive\n\n")
                continue
            data = json.dumps(payload, default=str)
            await response.write(f"event: {topic}\ndata: {data}\n\n".encode())
    except ConnectionResetError:
        # The viewer closed the page; nothing is left to send to.
        pass
    finally:
        unsubscribe()
    return response


def _int(raw: str | None, default: int, low: int, high: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        value = default
    return max(low, min(high, value))


def page_version(static: Path = STATIC) -> str | None:
    """Return the version the page's bundle is named with, as the build names it.

    A page left open across an update compares it with its own and reloads.
    """
    bundle = static / "app.js"
    if not bundle.is_file():
        return None
    return hashlib.sha256(bundle.read_bytes()).hexdigest()[:12]


def create_app(
    engine: Engine,
    *,
    trust_all: bool = False,
    translations: Path = TRANSLATIONS,
) -> web.Application:
    """Build the web application; ``trust_all`` lifts the ingress check."""
    app = web.Application(middlewares=[ingress_only, revalidate])
    app[ENGINE] = engine
    app[TRUST_ALL] = trust_all
    app[TRANSLATION_DIR] = translations
    app[BUILD] = page_version()
    app.router.add_get("/", index)
    app.router.add_get("/api/overview", overview)
    app.router.add_get("/api/findings", findings)
    app.router.add_get("/api/topology", topology)
    app.router.add_post("/api/dismiss", dismiss)
    app.router.add_post("/api/habit", habit)
    app.router.add_get("/api/events", events)
    app.router.add_get("/api/languages", languages)
    app.router.add_get("/api/i18n/{language}", translation)
    app.router.add_get("/api/stream", stream)
    if STATIC.is_dir():
        app.router.add_static("/static", STATIC)
    return app
