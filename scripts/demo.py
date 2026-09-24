#!/usr/bin/env python3
"""Serve the page with an invented history, without any Home Assistant.

For looking at the UI and for the screenshots in the README: nothing here
comes from a real installation. The events go through the real rules, so what
the page shows is what the add-on would conclude from them.

    python3 scripts/demo.py            then open http://localhost:8099
    python3 scripts/demo.py --port 8100
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "matter_health" / "app"))

from matter_health import kinds, rules
from matter_health.config import Options
from matter_health.engine import RULES, Context, Engine, utcnow
from matter_health.store import Store
from matter_health.web import create_app

PLUGINS = (rules,)

NAMES = {
    "node:3": "Kitchen Plug",
    "node:7": "Garden Sensor",
    "node:9": "Bathroom Sensor",
    "node:12": "Hallway Motion Sensor",
    "node:21": "Desk Lamp Plug",
    "node:30": "Coffee Machine",
    "node:31": "Drawer Button",
    "br:0a1b2c3d4e5f6071": "Living Room TV",
    "br:1b2c3d4e5f607182": "Kitchen Speaker",
    "br:2c3d4e5f60718293": "Bedroom Speaker",
    "br:3d4e5f6071829304": "Home Assistant",
}

BORDER_ROUTERS = [
    {"subject": s, "name": n, "vendor": v, "model": None}
    for s, n, v in (
        ("br:3d4e5f6071829304", "Home Assistant", "Home Assistant"),
        ("br:2c3d4e5f60718293", "Bedroom Speaker", "Acme"),
        ("br:1b2c3d4e5f607182", "Kitchen Speaker", "Acme"),
        ("br:0a1b2c3d4e5f6071", "Living Room TV", "Acme"),
    )
]

PAIRING_STEPS = (
    "GetInitialData",
    "GeneralCommissioning.ArmFailsafe",
    "GeneralCommissioning.ConfigureRegulatoryInformation",
    "TimeSynchronization",
    "OperationalCredentials.DeviceAttestation",
    "OperationalCredentials.Certificates",
    "AccessControl",
    "ThreadNetworkSetup",
    "Reconnect",
)


class Clock:
    """A clock the scenario moves forward by hand."""

    def __init__(self, start: datetime) -> None:
        """Start at ``start``."""
        self.at = start

    def __call__(self) -> datetime:
        """Return the scenario's time."""
        return self.at

    def go(self, **delta: float) -> None:
        """Move forward."""
        self.at += timedelta(**delta)


async def seed(engine: Engine, clock: Clock) -> None:
    """Play an invented week through the rules."""
    ctx = engine.ctx
    for subject, name in NAMES.items():
        ctx.names.set(subject, name)

    async def emit(kind: str, subject: str | None = None, **data: Any) -> None:
        await ctx.emit(kind, "demo", subject, **data)

    async def tick(minutes: int) -> None:
        for _ in range(minutes * 2):
            clock.go(seconds=30)
            for rule in engine.rules:
                await rule.on_tick()

    async def pairing(node: int, fail_at: str | None, **reason: Any) -> None:
        await emit(kinds.COMMISSIONING_CONTACT)
        clock.go(seconds=4)
        await emit(kinds.COMMISSIONING_CONTACT_OK)
        await emit(kinds.COMMISSIONING_STARTED, f"node:{node}", node_id=node)
        for step in PAIRING_STEPS:
            clock.go(seconds=2)
            if step == fail_at:
                await emit(kinds.COMMISSIONING_FAILED, name=step, **reason)
                return
            await emit(kinds.COMMISSIONING_STEP, name=step)
        clock.go(seconds=3)
        await emit(kinds.COMMISSIONING_COMPLETED, f"node:{node}", node_id=node)

    async def tv_plug_off(then: Any = None) -> None:
        await emit(
            kinds.HA_POWER_OFF,
            "entity:switch.media_plug",
            name="Media Plug",
            origin="unknown",
        )
        clock.go(seconds=70)
        await emit(kinds.THREAD_LEADER_LOST)
        clock.go(seconds=15)
        await emit(kinds.THREAD_FOREIGN_PARTITION)
        if then:
            await then()
        clock.go(seconds=20)
        await emit(kinds.THREAD_PARTITION_CHANGED)
        await emit(kinds.THREAD_LEADER_CHANGED)
        clock.go(seconds=40)
        await emit(
            kinds.BORDER_ROUTER_GONE,
            "br:0a1b2c3d4e5f6071",
            name="Living Room TV",
            vendor="Acme",
        )
        await tick(12)
        await emit(
            kinds.HA_POWER_ON,
            "entity:switch.media_plug",
            name="Media Plug",
            origin="person",
            by="Alex",
        )
        clock.go(minutes=2)
        await emit(
            kinds.BORDER_ROUTER_APPEARED,
            "br:0a1b2c3d4e5f6071",
            name="Living Room TV",
            vendor="Acme",
        )
        await tick(3)

    # Five days ago: a sensor joins without trouble.
    await pairing(12, None)
    await tick(3)

    # Three days ago: the TV's plug goes off for the first time.
    clock.go(days=2, hours=3)
    await tv_plug_off()

    # Yesterday evening: again, while someone tries to add a plug.
    clock.go(days=1, hours=5)
    await tv_plug_off(lambda: pairing(21, "Reconnect", reason="peer not reachable"))
    clock.go(minutes=25)
    await pairing(21, None)
    await tick(3)

    # All week: the coffee machine is plugged in only when it is used.
    clock.at = utcnow() - timedelta(days=4)
    for _ in range(3):
        await emit(kinds.MATTER_NODE_UNAVAILABLE, "node:30", name="Coffee Machine")
        clock.go(hours=20)
        await emit(kinds.MATTER_NODE_AVAILABLE, "node:30", name="Coffee Machine")
        clock.go(hours=4)
    clock.at = utcnow() - timedelta(hours=2)
    await emit(kinds.MATTER_NODE_UNAVAILABLE, "node:30", name="Coffee Machine")
    await tick(12)

    # A button without batteries; its owner knows.
    clock.at = utcnow() - timedelta(hours=5)
    await emit(kinds.MATTER_NODE_UNAVAILABLE, "node:31", name="Drawer Button")
    await tick(12)

    # This morning: a bathroom sensor stops answering and does not come back.
    clock.at = utcnow() - timedelta(hours=3)
    await emit(kinds.MATTER_NODE_UNAVAILABLE, "node:9", name="Bathroom Sensor")
    await tick(12)

    # The latest radio picture: the garden sensor barely hears anyone.
    clock.at = utcnow() - timedelta(minutes=20)
    await emit(
        kinds.THREAD_TOPOLOGY,
        devices=[
            {
                "subject": "node:7",
                "neighbour": "node:3",
                "role": "sleepy_end_device",
                "rssi": -93,
                "lqi": 1,
                "strength": "weak",
            },
            {
                "subject": "node:3",
                "neighbour": "br:1b2c3d4e5f607182",
                "role": "router",
                "rssi": -58,
                "lqi": 3,
                "strength": "strong",
            },
        ],
    )

    store = ctx.store
    await store.set_state(
        "otbr.node",
        {
            "role": "router",
            "partition_id": 1,
            "leader_router_id": 12,
            "router_count": 9,
            "network_name": "DemoNet",
        },
    )
    await store.set_state("border_routers", BORDER_ROUTERS)
    await store.set_state(
        "matter.nodes",
        {"total": 24, "unavailable": ["node:9", "node:30", "node:31"]},
    )
    found = await store.findings()
    button = next(f for f in found if f.subjects == ["node:31"] and not f.ended_at)
    await store.set_state("dismissed", {button.key: button.started_at.isoformat()})
    for source in ("home_assistant", "matter_server", "matter_server_log", "otbr"):
        await engine.set_status(source, True)
    await engine.set_status("otbr_log", True)


async def main(host: str, port: int) -> None:
    """Seed, then serve until interrupted."""
    folder = Path(tempfile.mkdtemp(prefix="matter-health-demo-"))
    clock = Clock(utcnow() - timedelta(days=5))
    store = Store(folder / "demo.db")
    ctx = Context(store=store, options=Options(data_dir=folder), now=clock)
    engine = Engine(ctx, sources=[], rules=list(RULES))
    for name in ("home_assistant", "matter_server", "matter_server_log", "otbr"):
        engine.status[name] = {"ok": None, "since": None, "detail": None}
    engine.status["otbr_log"] = {"ok": None, "since": None, "detail": None}
    await seed(engine, clock)
    ctx.now = utcnow
    runner = web.AppRunner(create_app(engine, trust_all=True))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    print(f"Demo on http://{host}:{port}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    arguments = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(arguments.host, arguments.port))
