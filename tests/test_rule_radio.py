import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Severity
from matter_health.store import Store
from matter_health.transports.thread.radio import RadioRule


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx, rules=[RadioRule])


async def test_a_busy_channel_until_it_calms_down(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    async def busy(at_minutes: float, count: int) -> None:
        # "minutes" is a field of the event and a parameter of emit_at.
        clock.set(at(at_minutes))
        await ctx.emit(kinds.THREAD_CHANNEL_BUSY, "test", count=count, minutes=10)

    await busy(0, 20)
    await busy(30, 35)

    finding = await only_finding(store)
    assert finding.key == f"radio:busy:{at(0).isoformat()}"
    assert finding.severity is Severity.WARNING
    assert finding.title == "finding.channel_busy.title"
    assert finding.params == {"count": 35, "minutes": 10}
    assert chain_keys(finding) == [
        "link.channel_busy_cause",
        "link.channel_busy",
        "link.channel_busy_impact",
        "fix.channel_busy",
    ]
    assert len(finding.chain[1].evidence) == 2

    await tick_at(engine, clock, 74)
    assert (await only_finding(store)).ended_at is None
    await tick_at(engine, clock, 75)
    assert (await only_finding(store)).ended_at == at(75)


async def test_losing_the_radio_is_reported_at_once(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(
        ctx, clock, 0, kinds.THREAD_RADIO_FAULT, reason="RCP failure detected"
    )

    finding = await only_finding(store)
    assert finding.severity is Severity.PROBLEM
    assert finding.title == "finding.radio_fault.title"
    assert finding.params == {"reason": "RCP failure detected"}
    assert chain_keys(finding) == [
        "link.radio_fault",
        "link.radio_fault_impact",
        "fix.radio_fault",
    ]

    await tick_at(engine, clock, 29)
    assert (await only_finding(store)).ended_at is None
    await tick_at(engine, clock, 30)
    assert (await only_finding(store)).ended_at == at(30)
    assert "faults" not in await store.get_state("radio.state")


async def test_small_hiccups_count_only_when_repeated(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_RADIO_FAULT, reason="NoBufs")
    await emit_at(ctx, clock, 10, kinds.THREAD_RADIO_FAULT, reason="NoBufs")
    # The first one is too long ago to count with the next two.
    await emit_at(ctx, clock, 35, kinds.THREAD_RADIO_FAULT, reason="NoBufs")
    assert await store.findings() == []

    await emit_at(
        ctx, clock, 36, kinds.THREAD_RADIO_FAULT, reason="Wait for response timeout"
    )
    finding = await only_finding(store)
    assert finding.started_at == at(10)
    assert finding.params == {"reason": "Wait for response timeout"}
    assert len(finding.chain[0].evidence) == 3


@pytest.mark.parametrize("role", ["detached", "disabled"])
async def test_the_border_router_outside_the_network(
    ctx: Context, store: Store, clock: Clock, engine: Engine, role: str
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_STATE, role=role)
    await emit_at(ctx, clock, 1, kinds.THREAD_STATE, role=role)
    await tick_at(engine, clock, 2)
    assert await store.findings() == []

    await tick_at(engine, clock, 3)
    await tick_at(engine, clock, 4)
    finding = await only_finding(store)
    assert finding.key == f"radio:role:{at(0).isoformat()}"
    assert finding.title == f"finding.otbr_{role}.title"
    assert chain_keys(finding) == [
        f"link.otbr_{role}",
        "link.otbr_outside_impact",
        f"fix.otbr_{role}",
    ]

    await emit_at(ctx, clock, 10, kinds.THREAD_STATE, role="router")
    assert (await only_finding(store)).ended_at == at(10)


async def test_a_short_detach_and_a_role_change_between_outside_roles(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_STATE, role="detached")
    await emit_at(ctx, clock, 1, kinds.THREAD_STATE, role="leader")
    await tick_at(engine, clock, 10)
    assert await store.findings() == []

    await emit_at(ctx, clock, 20, kinds.THREAD_STATE, role="detached")
    await emit_at(ctx, clock, 21, kinds.THREAD_STATE, role="disabled")
    await tick_at(engine, clock, 23)
    assert await store.findings() == []
    await tick_at(engine, clock, 24)
    assert (await only_finding(store)).started_at == at(21)
