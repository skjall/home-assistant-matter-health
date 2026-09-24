from typing import Any

import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Severity
from matter_health.rules.offline import OfflineRule
from matter_health.store import Store

PLUG = "node:7"


@pytest.fixture
def engine(ctx: Context) -> Engine:
    ctx.names.set(PLUG, "Living Room Plug")
    return make_engine(ctx, rules=[OfflineRule])


async def test_a_short_dropout_is_no_finding(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, PLUG)
    await tick_at(engine, clock, 9)
    await emit_at(ctx, clock, 9.5, kinds.MATTER_NODE_AVAILABLE, PLUG)
    await tick_at(engine, clock, 30)
    # Events without a subject cannot be followed.
    await emit_at(ctx, clock, 31, kinds.MATTER_NODE_UNAVAILABLE)
    await tick_at(engine, clock, 60)

    assert await store.findings() == []


async def test_a_device_that_stays_away(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, PLUG, name="Old Name")
    # A second report does not restart the clock.
    await emit_at(ctx, clock, 5, kinds.MATTER_NODE_UNAVAILABLE, PLUG)
    await tick_at(engine, clock, 10)
    await tick_at(engine, clock, 11)

    finding = await only_finding(store)
    assert finding.key == f"offline:{PLUG}:{at(0).isoformat()}"
    assert finding.severity is Severity.WARNING
    assert finding.title == "finding.device_unreachable.title"
    assert finding.params == {"device": "Living Room Plug"}
    assert finding.subjects == [PLUG]
    assert chain_keys(finding) == [
        "link.cause_unknown",
        "link.device_unreachable",
        "link.device_unreachable_impact",
        "fix.device_unreachable",
    ]

    await emit_at(ctx, clock, 30, kinds.MATTER_NODE_AVAILABLE, PLUG)

    assert (await only_finding(store)).ended_at == at(30)


async def test_removal_closes_the_finding(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, "node:8", name="Lamp")
    await tick_at(engine, clock, 10)
    await emit_at(ctx, clock, 12, kinds.MATTER_NODE_REMOVED, "node:8")

    finding = await only_finding(store)
    assert finding.params == {"device": "Lamp"}
    assert finding.ended_at == at(12)


async def test_mesh_trouble_and_a_switch_are_named_as_causes(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_LEADER_LOST)
    await emit_at(
        ctx,
        clock,
        1,
        kinds.HA_POWER_OFF,
        "entity:switch.media_plug",
        name="Media Plug",
        origin="unknown",
        by=None,
    )
    await emit_at(ctx, clock, 2, kinds.MATTER_NODE_UNAVAILABLE, PLUG)
    await tick_at(engine, clock, 12)

    finding = await only_finding(store)
    assert chain_keys(finding) == [
        "link.mesh_disturbed",
        "link.power_off",
        "link.device_unreachable",
        "link.device_unreachable_impact",
        "fix.device_unreachable",
    ]
    assert finding.chain[0].confidence is Confidence.LIKELY
    assert finding.chain[0].at == at(0)
    assert finding.chain[1].confidence is Confidence.POSSIBLE


async def test_a_device_leaving_while_findings_are_published(
    ctx: Context,
    store: Store,
    clock: Clock,
    engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, PLUG)
    emitted: list[str] = []

    async def another_one_leaves(topic: str, payload: dict[str, Any]) -> None:
        if topic == "finding" and not emitted:
            emitted.append("node:9")
            await ctx.emit(kinds.MATTER_NODE_UNAVAILABLE, "test", "node:9")

    engine.subscribe(another_one_leaves)
    await tick_at(engine, clock, 10)

    assert "failed on tick" not in caplog.text
    assert len(await store.findings()) == 1
