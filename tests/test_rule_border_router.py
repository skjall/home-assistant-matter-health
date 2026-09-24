from typing import Any

import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Severity
from matter_health.rules.border_router import BorderRouterRule
from matter_health.store import Store

TV = {"name": "Living Room TV", "vendor": "Acme", "model": "TV Box"}
TV_SUBJECT = "br:0a1b2c3d4e5f6071"


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx, rules=[BorderRouterRule])


async def power_off(ctx: Context, clock: Clock, minutes: float) -> None:
    await emit_at(
        ctx,
        clock,
        minutes,
        kinds.HA_POWER_OFF,
        "entity:switch.media_plug",
        name="Media Plug",
        origin="person",
        by="Alex",
    )


async def test_a_short_absence_is_no_finding(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    await tick_at(engine, clock, 4)
    await emit_at(ctx, clock, 4.5, kinds.BORDER_ROUTER_APPEARED, TV_SUBJECT, **TV)
    await tick_at(engine, clock, 10)
    # A border router nobody saw leave.
    await emit_at(ctx, clock, 11, kinds.BORDER_ROUTER_APPEARED, "br:ff", name="Hub")

    assert await store.findings() == []


async def test_a_long_absence_without_known_cause(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    await tick_at(engine, clock, 5)
    await tick_at(engine, clock, 6)

    finding = await only_finding(store)
    assert finding.key == f"border_router:Living Room TV:{at(0).isoformat()}"
    assert finding.severity is Severity.WARNING
    assert finding.title == "finding.border_router_gone.title"
    assert finding.subjects == [TV_SUBJECT]
    assert finding.ended_at is None
    assert chain_keys(finding) == [
        "link.cause_unknown",
        "link.border_router_gone",
        "link.border_router_gone_impact",
        "fix.check_border_router_power",
    ]
    assert finding.chain[1].evidence

    await emit_at(ctx, clock, 20, kinds.BORDER_ROUTER_APPEARED, TV_SUBJECT, **TV)

    closed = await only_finding(store)
    assert closed.key == finding.key
    assert closed.ended_at == at(20)


async def test_a_switch_turned_off_just_before_is_the_likely_cause(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await power_off(ctx, clock, 0)
    await emit_at(ctx, clock, 3, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    await tick_at(engine, clock, 8)

    finding = await only_finding(store)
    assert chain_keys(finding) == [
        "link.power_off",
        "link.border_router_gone",
        "link.border_router_gone_impact",
        "fix.keep_border_router_powered",
    ]
    cause = finding.chain[0]
    assert cause.confidence is Confidence.LIKELY
    assert cause.params == {"switch": "Media Plug", "origin": "person", "by": "Alex"}
    assert finding.chain[-1].params == {
        "border_router": "Living Room TV",
        "switch": "Media Plug",
    }


async def test_the_same_switch_twice_means_the_router_hangs_on_it(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await power_off(ctx, clock, 0)
    await emit_at(ctx, clock, 1, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    await emit_at(ctx, clock, 2, kinds.BORDER_ROUTER_APPEARED, TV_SUBJECT, **TV)
    assert await store.findings() == []

    await power_off(ctx, clock, 60)
    await emit_at(ctx, clock, 61, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)

    finding = await only_finding(store)
    assert finding.key == (
        "switched_border_router:Living Room TV:entity:switch.media_plug"
    )
    assert finding.title == "finding.border_router_switched.title"
    assert finding.started_at == at(1)
    assert finding.subjects == [TV_SUBJECT, "entity:switch.media_plug"]
    assert chain_keys(finding) == [
        "link.border_router_on_switch",
        "link.border_router_on_switch_impact",
        "fix.keep_border_router_powered",
    ]
    assert finding.chain[0].params["count"] == 2
    assert finding.chain[0].confidence is Confidence.LIKELY


async def test_a_switch_without_subject_is_not_counted(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    for minute in (0, 60):
        await emit_at(ctx, clock, minute, kinds.HA_POWER_OFF, name="Somewhere")
        await emit_at(ctx, clock, minute + 1, kinds.BORDER_ROUTER_GONE, TV_SUBJECT)
        await emit_at(ctx, clock, minute + 2, kinds.BORDER_ROUTER_APPEARED, TV_SUBJECT)

    assert await store.findings() == []


async def test_a_router_leaving_while_findings_are_published(
    ctx: Context,
    store: Store,
    clock: Clock,
    engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await emit_at(ctx, clock, 0, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    emitted: list[str] = []

    async def another_one_leaves(topic: str, payload: dict[str, Any]) -> None:
        if topic == "finding" and not emitted:
            emitted.append("hub")
            await ctx.emit(kinds.BORDER_ROUTER_GONE, "test", "br:ff", name="Hub")

    engine.subscribe(another_one_leaves)
    await tick_at(engine, clock, 5)

    assert "failed on tick" not in caplog.text
    assert len(await store.findings()) == 1
    await tick_at(engine, clock, 10)
    assert len(await store.findings()) == 2
