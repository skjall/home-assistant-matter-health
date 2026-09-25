"""Something else sends on the Thread channel, measured by the devices themselves.

Before sending, a Thread radio listens; when it hears the channel busy it
waits and tries again, and after a few tries gives up. Devices that stay
awake count those give-ups. Read every ten minutes, the counts tell where the
channel is crowded:

- nearly every device alike: the interference reaches the whole home - a
  Wi-Fi network on an overlapping channel, often a neighbour's;
- a few devices only: something near them - an access point, a microwave
  oven, a Zigbee coordinator.

The radio cannot tell who else is sending. What the add-on does know are
its own access points' Wi-Fi channels, from the Matter devices on them; one
on a channel that overlaps or borders the Thread channel is named as a
possible cause, with a channel further away to try.

Home Assistant's own border router reports the same in its log (see
:mod:`.radio`); the devices add where it happens. A disturbed channel
explains devices dropping out, which are told as part of this story.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any, ClassVar

from ... import enrichers, kinds
from ...engine import RULES, Context, Rule
from ...model import Confidence, Event, Finding, Link, Role, Severity
from .. import ACCESS_POINTS

#: A device that finds the channel busy about once a minute or more.
DISTURBED_PER_HOUR = 60

#: At least this many devices must have been measured to tell anything.
MEASURED_AT_LEAST = 3

#: At least this share of the measured devices disturbed: the whole home.
WIDE_SHARE = 0.5

#: A finding opens after this many readings in a row show it ...
OPEN_AFTER = 2
#: ... and closes after this many readings without it.
CLOSE_AFTER = 2

#: Named devices in a local finding, the rest are counted.
NAMED = 4

#: How far a Wi-Fi channel's centre may be from the Thread channel's, in MHz,
#: to share it: within the 22 MHz a Wi-Fi channel fills it overlaps; within
#: its first side lobes (about -28 dB at 20 MHz off) a strong access point
#: nearby is still heard.
OVERLAPS_WITHIN = 11
BORDERS_WITHIN = 20

#: The Wi-Fi channels that do not overlap each other, to suggest one from.
SEPARATE_WIFI_CHANNELS = (1, 6, 11)

STATE = "interference.state"


def thread_mhz(channel: int) -> int:
    """Centre frequency of a Thread channel (11-26)."""
    return 2405 + 5 * (channel - 11)


def wifi_mhz(channel: int) -> int | None:
    """Centre frequency of a 2.4 GHz Wi-Fi channel; None for other bands."""
    if channel == 14:
        return 2484
    return 2407 + 5 * channel if 1 <= channel <= 13 else None


def shared(thread: int, wifi: int) -> str | None:
    """Whether a Wi-Fi channel ``overlaps`` or ``borders`` a Thread channel."""
    mhz = wifi_mhz(wifi)
    if mhz is None:
        return None
    apart = abs(mhz - thread_mhz(thread))
    if apart <= OVERLAPS_WITHIN:
        return "overlaps"
    return "borders" if apart <= BORDERS_WITHIN else None


def further(thread: int) -> int:
    """Return the non-overlapping Wi-Fi channel furthest from a Thread channel."""
    return max(
        SEPARATE_WIFI_CHANNELS,
        key=lambda c: abs((wifi_mhz(c) or 0) - thread_mhz(thread)),
    )


@RULES.register("interference")
class InterferenceRule(Rule):
    """Reports a crowded Thread channel, everywhere or around some devices."""

    name: ClassVar[str] = "interference"
    story_margin: ClassVar[tuple[timedelta, timedelta] | None] = (
        timedelta(minutes=10),
        timedelta(minutes=10),
    )
    listens: ClassVar[frozenset[str]] = frozenset({kinds.THREAD_INTERFERENCE})

    def __init__(self, ctx: Context) -> None:
        """Read what is open from the store on first use."""
        super().__init__(ctx)
        self._state: dict[str, Any] | None = None

    async def _load(self) -> dict[str, Any]:
        if self._state is None:
            self._state = dict(await self.ctx.store.get_state(STATE) or {})
        return self._state

    async def on_event(self, event: Event) -> None:
        """Judge one reading of every device's counters."""
        state = await self._load()
        measured = [
            d
            for d in event.data.get("devices") or []
            if isinstance(d.get("cca_per_hour"), int)
        ]
        disturbed = [d for d in measured if d["cca_per_hour"] >= DISTURBED_PER_HOUR]
        if len(measured) < MEASURED_AT_LEAST or not disturbed:
            kind = None
        elif len(disturbed) >= WIDE_SHARE * len(measured):
            kind = "wide"
        else:
            kind = "local"
        open_ = state.get("open")
        if kind is not None:
            state["quiet"] = 0
            state["streak"] = (
                state.get("streak", 0) + 1 if state.get("kind") == kind else 1
            )
            state["kind"] = kind
            if open_ is None and state["streak"] >= OPEN_AFTER:
                open_ = state["open"] = {"since": event.at.isoformat(), "kind": kind}
            if open_ is not None:
                open_.update(
                    kind=kind,
                    disturbed=[d["subject"] for d in disturbed],
                    measured=len(measured),
                    rate=int(median(d["cca_per_hour"] for d in disturbed)),
                )
                open_.setdefault("evidence", []).append(event.id)
                open_["access_points"] = await self._access_points()
                await self.ctx.publish(self.describe(open_))
        else:
            state["streak"] = 0
            state["kind"] = None
            if open_ is not None:
                state["quiet"] = state.get("quiet", 0) + 1
                if state["quiet"] >= CLOSE_AFTER:
                    finding = self.describe(open_)
                    finding.ended_at = event.at
                    await self.ctx.publish(finding)
                    state.pop("open")
        await self.ctx.store.set_state(STATE, state)

    async def _access_points(self) -> list[dict[str, Any]]:
        """Find the access points whose Wi-Fi channel shares the Thread channel."""
        thread = await self.ctx.store.get_state("thread.channel")
        points = await self.ctx.store.get_state(ACCESS_POINTS) or {}
        if not isinstance(thread, int) or not points:
            return []
        network = await enrichers.known(self.ctx)
        found = []
        for bssid, point in sorted(points.items()):
            channel = point.get("channel")
            how = shared(thread, channel) if isinstance(channel, int) else None
            if how is None:
                continue
            found.append(
                {
                    "access_point": network.access_point(point.get("clients") or [])
                    or bssid,
                    "how": how,
                    "wifi_channel": channel,
                    "thread_channel": thread,
                    "suggest": further(thread),
                }
            )
        # An overlapping channel first: it is the likelier one.
        return sorted(found, key=lambda p: p["how"] != "overlaps")

    def describe(self, open_: dict[str, Any]) -> Finding:
        """Build the finding for a crowded channel, wide or local."""
        since = datetime.fromisoformat(open_["since"])
        kind = open_["kind"]
        subjects = list(open_.get("disturbed", []))
        names = [self.ctx.names.get(s) or s for s in subjects]
        more = len(names) - NAMED
        shown = ", ".join(names[:NAMED]) + (f" (+{more})" if more > 0 else "")
        params = {
            "count": len(subjects),
            "total": open_.get("measured", 0),
            "rest": open_.get("measured", 0) - len(subjects),
            "rate": open_.get("rate", 0),
            "devices": shown,
        }
        points = list(open_.get("access_points") or [])
        return Finding(
            key=f"interference:{open_['since']}",
            rule=self.name,
            severity=Severity.WARNING,
            title=f"finding.interference_{kind}.title",
            params=params,
            started_at=since,
            chain=[
                *(
                    Link(
                        Role.CAUSE,
                        f"link.interference_access_point_{p['how']}",
                        p,
                        confidence=Confidence.POSSIBLE,
                    )
                    for p in points
                ),
                Link(
                    Role.CAUSE,
                    f"link.interference_{kind}_cause",
                    confidence=Confidence.POSSIBLE,
                ),
                Link(
                    Role.EFFECT,
                    f"link.interference_{kind}",
                    params,
                    at=since,
                    evidence=[i for i in open_.get("evidence", []) if i][-20:],
                ),
                Link(Role.IMPACT, "link.channel_busy_impact"),
                *(Link(Role.FIX, "fix.interference_access_point", p) for p in points),
                Link(
                    Role.FIX,
                    "fix.channel_busy" if kind == "wide" else "fix.interference_local",
                    params,
                ),
            ],
            subjects=subjects if kind == "local" else [],
        )
