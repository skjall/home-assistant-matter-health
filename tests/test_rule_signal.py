from typing import Any

import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Severity
from matter_health.rules.signal import SignalRule, is_weak
from matter_health.store import Store


@pytest.fixture
def engine(ctx: Context) -> Engine:
    ctx.names.set("node:1", "Hallway Sensor")
    ctx.names.set("br:0a1b2c3d4e5f6071", "Living Room TV")
    return make_engine(ctx, rules=[SignalRule])


def link(subject: Any, **values: Any) -> dict[str, Any]:
    return {"subject": subject, "neighbour": "br:0a1b2c3d4e5f6071", **values}


@pytest.mark.parametrize(
    ("values", "weak"),
    [
        ({"strength": "weak", "rssi": -40}, True),
        ({"rssi": -85}, True),
        ({"rssi": -84, "lqi": 2}, False),
        ({"lqi": 1}, True),
        ({}, False),
    ],
)
def test_is_weak(values: dict[str, Any], weak: bool) -> None:
    assert is_weak(values) is weak


async def test_weak_links_open_and_close_findings(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(
        ctx,
        clock,
        0,
        kinds.THREAD_TOPOLOGY,
        devices=[
            link("node:1", role="sleepy_end_device", rssi=-90, lqi=1),
            link("node:2", role="router", rssi=-60, lqi=3),
            link("node:3", role="router", strength="weak"),
            link("br:0a1b2c3d4e5f6071", rssi=-99),
            link(None, rssi=-99),
            link(7, rssi=-99),
        ],
    )

    found = {f.key: f for f in await store.findings()}
    assert sorted(found) == ["signal:node:1", "signal:node:3"]
    sensor = found["signal:node:1"]
    assert sensor.severity is Severity.WARNING
    assert sensor.title == "finding.weak_signal.title"
    assert sensor.params == {
        "device": "Hallway Sensor",
        "neighbour": "Living Room TV",
        "rssi": -90,
        "lqi": 1,
    }
    assert chain_keys(sensor) == [
        "link.weak_signal",
        "link.weak_signal_impact",
        "link.weak_signal_battery",
        "fix.weak_signal",
    ]
    assert chain_keys(found["signal:node:3"]) == [
        "link.weak_signal",
        "link.weak_signal_impact",
        "fix.weak_signal",
    ]

    await emit_at(
        ctx,
        clock,
        10,
        kinds.THREAD_TOPOLOGY,
        devices=[link("node:1", role="sleepy_end_device", rssi=-88)],
    )

    found = {f.key: f for f in await store.findings()}
    assert found["signal:node:1"].started_at == at(0)
    assert found["signal:node:1"].ended_at is None
    assert found["signal:node:1"].params["rssi"] == -88
    assert found["signal:node:3"].ended_at == at(10)

    await emit_at(ctx, clock, 20, kinds.THREAD_TOPOLOGY)

    found = {f.key: f for f in await store.findings()}
    assert found["signal:node:1"].ended_at == at(20)
