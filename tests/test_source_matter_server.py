from typing import Any, cast

import pytest
from aiohttp import web
from conftest import FakeSupervisor, source_for, start_supervisor
from pytest_aiohttp import AiohttpServer

from matter_health import kinds
from matter_health.engine import Context
from matter_health.sources import matter_server
from matter_health.sources.matter_server import (
    MatterServerSource,
    border_router_name,
    link_summary,
)
from matter_health.store import Store

#: A reply is a list of things to send, or None to hang up instead.
Reply = list[Any] | None


def result(value: Any) -> tuple[str, Any]:
    return ("result", value)


def error(code: int) -> tuple[str, Any]:
    return ("error", code)


class FakeMatterServer:
    """Speaks just enough of the Matter Server's websocket for the source.

    Every command takes the next reply from its own queue. A command without
    a reply left makes the server hang up, which is how a test ends a run.
    """

    def __init__(self, replies: dict[str, list[Reply]]) -> None:
        self.replies = replies
        self.commands: list[str] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/ws", self.handle)
        return app

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"fabric_id": 1, "schema_version": 11})
        async for message in ws:
            command = message.json()
            self.commands.append(command["command"])
            queue = self.replies.get(command["command"], [])
            reply = queue.pop(0) if queue else None
            if reply is None:
                break
            for item in reply:
                await self._send(ws, command["message_id"], item)
        await ws.close()
        return ws

    @staticmethod
    async def _send(ws: web.WebSocketResponse, message_id: str, item: Any) -> None:
        if isinstance(item, bytes):
            await ws.send_bytes(item)
        elif isinstance(item, tuple) and item[0] == "result":
            await ws.send_json({"message_id": message_id, "result": item[1]})
        elif isinstance(item, tuple):
            await ws.send_json(
                {"message_id": message_id, "error_code": item[1], "details": "boom"}
            )
        else:
            await ws.send_json(item)


def router(
    ext: str, hostname: str | None = None, model: str | None = None
) -> dict[str, Any]:
    return {
        "extAddressHex": ext,
        "hostname": hostname,
        "vendorName": "Acme",
        "modelName": model,
    }


TV = router("0A1B2C3D4E5F6071", hostname="Living-Room-TV-Wall.local.", model="TV Box")
HUB = router("1122334455667788", model="Hub Mini")
HUB_RESTARTED = router("8877665544332211", model="Hub Mini")


async def serve(aiohttp_server: AiohttpServer, fake: FakeMatterServer) -> str:
    server = await aiohttp_server(fake.app())
    return f"ws://{server.host}:{server.port}"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(matter_server, "BORDER_ROUTER_POLL_S", 0)


async def test_device_changes_become_events(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    ctx.names.set("node:1", "Living Room Plug")
    fake = FakeMatterServer(
        {
            "start_listening": [
                [
                    result(
                        [
                            {"node_id": 1, "available": True},
                            {"node_id": 2, "available": False},
                        ]
                    )
                ]
            ],
            "get_thread_border_routers": [
                [
                    {"event": "node_updated", "data": {"node_id": 1}},
                    {"event": "node_updated", "data": {"node_id": 1}},
                    {"event": "node_added", "data": {"node_id": 3, "available": True}},
                    {
                        "event": "node_updated",
                        "data": {"node_id": 4, "available": True},
                    },
                    {
                        "event": "node_updated",
                        "data": {"node_id": 1, "available": True},
                    },
                    {"event": "node_removed", "data": 2},
                    {"event": "node_removed", "data": None},
                    {"event": "attribute_updated", "data": [1, "0/40/5", "x"]},
                    {"message_id": "999", "result": "nobody asked"},
                    result([]),
                ]
            ],
            "get_network_topology": [[result({"nodes": [], "connections": []})]],
        }
    )
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(ConnectionError, match="Matter Server closed"):
        await source.run()

    found = [(e.kind, e.subject, e.data) for e in await store.events()]
    assert found == [
        (kinds.MATTER_NODE_UNAVAILABLE, "node:1", {"name": "Living Room Plug"}),
        (kinds.MATTER_NODE_ADDED, "node:3", {"name": None}),
        (kinds.MATTER_NODE_ADDED, "node:4", {"name": None}),
        (kinds.MATTER_NODE_AVAILABLE, "node:1", {"name": "Living Room Plug"}),
        (kinds.MATTER_NODE_REMOVED, "node:2", {"name": None}),
        (kinds.THREAD_TOPOLOGY, None, {"devices": []}),
    ]
    assert await store.get_state("matter.nodes") == {"total": 3, "unavailable": []}
    assert fake.commands == [
        "start_listening",
        "get_thread_border_routers",
        "get_network_topology",
        "get_thread_border_routers",
    ]
    assert set(fake.commands) <= matter_server.READ_COMMANDS
    assert source.ctx._engine is not None
    assert source.ctx._engine.status["matter_server"]["ok"] is True


async def test_border_routers_come_and_go(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    rounds: list[Reply] = [
        [result([TV, HUB, router("")])],  # baseline, no events
        [result([TV])],  # the hub is missing once: not yet gone
        [result([TV])],  # twice: gone
        [result([TV, HUB_RESTARTED])],  # back, with a new address
        [result([])],
        [result(None)],  # both gone
        [result([TV])],  # the last one to leave comes back
    ]
    fake = FakeMatterServer(
        {
            "start_listening": [[result(None)]],
            "get_thread_border_routers": rounds,
            "get_network_topology": [[result({})]],
        }
    )
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )
    # The name the user gave, of which the host name is a plainer copy.
    ctx.names.know_device("Living Room TV (Wall)")

    with pytest.raises(ConnectionError):
        await source.run()

    found = [
        (e.kind, e.subject, e.data)
        for e in await store.events()
        if e.kind != kinds.THREAD_TOPOLOGY
    ]
    tv = {"name": "Living Room TV (Wall)", "vendor": "Acme", "model": "TV Box"}
    hub = {"name": "Hub Mini", "vendor": "Acme", "model": "Hub Mini"}
    assert found[:2] == [
        (kinds.BORDER_ROUTER_GONE, "br:1122334455667788", hub),
        (kinds.BORDER_ROUTER_APPEARED, "br:8877665544332211", hub),
    ]
    # Both leave in the same round, in no particular order.
    assert sorted(found[2:4]) == [
        (kinds.BORDER_ROUTER_GONE, "br:0a1b2c3d4e5f6071", tv),
        (kinds.BORDER_ROUTER_GONE, "br:8877665544332211", hub),
    ]
    assert found[4:] == [(kinds.BORDER_ROUTER_APPEARED, "br:0a1b2c3d4e5f6071", tv)]
    assert await store.get_state("border_routers") == [
        {"subject": "br:0a1b2c3d4e5f6071", **tv}
    ]
    assert ctx.names.get("br:1122334455667788") == "Hub Mini"
    assert await store.get_state("matter.nodes") == {"total": 0, "unavailable": []}


async def test_a_refused_command_stops_the_run(
    ctx: Context, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeMatterServer({"start_listening": [[error(5)]]})
    # The user may give the full websocket address.
    url = await serve(aiohttp_server, fake) + "/ws"
    source = source_for(MatterServerSource, ctx, matter_server_url=url)

    with pytest.raises(RuntimeError, match="5: boom"):
        await source.run()


async def test_a_binary_frame_ends_the_connection(
    ctx: Context, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeMatterServer({"start_listening": [[b"\x00"]]})
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(ConnectionError, match="Matter Server closed"):
        await source.run()


async def test_fails_when_the_addon_is_not_running(
    ctx: Context, aiohttp_server: AiohttpServer
) -> None:
    base = await start_supervisor(aiohttp_server, FakeSupervisor())
    source = source_for(MatterServerSource, ctx, supervisor_url=base)

    with pytest.raises(ConnectionError, match="not running"):
        await source.run()


async def test_only_read_commands_are_ever_sent(ctx: Context) -> None:
    source = source_for(MatterServerSource, ctx)

    with pytest.raises(ValueError, match="not a read-only command"):
        await source._command(cast(Any, None), "remove_node")


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ({"hostname": "Living-Room-TV.local."}, "Living Room TV"),
        ({"hostname": "kitchen-hub."}, "kitchen hub"),
        ({"hostname": "", "modelName": "Hub Mini", "vendorName": "Acme"}, "Hub Mini"),
        ({"vendorName": "Acme"}, "Acme"),
        ({}, "Border router"),
    ],
)
def test_border_router_name(raw: dict[str, Any], name: str) -> None:
    assert border_router_name(raw) == name


def test_link_summary_keeps_the_best_link_per_device() -> None:
    topology = {
        "nodes": [
            {"id": "n1", "node_id": 1, "role": "router"},
            {"id": "n2", "node_id": 2, "role": "sleepy_end_device"},
            {"id": "b1", "ext_address": "0A1B2C3D4E5F6071", "role": "leader"},
            {"id": "x"},
        ],
        "connections": [
            {
                "source": "n1",
                "target": "b1",
                "source_to_target": {"rssi": -60, "lqi": 3},
                "target_to_source": {"rssi": -55, "lqi": 3, "strength": "strong"},
            },
            {
                # Heard in one direction only; the strength is the link's.
                "source": "n2",
                "target": "n1",
                "target_to_source": {"rssi": -88, "lqi": 1},
                "strength": "weak",
            },
            {"source": "n2", "target": "b1", "target_to_source": {"rssi": -92}},
            {"source": "x", "target": "zz", "source_to_target": {"rssi": -70}},
        ],
    }

    assert link_summary(topology) == [
        {
            "subject": "br:0a1b2c3d4e5f6071",
            "neighbour": "node:1",
            "role": "leader",
            "rssi": -60,
            "lqi": 3,
            "strength": None,
        },
        {
            "subject": "node:1",
            "neighbour": "br:0a1b2c3d4e5f6071",
            "role": "router",
            "rssi": -55,
            "lqi": 3,
            "strength": "strong",
        },
        {
            "subject": "node:2",
            "neighbour": "node:1",
            "role": "sleepy_end_device",
            "rssi": -88,
            "lqi": 1,
            "strength": "weak",
        },
        {
            "subject": "thread:zz",
            "neighbour": None,
            "role": None,
            "rssi": -70,
            "lqi": None,
            "strength": None,
        },
    ]
    assert link_summary({}) == []
