import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import batteries, kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Severity
from matter_health.rules.reboots import RebootRule
from matter_health.store import Store

DAY = 24 * 60


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx, rules=[RebootRule])


async def restart(
    ctx: Context, clock: Clock, minute: float, count: int = 1, **data: object
) -> None:
    await emit_at(
        ctx,
        clock,
        minute,
        kinds.MATTER_NODE_REBOOTED,
        "node:1",
        name="Window",
        count=count,
        **data,
    )


async def test_many_restarts_in_a_few_days_are_told(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await restart(ctx, clock, 0, 4)
    await restart(ctx, clock, DAY, 5)
    assert await store.findings() == []
    await restart(ctx, clock, 2 * DAY)

    finding = await only_finding(store)
    assert finding.key == f"reboots:node:1:{at(0).isoformat()}"
    assert finding.severity is Severity.WARNING
    assert finding.params == {"device": "Window"}
    assert chain_keys(finding) == [
        "link.reboots_cause",
        "link.reboots",
        "link.reboots_impact",
        "fix.reboots",
    ]
    assert finding.chain[0].confidence is Confidence.POSSIBLE
    assert finding.chain[1].params == {"device": "Window", "count": 10, "days": 7}
    assert finding.subjects == ["node:1"]

    # Still restarting a day later: it goes on.
    await restart(ctx, clock, 3 * DAY)
    await tick_at(engine, clock, 4 * DAY)
    assert (await only_finding(store)).ended_at is None
    # Two days without: it has settled.
    await tick_at(engine, clock, 5 * DAY)
    ended = await only_finding(store)
    assert ended.ended_at == at(5 * DAY)
    assert await store.get_state("reboots.open") == {}


async def test_restarts_long_ago_do_not_count(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await restart(ctx, clock, 0, 9)
    await restart(ctx, clock, 8 * DAY)

    assert await store.findings() == []


async def test_a_weak_battery_is_the_likely_cause(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state(
        batteries.BATTERIES, {"node:1": {"cell_millivolts": 1263, "weak": True}}
    )
    await restart(ctx, clock, 0, 10)

    finding = await only_finding(store)
    assert finding.chain[0].key == "link.reboots_weak_battery"
    assert finding.chain[0].params["volts"] == 1.26
    assert finding.chain[0].confidence is Confidence.LIKELY


@pytest.mark.parametrize(
    ("reason", "cause", "fix"),
    [
        ("brown_out", "link.reboots_brown_out", "fix.reboots"),
        ("hardware_watchdog", "link.reboots_watchdog", "fix.reboots_firmware"),
    ],
)
async def test_the_device_may_name_the_reason(
    ctx: Context,
    store: Store,
    clock: Clock,
    engine: Engine,
    reason: str,
    cause: str,
    fix: str,
) -> None:
    await restart(ctx, clock, 0, 12, reason=reason)

    finding = await only_finding(store)
    assert (finding.chain[0].key, chain_keys(finding)[-1]) == (cause, fix)
    assert finding.chain[0].confidence is Confidence.CERTAIN


async def test_a_restart_without_a_device_is_ignored(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_REBOOTED, None, count=20)

    assert await store.findings() == []
