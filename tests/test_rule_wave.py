import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Severity
from matter_health.rules.wave import WaveRule
from matter_health.store import Store

THREAD = [f"node:{n}" for n in range(1, 7)]


@pytest.fixture
async def engine(ctx: Context, store: Store) -> Engine:
    await store.set_state(
        "thread.links",
        [{"subject": s, "neighbour": "br:aa"} for s in THREAD]
        + [{"subject": "br:aa", "neighbour": None}],
    )
    return make_engine(ctx, rules=[WaveRule])


async def gone(
    ctx: Context, clock: Clock, store: Store, subjects: list[str], start: float = 0
) -> None:
    for offset, subject in enumerate(subjects):
        await emit_at(
            ctx, clock, start + offset * 0.2, kinds.MATTER_NODE_UNAVAILABLE, subject
        )
    await store.set_state("matter.nodes", {"total": 10, "unavailable": subjects})


async def test_half_the_thread_devices_at_once(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    # A Wi-Fi device going too changes nothing.
    await emit_at(ctx, clock, 0, kinds.MATTER_NODE_UNAVAILABLE, "node:99")
    await gone(ctx, clock, store, THREAD[:3])

    finding = await only_finding(store)
    assert finding.key == f"wave:{at(0).isoformat()}"
    assert finding.severity is Severity.PROBLEM
    assert finding.title == "finding.thread_wave.title"
    assert finding.params == {"count": 3, "total": 6}
    assert finding.subjects == THREAD[:3]
    assert chain_keys(finding) == [
        "link.route_probably_missing",
        "link.thread_wave",
        "link.thread_wave_impact",
        "fix.thread_wave",
    ]
    assert finding.chain[0].confidence is Confidence.POSSIBLE

    # Another device while the wave lasts opens nothing new.
    await emit_at(ctx, clock, 1, kinds.MATTER_NODE_UNAVAILABLE, THREAD[3])
    assert len(await store.findings()) == 1

    await store.set_state("matter.nodes", {"total": 10, "unavailable": THREAD[:2]})
    await tick_at(engine, clock, 5)
    assert (await only_finding(store)).ended_at is None
    await store.set_state("matter.nodes", {"total": 10, "unavailable": THREAD[:1]})
    await tick_at(engine, clock, 6)
    assert (await only_finding(store)).ended_at == at(6)
    await tick_at(engine, clock, 7)


async def test_too_few_or_too_spread_out_is_no_wave(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await gone(ctx, clock, store, THREAD[:2])
    await emit_at(ctx, clock, 10, kinds.MATTER_NODE_UNAVAILABLE, THREAD[2])

    assert await store.findings() == []


async def test_a_missing_route_in_the_log_and_an_update(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(
        ctx,
        clock,
        -60,
        kinds.SYSTEM_UPDATED,
        "software:os",
        name="Home Assistant OS",
        previous="1",
        current="2",
    )
    await emit_at(ctx, clock, -1, kinds.MATTER_ROUTE_UNREACHABLE, THREAD[0])
    await gone(ctx, clock, store, THREAD[:4])

    finding = await only_finding(store)
    assert chain_keys(finding)[:2] == ["link.route_missing", "link.updated_before"]
    assert finding.chain[0].confidence is Confidence.LIKELY
    assert finding.chain[0].evidence


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (kinds.THREAD_LEADER_LOST, {}),
        (kinds.BORDER_ROUTER_GONE, {"name": "Kitchen Speaker"}),
    ],
)
async def test_mesh_trouble_or_a_lost_border_router_tell_another_story(
    ctx: Context,
    store: Store,
    clock: Clock,
    engine: Engine,
    kind: str,
    data: dict[str, str],
) -> None:
    await emit_at(ctx, clock, -2, kind, "br:bb", **data)
    await gone(ctx, clock, store, THREAD[:4])

    assert await store.findings() == []


async def test_without_a_radio_picture_nothing_is_judged(
    ctx: Context, store: Store, clock: Clock
) -> None:
    engine = make_engine(ctx, rules=[WaveRule])
    await gone(ctx, clock, store, THREAD[:4])
    await tick_at(engine, clock, 5)

    assert await store.findings() == []
