"""A border router went away - and whether it hangs on a switched outlet.

Border routers connect the Thread mesh to the home network. Each one that
disappears removes a way in; the last one takes every Thread device offline.
The transport reports a router gone only after it has been silent longer
than it usually pauses, so a restart or a pause never becomes a finding.
Its return closes the finding, even if the add-on restarted meanwhile. The
finding starts when the router last announced itself: that is when it went,
and a switch turned off between that announcement and the next one due is
what took its power.

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

from ... import kinds
from ...engine import RULES, Rule
from ...model import Confidence, Event, Finding, Link, Role, Severity
from ...rules.common import power_off_link, switched_off
from .transport import SILENT_AT_MOST

#: A switch turned off this shortly before the last announcement may still
#: have taken the power: announcements are seen a poll or two late.
POWER_WINDOW = timedelta(minutes=4)

#: A border router announces itself again within this; a switch turned off
#: after its last announcement and before the next was due took its power.
ANNOUNCED_EVERY = timedelta(minutes=20)

#: After a suspected switch went off, its router is reported gone within
#: this: its next announcement missed, then the silence waited out.
REPORTED_WITHIN = ANNOUNCED_EVERY + SILENT_AT_MOST + timedelta(minutes=5)

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

    async def on_event(self, event: Event) -> None:
        """Follow routers leaving and returning, and switches going off."""
        if event.kind == kinds.HA_POWER_OFF:
            if not event.data.get("flicker"):
                await self._switch_off(event)
            return
        if event.data.get("own") is False:
            # A router of another Thread network carries none of ours.
            return
        name = str(event.data.get("name") or event.subject)
        if event.kind == kinds.BORDER_ROUTER_GONE:
            await self._router_gone(name, event)
            # Its silence has been waited out already.
            await self.ctx.publish(await self.describe(event))
            return
        await self._router_back(name, event)
        # Read back from the store: what is kept in memory does not survive a
        # restart between leaving and returning.
        for finding in await self.ctx.store.findings(event.at):
            if (
                finding.ended_at is None
                and finding.rule == self.name
                and finding.key.startswith(f"border_router:{name}:")
            ):
                await self._close(finding, event.at)

    async def on_tick(self) -> None:
        """Close what a returned router left open; drop suspicions that failed."""
        now = self.ctx.now()
        await self._close_returned(now)
        suspects = await self._suspects()
        for pair_key, pair in list(suspects.items()):
            watch = pair.get("watch_until")
            if watch and now > datetime.fromisoformat(watch):
                # The switch went off and the router stayed.
                await self._drop(suspects, pair_key, now)
        await self._save(suspects)

    async def _close_returned(self, now: datetime) -> None:
        """Close findings of routers heard again since they went.

        Their return may have been missed - announced while the add-on was
        not running, or before this was checked at all.
        """
        heard = {
            r.get("name"): r.get("last_seen")
            for r in await self.ctx.store.get_state("border_routers") or []
        }
        for finding in await self.ctx.store.findings(now):
            last = heard.get(finding.params.get("border_router"))
            if (
                finding.ended_at is None
                and finding.rule == self.name
                and finding.key.startswith("border_router:")
                and isinstance(last, str)
                and datetime.fromisoformat(last) > finding.started_at
            ):
                await self._close(finding, now)

    async def _close(self, finding: Finding, back: datetime) -> None:
        """End the finding of a returned router, taking back a switch disproved.

        A router that returns while the switch named as the cause stayed off
        was not powered through it.
        """
        finding.ended_at = back
        offs = await self._offs_around(finding.started_at)
        if finding.chain and finding.chain[0].key == "link.power_off" and offs:
            off = offs[-1]
            ons = await self.ctx.store.events(
                (kinds.HA_POWER_ON,), since=off.at, until=back, subject=off.subject
            )
            if not ons:
                finding.chain = [
                    Link(
                        Role.CAUSE,
                        "link.border_router_not_switch",
                        {
                            "switch": finding.chain[0].params.get("switch"),
                            "border_router": finding.params.get("border_router"),
                        },
                        at=back,
                        confidence=Confidence.CERTAIN,
                    ),
                    *(
                        Link(Role.FIX, "fix.check_border_router_power", link.params)
                        if link.key == "fix.keep_border_router_powered"
                        else link
                        for link in finding.chain[1:]
                    ),
                ]
        await self.ctx.publish(finding)

    async def describe(self, event: Event) -> Finding:
        """Build the finding for a border router that stayed away."""
        name = event.data.get("name")
        went = went_at(event)
        chain: list[Link] = []
        offs = await self._offs_around(went)
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
                at=went,
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
            key=f"border_router:{name}:{went.isoformat()}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.border_router_gone.title",
            params={"border_router": name},
            started_at=went,
            chain=chain,
            subjects=[event.subject] if event.subject else [],
        )

    # --- evidence for a router on a switch ---------------------------------

    async def _offs_around(self, went: datetime) -> list[Event]:
        return switched_off(
            await self.ctx.store.events(
                (kinds.HA_POWER_OFF,),
                since=went - POWER_WINDOW,
                until=went + ANNOUNCED_EVERY,
            )
        )

    async def _suspects(self) -> dict[str, dict[str, Any]]:
        return dict(await self.ctx.store.get_state(SUSPECTS) or {})

    async def _save(self, suspects: dict[str, dict[str, Any]]) -> None:
        await self.ctx.store.set_state(SUSPECTS, suspects)

    async def _router_gone(self, name: str, event: Event) -> None:
        suspects = await self._suspects()
        went = went_at(event)
        offs = await self._offs_around(went)
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
                gone_at=went.isoformat(),
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
                pair["watch_until"] = (event.at + REPORTED_WITHIN).isoformat()
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


def went_at(event: Event) -> datetime:
    """When a border router reported gone last announced itself."""
    last = event.data.get("last_seen")
    return datetime.fromisoformat(last) if isinstance(last, str) else event.at
