"""A border router went away - and whether it hangs on a switched outlet.

Border routers connect the Thread mesh to the home network. Each one that
disappears removes a way in; the last one takes every Thread device offline.
A border router that restarts comes back within a minute or two, so only an
absence longer than that becomes a finding.

When a border router vanishes right after the same switch was turned off more
than once, it is almost certainly powered through that switch. That is worth
saying on its own: the next time someone switches off the TV, the mesh
suffers again.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity
from .common import last_power_off, power_off_link

#: Restarts of speakers and TV boxes take about this long.
GONE_FOR = timedelta(minutes=5)

#: The Matter Server reports a vanished border router up to two polls late;
#: the switch may have been turned off this long before the report.
POWER_WINDOW = timedelta(minutes=4)

#: How often a border router must vanish after the same switch went off before
#: the add-on says it is powered through that switch.
SWITCHED_AFTER = 2


@RULES.register("border_router")
class BorderRouterRule(Rule):
    """Reports border routers that stay away, and ones on switched outlets."""

    name: ClassVar[str] = "border_router"
    part_of: ClassVar[frozenset[str]] = frozenset({"mesh"})
    listens: ClassVar[frozenset[str]] = frozenset(
        {kinds.BORDER_ROUTER_GONE, kinds.BORDER_ROUTER_APPEARED}
    )

    @classmethod
    def stories_for(cls, finding: Finding) -> frozenset[str]:
        """Place a vanished router in a mesh story, but not a habit of vanishing."""
        if finding.key.startswith("switched_border_router:"):
            return frozenset()
        return cls.part_of

    def __init__(self, ctx: Context) -> None:
        """Nothing is missing at start."""
        super().__init__(ctx)
        self.gone: dict[str, Event] = {}
        self.reported: set[str] = set()

    async def on_event(self, event: Event) -> None:
        """Remember a disappearance; close its finding when it comes back."""
        name = str(event.data.get("name") or event.subject)
        if event.kind == kinds.BORDER_ROUTER_GONE:
            self.gone[name] = event
            await self._note_switch(name, event)
            return
        vanished = self.gone.pop(name, None)
        if vanished and name in self.reported:
            self.reported.discard(name)
            finding = await self.describe(vanished)
            finding.ended_at = event.at
            await self.ctx.publish(finding)

    async def on_tick(self) -> None:
        """Report border routers that have been away long enough."""
        now = self.ctx.now()
        # Publishing awaits, and a border router may come or go meanwhile.
        for name, event in list(self.gone.items()):
            if name not in self.reported and now - event.at >= GONE_FOR:
                self.reported.add(name)
                await self.ctx.publish(await self.describe(event))

    async def describe(self, event: Event) -> Finding:
        """Build the finding for a border router that stayed away."""
        name = event.data.get("name")
        chain: list[Link] = []
        power = await last_power_off(self.ctx, event.at, POWER_WINDOW)
        if power:
            chain.append(power_off_link(power, Confidence.LIKELY))
        else:
            chain.append(Link(Role.CAUSE, "link.cause_unknown"))
        chain.append(
            Link(
                Role.EFFECT,
                "link.border_router_gone",
                {"border_router": name},
                at=event.at,
                evidence=[event.id] if event.id else [],
            )
        )
        chain.append(Link(Role.IMPACT, "link.border_router_gone_impact"))
        chain.append(
            Link(
                Role.FIX,
                "fix.keep_border_router_powered"
                if power
                else "fix.check_border_router_power",
                {
                    "border_router": name,
                    "switch": power.data.get("name") if power else None,
                },
            )
        )
        return Finding(
            key=f"border_router:{name}:{event.at.isoformat()}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.border_router_gone.title",
            params={"border_router": name},
            started_at=event.at,
            chain=chain,
            subjects=[event.subject] if event.subject else [],
        )

    async def _note_switch(self, name: str, event: Event) -> None:
        power = await last_power_off(self.ctx, event.at, POWER_WINDOW)
        if power is None or power.subject is None:
            return
        state_name = f"border_router.switched.{name}.{power.subject}"
        seen: list[str] = list(await self.ctx.store.get_state(state_name) or [])
        seen.append(event.at.isoformat())
        await self.ctx.store.set_state(state_name, seen)
        if len(seen) < SWITCHED_AFTER:
            return
        first = datetime.fromisoformat(seen[0])
        await self.ctx.publish(
            Finding(
                key=f"switched_border_router:{name}:{power.subject}",
                rule=self.name,
                severity=Severity.WARNING,
                title="finding.border_router_switched.title",
                params={"border_router": name, "switch": power.data.get("name")},
                started_at=first,
                chain=[
                    Link(
                        Role.CAUSE,
                        "link.border_router_on_switch",
                        {
                            "border_router": name,
                            "switch": power.data.get("name"),
                            "count": len(seen),
                        },
                        confidence=Confidence.LIKELY,
                        evidence=[power.id] if power.id else [],
                    ),
                    Link(Role.IMPACT, "link.border_router_on_switch_impact"),
                    Link(
                        Role.FIX,
                        "fix.keep_border_router_powered",
                        {"border_router": name, "switch": power.data.get("name")},
                    ),
                ],
                subjects=[s for s in (event.subject, power.subject) if s],
            )
        )
