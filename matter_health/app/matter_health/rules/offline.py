"""A Matter device that stays unreachable.

Devices drop out for a moment all the time - a router restarts, a sleepy
device misses a poll. Only one that stays away is worth the user's attention.
The finding says what happened around the moment it went away: a disturbed
mesh, or a switch turned off just before.

What is away is kept in the store, not only in memory, and compared with the
Matter Server's current list on every tick. A device that was unreachable
before the add-on started, or while it was restarting, is reported all the
same, and one that came back unseen is closed.

A device that comes and goes by habit (see :mod:`.habits`) is reported only
once it stays away clearly longer than usual; until then the overview lists
it as away, as expected.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity
from .common import cause_or_update, last_power_off, mesh_trouble, power_off_link
from .habits import LONG_ABSENCE, marked, pattern

#: Short drop-outs heal by themselves; after this long they do not.
UNREACHABLE_FOR = LONG_ABSENCE

#: A switch turned off this shortly before the device went away may have been
#: its power.
POWER_WINDOW = timedelta(minutes=2)

#: Mesh trouble this close to the moment counts as the likely reason.
MESH_WINDOW = timedelta(minutes=3)

#: A device's entry may be missing from the Matter Server's list this long
#: before it counts as back.
GRACE = timedelta(minutes=1)

#: Devices away, by subject, with when they went and whether that was reported.
AWAY = "offline.away"

#: The Matter Server's current picture, as the Matter Server source stores it.
NODES = "matter.nodes"


@RULES.register("offline")
class OfflineRule(Rule):
    """Reports devices that have been unreachable for a while."""

    name: ClassVar[str] = "offline"
    part_of: ClassVar[frozenset[str]] = frozenset({"mesh", "wave", "relay"})
    listens: ClassVar[frozenset[str]] = frozenset(
        {
            kinds.MATTER_NODE_UNAVAILABLE,
            kinds.MATTER_NODE_AVAILABLE,
            kinds.MATTER_NODE_REMOVED,
        }
    )

    def __init__(self, ctx: Context) -> None:
        """Read what is away from the store on first use."""
        super().__init__(ctx)
        self._state: dict[str, dict[str, Any]] | None = None

    async def on_event(self, event: Event) -> None:
        """Track a device going away or coming back."""
        subject = event.subject
        if subject is None:
            return
        away = await self._away()
        if event.kind == kinds.MATTER_NODE_UNAVAILABLE:
            away.setdefault(
                subject,
                {
                    "since": event.at.isoformat(),
                    "event": event.id,
                    "name": event.data.get("name"),
                    "reported": False,
                },
            )
        elif subject in away:
            await self._back(subject, away.pop(subject), event.at)
        await self._save(away)

    async def on_tick(self) -> None:
        """Catch up with the Matter Server, then report long absences."""
        now = self.ctx.now()
        away = await self._away()
        nodes = await self.ctx.store.get_state(NODES)
        if nodes is not None:
            unavailable = set(nodes.get("unavailable", []))
            for subject in unavailable - away.keys():
                away[subject] = {"since": now.isoformat(), "event": None}
            for subject in list(away.keys() - unavailable):
                # The list is written right after the event; give it a moment
                # before taking a missing entry as a return nobody saw.
                if now - datetime.fromisoformat(away[subject]["since"]) >= GRACE:
                    await self._back(subject, away.pop(subject), now)
        marks = await marked(self.ctx)
        # Publishing awaits, and a device may come or go meanwhile.
        for subject, entry in list(away.items()):
            gone_for = now - datetime.fromisoformat(entry["since"])
            if entry.get("reported") or gone_for < UNREACHABLE_FOR:
                continue
            usual = await pattern(self.ctx, subject, marks)
            expected = usual.expected_for
            if expected is not None and gone_for < expected:
                entry["expected"] = True
                continue
            entry.pop("expected", None)
            if expected is not None:
                entry["usual"] = int((usual.longest or timedelta()).total_seconds())
            entry["reported"] = True
            await self.ctx.publish(await self.describe(subject, entry))
        await self._save(away)

    async def _away(self) -> dict[str, dict[str, Any]]:
        # One dictionary for events and ticks alike: a copy per call would let
        # a tick that awaits in between write back an older picture.
        if self._state is None:
            self._state = dict(await self.ctx.store.get_state(AWAY) or {})
        return self._state

    async def _save(self, away: dict[str, dict[str, Any]]) -> None:
        await self.ctx.store.set_state(AWAY, away)

    async def _back(self, subject: str, entry: dict[str, Any], at: datetime) -> None:
        if entry.get("reported"):
            finding = await self.describe(subject, entry)
            finding.ended_at = at
            await self.ctx.publish(finding)

    async def describe(self, subject: str, entry: dict[str, Any]) -> Finding:
        """Build the finding for a device that stayed unreachable."""
        since = datetime.fromisoformat(entry["since"])
        device = self.ctx.names.get(subject) or entry.get("name")
        chain: list[Link] = []
        trouble = await mesh_trouble(self.ctx, since - MESH_WINDOW, since + MESH_WINDOW)
        power = await last_power_off(self.ctx, since, POWER_WINDOW)
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
        await cause_or_update(self.ctx, chain, since)
        chain.append(
            Link(
                Role.EFFECT,
                "link.device_unreachable",
                {"device": device},
                at=since,
                evidence=[entry["event"]] if entry.get("event") else [],
            )
        )
        usual = entry.get("usual")
        if usual:
            chain.append(
                Link(Role.EFFECT, "link.usually_back", {"duration": int(usual)})
            )
        chain.append(Link(Role.IMPACT, "link.device_unreachable_impact"))
        chain.append(Link(Role.FIX, "fix.device_unreachable", {"device": device}))
        return Finding(
            key=f"offline:{subject}:{entry['since']}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.device_away_long.title"
            if "usual" in entry
            else "finding.device_unreachable.title",
            params={"device": device},
            started_at=since,
            chain=chain,
            subjects=[subject],
        )
