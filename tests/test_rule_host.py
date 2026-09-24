from typing import Any

import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, tick_at

from matter_health import kinds
from matter_health.config import OTBR_SLUG
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Severity
from matter_health.store import Store
from matter_health.transports.thread.host import HostRule


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx, rules=[HostRule])


async def network(store: Store, **values: Any) -> None:
    await store.set_state(
        "system.network",
        {
            "docker_ipv6": True,
            "ipv6_method": "auto",
            "interface": "eth0",
            "haos": True,
            **values,
        },
    )


async def test_nothing_known_is_nothing_reported(
    store: Store, clock: Clock, engine: Engine
) -> None:
    await tick_at(engine, clock, 0)
    await network(store)
    await store.set_state("system.versions", {OTBR_SLUG: "2.0"})
    await tick_at(engine, clock, 1)

    assert await store.findings() == []


async def test_docker_without_ipv6_on_home_assistant_os(
    store: Store, clock: Clock, engine: Engine
) -> None:
    await network(store, docker_ipv6=None)
    await tick_at(engine, clock, 0)
    # Without the border router add-on, forwarding does not matter here.
    assert await store.findings() == []

    await store.set_state("system.versions", {OTBR_SLUG: "2.0"})
    await tick_at(engine, clock, 1)
    await tick_at(engine, clock, 2)
    [finding] = await store.findings()
    assert finding.key == f"host:forwarding:{at(1).isoformat()}"
    assert finding.severity is Severity.PROBLEM
    assert finding.title == "finding.forwarding_off.title"
    assert chain_keys(finding) == [
        "link.docker_ipv6_off",
        "link.forwarding_off",
        "link.forwarding_off_impact",
        "fix.enable_docker_ipv6",
    ]

    await network(store, docker_ipv6=True)
    await tick_at(engine, clock, 10)
    [finding] = await store.findings()
    assert finding.ended_at == at(10)


async def test_other_hosts_are_not_judged_by_docker(
    store: Store, clock: Clock, engine: Engine
) -> None:
    await network(store, docker_ipv6=None, haos=False)
    await store.set_state("system.versions", {OTBR_SLUG: "2.0"})
    await tick_at(engine, clock, 0)

    assert await store.findings() == []


async def test_the_border_router_warning_until_a_start_without_it(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.HOST_FORWARDING_OFF)
    await emit_at(ctx, clock, 0.1, kinds.THREAD_AGENT_STARTED)

    [finding] = await store.findings()
    assert chain_keys(finding) == [
        "link.forwarding_off_seen",
        "link.forwarding_off",
        "link.forwarding_off_impact",
        "fix.forwarding_update_os",
    ]
    assert finding.chain[0].at == at(0)
    assert finding.chain[0].evidence

    # The agent started again later, and this time without the warning.
    await emit_at(ctx, clock, 60, kinds.THREAD_AGENT_STARTED)
    [finding] = await store.findings()
    assert finding.ended_at == at(60)
    # Closed as it was told, with the warning that explained it.
    assert chain_keys(finding)[0] == "link.forwarding_off_seen"
    assert "warned" not in (await store.get_state("host.state"))


async def test_a_start_without_any_warning_changes_nothing(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_AGENT_STARTED)

    assert await store.findings() == []


@pytest.mark.parametrize(
    ("method", "severity", "title", "cause", "confidence"),
    [
        (
            "disabled",
            Severity.PROBLEM,
            "finding.ipv6_off.title",
            "link.ipv6_disabled",
            Confidence.CERTAIN,
        ),
        (
            "static",
            Severity.WARNING,
            "finding.ipv6_static.title",
            "link.ipv6_static",
            Confidence.POSSIBLE,
        ),
    ],
)
async def test_ipv6_off_or_set_by_hand(
    store: Store,
    clock: Clock,
    engine: Engine,
    method: str,
    severity: Severity,
    title: str,
    cause: str,
    confidence: Confidence,
) -> None:
    await network(store, ipv6_method=method, interface=None)
    await tick_at(engine, clock, 0)

    [finding] = await store.findings()
    assert finding.severity is severity
    assert finding.title == title
    assert finding.params == {"interface": "-"}
    assert chain_keys(finding) == [
        cause,
        "link.ipv6_no_routes",
        "link.ipv6_impact",
        "fix.ipv6_automatic",
    ]
    assert finding.chain[1].confidence is confidence

    await network(store, ipv6_method="auto")
    await tick_at(engine, clock, 5)
    [finding] = await store.findings()
    assert finding.ended_at == at(5)
