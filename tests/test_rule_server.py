from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context
from matter_health.model import Severity
from matter_health.rules.server import ServerRule
from matter_health.store import Store


async def test_a_server_that_forgot_its_devices(
    ctx: Context, store: Store, clock: Clock
) -> None:
    engine = make_engine(ctx, rules=[ServerRule])
    await tick_at(engine, clock, -1)
    await emit_at(ctx, clock, 0, kinds.MATTER_NODES_LOST, previous=40)
    await emit_at(ctx, clock, 1, kinds.MATTER_NODES_LOST, previous=40)

    finding = await only_finding(store)
    assert finding.key == f"server:lost:{at(0).isoformat()}"
    assert finding.severity is Severity.PROBLEM
    assert finding.title == "finding.server_forgot.title"
    assert finding.params == {"count": 40}
    assert chain_keys(finding) == [
        "link.cause_unknown",
        "link.server_forgot",
        "link.server_forgot_impact",
        "fix.server_restore_backup",
    ]

    # Pairing one device again is not having the data back.
    await store.set_state("matter.nodes", {"total": 1, "unavailable": []})
    await tick_at(engine, clock, 10)
    assert (await only_finding(store)).ended_at is None

    await store.set_state("matter.nodes", {"total": 32, "unavailable": []})
    await tick_at(engine, clock, 20)
    assert (await only_finding(store)).ended_at == at(20)
    assert await store.get_state("server.lost") == {}
