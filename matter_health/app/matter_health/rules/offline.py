"""A Matter device that stays unreachable.

Devices drop out for a moment all the time - a router restarts, a sleepy
device misses a poll. Only one that stays away is worth the user's attention.
The finding says what happened around the moment it went away: a disturbed
mesh, or a switch turned off just before.
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity
from .common import last_power_off, mesh_trouble, power_off_link

#: Short drop-outs heal by themselves; after this long they do not.
UNREACHABLE_FOR = timedelta(minutes=10)

#: A switch turned off this shortly before the device went away may have been
#: its power.
POWER_WINDOW = timedelta(minutes=2)

#: Mesh trouble this close to the moment counts as the likely reason.
MESH_WINDOW = timedelta(minutes=3)


@RULES.register("offline")
class OfflineRule(Rule):
    """Reports devices that have been unreachable for a while."""

    name: ClassVar[str] = "offline"
    part_of: ClassVar[frozenset[str]] = frozenset({"mesh"})
    listens: ClassVar[frozenset[str]] = frozenset(
        {
            kinds.MATTER_NODE_UNAVAILABLE,
            kinds.MATTER_NODE_AVAILABLE,
            kinds.MATTER_NODE_REMOVED,
        }
    )

    def __init__(self, ctx: Context) -> None:
        """Every device is assumed reachable at start."""
        super().__init__(ctx)
        self.away: dict[str, Event] = {}
        self.reported: dict[str, Finding] = {}

    async def on_event(self, event: Event) -> None:
        """Track a device going away or coming back."""
        subject = event.subject
        if subject is None:
            return
        if event.kind == kinds.MATTER_NODE_UNAVAILABLE:
            self.away.setdefault(subject, event)
            return
        self.away.pop(subject, None)
        finding = self.reported.pop(subject, None)
        if finding:
            finding.ended_at = event.at
            await self.ctx.publish(finding)

    async def on_tick(self) -> None:
        """Report devices that have been away long enough."""
        now = self.ctx.now()
        # Publishing awaits, and a device may come or go meanwhile.
        for subject, event in list(self.away.items()):
            if subject not in self.reported and now - event.at >= UNREACHABLE_FOR:
                finding = await self.describe(event)
                self.reported[subject] = finding
                await self.ctx.publish(finding)

    async def describe(self, event: Event) -> Finding:
        """Build the finding for a device that stayed unreachable."""
        subject = str(event.subject)
        device = self.ctx.names.get(subject) or event.data.get("name")
        chain: list[Link] = []
        trouble = await mesh_trouble(
            self.ctx, event.at - MESH_WINDOW, event.at + MESH_WINDOW
        )
        power = await last_power_off(self.ctx, event.at, POWER_WINDOW)
        if trouble:
            chain.append(
                Link(
                    Role.CAUSE,
                    "link.mesh_disturbed",
                    at=trouble[0].at,
                    confidence=Confidence.LIKELY,
                    evidence=[item.id for item in trouble if item.id],
                )
            )
        if power:
            chain.append(power_off_link(power, Confidence.POSSIBLE))
        if not chain:
            chain.append(Link(Role.CAUSE, "link.cause_unknown"))
        chain.append(
            Link(
                Role.EFFECT,
                "link.device_unreachable",
                {"device": device},
                at=event.at,
                evidence=[event.id] if event.id else [],
            )
        )
        chain.append(Link(Role.IMPACT, "link.device_unreachable_impact"))
        chain.append(Link(Role.FIX, "fix.device_unreachable", {"device": device}))
        return Finding(
            key=f"offline:{subject}:{event.at.isoformat()}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.device_unreachable.title",
            params={"device": device},
            started_at=event.at,
            chain=chain,
            subjects=[subject],
        )
