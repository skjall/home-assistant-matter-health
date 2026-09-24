"""The host does not pass IPv6 between the home network and the Thread mesh.

Thread devices live in an IPv6 network of their own. For them to answer
anything outside the mesh - a phone adding them, Home Assistant through a
border router of another brand - the host of Home Assistant's own border
router has to forward IPv6. Home Assistant OS does that only with IPv6 enabled
for Docker, and some versions switch it off regardless. Existing devices often
keep working, because Home Assistant reaches them directly; adding new ones
stops at "checking connectivity to the Thread network".

The border router add-on says so in its log when it starts; the Supervisor
says whether Docker has IPv6. Either is enough.

IPv6 switched off on the host, or set by hand, breaks Matter in a similar
way: the routes into the mesh arrive as router advertisements, which a host
without automatic IPv6 does not process.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, ClassVar

from .. import kinds
from ..config import OTBR_SLUG
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity

#: The warning comes right before the agent starts; an agent started later
#: than this after the last warning started without it.
WARNING_PRECEDES_START = timedelta(minutes=2)

#: Written by the system source.
NETWORK = "system.network"
VERSIONS = "system.versions"

#: What this rule keeps: the last forwarding warning and open findings.
STATE = "host.state"

Builder = Callable[[datetime], Awaitable[Finding]]


@RULES.register("host")
class HostRule(Rule):
    """Reports IPv6 settings of the host that keep Thread devices out."""

    name: ClassVar[str] = "host"
    listens: ClassVar[frozenset[str]] = frozenset(
        {kinds.HOST_FORWARDING_OFF, kinds.THREAD_AGENT_STARTED}
    )

    def __init__(self, ctx: Context) -> None:
        """Read what is open from the store on first use."""
        super().__init__(ctx)
        self._state: dict[str, Any] | None = None

    async def _load(self) -> dict[str, Any]:
        if self._state is None:
            self._state = dict(await self.ctx.store.get_state(STATE) or {})
            self._state.setdefault("open", {})
        return self._state

    async def on_event(self, event: Event) -> None:
        """Note a forwarding warning, or a start without one."""
        state = await self._load()
        if event.kind == kinds.HOST_FORWARDING_OFF:
            state["warned"] = event.at.isoformat()
            state["warning"] = event.id
        elif "warned" in state:
            warned = datetime.fromisoformat(state["warned"])
            if event.at - warned > WARNING_PRECEDES_START:
                state.pop("warned")
                state.pop("warning", None)
        await self.evaluate()

    async def on_tick(self) -> None:
        """Compare the settings with what is reported."""
        await self.evaluate()

    async def evaluate(self) -> None:
        """Open or close each finding according to the current facts."""
        state = await self._load()
        network: dict[str, Any] = await self.ctx.store.get_state(NETWORK) or {}
        versions: dict[str, Any] = await self.ctx.store.get_state(VERSIONS) or {}
        docker_off = bool(
            network.get("haos")
            and OTBR_SLUG in versions
            and "docker_ipv6" in network
            and network["docker_ipv6"] is not True
        )
        warned = state.get("warned")

        async def forwarding(since: datetime) -> Finding:
            return self.forwarding(since, docker_off, warned, state.get("warning"))

        method = network.get("ipv6_method")

        async def ipv6(since: datetime) -> Finding:
            return self.ipv6(since, str(method), network.get("interface"))

        await self._set(state, "forwarding", docker_off or bool(warned), forwarding)
        await self._set(state, "ipv6", method in ("disabled", "static"), ipv6)
        await self.ctx.store.set_state(STATE, state)

    async def _set(
        self, state: dict[str, Any], name: str, active: bool, build: Builder
    ) -> None:
        opened: dict[str, str] = state["open"]
        now = self.ctx.now()
        if active and name not in opened:
            opened[name] = now.isoformat()
            await self.ctx.publish(await build(now))
        elif not active and name in opened:
            built = await build(datetime.fromisoformat(opened.pop(name)))
            # The facts that explained it are gone now; close it as it was told.
            finding = await self.ctx.store.finding(built.key) or built
            finding.ended_at = now
            await self.ctx.publish(finding)

    def forwarding(
        self,
        since: datetime,
        docker_off: bool,
        warned: str | None,
        warning: int | None,
    ) -> Finding:
        """Build the finding for a host that does not forward IPv6."""
        chain: list[Link] = []
        if docker_off:
            chain.append(Link(Role.CAUSE, "link.docker_ipv6_off"))
        if warned:
            chain.append(
                Link(
                    Role.CAUSE,
                    "link.forwarding_off_seen",
                    at=datetime.fromisoformat(warned),
                    evidence=[warning] if warning else [],
                )
            )
        chain.append(Link(Role.EFFECT, "link.forwarding_off", at=since))
        chain.append(Link(Role.IMPACT, "link.forwarding_off_impact"))
        chain.append(
            Link(
                Role.FIX,
                "fix.enable_docker_ipv6" if docker_off else "fix.forwarding_update_os",
            )
        )
        return Finding(
            key=f"host:forwarding:{since.isoformat()}",
            rule=self.name,
            severity=Severity.PROBLEM,
            title="finding.forwarding_off.title",
            started_at=since,
            chain=chain,
        )

    def ipv6(self, since: datetime, method: str, interface: str | None) -> Finding:
        """Build the finding for a host whose IPv6 is off or set by hand."""
        disabled = method == "disabled"
        params = {"interface": interface or "-"}
        chain = [
            Link(
                Role.CAUSE,
                "link.ipv6_disabled" if disabled else "link.ipv6_static",
                params,
                at=since,
            ),
            Link(
                Role.EFFECT,
                "link.ipv6_no_routes",
                confidence=Confidence.CERTAIN if disabled else Confidence.POSSIBLE,
            ),
            Link(Role.IMPACT, "link.ipv6_impact"),
            Link(Role.FIX, "fix.ipv6_automatic", params),
        ]
        return Finding(
            key=f"host:ipv6:{since.isoformat()}",
            rule=self.name,
            severity=Severity.PROBLEM if disabled else Severity.WARNING,
            title="finding.ipv6_off.title" if disabled else "finding.ipv6_static.title",
            params=params,
            started_at=since,
            chain=chain,
        )
