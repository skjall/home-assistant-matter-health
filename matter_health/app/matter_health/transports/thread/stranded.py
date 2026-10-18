"""Battery devices that did not come back after the mesh changed.

A battery device sleeps most of the time and talks only to the router it
hangs on. When the mesh changes while it sleeps - a split, a new leader, a
border router gone - it can end up where Home Assistant no longer finds it,
and it does not notice: pressing its button or triggering it wakes it only
for a moment, and nothing arrives. Taking its battery out for a few seconds
makes it join the mesh anew, and it is back within a minute.

Such devices drop out one after another, over hours, not at once. While the
radio channel is disturbed across the home, that explains them; once the
devices measure a quiet channel again, or a busy one only around a few of
them, and they are still away, the disturbance no longer does. Then this
story takes over from the one they were told in, and names them together
with what helps.

Where the devices last reported their battery weak, that is said too: the
battery comes out anyway, and a fresh one goes in.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from ... import batteries, kinds
from ...engine import RULES, Context, Rule
from ...model import Confidence, Event, Finding, Link, Role, Severity
from ...rules.common import MESH_TROUBLE
from ...rules.offline import AWAY

#: The routing role of a sleepy end device.
SLEEPY = 2

#: A sleepy device away this long has missed several check-ins.
STRANDED_AFTER = timedelta(minutes=30)

#: At least this many: one device alone is more likely its own battery.
AT_LEAST = 3

#: A change of the mesh this long before the first went away can have started it.
LOOKBACK = timedelta(hours=6)

#: Changes of the mesh that leave sleeping devices behind.
TRIGGERS = (*MESH_TROUBLE, kinds.BORDER_ROUTER_GONE, kinds.THREAD_PARTITIONS)

#: Named devices, the rest are counted.
NAMED = 5

STATE = "stranded.state"


@RULES.register("stranded")
class StrandedRule(Rule):
    """Reports battery devices stuck away after the mesh changed."""

    name: ClassVar[str] = "stranded"
    listens: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, ctx: Context) -> None:
        """Read what is open from the store on first use."""
        super().__init__(ctx)
        self._state: dict[str, Any] | None = None

    async def _load(self) -> dict[str, Any]:
        if self._state is None:
            self._state = dict(await self.ctx.store.get_state(STATE) or {})
        return self._state

    async def on_event(self, event: Event) -> None:
        """Nothing to follow; the offline rule keeps who is away."""

    async def on_tick(self) -> None:
        """Compare who is away with what the mesh and the radio say."""
        state = await self._load()
        now = self.ctx.now()
        away: dict[str, dict[str, Any]] = await self.ctx.store.get_state(AWAY) or {}
        roles: dict[str, int] = await self.ctx.store.get_state("thread.roles") or {}
        stranded = {
            subject: entry
            for subject, entry in away.items()
            if roles.get(subject) == SLEEPY
            and not entry.get("expected")
            and now - datetime.fromisoformat(entry["since"]) >= STRANDED_AFTER
        }
        open_ = state.get("open")
        if open_ is not None:
            trigger = datetime.fromisoformat(open_["trigger"])
            still = sorted(
                s
                for s, e in stranded.items()
                if datetime.fromisoformat(e["since"]) >= trigger
            )
            if still == open_["subjects"]:
                return
            if still:
                open_["subjects"] = still
                await self.ctx.publish(await self.describe(open_, stranded))
            else:
                finding = await self.describe(open_, stranded)
                finding.ended_at = now
                await self.ctx.publish(finding)
                state.pop("open")
            await self.ctx.store.set_state(STATE, state)
            return
        if len(stranded) < AT_LEAST:
            return
        radio = await self._radio()
        if radio == "wide":
            # The channel may still explain them.
            return
        change = await self._change_before(stranded)
        if change is None:
            return
        after = {
            s: e
            for s, e in stranded.items()
            if datetime.fromisoformat(e["since"]) >= change.at
        }
        first = min(datetime.fromisoformat(e["since"]) for e in after.values())
        open_ = state["open"] = {
            "since": first.isoformat(),
            "trigger": change.at.isoformat(),
            "evidence": [change.id] if change.id else [],
            "subjects": sorted(after),
            "radio": radio,
        }
        await self.ctx.publish(await self.describe(open_, stranded))
        await self.ctx.store.set_state(STATE, state)

    async def _change_before(self, stranded: dict[str, dict[str, Any]]) -> Event | None:
        """Find the change of the mesh that at least a few of them went after.

        One that went long before, for a reason of its own, does not hide
        the rest: the devices are tried in the order they went.
        """
        went = sorted(datetime.fromisoformat(e["since"]) for e in stranded.values())
        for index, since in enumerate(went):
            if len(went) - index < AT_LEAST:
                break
            changes = [
                e
                for e in await self.ctx.store.events(
                    TRIGGERS, since=since - LOOKBACK, until=since
                )
                if e.kind != kinds.THREAD_PARTITIONS
                or len(e.data.get("parts") or []) > 1
            ]
            if changes:
                return changes[-1]
        return None

    async def _radio(self) -> str | None:
        """Whether the channel is ``quiet``, busy ``local``-ly or ``wide``."""
        measured = await self.ctx.store.get_state("thread.interference") or {}
        if not measured.get("devices"):
            return None
        judged = await self.ctx.store.get_state("interference.state") or {}
        return str(judged.get("kind") or "quiet")

    async def describe(
        self, open_: dict[str, Any], away: dict[str, dict[str, Any]]
    ) -> Finding:
        """Build the finding: which devices, since when, and what helps."""
        subjects = list(open_["subjects"])
        known = await self.ctx.store.get_state(batteries.BATTERIES) or {}

        def name(subject: str) -> str:
            return str(
                self.ctx.names.get(subject)
                or (away.get(subject) or {}).get("name")
                or subject
            )

        params = {
            "count": len(subjects),
            "devices": listed([name(s) for s in subjects]),
        }
        weak = [s for s in subjects if (known.get(s) or {}).get("weak")]
        weak_params = {"count": len(weak), "devices": listed([name(s) for s in weak])}
        since = datetime.fromisoformat(open_["since"])
        chain = [
            Link(Role.CAUSE, "link.stranded_cause", confidence=Confidence.LIKELY),
            Link(
                Role.EFFECT,
                "link.stranded_trigger",
                at=datetime.fromisoformat(open_["trigger"]),
                evidence=list(open_.get("evidence", [])),
            ),
            Link(Role.EFFECT, "link.stranded", params, at=since),
        ]
        if open_.get("radio") in ("quiet", "local"):
            chain.append(Link(Role.EFFECT, f"link.stranded_radio_{open_['radio']}"))
        if weak:
            chain.append(Link(Role.EFFECT, "link.stranded_weak_battery", weak_params))
        chain.append(Link(Role.IMPACT, "link.stranded_impact", params))
        chain.append(Link(Role.FIX, "fix.stranded", params))
        if weak:
            chain.append(Link(Role.FIX, "fix.stranded_weak_battery", weak_params))
        return Finding(
            key=f"stranded:{open_['since']}",
            rule=self.name,
            severity=Severity.PROBLEM,
            title="finding.stranded.title",
            params={"count": len(subjects)},
            started_at=since,
            chain=chain,
            subjects=subjects,
        )


def listed(names: list[str]) -> str:
    """Name the first few, count the rest."""
    more = len(names) - NAMED
    return ", ".join(names[:NAMED]) + (f" (+{more})" if more > 0 else "")
