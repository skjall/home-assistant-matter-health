"""The Thread mesh has fallen apart into parts that do not reach each other.

Every border router announces the partition it belongs to. In a healthy mesh
all of them name the same one. When some name another, the mesh is split:
each part elects its own leader, and devices in one part cannot reach
routers and border routers in the other. A split after a leader was lost
heals within a minute or two; one that lasts means the parts do not hear
each other, or a border router is stuck in a part of its own.

Many consequences follow - devices unreachable one after another, pairing
failing - and they are told as part of this story. Home Assistant's border
router often noticed the other part earlier in its log; the finding begins
there.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from ... import kinds
from ...engine import RULES, Context, Rule
from ...model import Event, Finding, Link, Role, Severity

#: A split shorter than this is a leader being elected, told by the mesh rule.
SPLIT_FOR = timedelta(minutes=5)

#: Home Assistant's border router seeing another partition this long before
#: the announcements tell it is taken as the split's beginning.
NOTICED_BEFORE = timedelta(hours=12)

STATE = "partitions.split"


@RULES.register("partitions")
class PartitionRule(Rule):
    """Reports border routers of one network that stay in different partitions."""

    name: ClassVar[str] = "partitions"
    story_margin: ClassVar[tuple[timedelta, timedelta] | None] = (
        timedelta(minutes=10),
        timedelta(minutes=10),
    )
    listens: ClassVar[frozenset[str]] = frozenset({kinds.THREAD_PARTITIONS})

    def __init__(self, ctx: Context) -> None:
        """Read an open split from the store on first use."""
        super().__init__(ctx)
        self._state: dict[str, Any] | None = None

    async def _load(self) -> dict[str, Any]:
        if self._state is None:
            self._state = dict(await self.ctx.store.get_state(STATE) or {})
        return self._state

    async def on_event(self, event: Event) -> None:
        """Follow the grouping of the border routers."""
        state = await self._load()
        parts = list(event.data.get("parts") or [])
        if len(parts) > 1:
            if not state:
                noticed = await self.ctx.store.events(
                    (kinds.THREAD_FOREIGN_PARTITION,),
                    since=event.at - NOTICED_BEFORE,
                    until=event.at,
                )
                # The latest sighting, newest first: an earlier split may have healed.
                since = noticed[0] if noticed else event
                state.update(
                    since=since.at.isoformat(),
                    seen=event.at.isoformat(),
                    evidence=[i for i in (since.id, event.id) if i],
                )
            state["parts"] = parts
            if state.get("reported"):
                await self.ctx.publish(self.describe(state))
        elif state:
            if state.get("reported"):
                finding = self.describe(state)
                finding.ended_at = event.at
                await self.ctx.publish(finding)
            state.clear()
        await self.ctx.store.set_state(STATE, state)

    async def on_tick(self) -> None:
        """Report a split once it has lasted."""
        state = await self._load()
        if (
            state
            and not state.get("reported")
            and self.ctx.now() - datetime.fromisoformat(state["seen"]) >= SPLIT_FOR
        ):
            state["reported"] = True
            await self.ctx.publish(self.describe(state))
            await self.ctx.store.set_state(STATE, state)

    def describe(self, state: dict[str, Any]) -> Finding:
        """Build the finding: which border routers are apart from the rest."""
        parts = state["parts"]
        since = datetime.fromisoformat(state["since"])
        apart = [r for part in parts[1:] for r in part["border_routers"]]
        names = ", ".join(r["name"] for r in apart)
        main = parts[0]
        params = {"count": len(apart), "apart": names, "leader": main.get("leader")}
        return Finding(
            key=f"partitions:{state['since']}",
            rule=self.name,
            severity=Severity.PROBLEM,
            title="finding.thread_split.title",
            params={"count": len(parts)},
            started_at=since,
            chain=[
                Link(
                    Role.EFFECT,
                    "link.thread_split",
                    params,
                    at=since,
                    evidence=list(state.get("evidence", [])),
                ),
                Link(
                    Role.EFFECT,
                    "link.thread_split_leader"
                    if main.get("leader")
                    else "link.thread_split_no_leader",
                    params,
                ),
                Link(Role.IMPACT, "link.thread_split_impact"),
                Link(Role.FIX, "fix.thread_split", params),
            ],
            subjects=[r["subject"] for r in apart],
        )
