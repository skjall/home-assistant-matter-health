from typing import Any

import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.enrichers import Client, Knowledge, state_key
from matter_health.model import Severity
from matter_health.store import Store
from matter_health.transports import ACCESS_POINTS
from matter_health.transports.thread.interference import (
    InterferenceRule,
    further,
    shared,
)


@pytest.fixture
def engine(ctx: Context) -> Engine:
    return make_engine(ctx, rules=[InterferenceRule])


def reading(*busy: int | None) -> list[dict[str, Any]]:
    return [
        {"subject": f"node:{i}", "cca_per_hour": rate, "retries_per_frame": 0.1}
        for i, rate in enumerate(busy, start=1)
    ]


async def test_interference_everywhere(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_INTERFERENCE, devices=reading(90, 120, 5))
    assert await store.findings() == []

    devices = reading(80, 200, 5)
    await emit_at(ctx, clock, 10, kinds.THREAD_INTERFERENCE, devices=devices)

    finding = await only_finding(store)
    assert finding.title == "finding.interference_wide.title"
    assert finding.severity is Severity.WARNING
    assert finding.started_at == at(10)
    assert finding.subjects == []
    assert finding.params == {
        "count": 2,
        "total": 3,
        "rest": 1,
        "rate": 140,
        "devices": "node:1, node:2",
    }
    assert chain_keys(finding) == [
        "link.interference_wide_cause",
        "link.interference_wide",
        "link.channel_busy_impact",
        "fix.channel_busy",
    ]

    # One quiet reading is not the end; two are.
    await emit_at(ctx, clock, 20, kinds.THREAD_INTERFERENCE, devices=reading(1, 2, 3))
    assert (await only_finding(store)).ended_at is None
    await emit_at(ctx, clock, 30, kinds.THREAD_INTERFERENCE, devices=reading(1, 2, 3))
    assert (await only_finding(store)).ended_at == at(30)


async def test_interference_around_a_few_devices(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    for name in ("A", "B", "C", "D", "E"):
        ctx.names.set(f"node:{'ABCDE'.index(name) + 1}", name)
    busy = reading(100, 100, 100, 100, 100, 1, 1, 1, 1, 1, 1)

    await emit_at(ctx, clock, 0, kinds.THREAD_INTERFERENCE, devices=busy)
    await emit_at(ctx, clock, 10, kinds.THREAD_INTERFERENCE, devices=busy)

    finding = await only_finding(store)
    assert finding.title == "finding.interference_local.title"
    assert finding.params["devices"] == "A, B, C, D (+1)"
    assert finding.params["rest"] == 6
    assert finding.subjects == [f"node:{i}" for i in range(1, 6)]
    assert chain_keys(finding)[-1] == "fix.interference_local"


async def test_too_few_measurements_tell_nothing(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    for minutes in (0, 10, 20):
        await emit_at(
            ctx, clock, minutes, kinds.THREAD_INTERFERENCE, devices=reading(500, None)
        )

    assert await store.findings() == []


@pytest.mark.parametrize(
    ("thread", "wifi", "how"),
    [
        (20, 9, "overlaps"),
        (20, 11, "borders"),
        (20, 6, "borders"),
        (20, 1, None),
        (11, 1, "overlaps"),
        (26, 14, "overlaps"),
        (26, 11, "borders"),
        (20, 36, None),
    ],
)
def test_which_wifi_channels_share_a_thread_channel(
    thread: int, wifi: int, how: str | None
) -> None:
    assert shared(thread, wifi) == how


def test_the_wifi_channel_to_try_is_furthest_away() -> None:
    assert further(20) == 1
    assert further(11) == 11


async def test_access_points_near_the_thread_channel_are_named(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state("thread.channel", 20)
    await store.set_state(
        ACCESS_POINTS,
        {
            "02:00:00:00:00:01": {"channel": 1, "clients": []},
            "02:00:00:00:00:02": {"channel": 11, "clients": []},
            "02:00:00:00:00:03": {"channel": None, "clients": []},
            "02:00:00:00:00:04": {
                "channel": 9,
                "clients": [{"mac": "02:00:00:00:10:01", "addresses": []}],
            },
        },
    )
    await store.set_state(
        state_key("unifi"),
        Knowledge([Client("02:00:00:00:10:01", None, "Plug", False, "Hall AP")]).dump(),
    )

    await emit_at(ctx, clock, 0, kinds.THREAD_INTERFERENCE, devices=reading(90, 120, 5))
    await emit_at(
        ctx, clock, 10, kinds.THREAD_INTERFERENCE, devices=reading(90, 120, 5)
    )

    finding = await only_finding(store)
    assert chain_keys(finding) == [
        "link.interference_access_point_overlaps",
        "link.interference_access_point_borders",
        "link.interference_wide_cause",
        "link.interference_wide",
        "link.channel_busy_impact",
        "fix.interference_access_point",
        "fix.interference_access_point",
        "fix.channel_busy",
    ]
    assert finding.chain[0].params == {
        "access_point": "Hall AP",
        "how": "overlaps",
        "wifi_channel": 9,
        "thread_channel": 20,
        "suggest": 1,
    }
    assert finding.chain[1].params["access_point"] == "02:00:00:00:00:02"


async def test_without_the_thread_channel_no_access_point_is_named(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await store.set_state(ACCESS_POINTS, {"02:00:00:00:00:02": {"channel": 11}})

    await emit_at(ctx, clock, 0, kinds.THREAD_INTERFERENCE, devices=reading(90, 120, 5))
    await emit_at(
        ctx, clock, 10, kinds.THREAD_INTERFERENCE, devices=reading(90, 120, 5)
    )

    assert chain_keys(await only_finding(store))[0] == "link.interference_wide_cause"
