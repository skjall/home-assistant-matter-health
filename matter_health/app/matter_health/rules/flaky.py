"""A device that keeps dropping out for a moment.

Short drop-outs are not reported one by one: each heals within minutes. Many
of them in a day are a different matter. A device plugged in and out goes
away for hours at a time; one that loses its connection every hour for a few
minutes has a problem - usually its radio link, sometimes its firmware.
Commands sent in such a moment get lost, and its values lag behind.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity
from .common import cause_or_update
from .habits import pattern

#: The window in which drop-outs are counted.
WINDOW = timedelta(hours=24)

#: This many short drop-outs in the window make a device unreliable.
FLAKY_AFTER = 4

#: Devices currently reported, by subject, with the first counted drop-out.
OPEN = "flaky.open"


@RULES.register("flaky")
class FlakyRule(Rule):
    """Reports devices that lose their connection again and again."""

    name: ClassVar[str] = "flaky"
    part_of: ClassVar[frozenset[str]] = frozenset(
        {"partitions", "interference", "radio"}
    )
    listens: ClassVar[frozenset[str]] = frozenset({kinds.MATTER_NODE_AVAILABLE})

    def __init__(self, ctx: Context) -> None:
        """Read the open findings from the store on first use."""
        super().__init__(ctx)
        self._state: dict[str, dict[str, Any]] | None = None

    async def _open(self) -> dict[str, dict[str, Any]]:
        if self._state is None:
            self._state = dict(await self.ctx.store.get_state(OPEN) or {})
        return self._state

    async def on_event(self, event: Event) -> None:
        """Count the drop-outs of a device that just came back."""
        subject = event.subject
        if subject is None:
            return
        since = event.at - WINDOW
        usual = await pattern(self.ctx, subject)
        drops = usual.short_drops(since)
        opened = await self._open()
        if drops < FLAKY_AFTER and subject not in opened:
            return
        first = min((a for a, b in usual.absences if a >= since), default=event.at)
        entry = opened.setdefault(
            subject, {"since": first.isoformat(), "name": event.data.get("name")}
        )
        entry["drops"] = max(int(entry.get("drops", 0)), usual.drops(since))
        entry["last"] = event.at.isoformat()
        await self.ctx.store.set_state(OPEN, opened)
        await self.ctx.publish(await self.describe(subject, entry, ended=None))

    async def on_tick(self) -> None:
        """Close findings of devices that stayed connected for a day."""
        now = self.ctx.now()
        opened = await self._open()
        for subject, entry in list(opened.items()):
            last = datetime.fromisoformat(entry["last"])
            if now - last >= WINDOW:
                opened.pop(subject)
                await self.ctx.publish(await self.describe(subject, entry, ended=now))
        await self.ctx.store.set_state(OPEN, opened)

    async def describe(
        self, subject: str, entry: dict[str, Any], ended: datetime | None
    ) -> Finding:
        """Build the finding for a device that keeps dropping out."""
        since = datetime.fromisoformat(entry["since"])
        device = self.ctx.names.get(subject) or entry.get("name")
        chain: list[Link] = []
        weak = await self.ctx.store.finding(f"signal:{subject}")
        if weak is not None and weak.ended_at is None:
            chain.append(
                Link(
                    Role.CAUSE,
                    "link.weak_signal_cause",
                    {"device": device},
                    at=weak.started_at,
                    confidence=Confidence.LIKELY,
                )
            )
        await cause_or_update(self.ctx, chain, since)
        chain.append(
            Link(
                Role.EFFECT,
                "link.device_flaky",
                {"device": device, "count": int(entry.get("drops", 0))},
                at=since,
            )
        )
        chain.append(Link(Role.IMPACT, "link.device_flaky_impact"))
        chain.append(Link(Role.FIX, "fix.device_flaky", {"device": device}))
        return Finding(
            key=f"flaky:{subject}:{entry['since']}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.device_flaky.title",
            params={"device": device},
            started_at=since,
            ended_at=ended,
            chain=chain,
            subjects=[subject],
        )
