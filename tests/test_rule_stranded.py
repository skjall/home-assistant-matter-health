import pytest
from conftest import (
    Clock,
    at,
    chain_keys,
    emit_at,
    make_engine,
    only_finding,
    tick_at,
)

from matter_health import batteries, kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Severity
from matter_health.rules.offline import AWAY
from matter_health.store import Store
from matter_health.transports.thread.stranded import StrandedRule, listed


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx, rules=[StrandedRule])


async def away(store: Store, *entries: tuple[str, float]) -> None:
    """Devices away, each since the given minute."""
    await store.set_state(
        AWAY,
        {
            subject: {"since": at(minute).isoformat(), "name": subject.upper()}
            for subject, minute in entries
        },
    )


SLEEPY = {"node:1": 2, "node:2": 2, "node:3": 2, "node:4": 2, "node:9": 5}


async def quiet_channel(store: Store) -> None:
    await store.set_state("thread.interference", {"devices": {"node:9": {}}})
    await store.set_state("interference.state", {"kind": None})


async def test_sleepy_devices_left_behind_after_the_mesh_changed(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state("thread.roles", SLEEPY)
    await store.set_state(
        batteries.BATTERIES,
        {"node:2": {"weak": True}, "node:3": {"weak": False}},
    )
    await quiet_channel(store)
    await emit_at(ctx, clock, 0, kinds.THREAD_PARTITIONS, parts=[{}])  # whole
    await emit_at(ctx, clock, 5, kinds.THREAD_PARTITIONS, parts=[{}, {}])
    # A router away is not left behind; one away since before stays too.
    await away(store, ("node:1", 30), ("node:2", 60), ("node:3", 120), ("node:9", 30))
    await tick_at(engine, clock, 140)
    assert await store.findings() == []

    await tick_at(engine, clock, 150)

    finding = await only_finding(store)
    assert finding.title == "finding.stranded.title"
    assert finding.severity is Severity.PROBLEM
    assert finding.started_at == at(30)
    assert finding.subjects == ["node:1", "node:2", "node:3"]
    assert chain_keys(finding) == [
        "link.stranded_cause",
        "link.stranded_trigger",
        "link.stranded",
        "link.stranded_radio_quiet",
        "link.stranded_weak_battery",
        "link.stranded_impact",
        "fix.stranded",
        "fix.stranded_weak_battery",
    ]
    assert finding.chain[0].confidence is Confidence.LIKELY
    assert finding.chain[1].at == at(5)
    assert finding.chain[2].params == {"count": 3, "devices": "NODE:1, NODE:2, NODE:3"}
    assert finding.chain[4].params == {"count": 1, "devices": "NODE:2"}

    # One comes back: the rest stay told.
    await away(store, ("node:2", 60), ("node:3", 120))
    await tick_at(engine, clock, 160)
    assert (await only_finding(store)).subjects == ["node:2", "node:3"]
    await tick_at(engine, clock, 161)

    await away(store)
    await tick_at(engine, clock, 170)
    assert (await only_finding(store)).ended_at == at(170)
    assert await store.get_state("stranded.state") == {}


async def test_while_the_channel_is_disturbed_it_may_still_be_the_radio(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state("thread.roles", SLEEPY)
    await store.set_state("thread.interference", {"devices": {"node:9": {}}})
    await store.set_state("interference.state", {"kind": "wide"})
    await emit_at(ctx, clock, 0, kinds.THREAD_LEADER_LOST)
    await away(store, ("node:1", 10), ("node:2", 20), ("node:3", 30))
    await tick_at(engine, clock, 120)

    assert await store.findings() == []


async def test_a_channel_busy_only_around_a_few_does_not_hold_it_back(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state("thread.roles", SLEEPY)
    await store.set_state("thread.interference", {"devices": {"node:9": {}}})
    await store.set_state("interference.state", {"kind": "local"})
    await emit_at(ctx, clock, 0, kinds.THREAD_LEADER_LOST)
    await away(store, ("node:1", 10), ("node:2", 20), ("node:3", 30))
    await tick_at(engine, clock, 120)

    chain = chain_keys(await only_finding(store))
    assert "link.stranded_radio_local" in chain
    assert "link.stranded_radio_quiet" not in chain


async def test_without_a_change_of_the_mesh_it_is_not_told(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state("thread.roles", SLEEPY)
    await away(store, ("node:1", 10), ("node:2", 20), ("node:3", 30))
    await tick_at(engine, clock, 120)
    # Two are not enough, whatever happened before.
    await emit_at(ctx, clock, 121, kinds.THREAD_LEADER_LOST)
    await away(store, ("node:1", 130), ("node:2", 130))
    await tick_at(engine, clock, 200)

    assert await store.findings() == []


async def test_unmeasured_radio_says_nothing_about_it(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state("thread.roles", SLEEPY)
    await emit_at(ctx, clock, 0, kinds.BORDER_ROUTER_GONE, "br:1", name="TV")
    await away(store, ("node:1", 10), ("node:2", 20), ("node:3", 30))
    await tick_at(engine, clock, 70)

    finding = await only_finding(store)
    assert "link.stranded_radio_quiet" not in chain_keys(finding)
    assert "fix.stranded_weak_battery" not in chain_keys(finding)
    # Names come from Home Assistant where it knows them.
    ctx.names.set("node:1", "Kitchen Window")
    await away(store, ("node:1", 10), ("node:2", 20), ("node:3", 30), ("node:4", 40))
    await tick_at(engine, clock, 80)
    assert (
        (await only_finding(store))
        .chain[2]
        .params["devices"]
        .startswith("Kitchen Window")
    )


def test_many_devices_are_counted() -> None:
    assert listed([f"D{i}" for i in range(7)]) == "D0, D1, D2, D3, D4 (+2)"
    assert listed(["A"]) == "A"


async def test_one_gone_long_before_does_not_hide_the_rest(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state("thread.roles", SLEEPY)
    await emit_at(ctx, clock, 600, kinds.THREAD_LEADER_LOST)
    await away(store, ("node:4", 0), ("node:1", 610), ("node:2", 620), ("node:3", 630))
    await tick_at(engine, clock, 700)

    finding = await only_finding(store)
    assert finding.subjects == ["node:1", "node:2", "node:3"]
    assert finding.started_at == at(610)
