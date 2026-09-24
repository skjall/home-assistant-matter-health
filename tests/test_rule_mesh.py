import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Severity
from matter_health.rules.mesh import MeshRule
from matter_health.store import Store

TV_SUBJECT = "br:0a1b2c3d4e5f6071"


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx, rules=[MeshRule])


async def power_off(ctx: Context, clock: Clock, minutes: float) -> None:
    await emit_at(
        ctx,
        clock,
        minutes,
        kinds.HA_POWER_OFF,
        "entity:switch.media_plug",
        name="Media Plug",
        origin="automation",
        by="Evening Off",
    )


async def router_gone(ctx: Context, clock: Clock, minutes: float) -> None:
    await emit_at(
        ctx, clock, minutes, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, name="Living Room TV"
    )


async def test_a_new_leader_then_a_split_is_one_episode(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_LEADER_LOST)

    finding = await only_finding(store)
    assert finding.key == f"mesh:{at(0).isoformat()}"
    assert finding.title == "finding.new_leader.title"
    assert finding.severity is Severity.WARNING
    assert chain_keys(finding) == [
        "link.cause_unknown",
        "link.new_leader",
        "link.mesh_trouble_impact",
        "fix.mesh_trouble_rare",
    ]

    await emit_at(ctx, clock, 0.5, kinds.THREAD_FOREIGN_PARTITION)
    await tick_at(engine, clock, 2)

    finding = await only_finding(store)
    assert finding.title == "finding.mesh_split.title"
    assert finding.params == {"duration": 30}
    assert finding.chain[1].key == "link.mesh_split"
    assert len(finding.chain[1].evidence) == 2
    assert finding.ended_at is None

    await tick_at(engine, clock, 2.5)
    closed = await only_finding(store)
    assert closed.ended_at == at(0.5)

    # Closed episodes are not retold.
    await tick_at(engine, clock, 10)
    assert (await only_finding(store)).ended_at == at(0.5)


async def test_a_quiet_gap_starts_a_new_episode(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_LEADER_CHANGED, previous=1, current=2)
    await emit_at(ctx, clock, 4, kinds.THREAD_PARTITION_CHANGED, previous=7, current=8)

    keys = sorted(f.key for f in await store.findings())
    assert keys == [f"mesh:{at(0).isoformat()}", f"mesh:{at(4).isoformat()}"]


async def test_a_switched_off_leader_explains_the_episode(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await power_off(ctx, clock, 0)
    await router_gone(ctx, clock, 1)
    await emit_at(ctx, clock, 2, kinds.THREAD_LEADER_LOST)

    finding = await only_finding(store)
    assert chain_keys(finding) == [
        "link.power_off",
        "link.border_router_gone",
        "link.was_probably_leader",
        "link.new_leader",
        "link.mesh_trouble_impact",
        "fix.keep_border_router_powered",
    ]
    assert finding.chain[0].confidence is Confidence.LIKELY
    assert finding.chain[2].confidence is Confidence.LIKELY
    assert finding.chain[-1].params == {
        "border_router": "Living Room TV",
        "switch": "Media Plug",
    }
    assert finding.subjects == [TV_SUBJECT]


async def test_a_router_reported_late_still_counts(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_LEADER_LOST)
    await router_gone(ctx, clock, 2)
    await tick_at(engine, clock, 3)

    finding = await only_finding(store)
    assert finding.ended_at == at(0)
    assert chain_keys(finding) == [
        "link.border_router_gone",
        "link.was_probably_leader",
        "link.new_leader",
        "link.mesh_trouble_impact",
        "fix.check_border_router_power",
    ]


async def test_a_switch_alone_is_only_a_possible_cause(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await power_off(ctx, clock, 0)
    await emit_at(ctx, clock, 1, kinds.THREAD_LEADER_LOST)

    finding = await only_finding(store)
    assert chain_keys(finding)[0] == "link.power_off"
    assert finding.chain[0].confidence is Confidence.POSSIBLE
    assert chain_keys(finding)[-1] == "fix.mesh_trouble_rare"
    assert finding.subjects == []


async def test_a_failed_pairing_meanwhile_is_mentioned(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_FOREIGN_PARTITION)
    await emit_at(ctx, clock, 1, kinds.COMMISSIONING_FAILED, reason="timeout")
    await emit_at(ctx, clock, 1.5, kinds.THREAD_FOREIGN_PARTITION)

    finding = await only_finding(store)
    assert chain_keys(finding) == [
        "link.cause_unknown",
        "link.mesh_split",
        "link.mesh_trouble_impact",
        "link.pairing_failed_meanwhile",
        "fix.mesh_trouble_rare",
    ]
