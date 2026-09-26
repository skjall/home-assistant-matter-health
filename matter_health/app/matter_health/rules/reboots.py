"""A device that keeps restarting.

A restart now and then is normal: a battery changed, an update installed.
Many in a few days are not. Most often the battery cannot hold its voltage
when the device transmits - an old one, or a rechargeable one, whose cells
give less than a fresh battery - and the device resets. Sometimes it is the
firmware. After every restart the device has to join the network anew;
what happens meanwhile does not reach Home Assistant, and the restarts
drain the battery faster still.

Restarts are counted over the last days from the device's own count, so a
device that restarted hundreds of times long ago is not reported for it.
Once it has not restarted for two days, the finding ends.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from .. import batteries, kinds
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity

#: The window in which restarts are counted.
WINDOW = timedelta(days=7)

#: This many restarts in the window make a device worth a look.
REBOOTING_AFTER = 10

#: A device that has not restarted for this long has settled.
SETTLED_AFTER = timedelta(days=2)

#: Reasons that name the cause themselves.
TOLD = {
    "brown_out": "link.reboots_brown_out",
    "software_watchdog": "link.reboots_watchdog",
    "hardware_watchdog": "link.reboots_watchdog",
}

#: Devices currently reported, by subject.
OPEN = "reboots.open"


@RULES.register("reboots")
class RebootRule(Rule):
    """Reports devices that restart again and again."""

    name: ClassVar[str] = "reboots"
    listens: ClassVar[frozenset[str]] = frozenset({kinds.MATTER_NODE_REBOOTED})

    def __init__(self, ctx: Context) -> None:
        """Read the open findings from the store on first use."""
        super().__init__(ctx)
        self._state: dict[str, dict[str, Any]] | None = None

    async def _open(self) -> dict[str, dict[str, Any]]:
        if self._state is None:
            self._state = dict(await self.ctx.store.get_state(OPEN) or {})
        return self._state

    async def on_event(self, event: Event) -> None:
        """Count a device's restarts of the last days."""
        subject = event.subject
        if subject is None:
            return
        restarts = [
            e
            for e in await self.ctx.store.events(
                (kinds.MATTER_NODE_REBOOTED,), since=event.at - WINDOW, until=event.at
            )
            if e.subject == subject
        ]
        count = sum(int(e.data.get("count") or 0) for e in restarts)
        opened = await self._open()
        if count < REBOOTING_AFTER and subject not in opened:
            return
        entry = opened.setdefault(subject, {"since": restarts[0].at.isoformat()})
        entry.update(
            count=max(count, int(entry.get("count", 0))),
            last=event.at.isoformat(),
            reason=event.data.get("reason"),
            name=event.data.get("name") or entry.get("name"),
        )
        await self.ctx.store.set_state(OPEN, opened)
        await self.ctx.publish(await self.describe(subject, entry, ended=None))

    async def on_tick(self) -> None:
        """Close findings of devices that stopped restarting."""
        now = self.ctx.now()
        opened = await self._open()
        for subject, entry in list(opened.items()):
            if now - datetime.fromisoformat(entry["last"]) >= SETTLED_AFTER:
                opened.pop(subject)
                await self.ctx.publish(await self.describe(subject, entry, ended=now))
                await self.ctx.store.set_state(OPEN, opened)

    async def describe(
        self, subject: str, entry: dict[str, Any], ended: datetime | None
    ) -> Finding:
        """Build the finding: how often, the likely cause, what helps."""
        since = datetime.fromisoformat(entry["since"])
        device = self.ctx.names.get(subject) or entry.get("name") or subject
        known = (await self.ctx.store.get_state(batteries.BATTERIES) or {}).get(
            subject
        ) or {}
        params: dict[str, Any] = {
            "device": device,
            "count": int(entry["count"]),
            "days": WINDOW.days,
        }
        chain: list[Link] = []
        told = TOLD.get(str(entry.get("reason")))
        if told:
            chain.append(Link(Role.CAUSE, told, params, confidence=Confidence.CERTAIN))
        elif known.get("weak"):
            chain.append(
                Link(
                    Role.CAUSE,
                    "link.reboots_weak_battery",
                    {**params, "volts": round(known["cell_millivolts"] / 1000, 2)},
                    confidence=Confidence.LIKELY,
                )
            )
        else:
            chain.append(
                Link(Role.CAUSE, "link.reboots_cause", confidence=Confidence.POSSIBLE)
            )
        chain.append(Link(Role.EFFECT, "link.reboots", params, at=since))
        chain.append(Link(Role.IMPACT, "link.reboots_impact"))
        chain.append(
            Link(
                Role.FIX,
                "fix.reboots_firmware"
                if told == "link.reboots_watchdog"
                else "fix.reboots",
                params,
            )
        )
        return Finding(
            key=f"reboots:{subject}:{entry['since']}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.reboots.title",
            params={"device": device},
            started_at=since,
            ended_at=ended,
            chain=chain,
            subjects=[subject],
        )
