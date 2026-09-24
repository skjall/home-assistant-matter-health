"""Most Thread devices become unreachable at the same moment.

When the mesh splits or a border router goes away, the mesh and border router
rules tell that story. When neither happened and still half the Thread
devices vanish within a minute, the mesh is usually fine and it is Home
Assistant's host that lost its way into it: the route into the mesh expired,
or forwarding stopped. Other controllers such as a phone's home app often
keep working meanwhile, which makes this so confusing.

The devices' own findings are told as part of this story.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity
from .common import MESH_TROUBLE, last_border_router_gone, recent_update, update_link

#: Devices going away within this span went together.
TOGETHER = timedelta(minutes=2)

#: At least this many devices ...
WAVE_AT_LEAST = 3

#: ... and at least this share of all Thread devices.
WAVE_SHARE = 0.5

#: Mesh trouble or a lost border router this close explains it otherwise.
OTHER_STORY = timedelta(minutes=5)

#: A missing route shows in the Matter Server's log this close to the wave.
ROUTE_WINDOW = timedelta(minutes=5)

#: The wave is over once fewer than this share of its devices are still away.
OVER_BELOW = 0.5

STATE = "wave.open"


@RULES.register("wave")
class WaveRule(Rule):
    """Reports Thread devices that all went away at once for no mesh reason."""

    name: ClassVar[str] = "wave"
    story_margin: ClassVar[tuple[timedelta, timedelta] | None] = (
        TOGETHER,
        timedelta(minutes=15),
    )
    listens: ClassVar[frozenset[str]] = frozenset({kinds.MATTER_NODE_UNAVAILABLE})

    def __init__(self, ctx: Context) -> None:
        """Read what is open from the store on first use."""
        super().__init__(ctx)
        self._open: dict[str, Any] | None = None

    async def _load(self) -> dict[str, Any]:
        if self._open is None:
            self._open = dict(await self.ctx.store.get_state(STATE) or {})
        return self._open

    async def _thread_devices(self) -> set[str]:
        links = await self.ctx.store.get_state("thread.links") or []
        return {
            str(link["subject"])
            for link in links
            if str(link.get("subject", "")).startswith("node:")
        }

    async def on_event(self, event: Event) -> None:
        """Check whether this device is one of many going at once."""
        wave = await self._load()
        if wave:
            return
        thread = await self._thread_devices()
        if event.subject not in thread:
            return
        recent = await self.ctx.store.events(
            (kinds.MATTER_NODE_UNAVAILABLE,), since=event.at - TOGETHER, until=event.at
        )
        gone = {e.subject for e in recent if e.subject in thread}
        if len(gone) < WAVE_AT_LEAST or len(gone) < WAVE_SHARE * len(thread):
            return
        start = min(e.at for e in recent if e.subject in gone)
        if await self._other_story(start, event.at):
            return
        wave.update(
            {
                "since": start.isoformat(),
                "devices": sorted(str(s) for s in gone),
                "total": len(thread),
                "evidence": [e.id for e in recent if e.subject in gone and e.id],
            }
        )
        await self.ctx.store.set_state(STATE, wave)
        await self.ctx.publish(await self.describe(wave))

    async def _other_story(self, start: datetime, until: datetime) -> bool:
        mesh = await self.ctx.store.events(
            MESH_TROUBLE, since=start - OTHER_STORY, until=until
        )
        router = await last_border_router_gone(self.ctx, start - OTHER_STORY, until)
        return bool(mesh) or router is not None

    async def on_tick(self) -> None:
        """Close the wave once most of its devices are back."""
        wave = await self._load()
        if not wave:
            return
        nodes = await self.ctx.store.get_state("matter.nodes") or {}
        away = set(nodes.get("unavailable", [])) & set(wave["devices"])
        if len(away) < OVER_BELOW * len(wave["devices"]):
            finding = await self.describe(wave)
            finding.ended_at = self.ctx.now()
            wave.clear()
            await self.ctx.store.set_state(STATE, wave)
            await self.ctx.publish(finding)

    async def describe(self, wave: dict[str, Any]) -> Finding:
        """Build the finding for a wave of unreachable Thread devices."""
        since = datetime.fromisoformat(wave["since"])
        unreachable = await self.ctx.store.events(
            (kinds.MATTER_ROUTE_UNREACHABLE,),
            since=since - ROUTE_WINDOW,
            until=since + ROUTE_WINDOW,
        )
        chain = [
            Link(
                Role.CAUSE,
                "link.route_missing" if unreachable else "link.route_probably_missing",
                confidence=Confidence.LIKELY if unreachable else Confidence.POSSIBLE,
                evidence=[e.id for e in unreachable if e.id],
            )
        ]
        update = await recent_update(self.ctx, since)
        if update:
            chain.append(update_link(update))
        params = {"count": len(wave["devices"]), "total": wave["total"]}
        chain += [
            Link(
                Role.EFFECT,
                "link.thread_wave",
                params,
                at=since,
                evidence=list(wave.get("evidence", [])),
            ),
            Link(Role.IMPACT, "link.thread_wave_impact"),
            Link(Role.FIX, "fix.thread_wave"),
        ]
        return Finding(
            key=f"wave:{wave['since']}",
            rule=self.name,
            severity=Severity.PROBLEM,
            title="finding.thread_wave.title",
            params=params,
            started_at=since,
            chain=chain,
            subjects=list(wave["devices"]),
        )
