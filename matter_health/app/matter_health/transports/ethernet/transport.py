"""Ethernet: devices wired to the home network.

A wired device has no parent to speak of and no signal to measure; the
switch it is plugged into is invisible to Matter. It hangs on the home
network directly.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .. import ROOT, TRANSPORTS, Transport


@TRANSPORTS.register("ethernet")
class EthernetTransport(Transport):
    """Wired devices, straight on the home network."""

    name: ClassVar[str] = "ethernet"
    feature: ClassVar[int] = 0b100

    async def picture(self, away: set[str]) -> list[dict[str, Any]]:
        """Every wired device on the home network."""
        del away
        mine: dict[str, str] = await self.ctx.store.get_state("matter.transports") or {}
        return [
            {
                "id": subject,
                "subject": subject,
                "kind": "device",
                "parent": ROOT,
                "link": {},
                "alternatives": 0,
                "vendor": None,
            }
            for subject, transport in sorted(mine.items())
            if transport == self.name
        ]

    async def summary(self, devices: int) -> dict[str, Any]:
        """Nothing stands between a wired device and the home network."""
        del devices
        return {"gateways": None, "connected": None}
