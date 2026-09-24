"""What is normal for a device: how often and how long it is usually away.

Some devices are meant to come and go. A kitchen appliance is plugged in only
when it is used; a button waits in a drawer. Reporting each of their absences
teaches the user to ignore the page. Hiding them for good would miss the day
one of them really breaks.

So a device's own history decides. One that went away and came back by itself
several times lately is expected to do so again, and is reported only once it
stays away clearly longer than it ever did. The user can say the same for a
device without history, or say the opposite for one the history misjudges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .. import kinds
from ..engine import Context

#: How far back a device's habits are read.
HISTORY = timedelta(days=14)

#: A device away this long did not just miss a poll.
LONG_ABSENCE = timedelta(minutes=10)

#: Returns after a long absence that make a device one that comes and goes.
COMES_AND_GOES_AFTER = 3

#: A device that comes and goes is reported once it stays away this many
#: times longer than its longest absence so far ...
LONGER_THAN_USUAL = 2

#: ... but not before this, so a device plugged in every morning is not
#: reported every evening, ...
EXPECTED_AT_LEAST = timedelta(hours=12)

#: ... and not later than this.
EXPECTED_AT_MOST = timedelta(days=14)

#: What the user said about devices: ``{subject: true}`` for "comes and goes",
#: ``{subject: false}`` for "always report", whatever the history says.
HABITS = "devices.habits"

#: Events that end an absence.
RETURNS = (kinds.MATTER_NODE_AVAILABLE, kinds.MATTER_NODE_REMOVED)


@dataclass
class Pattern:
    """A device's recent absences and what the user said about it."""

    absences: list[tuple[datetime, datetime]] = field(default_factory=list)
    marked: bool | None = None

    @property
    def long_absences(self) -> list[tuple[datetime, datetime]]:
        """Absences long enough that the device was really gone."""
        return [(a, b) for a, b in self.absences if b - a >= LONG_ABSENCE]

    @property
    def longest(self) -> timedelta | None:
        """The longest absence the device came back from."""
        spans = [b - a for a, b in self.absences]
        return max(spans) if spans else None

    @property
    def comes_and_goes(self) -> bool:
        """Whether being away is normal for this device."""
        if self.marked is not None:
            return self.marked
        return len(self.long_absences) >= COMES_AND_GOES_AFTER

    @property
    def expected_for(self) -> timedelta | None:
        """How long an absence is unremarkable; None if every absence counts."""
        if not self.comes_and_goes:
            return None
        usual = (self.longest or timedelta()) * LONGER_THAN_USUAL
        return min(max(usual, EXPECTED_AT_LEAST), EXPECTED_AT_MOST)

    def short_drops(self, since: datetime) -> int:
        """Absences since ``since`` too short to have been reported."""
        return sum(1 for a, b in self.absences if a >= since and b - a < LONG_ABSENCE)

    def drops(self, since: datetime) -> int:
        """All absences that began since ``since``."""
        return sum(1 for a, _ in self.absences if a >= since)


def absences(events: list[Any]) -> list[tuple[datetime, datetime]]:
    """Pair each going away with the return that ended it, oldest first."""
    spans: list[tuple[datetime, datetime]] = []
    gone: datetime | None = None
    for event in events:
        if event.kind == kinds.MATTER_NODE_UNAVAILABLE:
            gone = gone or event.at
        elif event.kind in RETURNS and gone is not None:
            spans.append((gone, event.at))
            gone = None
    return spans


async def marked(ctx: Context) -> dict[str, bool]:
    """Return what the user said about devices."""
    raw = await ctx.store.get_state(HABITS) or {}
    return {str(k): bool(v) for k, v in raw.items()}


async def pattern(
    ctx: Context, subject: str, marks: dict[str, bool] | None = None
) -> Pattern:
    """Read a device's recent absences from the history."""
    events = await ctx.store.events(
        (kinds.MATTER_NODE_UNAVAILABLE, *RETURNS),
        since=ctx.now() - HISTORY,
        subject=subject,
    )
    if marks is None:
        marks = await marked(ctx)
    return Pattern(absences=absences(events), marked=marks.get(subject))
