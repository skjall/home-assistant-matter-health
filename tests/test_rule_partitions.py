from typing import Any

import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Severity
from matter_health.store import Store
from matter_health.transports.thread.partitions import PartitionRule


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx, rules=[PartitionRule])


def part(partition: str, *names: str, leader: str | None = None) -> dict[str, Any]:
    return {
        "partition": partition,
        "leader": leader,
        "border_routers": [
            {"subject": f"br:{n.lower()}", "name": n, "role": "router"} for n in names
        ],
    }


WHOLE = [part("aa", "Home Assistant", "Kitchen", "TV", leader="Kitchen")]
SPLIT = [
    part("aa", "Home Assistant", "Kitchen", leader="Kitchen"),
    part("bb", "TV", "Hall"),
]


async def test_a_lasting_split_is_told_from_when_it_was_first_noticed(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_PARTITIONS, parts=WHOLE)
    # Home Assistant's border router heard the other part first.
    await emit_at(ctx, clock, 10, kinds.THREAD_FOREIGN_PARTITION)
    await emit_at(ctx, clock, 30, kinds.THREAD_PARTITIONS, parts=SPLIT)
    await tick_at(engine, clock, 34)
    assert await store.findings() == []

    await tick_at(engine, clock, 35)

    finding = await only_finding(store)
    assert finding.key == f"partitions:{at(10).isoformat()}"
    assert finding.started_at == at(10)
    assert finding.severity is Severity.PROBLEM
    assert finding.params == {"count": 2}
    assert finding.subjects == ["br:tv", "br:hall"]
    assert chain_keys(finding) == [
        "link.thread_split",
        "link.thread_split_leader",
        "link.thread_split_impact",
        "fix.thread_split",
    ]
    assert finding.chain[0].params == {
        "count": 2,
        "apart": "TV, Hall",
        "leader": "Kitchen",
    }

    # The parts change while it lasts; then the mesh is whole again.
    await emit_at(
        ctx,
        clock,
        40,
        kinds.THREAD_PARTITIONS,
        parts=[part("aa", "Home Assistant", "Kitchen", "Hall"), part("bb", "TV")],
    )
    changed = await only_finding(store)
    assert changed.chain[0].params["apart"] == "TV"
    assert chain_keys(changed)[1] == "link.thread_split_no_leader"
    await emit_at(ctx, clock, 50, kinds.THREAD_PARTITIONS, parts=WHOLE)
    assert (await only_finding(store)).ended_at == at(50)


async def test_a_short_split_is_left_to_the_mesh_rule(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_PARTITIONS, parts=SPLIT)
    await tick_at(engine, clock, 2)
    await emit_at(ctx, clock, 3, kinds.THREAD_PARTITIONS, parts=WHOLE)
    await tick_at(engine, clock, 10)

    assert await store.findings() == []
    assert await store.get_state("partitions.split") == {}
