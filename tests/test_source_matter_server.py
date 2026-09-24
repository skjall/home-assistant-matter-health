from typing import Any, cast

import pytest
from aiohttp import web
from conftest import T0, FakeSupervisor, source_for, start_supervisor
from pytest_aiohttp import AiohttpServer

from matter_health import bridges, kinds
from matter_health.engine import Context
from matter_health.sources import matter_server
from matter_health.sources.matter_server import MatterServerSource
from matter_health.store import Store
from matter_health.transports.thread.transport import (
    border_router_name,
    display_name,
    link_summary,
    own_part,
)

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
    monkeypatch.setattr(matter_server, "POLL_S", 0)


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
    assert await store.get_state("thread.links") == []
    assert await store.get_state("thread.tree") == {"at": T0.isoformat(), "nodes": []}
    assert await store.get_state("thread.parents") == {}
    assert fake.commands == [
        "start_listening",
        "get_thread_border_routers",
        "get_network_topology",
        "get_thread_border_routers",
    ]
    assert set(fake.commands) <= matter_server.read_commands()
    assert source.ctx._engine is not None
    assert source.ctx._engine.status["matter_server"]["ok"] is True


def in_network(raw: dict[str, Any], name: str, pan: str) -> dict[str, Any]:
    return {**raw, "networkName": name, "extendedPanIdHex": pan}


@pytest.mark.parametrize(
    ("otbr_network", "own"),
    [
        # Home Assistant's own border router names the network.
        ("SmallNet", {"Living Room TV (Wall)": False, "Hub Mini": True}),
        # Without it, most border routers decide.
        (None, {"Living Room TV (Wall)": True, "Hub Mini": False}),
    ],
)
async def test_border_routers_of_another_thread_network(
    ctx: Context,
    store: Store,
    aiohttp_server: AiohttpServer,
    otbr_network: str | None,
    own: dict[str, bool],
) -> None:
    if otbr_network:
        await store.set_state("otbr.node", {"network_name": otbr_network})
    routers = [
        in_network(TV, "HomeNet", "00AA00AA00AA00AA"),
        in_network(router("99", hostname="Speaker"), "HomeNet", "00AA00AA00AA00AA"),
        in_network(HUB, "SmallNet", "00BB00BB00BB00BB"),
        router("77", hostname="Unannounced"),
    ]
    fake = FakeMatterServer(
        {
            "start_listening": [[result(None)]],
            "get_thread_border_routers": [[result(routers)]],
            "get_network_topology": [[result({})]],
        }
    )
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )
    ctx.names.know_device("Living Room TV (Wall)")

    with pytest.raises(ConnectionError):
        await source.run()

    stored = {r["name"]: r for r in await store.get_state("border_routers")}
    assert {name: stored[name]["own"] for name in own} == own
    assert stored["Unannounced"]["own"] is True
    assert stored["Hub Mini"]["network"] == "SmallNet"
    assert stored["Hub Mini"]["pan"] == "00bb00bb00bb00bb"


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
    unknown = {"network": None, "pan": None, "own": True}
    tv = {"name": "Living Room TV (Wall)", "vendor": "Acme", "model": "TV Box"}
    tv |= unknown
    hub = {"name": "Hub Mini", "vendor": "Acme", "model": "Hub Mini"} | unknown
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
        {"subject": "br:0a1b2c3d4e5f6071", **tv, "addresses": []}
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


TOPOLOGY_NODES = [
    {"id": "plug", "node_id": 1, "role": "router"},
    {"id": "lamp", "node_id": 2, "role": "router"},
    {"id": "spare", "node_id": 3, "role": "reed"},
    {"id": "sensor", "node_id": 4, "role": "sleepy_end_device"},
    {"id": "quiet", "node_id": 5, "role": "router"},
    {"id": "tv", "kind": "border_router", "ext_address": "0A1B2C3D4E5F6071"},
]


def summary_of(connections: list[dict[str, Any]]) -> dict[str, tuple[Any, ...]]:
    found = link_summary({"nodes": TOPOLOGY_NODES, "connections": connections})
    return {item["subject"]: (item["neighbour"], item["rssi"]) for item in found}


def test_a_device_is_judged_by_what_it_hears_itself() -> None:
    # The plug hears the TV well; the lamp hears the plug only faintly. That
    # faint value says something about the lamp, not about the plug.
    found = summary_of(
        [
            {"source": "plug", "target": "tv", "source_to_target": {"rssi": -60}},
            {"source": "lamp", "target": "plug", "source_to_target": {"rssi": -92}},
        ]
    )

    assert found["node:1"] == ("br:0a1b2c3d4e5f6071", -60)
    assert found["node:2"] == ("node:1", -92)


def test_only_neighbours_that_relay_count() -> None:
    # A device next door that passes nothing on is no way into the mesh.
    found = summary_of(
        [
            {"source": "plug", "target": "spare", "source_to_target": {"rssi": -40}},
            {"source": "plug", "target": "lamp", "source_to_target": {"rssi": -80}},
        ]
    )

    assert found["node:1"] == ("node:2", -80)


def test_a_child_is_judged_by_its_parents_entry() -> None:
    found = summary_of(
        [
            {
                "source": "plug",
                "target": "sensor",
                "source_to_target": {"rssi": -90, "lqi": 1, "strength": "weak"},
            },
        ]
    )

    assert found["node:4"] == ("node:1", -90)
    (sensor,) = [
        item
        for item in link_summary(
            {
                "nodes": TOPOLOGY_NODES,
                "connections": [
                    {
                        "source": "sensor",
                        "target": "plug",
                        "target_to_source": {"rssi": -90, "lqi": 1, "strength": "weak"},
                    }
                ],
            }
        )
        if item["subject"] == "node:4"
    ]
    assert sensor == {
        "subject": "node:4",
        "neighbour": "node:1",
        "role": "sleepy_end_device",
        "rssi": -90,
        "lqi": 1,
        "strength": "weak",
    }


def test_a_router_that_reports_nothing_is_not_judged() -> None:
    found = summary_of(
        [
            {"source": "plug", "target": "quiet", "source_to_target": {"rssi": -95}},
            {"source": "plug", "target": "spare", "source_to_target": None},
            {"source": "spare", "target": "ghost", "source_to_target": {"rssi": -50}},
        ]
    )

    # The plug is judged by what it hears; the quiet router is not judged.
    # The spare device hears only something that relays nothing.
    assert found == {"node:1": ("node:5", -95)}
    assert link_summary({}) == []


def test_home_assistants_own_border_router_is_named_after_it() -> None:
    assert (
        display_name(
            {"hostname": "homeassistant-otbr.local", "vendorName": "Home Assistant"}
        )
        == "Home Assistant"
    )
    assert display_name({"vendorName": "Acme"}) is None


async def test_a_server_that_forgot_every_device(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    await store.set_state("matter.nodes", {"total": 12, "unavailable": []})
    fake = FakeMatterServer({"start_listening": [[result([])]]})
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(ConnectionError):
        await source.run()

    found = [(e.kind, e.data) for e in await store.events()]
    assert found == [(kinds.MATTER_NODES_LOST, {"previous": 12})]
    assert await store.get_state("matter.nodes") == {"total": 0, "unavailable": []}


async def test_an_empty_server_that_was_empty_before_is_fine(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeMatterServer({"start_listening": [[result([])]]})
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(ConnectionError):
        await source.run()

    assert await store.events() == []


async def test_each_transport_gets_its_devices(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    wifi = {"0/49/65532": 1, "0/54/0": "AgAAAAAB", "0/54/4": -50, "0/40/5": "x"}
    fake = FakeMatterServer(
        {
            "start_listening": [
                [
                    result(
                        [
                            {"node_id": 1, "available": True, "attributes": wifi},
                            {"node_id": 2, "available": True},
                            {
                                "node_id": 3,
                                "available": True,
                                "attributes": {"0/49/65532": 2},
                            },
                        ]
                    )
                ]
            ],
            "get_thread_border_routers": [
                [
                    {"event": "attribute_updated", "data": [1, "0/54/4", -80]},
                    {"event": "attribute_updated", "data": [1, "0/6/0", True]},
                    {"event": "attribute_updated", "data": ["bad"]},
                    {
                        "event": "node_updated",
                        "data": {
                            "node_id": 4,
                            "available": True,
                            "attributes": {"0/49/65532": 4},
                        },
                    },
                    {"event": "node_removed", "data": 3},
                    result([]),
                ]
            ],
            "get_network_topology": [[result({"nodes": [], "connections": []})]],
        }
    )
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(ConnectionError):
        await source.run()

    assert await store.get_state("matter.transports") == {
        "node:1": "wifi",
        "node:4": "ethernet",
    }
    stored = await store.get_state("wifi.devices")
    assert stored["node:1"]["rssi"] == -80
    assert stored["node:1"]["bssid"] == "02:00:00:00:00:01"
    links = [e for e in await store.events() if e.kind == kinds.WIFI_LINKS]
    assert [e.data["devices"][0]["quality"] for e in links] == ["strong", "weak"]


def test_the_thread_picture_leaves_other_networks_out() -> None:
    topology = {
        "nodes": [
            {"id": "1", "node_id": 1, "network_type": "thread"},
            {"id": "2", "node_id": 2, "network_type": "wifi"},
            {"id": "ap_1", "kind": "wifi_ap", "network_type": "wifi"},
            {"id": "br_A", "kind": "border_router"},
        ],
        "connections": [
            {"source": "1", "target": "br_A", "network": "thread"},
            {"source": "2", "target": "ap_1", "network": "wifi"},
            {"source": "1", "target": "2"},
        ],
    }

    part = own_part(topology)

    assert [n["id"] for n in part["nodes"]] == ["1", "br_A"]
    assert [c["target"] for c in part["connections"]] == ["br_A", "2"]


async def test_devices_behind_a_bridge_come_and_go(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    ctx.names.set("node:5:4", "Hall Door")
    behind = {
        "0/49/65532": 4,
        "3/57/5": "Balcony Light",
        "4/57/5": "Door Sensor",
        "4/57/17": True,
    }
    fake = FakeMatterServer(
        {
            "start_listening": [
                [result([{"node_id": 5, "available": True, "attributes": behind}])]
            ],
            "get_thread_border_routers": [
                [
                    {"event": "attribute_updated", "data": [5, "4/57/17", False]},
                    {"event": "attribute_updated", "data": [5, "6/57/5", "Stairs"]},
                    result([]),
                ]
            ],
            "get_network_topology": [[result({"nodes": [], "connections": []})]],
        }
    )
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(ConnectionError):
        await source.run()

    found = [(e.kind, e.subject, e.data) for e in await store.events()]
    # What was there on connecting is the baseline, not news.
    assert found[:2] == [
        (kinds.MATTER_NODE_UNAVAILABLE, "node:5:4", {"name": "Hall Door"}),
        (kinds.MATTER_NODE_ADDED, "node:5:6", {"name": "Stairs"}),
    ]
    # Named by the bridge until Home Assistant says better.
    assert ctx.names.get("node:5:3") == "Balcony Light"
    assert await store.get_state("matter.nodes") == {
        "total": 4,
        "unavailable": ["node:5:4"],
    }
    stored = await store.get_state(bridges.BRIDGED)
    assert [d["subject"] for d in stored["node:5"]] == [
        "node:5:3",
        "node:5:4",
        "node:5:6",
    ]


async def test_with_the_bridge_away_only_the_bridge_is_news(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    behind = {"0/49/65532": 4, "3/57/5": "Lamp", "3/57/17": True}
    fake = FakeMatterServer(
        {
            "start_listening": [
                [result([{"node_id": 5, "available": True, "attributes": behind}])]
            ],
            "get_thread_border_routers": [
                [
                    {
                        "event": "node_updated",
                        "data": {"node_id": 5, "available": False},
                    },
                    {"event": "attribute_updated", "data": [5, "3/57/17", False]},
                    {"event": "node_removed", "data": 5},
                    result([]),
                ]
            ],
            "get_network_topology": [[result({"nodes": [], "connections": []})]],
        }
    )
    source = source_for(
        MatterServerSource, ctx, matter_server_url=await serve(aiohttp_server, fake)
    )

    with pytest.raises(ConnectionError):
        await source.run()

    found = [(e.kind, e.subject) for e in await store.events()]
    assert found[:3] == [
        (kinds.MATTER_NODE_UNAVAILABLE, "node:5"),
        (kinds.MATTER_NODE_REMOVED, "node:5"),
        (kinds.MATTER_NODE_REMOVED, "node:5:3"),
    ]
    assert await store.get_state("matter.nodes") == {"total": 0, "unavailable": []}
