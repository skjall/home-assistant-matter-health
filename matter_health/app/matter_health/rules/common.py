"""Look-ups several rules need: what happened just before, and to what."""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import kinds
from ..engine import Context
from ..model import Confidence, Event, Link, Role

#: A switch turned off this long before something vanished is a candidate
#: cause. Devices notice a lost neighbour within about a minute; the Matter
#: Server polls border routers once a minute on top.
POWER_CAUSE_WINDOW = timedelta(minutes=3)

#: Events that mean the mesh itself was in trouble.
MESH_TROUBLE = (
    kinds.THREAD_LEADER_LOST,
    kinds.THREAD_FOREIGN_PARTITION,
    kinds.THREAD_PARTITION_CHANGED,
    kinds.THREAD_LEADER_CHANGED,
)


async def last_power_off(
    ctx: Context, before: datetime, window: timedelta = POWER_CAUSE_WINDOW
) -> Event | None:
    """Return the switch turned off last within ``window`` before ``before``."""
    events = await ctx.store.events(
        (kinds.HA_POWER_OFF,), since=before - window, until=before
    )
    return events[-1] if events else None


async def last_border_router_gone(
    ctx: Context, since: datetime, until: datetime
) -> Event | None:
    """Return the last border router of our network that disappeared."""
    events = await ctx.store.events(
        (kinds.BORDER_ROUTER_GONE,), since=since, until=until
    )
    ours = [event for event in events if event.data.get("own") is not False]
    return ours[-1] if ours else None


async def mesh_trouble(ctx: Context, since: datetime, until: datetime) -> list[Event]:
    """Signs of a disturbed mesh in the given time."""
    return await ctx.store.events(MESH_TROUBLE, since=since, until=until)


def power_off_link(event: Event, confidence: Confidence) -> Link:
    """Build the chain link "<switch> was switched off (by ...)"."""
    return Link(
        role=Role.CAUSE,
        key="link.power_off",
        params={
            "switch": event.data.get("name") or event.subject,
            "origin": event.data.get("origin", "unknown"),
            "by": event.data.get("by"),
        },
        at=event.at,
        confidence=confidence,
        evidence=[event.id] if event.id else [],
    )


def seconds(start: datetime, end: datetime) -> int:
    """Whole seconds between two times, never negative."""
    return max(0, int((end - start).total_seconds()))
