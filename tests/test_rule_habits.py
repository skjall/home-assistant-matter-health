from datetime import datetime, timedelta

from conftest import (
    T0,
    Clock,
    at,
    chain_keys,
    emit_at,
    make_engine,
    only_finding,
    tick_at,
)

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Event
from matter_health.rules.habits import (
    EXPECTED_AT_LEAST,
    EXPECTED_AT_MOST,
    HABITS,
    Pattern,
    absences,
    marked,
    pattern,
)
from matter_health.rules.offline import OfflineRule
from matter_health.store import Store

COOKER = "node:21"


def span(start: float, minutes: float) -> tuple[datetime, datetime]:
    return (at(start), at(start + minutes))


def event(kind: str, minutes: float) -> Event:
    return Event(kind=kind, at=at(minutes), source="test", subject=COOKER)


def test_absences_pair_each_leaving_with_its_return() -> None:
    found = absences(
        [
            event(kinds.MATTER_NODE_AVAILABLE, 0),  # a return without a leaving
            event(kinds.MATTER_NODE_UNAVAILABLE, 1),
            event(kinds.MATTER_NODE_UNAVAILABLE, 2),  # still the same absence
            event(kinds.MATTER_NODE_AVAILABLE, 5),
            event(kinds.MATTER_NODE_UNAVAILABLE, 10),
            event(kinds.MATTER_NODE_REMOVED, 12),
            event(kinds.MATTER_NODE_UNAVAILABLE, 20),  # not back yet
        ]
    )

    assert found == [span(1, 4), span(10, 2)]


def test_a_device_without_history_is_reported_every_time() -> None:
    usual = Pattern()

    assert usual.longest is None
    assert not usual.comes_and_goes
    assert usual.expected_for is None


def test_three_long_absences_make_a_device_come_and_go() -> None:
    usual = Pattern(absences=[span(0, 30), span(100, 60), span(300, 5)])
    assert not usual.comes_and_goes  # the short one does not count

    usual.absences.append(span(500, 120))
    assert usual.comes_and_goes
    # Twice the longest absence, but never less than half a day.
    assert usual.expected_for == EXPECTED_AT_LEAST

    usual.absences.append(span(1000, 24 * 60))
    assert usual.expected_for == timedelta(days=2)

    usual.absences.append(span(5000, 10 * 24 * 60))
    assert usual.expected_for == EXPECTED_AT_MOST


def test_what_the_user_said_wins_over_the_history() -> None:
    long_ones = [span(0, 30), span(100, 30), span(200, 30)]

    assert not Pattern(absences=long_ones, marked=False).comes_and_goes
    assert Pattern(marked=True).comes_and_goes
    assert Pattern(marked=True).expected_for == EXPECTED_AT_LEAST


def test_counting_drops() -> None:
    usual = Pattern(absences=[span(0, 2), span(10, 3), span(20, 30), span(-100, 1)])

    assert usual.short_drops(at(0)) == 2
    assert usual.drops(at(0)) == 3


async def test_pattern_reads_history_and_marks(
    ctx: Context, store: Store, clock: Clock
) -> None:
    make_engine(ctx)
    await store.set_state(HABITS, {COOKER: 1, "node:2": 0})
    assert await marked(ctx) == {COOKER: True, "node:2": False}

    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, COOKER)
    await emit_at(ctx, clock, 15, kinds.MATTER_NODE_AVAILABLE, COOKER)
    await emit_at(ctx, clock, 16, kinds.MATTER_NODE_UNAVAILABLE, "node:3")

    usual = await pattern(ctx, COOKER)
    assert usual.absences == [span(0, 15)]
    assert usual.marked is True
    assert (await pattern(ctx, COOKER, {})).marked is None


async def comes_and_goes(ctx: Context, clock: Clock, engine: Engine) -> None:
    """Three evenings away for an hour each, reported the first times."""
    for day in range(3):
        start = day * 24 * 60
        await emit_at(ctx, clock, start, kinds.MATTER_NODE_UNAVAILABLE, COOKER)
        await tick_at(engine, clock, start + 11)
        await emit_at(ctx, clock, start + 60, kinds.MATTER_NODE_AVAILABLE, COOKER)


async def test_a_device_that_comes_and_goes_is_reported_only_when_away_long(
    ctx: Context, store: Store, clock: Clock
) -> None:
    ctx.names.set(COOKER, "Rice Cooker")
    engine = make_engine(ctx, rules=[OfflineRule])
    await comes_and_goes(ctx, clock, engine)
    assert len(await store.findings()) == 3

    start = 3 * 24 * 60
    await emit_at(ctx, clock, start, kinds.MATTER_NODE_UNAVAILABLE, COOKER)
    await tick_at(engine, clock, start + 11)
    assert len(await store.findings()) == 3
    away = await store.get_state("offline.away")
    assert away[COOKER]["expected"] is True
    assert away[COOKER]["reported"] is False

    # Half a day is the least an habitual absence may last unreported.
    await tick_at(engine, clock, start + 12 * 60 + 1)
    found = [f for f in await store.findings() if f.started_at == at(start)]
    assert len(found) == 1
    finding = found[0]
    assert finding.title == "finding.device_away_long.title"
    assert "link.usually_back" in chain_keys(finding)
    usually = next(link for link in finding.chain if link.key == "link.usually_back")
    assert usually.params == {"duration": 3600}
    assert "expected" not in (await store.get_state("offline.away"))[COOKER]


async def test_the_user_can_say_always_report(
    ctx: Context, store: Store, clock: Clock
) -> None:
    engine = make_engine(ctx, rules=[OfflineRule])
    await comes_and_goes(ctx, clock, engine)
    await store.set_state(HABITS, {COOKER: False})

    start = 3 * 24 * 60
    await emit_at(ctx, clock, start, kinds.MATTER_NODE_UNAVAILABLE, COOKER)
    await tick_at(engine, clock, start + 11)

    latest = [f for f in await store.findings() if f.started_at == at(start)]
    assert latest[0].title == "finding.device_unreachable.title"


async def test_a_marked_device_without_history_waits_half_a_day(
    ctx: Context, store: Store, clock: Clock
) -> None:
    engine = make_engine(ctx, rules=[OfflineRule])
    await store.set_state(HABITS, {COOKER: True})

    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, COOKER)
    await tick_at(engine, clock, 11 * 60)
    assert await store.findings() == []

    await tick_at(engine, clock, 12 * 60)
    finding = await only_finding(store)
    assert finding.title == "finding.device_away_long.title"
    # Nothing to compare with, so no "usually back after" line.
    assert "link.usually_back" not in chain_keys(finding)
    assert finding.started_at == T0
