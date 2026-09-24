"""A border router went away - and whether it hangs on a switched outlet.

Border routers connect the Thread mesh to the home network. Each one that
disappears removes a way in; the last one takes every Thread device offline.
A border router that restarts comes back within a minute or two, so only an
absence longer than that becomes a finding.

Whether a border router is powered through a switch is not known anywhere; it
can only be concluded from what happens. One coincidence proves nothing, so
the evidence has to be specific: the router vanishes right after that switch,
and only that switch, went off, and it returns shortly after that same switch
went back on. Twice. Anything that contradicts it - the router vanishing
while the switch stayed on, returning while it stayed off, or staying while
the switch went off - drops the suspicion and closes the finding.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity
from .common import power_off_link

#: Restarts of speakers and TV boxes take about this long.
GONE_FOR = timedelta(minutes=5)

#: The Matter Server reports a vanished border router up to two polls late;
#: the switch may have been turned off this long before the report.
POWER_WINDOW = timedelta(minutes=4)

#: A border router needs to boot and be seen by a poll again after its power
#: returns; coming back later than this is not taken as proof.
RETURN_WINDOW = timedelta(minutes=10)

#: How often the whole pattern - off, gone, on, back - must be seen before the
#: add-on says the router is powered through that switch.
SWITCHED_AFTER = 2

#: Where the evidence per router and switch is kept, across restarts.
SUSPECTS = "border_router.suspects"


@RULES.register("border_router")
class BorderRouterRule(Rule):
    """Reports border routers that stay away, and ones on switched outlets."""

    name: ClassVar[str] = "border_router"
    part_of: ClassVar[frozenset[str]] = frozenset({"mesh"})
    listens: ClassVar[frozenset[str]] = frozenset(
        {kinds.BORDER_ROUTER_GONE, kinds.BORDER_ROUTER_APPEARED, kinds.HA_POWER_OFF}
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
        """Follow routers leaving and returning, and switches going off."""
        if event.kind == kinds.HA_POWER_OFF:
            await self._switch_off(event)
            return
        name = str(event.data.get("name") or event.subject)
        if event.kind == kinds.BORDER_ROUTER_GONE:
            self.gone[name] = event
            await self._router_gone(name, event)
            return
        await self._router_back(name, event)
        vanished = self.gone.pop(name, None)
        if vanished and name in self.reported:
            self.reported.discard(name)
            finding = await self.describe(vanished)
            finding.ended_at = event.at
            await self.ctx.publish(finding)

    async def on_tick(self) -> None:
        """Report routers away long enough; drop suspicions that did not hold."""
        now = self.ctx.now()
        # Publishing awaits, and a border router may come or go meanwhile.
        for name, event in list(self.gone.items()):
            if name not in self.reported and now - event.at >= GONE_FOR:
                self.reported.add(name)
                await self.ctx.publish(await self.describe(event))
        suspects = await self._suspects()
        for pair_key, pair in list(suspects.items()):
            watch = pair.get("watch_until")
            if watch and now > datetime.fromisoformat(watch):
                # The switch went off and the router stayed.
                await self._drop(suspects, pair_key, now)
        await self._save(suspects)

    async def describe(self, event: Event) -> Finding:
        """Build the finding for a border router that stayed away."""
        name = event.data.get("name")
        chain: list[Link] = []
        offs = await self._offs_before(event.at)
        power = offs[-1] if offs else None
        if power:
            switches = {off.subject for off in offs}
            chain.append(
                power_off_link(
                    power,
                    Confidence.LIKELY if len(switches) == 1 else Confidence.POSSIBLE,
                )
            )
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

    # --- evidence for a router on a switch ---------------------------------

    async def _offs_before(self, at: datetime) -> list[Event]:
        return await self.ctx.store.events(
            (kinds.HA_POWER_OFF,), since=at - POWER_WINDOW, until=at
        )

    async def _suspects(self) -> dict[str, dict[str, Any]]:
        return dict(await self.ctx.store.get_state(SUSPECTS) or {})

    async def _save(self, suspects: dict[str, dict[str, Any]]) -> None:
        await self.ctx.store.set_state(SUSPECTS, suspects)

    async def _router_gone(self, name: str, event: Event) -> None:
        suspects = await self._suspects()
        offs = await self._offs_before(event.at)
        switched = {off.subject: off for off in offs if off.subject}
        for pair_key, pair in list(suspects.items()):
            if pair["router"] != name:
                continue
            if pair["switch"] in switched:
                pair["watch_until"] = None
            else:
                # Gone while its supposed switch stayed on.
                await self._drop(suspects, pair_key, event.at)
        # With several switches turned off at once, none of them is evidence.
        if len(switched) == 1:
            ((subject, off),) = switched.items()
            pair_key = f"{name}|{subject}"
            pair = suspects.setdefault(
                pair_key,
                {"router": name, "switch": subject, "hits": [], "evidence": []},
            )
            pair.update(
                switch_name=off.data.get("name"),
                router_subject=event.subject,
                gone_at=event.at.isoformat(),
                watch_until=None,
            )
            pair["evidence"] = [*pair["evidence"], off.id, event.id][-12:]
        await self._save(suspects)

    async def _router_back(self, name: str, event: Event) -> None:
        suspects = await self._suspects()
        for pair_key, pair in list(suspects.items()):
            if pair["router"] != name or not pair.get("gone_at"):
                continue
            gone_at = datetime.fromisoformat(pair["gone_at"])
            ons = await self.ctx.store.events(
                (kinds.HA_POWER_ON,),
                since=gone_at - POWER_WINDOW,
                until=event.at,
                subject=pair["switch"],
            )
            if not ons or event.at - ons[-1].at > RETURN_WINDOW:
                # Back while its supposed switch stayed off.
                await self._drop(suspects, pair_key, event.at)
                continue
            pair["gone_at"] = None
            pair["hits"] = [*pair["hits"], gone_at.isoformat()]
            pair["evidence"] = [*pair["evidence"], ons[-1].id, event.id][-12:]
            if len(pair["hits"]) >= SWITCHED_AFTER:
                await self.ctx.publish(self._switched(pair))
        await self._save(suspects)

    async def _switch_off(self, event: Event) -> None:
        """Expect every router suspected on this switch to vanish soon."""
        suspects = await self._suspects()
        for pair in suspects.values():
            if pair["switch"] == event.subject and pair["hits"]:
                pair["watch_until"] = (event.at + POWER_WINDOW).isoformat()
        await self._save(suspects)

    async def _drop(
        self, suspects: dict[str, dict[str, Any]], pair_key: str, at: datetime
    ) -> None:
        pair = suspects.pop(pair_key)
        if len(pair["hits"]) >= SWITCHED_AFTER:
            finding = self._switched(pair)
            finding.ended_at = at
            await self.ctx.publish(finding)

    def _switched(self, pair: dict[str, Any]) -> Finding:
        name, switch = pair["router"], pair.get("switch_name")
        first = pair["hits"][0]
        return Finding(
            key=f"switched_border_router:{name}:{pair['switch']}:{first}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.border_router_switched.title",
            params={"border_router": name, "switch": switch},
            started_at=datetime.fromisoformat(first),
            chain=[
                Link(
                    Role.CAUSE,
                    "link.border_router_on_switch",
                    {
                        "border_router": name,
                        "switch": switch,
                        "count": len(pair["hits"]),
                    },
                    confidence=Confidence.LIKELY,
                    evidence=[e for e in pair["evidence"] if e],
                ),
                Link(Role.IMPACT, "link.border_router_on_switch_impact"),
                Link(
                    Role.FIX,
                    "fix.keep_border_router_powered",
                    {"border_router": name, "switch": switch},
                ),
            ],
            subjects=[s for s in (pair.get("router_subject"), pair["switch"]) if s],
        )
