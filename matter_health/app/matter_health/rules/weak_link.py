"""Devices with a weak link to their way into the network, whatever the transport.

A device is only as well connected as its link to its parent - a Thread
router, a Wi-Fi access point. With a weak one, messages need several tries,
arrive late or not at all. What counts as weak and what helps is the
transport's to say; opening, updating and closing a finding per device is the
same for all of them, and lives here.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from ..engine import Context, Rule
from ..model import Event, Finding, Link, Role, Severity


class WeakLinkRule(Rule):
    """Opens a finding per weakly linked device and closes it when better.

    The events it listens to carry ``devices``: one entry per device with its
    ``subject``, the ``neighbour`` it hangs on and what was measured.
    """

    #: What happened and the advice, in the transport's words. Both get
    #: ``device``, ``neighbour`` and ``rssi``.
    effect: ClassVar[str] = "link.weak_signal"
    fix: ClassVar[str]

    def __init__(self, ctx: Context) -> None:
        """Nothing is known to be weak at start."""
        super().__init__(ctx)
        self.open: dict[str, Finding] = {}

    @abstractmethod
    def is_weak(self, link: dict[str, Any]) -> bool:
        """Whether a device's link is too weak to rely on."""

    def impacts(self, link: dict[str, Any]) -> list[Link]:
        """Return what a weak link means beyond late messages, for this device."""
        del link
        return []

    async def on_event(self, event: Event) -> None:
        """Compare this snapshot of the links with the last one."""
        weak_now: set[str] = set()
        for link in event.data.get("devices", []):
            subject = link.get("subject")
            if not isinstance(subject, str) or not subject.startswith("node:"):
                continue
            if not self.is_weak(link):
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
        """Build the finding for one weakly linked device."""
        subject = str(link["subject"])
        params = {
            "device": self.ctx.names.get(subject),
            "neighbour": self.ctx.names.get(link.get("neighbour")),
            "rssi": link.get("rssi"),
            "lqi": link.get("lqi"),
        }
        chain = [
            Link(
                Role.EFFECT,
                self.effect,
                params,
                at=event.at,
                evidence=[event.id] if event.id else [],
            ),
            Link(Role.IMPACT, "link.weak_signal_impact"),
            *self.impacts(link),
            Link(Role.FIX, self.fix, params),
        ]
        # One key per device: a device uses one transport, and other rules
        # ask whether its link is weak without caring which.
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
