"""A device that relayed for others went away, and took them with it.

Battery devices do not talk to the mesh on their own; each hangs on one
mains-powered device nearby, its parent. When the parent loses power, its
children have to find a new one. Most do within minutes; some wait a long time
before trying again, and a child with no other parent in range stays away.

So several devices going away right after the device they all hung on is one
story, not several: the parent is the one to look at.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Event, Finding, Link, Role, Severity

#: Children notice a lost parent within a few minutes.
FOLLOW_WITHIN = timedelta(minutes=10)

#: A child may notice a moment before the Matter Server marks its parent.
NOTICED_EARLY = timedelta(minutes=1)

#: This many children away make the parent the likely reason.
CHILDREN_AT_LEAST = 2

#: Roles of devices that hang on a parent.
CHILD_ROLES = frozenset({"sleepy_end_device", "end_device", "reed", "child"})

STATE = "relay.open"


@RULES.register("relay")
class RelayRule(Rule):
    """Ties devices that went away to the parent they all hung on."""

    name: ClassVar[str] = "relay"
    story_margin: ClassVar[tuple[timedelta, timedelta] | None] = (
        timedelta(minutes=1),
        FOLLOW_WITHIN,
    )
    listens: ClassVar[frozenset[str]] = frozenset(
        {kinds.MATTER_NODE_UNAVAILABLE, kinds.MATTER_NODE_AVAILABLE}
    )

    def __init__(self, ctx: Context) -> None:
        """Read what is open from the store on first use."""
        super().__init__(ctx)
        self._open: dict[str, dict[str, Any]] | None = None

    async def _load(self) -> dict[str, dict[str, Any]]:
        if self._open is None:
            self._open = dict(await self.ctx.store.get_state(STATE) or {})
        return self._open

    async def on_event(self, event: Event) -> None:
        """Look for a lost parent, or close the story when it is back."""
        opened = await self._load()
        subject = event.subject
        if subject is None:
            return
        if event.kind == kinds.MATTER_NODE_AVAILABLE:
            if subject in opened:
                story = opened.pop(subject)
                await self.ctx.store.set_state(STATE, opened)
                finding = self.describe(subject, story)
                finding.ended_at = event.at
                await self.ctx.publish(finding)
            return
        links = await self.ctx.store.get_state("thread.links") or []
        parents = {
            str(link["subject"]): str(link["neighbour"])
            for link in links
            if link.get("role") in CHILD_ROLES and link.get("neighbour")
        }
        # The device that went away may be a parent, or one of its children.
        parent = subject if subject in parents.values() else parents.get(subject)
        if parent is None or not parent.startswith("node:"):
            return
        went = await self._went(parent, event.at)
        if went is None:
            return
        recent = await self.ctx.store.events(
            (kinds.MATTER_NODE_UNAVAILABLE,),
            since=went.at - NOTICED_EARLY,
            until=event.at,
        )
        children = sorted(
            {str(e.subject) for e in recent if parents.get(str(e.subject)) == parent}
        )
        if len(children) < CHILDREN_AT_LEAST:
            return
        story = opened.setdefault(
            parent, {"since": went.at.isoformat(), "event": went.id}
        )
        story["children"] = children
        await self.ctx.store.set_state(STATE, opened)
        await self.ctx.publish(self.describe(parent, story))

    async def _went(self, parent: str, now: datetime) -> Event | None:
        """When ``parent`` went away, if it is away now and went lately."""
        events = await self.ctx.store.events(
            (kinds.MATTER_NODE_UNAVAILABLE, kinds.MATTER_NODE_AVAILABLE),
            since=now - FOLLOW_WITHIN,
            until=now,
            subject=parent,
        )
        if not events or events[-1].kind != kinds.MATTER_NODE_UNAVAILABLE:
            return None
        return events[-1]

    def describe(self, parent: str, story: dict[str, Any]) -> Finding:
        """Build the finding for a parent that took its children along."""
        since = datetime.fromisoformat(story["since"])
        relay = self.ctx.names.get(parent)
        children = [self.ctx.names.get(c) or c for c in story["children"]]
        params = {
            "relay": relay,
            "count": len(children),
            "devices": ", ".join(str(c) for c in children),
        }
        return Finding(
            key=f"relay:{parent}:{story['since']}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.relay_gone.title",
            params=params,
            started_at=since,
            chain=[
                Link(
                    Role.CAUSE,
                    "link.relay_gone",
                    params,
                    at=since,
                    evidence=[story["event"]] if story.get("event") else [],
                ),
                Link(Role.EFFECT, "link.relay_children_gone", params),
                Link(Role.IMPACT, "link.relay_impact"),
                Link(Role.FIX, "fix.relay", params),
            ],
            subjects=[parent, *story["children"]],
        )
