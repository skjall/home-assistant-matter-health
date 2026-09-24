from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from conftest import source_for
from pytest_aiohttp import AiohttpServer

from matter_health import enrichers
from matter_health.engine import Context
from matter_health.enrichers import Client, Knowledge, known, state_key
from matter_health.enrichers.unifi import UnifiEnricher, parse
from matter_health.store import Store

AP = "02:00:00:00:00:0b"

TRACKERS = {
    "device_tracker.tv",
    "device_tracker.speaker",
    "device_tracker.laptop",
    "device_tracker.access_point",
    "device_tracker.no_mac",
}

STATES: list[dict[str, Any]] = [
    {
        "entity_id": "device_tracker.tv",
        "state": "home",
        "attributes": {
            "mac": "02:00:00:00:10:01",
            "ip": "192.0.2.10",
            "host_name": "tv",
            "is_guest": False,
        },
    },
    {
        "entity_id": "device_tracker.speaker",
        "state": "home",
        "attributes": {
            "mac": "02:00:00:00:10:02",
            "ip": "192.0.2.11",
            "name": "Speaker",
            "is_guest": False,
            "ap_mac": AP.upper(),
            "essid": "Home",
        },
    },
    {
        # Away: where it was is no news.
        "entity_id": "device_tracker.laptop",
        "state": "not_home",
        "attributes": {"mac": "02:00:00:00:10:03", "host_name": "laptop"},
    },
    {
        # The integration's own access point: no client details.
        "entity_id": "device_tracker.access_point",
        "state": "home",
        "attributes": {"mac": AP, "ip": "192.0.2.3"},
    },
    {"entity_id": "device_tracker.no_mac", "state": "home", "attributes": {}},
    {
        "entity_id": "device_tracker.phone",
        "state": "home",
        "attributes": {"mac": "02:00:00:00:10:04", "host_name": "phone"},
    },
]

DEVICES: list[dict[str, Any]] = [
    {"name": "AP-1", "name_by_user": "Hallway AP", "connections": [["mac", AP]]},
    {"name": "Hub", "connections": [["zigbee", "0x01"]]},
    {"connections": [["mac", "02:00:00:00:00:0c"]]},
]


def test_what_the_unifi_integration_tells() -> None:
    knowledge = parse(TRACKERS, STATES, DEVICES)

    assert knowledge.clients == [
        Client("02:00:00:00:10:01", "192.0.2.10", "tv"),
        Client(
            "02:00:00:00:10:02", "192.0.2.11", "Speaker", False, "Hallway AP", "Home"
        ),
    ]
    assert knowledge.uplink(["fe80::1", "192.0.2.10"]) == {
        "wired": True,
        "via": None,
        "ssid": None,
    }
    assert knowledge.uplink(["192.0.2.3"]) is None
    assert knowledge.uplink(None) is None


async def test_what_every_enricher_learned_is_merged(
    ctx: Context, store: Store
) -> None:
    assert (await known(ctx)).clients == []
    await store.set_state(state_key("unifi"), parse(TRACKERS, STATES, DEVICES).dump())

    assert len((await known(ctx)).clients) == 2


class FakeCore:
    """Home Assistant's websocket, answering the registry and state lists."""

    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self.entities = entities
        self.asked: list[str] = []

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        await ws.receive_json()
        await ws.send_json({"type": "auth_ok"})
        answers = {
            "config/entity_registry/list": self.entities,
            "get_states": STATES,
            "config/device_registry/list": DEVICES,
        }
        async for message in ws:
            command = message.json()
            self.asked.append(command["type"])
            await ws.send_json(
                {
                    "id": command["id"],
                    "type": "result",
                    "success": True,
                    "result": answers[command["type"]],
                }
            )
        return ws


async def run_once(source: UnifiEnricher, monkeypatch: pytest.MonkeyPatch) -> None:
    class Stop(Exception):
        pass

    async def stop(_: float) -> None:
        raise Stop

    monkeypatch.setattr(enrichers, "asyncio", SimpleNamespace(sleep=stop))
    with pytest.raises(Stop):
        await source.run()


async def serve_core(aiohttp_server: AiohttpServer, core: FakeCore) -> str:
    app = web.Application()
    app.router.add_get("/core/websocket", core.handle)
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/")


async def test_the_integration_is_read_from_home_assistant(
    ctx: Context,
    store: Store,
    aiohttp_server: AiohttpServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = FakeCore(
        [{"entity_id": e, "platform": "unifi"} for e in sorted(TRACKERS)]
        + [
            {"entity_id": "sensor.speaker_uptime", "platform": "unifi"},
            {"entity_id": "device_tracker.phone", "platform": "mobile_app"},
        ]
    )
    url = await serve_core(aiohttp_server, core)
    source = source_for(UnifiEnricher, ctx, supervisor_url=url)

    await run_once(source, monkeypatch)

    assert core.asked == [
        "config/entity_registry/list",
        "get_states",
        "config/device_registry/list",
    ]
    stored = Knowledge.load(await store.get_state(state_key("unifi")))
    assert [c.name for c in stored.clients] == ["tv", "Speaker"]
    assert source.ctx._engine is not None
    assert source.ctx._engine.status["unifi"]["ok"] is True


async def test_without_the_integration_nothing_is_missing(
    ctx: Context,
    store: Store,
    aiohttp_server: AiohttpServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = FakeCore([{"entity_id": "light.x", "platform": "hue"}])
    url = await serve_core(aiohttp_server, core)
    source = source_for(UnifiEnricher, ctx, supervisor_url=url)

    await run_once(source, monkeypatch)

    assert core.asked == ["config/entity_registry/list"]
    assert await store.get_state(state_key("unifi")) == []
    assert source.ctx._engine is not None
    assert "unifi" not in source.ctx._engine.status
