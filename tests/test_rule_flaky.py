import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Finding, Link, Role, Severity
from matter_health.rules.flaky import FlakyRule
from matter_health.store import Store

SENSOR = "node:9"


@pytest.fixture
def engine(ctx: Context) -> Engine:
    ctx.names.set(SENSOR, "Bathroom Sensor")
    return make_engine(ctx, rules=[FlakyRule])


async def drop(ctx: Context, clock: Clock, start: float, minutes: float = 2) -> None:
    await emit_at(ctx, clock, start, kinds.MATTER_NODE_UNAVAILABLE, SENSOR)
    await emit_at(ctx, clock, start + minutes, kinds.MATTER_NODE_AVAILABLE, SENSOR)


async def test_a_few_drops_are_nothing(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    for start in (0, 60, 120):
        await drop(ctx, clock, start)
    # A long absence (unplugged) is not a short drop.
    await drop(ctx, clock, 200, minutes=120)
    # A return without a subject is ignored.
    await emit_at(ctx, clock, 400, kinds.MATTER_NODE_AVAILABLE)

    assert await store.findings() == []


async def test_many_short_drops_open_a_finding_that_closes_after_a_quiet_day(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    for start in (0, 60, 120, 180):
        await drop(ctx, clock, start)

    finding = await only_finding(store)
    assert finding.key == f"flaky:{SENSOR}:{at(0).isoformat()}"
    assert finding.severity is Severity.WARNING
    assert finding.title == "finding.device_flaky.title"
    assert finding.params == {"device": "Bathroom Sensor"}
    assert chain_keys(finding) == [
        "link.cause_unknown",
        "link.device_flaky",
        "link.device_flaky_impact",
        "fix.device_flaky",
    ]
    assert finding.chain[1].params == {"device": "Bathroom Sensor", "count": 4}

    # Every further drop updates the same finding.
    await drop(ctx, clock, 300, minutes=30)
    finding = await only_finding(store)
    assert finding.chain[1].params["count"] == 5
    assert finding.ended_at is None

    await tick_at(engine, clock, 330 + 23 * 60)
    assert (await only_finding(store)).ended_at is None
    await tick_at(engine, clock, 330 + 24 * 60)
    assert (await only_finding(store)).ended_at == at(330 + 24 * 60)
    assert await store.get_state("flaky.open") == {}


async def test_a_weak_link_is_the_likely_cause(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.put_finding(
        Finding(
            key=f"signal:{SENSOR}",
            rule="signal",
            severity=Severity.WARNING,
            title="finding.weak_signal.title",
            started_at=at(-60),
            chain=[Link(Role.EFFECT, "link.weak_signal")],
        ),
        at(-60),
    )
    for start in (0, 10, 20, 30):
        await drop(ctx, clock, start)

    finding = next(f for f in await store.findings() if f.rule == "flaky")
    assert finding.chain[0].key == "link.weak_signal_cause"
    assert finding.chain[0].confidence is Confidence.LIKELY


async def test_an_update_just_before_is_a_possible_cause(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(
        ctx,
        clock,
        -30,
        kinds.SYSTEM_UPDATED,
        "software:core",
        name="Home Assistant",
        previous="1.0",
        current="1.1",
    )
    for start in (0, 10, 20, 30):
        await drop(ctx, clock, start)

    finding = await only_finding(store)
    assert finding.chain[0].key == "link.updated_before"
    assert finding.chain[0].params == {"software": "Home Assistant", "version": "1.1"}
    assert finding.chain[0].confidence is Confidence.POSSIBLE
