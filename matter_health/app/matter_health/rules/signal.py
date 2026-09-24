"""Devices that hear their nearest neighbour only faintly.

A Thread device is only as well connected as its best link. With a weak one,
messages need several tries, arrive late or not at all, and a battery device
drains faster because it repeats itself. The fix is almost always physical:
another mains-powered Thread device in between, or the device a little
closer.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Event, Finding, Link, Role, Severity

#: Below this a link is unreliable in practice; Thread radios stop decoding
#: around -100 dBm and need a margin for interference.
WEAK_RSSI = -85

#: Link quality 0-3 as Thread reports it; 1 means most frames need repeats.
WEAK_LQI = 1

#: Battery devices that only wake up now and then.
SLEEPY_ROLES = frozenset({"sleepy_end_device", "end_device"})


def is_weak(link: dict[str, Any]) -> bool:
    """Whether a device's best link is too weak to rely on."""
    if link.get("strength") == "weak":
        return True
    rssi, lqi = link.get("rssi"), link.get("lqi")
    return (rssi is not None and rssi <= WEAK_RSSI) or (
        lqi is not None and lqi <= WEAK_LQI
    )


@RULES.register("signal")
class SignalRule(Rule):
    """Opens a finding per weakly connected device and closes it when better."""

    name: ClassVar[str] = "signal"
    listens: ClassVar[frozenset[str]] = frozenset({kinds.THREAD_TOPOLOGY})

    def __init__(self, ctx: Context) -> None:
        """Nothing is known to be weak at start."""
        super().__init__(ctx)
        self.open: dict[str, Finding] = {}

    async def on_event(self, event: Event) -> None:
        """Compare this snapshot of the radio links with the last one."""
        weak_now: set[str] = set()
        for link in event.data.get("devices", []):
            subject = link.get("subject")
            if not isinstance(subject, str) or not subject.startswith("node:"):
                continue
            if not is_weak(link):
                continue
            weak_now.add(subject)
            finding = self.describe(event, link)
            if subject in self.open:
                finding.started_at = self.open[subject].started_at
            self.open[subject] = finding
            await self.ctx.publish(finding)
        for subject in list(self.open.keys() - weak_now):
            finding = self.open.pop(subject)
            finding.ended_at = event.at
            await self.ctx.publish(finding)

    def describe(self, event: Event, link: dict[str, Any]) -> Finding:
        """Build the finding for one weakly connected device."""
        subject = str(link["subject"])
        device = self.ctx.names.get(subject)
        neighbour = self.ctx.names.get(link.get("neighbour"))
        params = {
            "device": device,
            "neighbour": neighbour,
            "rssi": link.get("rssi"),
            "lqi": link.get("lqi"),
        }
        chain = [
            Link(
                Role.EFFECT,
                "link.weak_signal",
                params,
                at=event.at,
                evidence=[event.id] if event.id else [],
            ),
            Link(Role.IMPACT, "link.weak_signal_impact"),
            Link(Role.FIX, "fix.weak_signal", params),
        ]
        if link.get("role") in SLEEPY_ROLES:
            chain.insert(2, Link(Role.IMPACT, "link.weak_signal_battery"))
        return Finding(
            key=f"signal:{subject}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.weak_signal.title",
            params=params,
            started_at=event.at,
            chain=chain,
            subjects=[subject],
        )
