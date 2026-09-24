from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from conftest import source_for
from pytest_aiohttp import AiohttpServer

from matter_health import enrichers
from matter_health.config import Options
from matter_health.engine import Context
from matter_health.enrichers import Client, Knowledge, known, state_key
from matter_health.enrichers.unifi import UnifiEnricher, parse
from matter_health.store import Store

SWITCH = "02:00:00:00:00:0a"
AP = "02:00:00:00:00:0b"
BSSID = "02:00:00:00:00:0c"

DEVICES: list[dict[str, Any]] = [
    {"mac": SWITCH.upper(), "name": "Office Switch", "uplink": {"type": "wire"}},
    {
        "mac": AP,
        "model": "AP-1",
        "uplink": {"type": "wire", "uplink_mac": SWITCH, "uplink_remote_port": 8},
        "vap_table": [{"bssid": BSSID.upper(), "essid": "Home"}, {"essid": "x"}],
    },
    {"mac": "02:00:00:00:00:0d", "name": "Console"},
    {"name": "no mac"},
]

STATIONS: list[dict[str, Any]] = [
    {
        "mac": "02:00:00:00:10:01",
        "ip": "192.0.2.10",
        "hostname": "tv",
        "is_wired": True,
        "sw_mac": SWITCH,
        "sw_port": 4,
    },
    {
        "mac": "02:00:00:00:10:02",
        "last_ip": "192.0.2.11",
        "name": "Speaker",
        "is_wired": False,
        "ap_mac": AP,
        "essid": "Home",
        "signal": -78,
    },
    {"ip": "192.0.2.99"},
]


def test_what_a_unifi_controller_tells() -> None:
    knowledge = parse(DEVICES, STATIONS)

    assert knowledge.access_points == {BSSID: AP}
    assert knowledge.uplink(["192.0.2.10"]) == {
        "wired": True,
        "via": "Office Switch",
        "port": 4,
        "ssid": None,
        "signal": None,
        "quality": None,
    }
    assert knowledge.uplink(["fe80::1", "192.0.2.11"]) == {
        "wired": False,
        "via": "AP-1",
        "port": None,
        "ssid": "Home",
        "signal": -78,
        "quality": "weak",
    }
    # An access point is found by the BSSID it sends.
    assert knowledge.access_point_name(BSSID.upper()) == "AP-1"
    uplink = knowledge.uplink(mac=BSSID)
    assert uplink is not None
    assert (uplink["via"], uplink["port"]) == ("Office Switch", 8)
    # A device whose way in is not told says nothing.
    assert knowledge.uplink(mac="02:00:00:00:00:0d") is None
    assert knowledge.uplink(["192.0.2.50"]) is None
    assert knowledge.access_point_name("02:00:00:00:00:ff") is None


def test_a_mac_beats_an_address() -> None:
    knowledge = Knowledge(
        [
            Client("02:00:00:00:00:01", "192.0.2.1", "old", True),
            Client("02:00:00:00:00:02", "192.0.2.1", "new", False),
        ],
        {},
    )

    found = knowledge.client(["192.0.2.1"], "02:00:00:00:00:02")
    assert found is not None
    assert found.name == "new"


async def test_what_every_enricher_learned_is_merged(
    ctx: Context, store: Store
) -> None:
    assert (await known(ctx)).clients == []
    await store.set_state(state_key("unifi"), parse(DEVICES, STATIONS).dump())

    merged = await known(ctx)

    assert len(merged.clients) == 5
    assert merged.access_points == {BSSID: AP}


def unifi_options(options: Options, **settings: Any) -> dict[str, Any]:
    return {"extra": {**options.extra, "unifi": settings}}


class FakeController:
    """A UniFi controller: UniFi OS, or the standalone application."""

    def __init__(self, unifi_os: bool = True, accept: bool = True) -> None:
        self.unifi_os = unifi_os
        self.accept = accept
        self.seen: list[tuple[str, str | None]] = []

    def app(self) -> web.Application:
        app = web.Application()
        prefix = "/proxy/network" if self.unifi_os else ""
        app.router.add_post(
            "/api/auth/login" if self.unifi_os else "/api/login", self.login
        )
        app.router.add_get(prefix + "/api/s/{site}/stat/{what}", self.stat)
        return app

    async def login(self, request: web.Request) -> web.Response:
        body = await request.json()
        if not self.accept or body["password"] != "secret":
            return web.Response(status=401)
        response = web.json_response({})
        response.set_cookie("TOKEN", "t")
        return response

    async def stat(self, request: web.Request) -> web.Response:
        key = request.headers.get("X-API-KEY")
        if key is None and "TOKEN" not in request.cookies:
            return web.Response(status=401)
        self.seen.append((request.match_info["site"], key))
        data = DEVICES if request.match_info["what"] == "device" else STATIONS
        return web.json_response({"data": data})


async def run_once(source: UnifiEnricher, monkeypatch: pytest.MonkeyPatch) -> None:
    class Stop(Exception):
        pass

    async def stop(_: float) -> None:
        raise Stop

    monkeypatch.setattr(enrichers, "asyncio", SimpleNamespace(sleep=stop))
    with pytest.raises(Stop):
        await source.run()


@pytest.mark.parametrize(
    ("unifi_os", "settings", "seen"),
    [
        (True, {"api_key": "k"}, ("default", "k")),
        (True, {"username": "u", "password": "secret"}, ("default", None)),
        (False, {"username": "u", "password": "secret", "site": "s"}, ("s", None)),
    ],
)
async def test_the_controller_is_read_however_it_lets_in(
    ctx: Context,
    store: Store,
    aiohttp_server: AiohttpServer,
    monkeypatch: pytest.MonkeyPatch,
    unifi_os: bool,
    settings: dict[str, Any],
    seen: tuple[str, str | None],
) -> None:
    controller = FakeController(unifi_os)
    server = await aiohttp_server(controller.app())
    url = str(server.make_url("/"))
    source = source_for(
        UnifiEnricher, ctx, **unifi_options(ctx.options, url=url, **settings)
    )

    await run_once(source, monkeypatch)

    assert controller.seen == [seen, seen]
    stored = await store.get_state(state_key("unifi"))
    assert Knowledge.load(stored).access_points == {BSSID: AP}
    assert source.ctx._engine is not None
    assert source.ctx._engine.status["unifi"]["ok"] is True


async def test_a_refused_login_is_said_plainly(
    ctx: Context, aiohttp_server: AiohttpServer
) -> None:
    server = await aiohttp_server(FakeController(accept=False).app())
    source = source_for(
        UnifiEnricher,
        ctx,
        **unifi_options(ctx.options, url=str(server.make_url("")), password="x"),
    )

    with pytest.raises(PermissionError):
        await source.run()


async def test_something_that_is_no_controller(
    ctx: Context, aiohttp_server: AiohttpServer
) -> None:
    server = await aiohttp_server(web.Application())
    source = source_for(
        UnifiEnricher, ctx, **unifi_options(ctx.options, url=str(server.make_url("")))
    )

    with pytest.raises(ConnectionError):
        await source.run()


def test_the_enricher_runs_only_when_configured(options: Options) -> None:
    assert not UnifiEnricher.enabled(options)
    assert not UnifiEnricher.enabled(
        Options(extra={"unifi": "not a dict"}, data_dir=options.data_dir)
    )
    assert UnifiEnricher.enabled(
        Options(extra={"unifi": {"url": "https://unifi"}}, data_dir=options.data_dir)
    )
