"""The Thread mesh lost its leader or fell apart for a while.

Every Thread network has one leader. When it disappears, the remaining routers
notice only after the leader timeout, elect a new one, and until then may form
separate partitions that cannot reach each other. Devices in another partition
than the border router are unreachable, and adding a device fails.

The most common reason in a home is mundane: the device that led the network
lost power. So the rule looks back for a border router that vanished and a
switch that was turned off just before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar

from ... import kinds
from ...engine import RULES, Rule
from ...model import Confidence, Event, Finding, Link, Role, Severity
from ...rules.common import (
    POWER_CAUSE_WINDOW,
    cause_or_update,
    last_border_router_gone,
    last_power_off,
    power_off_link,
    seconds,
)

#: Mesh events closer together than this belong to one episode.
EPISODE_GAP = timedelta(minutes=3)

#: How late a vanished border router may be reported after the fact.
REPORTING_DELAY = timedelta(minutes=5)

#: An episode is over when the mesh has been quiet this long.
SETTLED_AFTER = timedelta(minutes=2)


@dataclass
class Episode:
    """One disturbance, from its first to its last sign."""

    start: datetime
    last: datetime
    kinds: set[str] = field(default_factory=set)
    evidence: list[int] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable identity of the finding for this episode."""
        return f"mesh:{self.start.isoformat()}"

    @property
    def split(self) -> bool:
        """Whether routers of another partition were seen, not just a new leader."""
        return bool(
            self.kinds
            & {kinds.THREAD_FOREIGN_PARTITION, kinds.THREAD_PARTITION_CHANGED}
        )


@RULES.register("mesh")
class MeshRule(Rule):
    """Tells the story of a lost leader or split mesh, with its likely cause."""

    name: ClassVar[str] = "mesh"
    #: The mesh falling apart at the start of a split that lasts.
    part_of: ClassVar[frozenset[str]] = frozenset({"partitions"})
    #: A switch turned off a little before the split, and a vanished border
    #: router reported a little after it, still belong to this story.
    story_margin: ClassVar[tuple[timedelta, timedelta] | None] = (
        POWER_CAUSE_WINDOW,
        REPORTING_DELAY,
    )
    listens: ClassVar[frozenset[str]] = frozenset(
        {
            kinds.THREAD_LEADER_LOST,
            kinds.THREAD_FOREIGN_PARTITION,
            kinds.THREAD_PARTITION_CHANGED,
            kinds.THREAD_LEADER_CHANGED,
        }
    )

    episode: Episode | None = None

    async def on_event(self, event: Event) -> None:
        """Open or extend the current episode and retell it."""
        episode = self.episode
        if episode is None or event.at - episode.last > EPISODE_GAP:
            episode = self.episode = Episode(start=event.at, last=event.at)
        episode.last = max(episode.last, event.at)
        episode.kinds.add(event.kind)
        if event.id:
            episode.evidence.append(event.id)
        await self.ctx.publish(await self.describe(episode, ended=False))

    async def on_tick(self) -> None:
        """Close the episode once the mesh has been quiet for a while."""
        episode = self.episode
        if episode and self.ctx.now() - episode.last >= SETTLED_AFTER:
            await self.ctx.publish(await self.describe(episode, ended=True))
            self.episode = None

    async def describe(self, episode: Episode, ended: bool) -> Finding:
        """Build the finding for an episode: cause, effect, impact and fix."""
        ctx = self.ctx
        # A vanished border router is reported up to two polls late, often
        # after the mesh already settled; look a little past the episode.
        router = await last_border_router_gone(
            ctx,
            episode.start - POWER_CAUSE_WINDOW,
            min(ctx.now(), episode.last + REPORTING_DELAY),
        )
        # The leader is missed only after the leader timeout, so the switch
        # that cut its power was turned off shortly before the episode began.
        power = await last_power_off(ctx, episode.start)
        chain: list[Link] = []
        if power:
            chain.append(
                power_off_link(
                    power, Confidence.LIKELY if router else Confidence.POSSIBLE
                )
            )
        if router:
            chain.append(
                Link(
                    Role.CAUSE,
                    "link.border_router_gone",
                    {"border_router": router.data.get("name")},
                    at=router.at,
                    evidence=[router.id] if router.id else [],
                )
            )
            chain.append(
                Link(
                    Role.CAUSE,
                    "link.was_probably_leader",
                    {"border_router": router.data.get("name")},
                    confidence=Confidence.LIKELY,
                )
            )
        await cause_or_update(ctx, chain, episode.start)

        duration = seconds(episode.start, episode.last)
        chain.append(
            Link(
                Role.EFFECT,
                "link.mesh_split" if episode.split else "link.new_leader",
                {"duration": duration},
                at=episode.start,
                evidence=list(episode.evidence),
            )
        )
        chain.append(Link(Role.IMPACT, "link.mesh_trouble_impact"))
        failed = await ctx.store.events(
            (kinds.COMMISSIONING_FAILED,),
            since=episode.start - timedelta(minutes=1),
            until=episode.last + timedelta(minutes=2),
        )
        if failed:
            chain.append(Link(Role.IMPACT, "link.pairing_failed_meanwhile"))

        if router and power:
            chain.append(
                Link(
                    Role.FIX,
                    "fix.keep_border_router_powered",
                    {
                        "border_router": router.data.get("name"),
                        "switch": power.data.get("name"),
                    },
                )
            )
        elif router:
            chain.append(
                Link(
                    Role.FIX,
                    "fix.check_border_router_power",
                    {"border_router": router.data.get("name")},
                )
            )
        else:
            chain.append(Link(Role.FIX, "fix.mesh_trouble_rare"))

        return Finding(
            key=episode.key,
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.mesh_split.title"
            if episode.split
            else "finding.new_leader.title",
            params={"duration": duration},
            started_at=episode.start,
            ended_at=episode.last if ended else None,
            chain=chain,
            subjects=[router.subject] if router and router.subject else [],
        )
