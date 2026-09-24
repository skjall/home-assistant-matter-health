import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Severity
from matter_health.rules.relay import RelayRule
from matter_health.store import Store

PLUG = "node:3"
CHILDREN = ["node:11", "node:12", "node:13"]


@pytest.fixture
async def engine(ctx: Context, store: Store) -> Engine:
    ctx.names.set(PLUG, "Hallway Plug")
    ctx.names.set("node:11", "Door Sensor")
    ctx.names.set("node:12", "Window Sensor")
    await store.set_state(
        "thread.links",
        [
            {"subject": c, "neighbour": PLUG, "role": "sleepy_end_device"}
            for c in CHILDREN
        ]
        + [
            {"subject": PLUG, "neighbour": "br:aa", "role": "router"},
            {"subject": "node:20", "neighbour": "br:aa", "role": "sleepy_end_device"},
            {"subject": "node:21", "neighbour": "br:aa", "role": "sleepy_end_device"},
            {"subject": "node:30", "neighbour": None, "role": "sleepy_end_device"},
        ],
    )
    return make_engine(ctx, rules=[RelayRule])


async def test_children_follow_their_parent(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, PLUG)
    await emit_at(ctx, clock, 2, kinds.MATTER_NODE_UNAVAILABLE, "node:11")
    assert await store.findings() == []

    await emit_at(ctx, clock, 4, kinds.MATTER_NODE_UNAVAILABLE, "node:12")
    finding = await only_finding(store)
    assert finding.key == f"relay:{PLUG}:{at(0).isoformat()}"
    assert finding.severity is Severity.WARNING
    assert finding.title == "finding.relay_gone.title"
    assert finding.params == {
        "relay": "Hallway Plug",
        "count": 2,
        "devices": "Door Sensor, Window Sensor",
    }
    assert finding.subjects == [PLUG, "node:11", "node:12"]
    assert chain_keys(finding) == [
        "link.relay_gone",
        "link.relay_children_gone",
        "link.relay_impact",
        "fix.relay",
    ]

    await emit_at(ctx, clock, 5, kinds.MATTER_NODE_UNAVAILABLE, "node:13")
    assert (await only_finding(store)).params["count"] == 3

    # A child coming back does not close it; the parent does.
    await emit_at(ctx, clock, 6, kinds.MATTER_NODE_AVAILABLE, "node:11")
    await emit_at(ctx, clock, 20, kinds.MATTER_NODE_AVAILABLE, PLUG)
    assert (await only_finding(store)).ended_at == at(20)


async def test_children_noticed_first(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, "node:11")
    await emit_at(ctx, clock, 0.5, kinds.MATTER_NODE_UNAVAILABLE, "node:12")
    await emit_at(ctx, clock, 0.8, kinds.MATTER_NODE_UNAVAILABLE, PLUG)

    finding = await only_finding(store)
    assert finding.started_at == at(0.8)
    assert finding.params["count"] == 2


async def test_a_parent_that_is_back_or_a_border_router_explains_nothing(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, PLUG)
    await emit_at(ctx, clock, 1, kinds.MATTER_NODE_AVAILABLE, PLUG)
    await emit_at(ctx, clock, 2, kinds.MATTER_NODE_UNAVAILABLE, "node:11")
    await emit_at(ctx, clock, 3, kinds.MATTER_NODE_UNAVAILABLE, "node:12")
    # Children of a border router: the border router rule tells that story.
    await emit_at(ctx, clock, 4, kinds.MATTER_NODE_UNAVAILABLE, "node:20")
    await emit_at(ctx, clock, 5, kinds.MATTER_NODE_UNAVAILABLE, "node:21")
    # Without a parent, or without a subject, there is nothing to follow.
    await emit_at(ctx, clock, 6, kinds.MATTER_NODE_UNAVAILABLE, "node:30")
    await emit_at(ctx, clock, 7, kinds.MATTER_NODE_UNAVAILABLE)
    await emit_at(ctx, clock, 8, kinds.MATTER_NODE_AVAILABLE, "node:99")

    assert await store.findings() == []


async def test_a_parent_gone_too_long_ago(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, PLUG)
    await emit_at(ctx, clock, 30, kinds.MATTER_NODE_UNAVAILABLE, "node:11")
    await emit_at(ctx, clock, 31, kinds.MATTER_NODE_UNAVAILABLE, "node:12")

    assert await store.findings() == []
