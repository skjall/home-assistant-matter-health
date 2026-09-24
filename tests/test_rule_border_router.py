from typing import Any

import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Finding, Severity
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


async def power_on(ctx: Context, clock: Clock, minutes: float) -> None:
    await emit_at(
        ctx,
        clock,
        minutes,
        kinds.HA_POWER_ON,
        "entity:switch.media_plug",
        name="Media Plug",
        origin="person",
        by="Alex",
    )


async def plug_cycle(
    ctx: Context, clock: Clock, engine: Engine, minute: float, back_after: float = 3
) -> None:
    """Media Plug off, the TV gone, the plug on, the TV back."""
    await power_off(ctx, clock, minute)
    await emit_at(ctx, clock, minute + 1, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    await power_on(ctx, clock, minute + 2)
    await emit_at(
        ctx,
        clock,
        minute + 2 + back_after,
        kinds.BORDER_ROUTER_APPEARED,
        TV_SUBJECT,
        **TV,
    )
    await tick_at(engine, clock, minute + 2 + back_after)


def switched(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.key.startswith("switched_border_router:")]


async def test_off_gone_on_back_twice_means_the_router_hangs_on_it(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await plug_cycle(ctx, clock, engine, 0)
    assert switched(await store.findings()) == []

    await plug_cycle(ctx, clock, engine, 60)

    (finding,) = switched(await store.findings())
    assert finding.key == (
        "switched_border_router:Living Room TV:entity:switch.media_plug:"
        + at(1).isoformat()
    )
    assert finding.title == "finding.border_router_switched.title"
    assert finding.params == {"border_router": "Living Room TV", "switch": "Media Plug"}
    assert finding.started_at == at(1)
    assert finding.ended_at is None
    assert finding.subjects == [TV_SUBJECT, "entity:switch.media_plug"]
    assert chain_keys(finding) == [
        "link.border_router_on_switch",
        "link.border_router_on_switch_impact",
        "fix.keep_border_router_powered",
    ]
    assert finding.chain[0].params["count"] == 2
    assert finding.chain[0].confidence is Confidence.LIKELY
    assert len(finding.chain[0].evidence) == 8

    # The next time the pattern holds, the router is expected to go.
    await power_off(ctx, clock, 120)
    await emit_at(ctx, clock, 121, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    await tick_at(engine, clock, 130)
    (still,) = switched(await store.findings())
    assert still.ended_at is None


async def test_returning_long_after_the_switch_is_no_proof(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await plug_cycle(ctx, clock, engine, 0)
    await plug_cycle(ctx, clock, engine, 60, back_after=30)

    assert switched(await store.findings()) == []
    assert await store.get_state("border_router.suspects") == {}


async def test_several_switches_at_once_are_no_evidence(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    for minute in (0, 60):
        await emit_at(
            ctx, clock, minute, kinds.HA_POWER_OFF, "entity:switch.coffee", name="x"
        )
        await plug_cycle(ctx, clock, engine, minute)

    assert switched(await store.findings()) == []


async def test_a_switch_without_subject_is_not_counted(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    for minute in (0, 60):
        await emit_at(ctx, clock, minute, kinds.HA_POWER_OFF, name="Somewhere")
        await emit_at(ctx, clock, minute + 1, kinds.BORDER_ROUTER_GONE, TV_SUBJECT)
        await emit_at(ctx, clock, minute + 2, kinds.BORDER_ROUTER_APPEARED, TV_SUBJECT)

    assert await store.findings() == []


async def confirmed(ctx: Context, clock: Clock, engine: Engine, store: Store) -> None:
    await plug_cycle(ctx, clock, engine, 0)
    await plug_cycle(ctx, clock, engine, 60)
    assert len(switched(await store.findings())) == 1


async def test_gone_while_the_switch_stayed_on_closes_it(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await confirmed(ctx, clock, engine, store)

    await emit_at(ctx, clock, 200, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)

    (finding,) = switched(await store.findings())
    assert finding.ended_at == at(200)
    assert await store.get_state("border_router.suspects") == {}


async def test_back_while_the_switch_stayed_off_closes_it(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await confirmed(ctx, clock, engine, store)

    await power_off(ctx, clock, 200)
    await emit_at(ctx, clock, 201, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    await emit_at(ctx, clock, 205, kinds.BORDER_ROUTER_APPEARED, TV_SUBJECT, **TV)

    (finding,) = switched(await store.findings())
    assert finding.ended_at == at(205)


async def test_staying_while_the_switch_went_off_closes_it(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await confirmed(ctx, clock, engine, store)

    await power_off(ctx, clock, 200)
    await tick_at(engine, clock, 203)
    (open_still,) = switched(await store.findings())
    assert open_still.ended_at is None
    await tick_at(engine, clock, 205)

    (finding,) = switched(await store.findings())
    assert finding.ended_at == at(205)


async def test_a_single_hit_is_forgotten_quietly(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await plug_cycle(ctx, clock, engine, 0)
    await emit_at(ctx, clock, 30, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)

    assert switched(await store.findings()) == []
    assert await store.get_state("border_router.suspects") == {}


async def test_the_cause_is_only_possible_with_several_switches(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.HA_POWER_OFF, "entity:switch.coffee", name="x")
    await power_off(ctx, clock, 1)
    await emit_at(ctx, clock, 2, kinds.BORDER_ROUTER_GONE, TV_SUBJECT, **TV)
    await tick_at(engine, clock, 8)

    (finding,) = await store.findings()
    assert finding.chain[0].confidence is Confidence.POSSIBLE


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


async def test_other_routers_leave_the_suspicion_alone(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await confirmed(ctx, clock, engine, store)

    await emit_at(ctx, clock, 200, kinds.BORDER_ROUTER_GONE, "br:ff", name="Hub")
    await emit_at(ctx, clock, 201, kinds.BORDER_ROUTER_APPEARED, "br:ff", name="Hub")
    # Back without having been seen leaving.
    await emit_at(ctx, clock, 202, kinds.BORDER_ROUTER_APPEARED, TV_SUBJECT, **TV)
    await emit_at(ctx, clock, 203, kinds.HA_POWER_OFF, "entity:switch.lamp", name="x")
    await tick_at(engine, clock, 220)

    (finding,) = switched(await store.findings())
    assert finding.ended_at is None
