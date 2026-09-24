import asyncio
from typing import Any

import pytest
from aiohttp import web
from conftest import source_for
from pytest_aiohttp import AiohttpServer

from matter_health import kinds
from matter_health.engine import Context
from matter_health.sources import home_assistant
from matter_health.sources.home_assistant import (
    AUTOMATION_MEMORY,
    HomeAssistantSource,
)
from matter_health.store import Store

DEVICES = [
    {
        "id": "device-plug",
        "name": "Smart Plug",
        "name_by_user": "Living Room Plug",
        "identifiers": [
            ["matter", "deviceid_0000000000ABCDEF-000000000000002A-MatterNodeDevice"],
            ["other", "anything"],
        ],
    },
    {"name": "Hallway Light", "identifiers": [["matter", "serial_1234"]]},
    {"name": "Zigbee Button", "identifiers": [["zha", "deviceid_01-02-x"]]},
    {"name": "No Identifiers"},
]

ENTITIES = [
    {"entity_id": "switch.media_plug", "device_id": "dev-media"},
    {"entity_id": "sensor.media_plug_power", "device_id": "dev-media"},
    {"entity_id": "switch.tv_outlet", "device_id": None},
    {"entity_id": "switch.child_lock", "device_id": "dev-lock"},
    {"entity_id": "sensor.lock_battery", "device_id": "dev-lock"},
    {"entity_id": "sensor.orphan_energy"},
]

STATES = [
    {"entity_id": "switch.media_plug", "attributes": {"friendly_name": "Media Plug"}},
    {
        "entity_id": "sensor.media_plug_power",
        "attributes": {"device_class": "power", "friendly_name": "Media Plug Power"},
    },
    {
        "entity_id": "switch.tv_outlet",
        "attributes": {"device_class": "outlet", "friendly_name": "TV Outlet"},
    },
    {"entity_id": "switch.child_lock", "attributes": {}},
    {"entity_id": "sensor.lock_battery", "attributes": {"device_class": "battery"}},
    {"entity_id": "sensor.orphan_energy", "attributes": {"device_class": "energy"}},
    {"entity_id": "sensor.no_attributes"},
    {
        "entity_id": "person.alex",
        "attributes": {"friendly_name": "Alex", "user_id": "user-alex"},
    },
    {"entity_id": "person.guest", "attributes": {"user_id": "user-guest"}},
    {"entity_id": "person.nobody", "attributes": {"friendly_name": "Nobody"}},
]


def switched(
    entity_id: str,
    old: str | None,
    new: str,
    context: dict[str, Any] | None,
    name: str | None = None,
) -> dict[str, Any]:
    new_state: dict[str, Any] = {"state": new, "context": context}
    if name:
        new_state["attributes"] = {"friendly_name": name}
    return {
        "event_type": "state_changed",
        "data": {
            "entity_id": entity_id,
            "old_state": {"state": old} if old else None,
            "new_state": new_state,
        },
    }


EVENING_OFF = {
    "event_type": "automation_triggered",
    "data": {"name": "Evening Off", "entity_id": "automation.evening_off"},
    "context": {"id": "ctx-evening"},
}


class FakeHomeAssistant:
    """Home Assistant's websocket API, as far as the source uses it."""

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        greeting: str = "auth_required",
        failing: str | None = None,
        binary_at_end: bool = False,
        early: list[dict[str, Any]] | None = None,
    ) -> None:
        self.events = events or []
        self.early = early or []
        self.greeting = greeting
        self.failing = failing
        self.binary_at_end = binary_at_end
        self.devices_later: list[dict[str, Any]] = []
        self.connections = 0
        self.commands: list[dict[str, Any]] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/core/websocket", self.handle)
        return app

    def answer(self, command: dict[str, Any]) -> Any:
        return {
            "config/device_registry/list": (
                DEVICES if self.connections == 1 else self.devices_later
            ),
            "config/entity_registry/list": ENTITIES,
            "get_states": STATES,
        }.get(command["type"])

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        self.connections += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": self.greeting, "ha_version": "2030.5.0"})
        if self.greeting != "auth_required":
            await ws.close()
            return ws
        auth = await ws.receive_json()
        if auth.get("access_token") != "test-token":
            await ws.send_json({"type": "auth_invalid", "message": "Invalid"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        subscriptions = 0
        async for message in ws:
            command = message.json()
            self.commands.append(command)
            number = command["id"]
            # Messages for other commands arrive in between and are skipped.
            await ws.send_json({"id": number, "type": "pong"})
            await ws.send_json({"id": number + 100, "type": "result", "success": True})
            if command["type"] == self.failing:
                await ws.send_json(
                    {
                        "id": number,
                        "type": "result",
                        "success": False,
                        "error": {"code": "unknown_command"},
                    }
                )
                continue
            if command["type"] == "subscribe_events" and subscriptions == 0:
                # Home Assistant starts sending as soon as it subscribed, even
                # before the next subscription has been answered.
                for event in self.early:
                    await ws.send_json({"id": number, "type": "event", "event": event})
            await ws.send_json(
                {
                    "id": number,
                    "type": "result",
                    "success": True,
                    "result": self.answer(command),
                }
            )
            if command["type"] == "subscribe_events":
                subscriptions += 1
                if subscriptions == 3:
                    await self.push(ws, number)
                    break
        await ws.close()
        return ws

    async def push(self, ws: web.WebSocketResponse, number: int) -> None:
        for event in self.events:
            await ws.send_json({"id": number, "type": "event", "event": event})
        await ws.send_json({"id": number, "type": "result", "success": True})
        if self.binary_at_end:
            await ws.send_bytes(b"\x00")


async def serve(aiohttp_server: AiohttpServer, fake: FakeHomeAssistant) -> str:
    server = await aiohttp_server(fake.app())
    return f"http://{server.host}:{server.port}"


async def test_learns_names_and_reports_power_switches(
    ctx: Context,
    store: Store,
    aiohttp_server: AiohttpServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(home_assistant, "REFRESH_DELAY_S", 0)
    fake = FakeHomeAssistant(
        [
            EVENING_OFF,
            {"event_type": "automation_triggered", "data": {}, "context": None},
            switched("switch.media_plug", "on", "off", {"id": "ctx-evening"}),
            switched(
                "switch.tv_outlet",
                "off",
                "on",
                {"id": "c2", "user_id": "user-alex"},
                name="TV Outlet",
            ),
            switched("switch.media_plug", "off", "on", {"parent_id": "ctx-evening"}),
            switched("switch.media_plug", "on", "off", {"parent_id": "elsewhere"}),
            switched("switch.tv_outlet", "on", "off", None),
            switched("switch.child_lock", "on", "off", None),
            switched("switch.media_plug", "off", "off", None),
            switched("switch.media_plug", "off", "unavailable", None),
            switched("switch.media_plug", None, "on", None),
            {"event_type": "device_registry_updated", "data": {"action": "update"}},
            {"event_type": "device_registry_updated", "data": {"action": "update"}},
        ],
        binary_at_end=True,
    )
    fake.devices_later = [
        {
            "name": "Kitchen Sensor",
            "identifiers": [["matter", "deviceid_ABCDEF-2B-MatterNodeDevice"]],
        }
    ]
    source = source_for(
        HomeAssistantSource, ctx, supervisor_url=await serve(aiohttp_server, fake)
    )

    await source.run()
    assert source._refresh is not None
    await source._refresh

    assert source.power_switches == {"switch.media_plug", "switch.tv_outlet"}
    assert source.people == {"user-alex": "Alex", "user-guest": "person.guest"}
    assert ctx.names.get("node:42") == "Living Room Plug"
    assert ctx.names.device("node:42") == "device-plug"
    assert ctx.names.get("node:43") == "Kitchen Sensor"
    assert ctx.names.get("entity:switch.tv_outlet") == "TV Outlet"
    assert ctx.names.get("entity:switch.child_lock") is None

    found = [(e.kind, e.subject, e.data) for e in await store.events()]

    def power(kind: str, entity: str, name: str, origin: str, by: str | None) -> Any:
        return (kind, f"entity:{entity}", {"name": name, "origin": origin, "by": by})

    assert found == [
        power(
            kinds.HA_POWER_OFF,
            "switch.media_plug",
            "switch.media_plug",
            "automation",
            "Evening Off",
        ),
        power(kinds.HA_POWER_ON, "switch.tv_outlet", "TV Outlet", "person", "Alex"),
        power(
            kinds.HA_POWER_ON,
            "switch.media_plug",
            "switch.media_plug",
            "automation",
            "Evening Off",
        ),
        power(
            kinds.HA_POWER_OFF,
            "switch.media_plug",
            "switch.media_plug",
            "automation",
            None,
        ),
        power(
            kinds.HA_POWER_OFF, "switch.tv_outlet", "switch.tv_outlet", "unknown", None
        ),
    ]
    assert [c["type"] for c in fake.commands] == [
        "config/device_registry/list",
        "config/entity_registry/list",
        "get_states",
        "subscribe_events",
        "subscribe_events",
        "subscribe_events",
        "config/device_registry/list",
    ]
    assert fake.connections == 2
    assert source.ctx._engine is not None
    assert source.ctx._engine.status["home_assistant"]["ok"] is True


async def test_run_ends_when_home_assistant_closes(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeHomeAssistant()
    source = source_for(
        HomeAssistantSource, ctx, supervisor_url=await serve(aiohttp_server, fake)
    )

    await source.run()

    assert await store.events() == []


async def test_needs_a_token(ctx: Context) -> None:
    source = source_for(HomeAssistantSource, ctx, supervisor_token=None)

    with pytest.raises(PermissionError, match="no Supervisor token"):
        await source.run()


async def test_a_refused_token(ctx: Context, aiohttp_server: AiohttpServer) -> None:
    source = source_for(
        HomeAssistantSource,
        ctx,
        supervisor_url=await serve(aiohttp_server, FakeHomeAssistant()),
        supervisor_token="wrong",
    )

    with pytest.raises(PermissionError, match="refused the token"):
        await source.run()


async def test_an_unexpected_greeting(
    ctx: Context, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeHomeAssistant(greeting="auth_ok")
    source = source_for(
        HomeAssistantSource, ctx, supervisor_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(ConnectionError, match="unexpected greeting auth_ok"):
        await source.run()


async def test_a_failing_command(ctx: Context, aiohttp_server: AiohttpServer) -> None:
    fake = FakeHomeAssistant(failing="get_states")
    source = source_for(
        HomeAssistantSource, ctx, supervisor_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(RuntimeError, match="unknown_command"):
        await source.run()


async def test_remembers_only_recent_automation_runs(ctx: Context) -> None:
    source = source_for(HomeAssistantSource, ctx)

    for number in range(AUTOMATION_MEMORY + 1):
        await source.on_event(
            {
                "event_type": "automation_triggered",
                "data": {"entity_id": f"automation.run_{number}"},
                "context": {"id": f"ctx-{number}"},
            }
        )

    assert len(source.automations) == AUTOMATION_MEMORY
    assert "ctx-0" not in source.automations
    assert source.origin({"id": "ctx-1"}) == ("automation", "automation.run_1")
    assert source.origin({"id": 5}) == ("unknown", None)
    assert source.origin({"user_id": "user-unknown"}) == ("person", None)


async def test_events_during_subscribing_are_not_lost(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeHomeAssistant(
        early=[switched("switch.tv_outlet", "on", "off", None, name="TV Outlet")]
    )
    source = source_for(
        HomeAssistantSource, ctx, supervisor_url=await serve(aiohttp_server, fake)
    )

    await source.run()

    assert [(e.kind, e.subject) for e in await store.events()] == [
        (kinds.HA_POWER_OFF, "entity:switch.tv_outlet")
    ]


async def test_a_failed_name_refresh_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing() -> None:
        raise ConnectionError("gone")

    async def fine() -> None:
        return None

    for job in (failing, fine):
        task = asyncio.create_task(job())
        await asyncio.wait([task])
        home_assistant._log_failure(task)
    cancelled = asyncio.create_task(fine())
    cancelled.cancel()
    await asyncio.wait([cancelled])
    home_assistant._log_failure(cancelled)

    assert [r.getMessage() for r in caplog.records] == [
        "could not refresh device names: gone"
    ]


def test_a_device_behind_a_bridge_is_named_apart_from_its_bridge(ctx: Context) -> None:
    source = HomeAssistantSource(ctx)
    source.learn_device_names(
        [
            {
                "id": "dev-bridge",
                "name": "Hub",
                "identifiers": [["matter", "deviceid_ABCDEF-2B-MatterNodeDevice"]],
            },
            {
                "id": "dev-lamp",
                "name": "Balcony Light",
                "identifiers": [["matter", "deviceid_ABCDEF-2B-3"]],
            },
        ]
    )

    assert ctx.names.get("node:43") == "Hub"
    assert ctx.names.get("node:43:3") == "Balcony Light"
    assert ctx.names.device("node:43:3") == "dev-lamp"
