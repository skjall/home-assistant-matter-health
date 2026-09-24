"""The Matter Server forgot every device.

The Matter Server keeps the keys of every paired device in its own storage.
If that storage is lost - a power cut at the wrong moment, the add-on removed
and installed again - the server starts empty. The devices still exist and
still trust the old keys; nothing can reach them until the storage is back.
A backup of the add-on brings them all back at once; pairing each device anew
is the slow way.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Event, Finding, Link, Role, Severity
from .common import cause_or_update

#: Back to this share of the devices it knew, the server has its data again.
RESTORED_AT = 0.8

STATE = "server.lost"


@RULES.register("server")
class ServerRule(Rule):
    """Reports a Matter Server that lost its devices."""

    name: ClassVar[str] = "server"
    listens: ClassVar[frozenset[str]] = frozenset({kinds.MATTER_NODES_LOST})

    def __init__(self, ctx: Context) -> None:
        """Read what is open from the store on first use."""
        super().__init__(ctx)
        self._lost: dict[str, Any] | None = None

    async def _load(self) -> dict[str, Any]:
        if self._lost is None:
            self._lost = dict(await self.ctx.store.get_state(STATE) or {})
        return self._lost

    async def on_event(self, event: Event) -> None:
        """Open the finding."""
        lost = await self._load()
        if lost:
            return
        lost.update(
            {
                "since": event.at.isoformat(),
                "previous": int(event.data.get("previous", 0)),
                "event": event.id,
            }
        )
        await self.ctx.store.set_state(STATE, lost)
        await self.ctx.publish(await self.describe(lost))

    async def on_tick(self) -> None:
        """Close the finding once most devices are known again."""
        lost = await self._load()
        if not lost:
            return
        nodes = await self.ctx.store.get_state("matter.nodes") or {}
        if int(nodes.get("total") or 0) >= RESTORED_AT * lost["previous"]:
            finding = await self.describe(lost)
            finding.ended_at = self.ctx.now()
            lost.clear()
            await self.ctx.store.set_state(STATE, lost)
            await self.ctx.publish(finding)

    async def describe(self, lost: dict[str, Any]) -> Finding:
        """Build the finding for a server without its devices."""
        since = datetime.fromisoformat(lost["since"])
        params = {"count": lost["previous"]}
        chain: list[Link] = []
        await cause_or_update(self.ctx, chain, since)
        chain += [
            Link(
                Role.EFFECT,
                "link.server_forgot",
                params,
                at=since,
                evidence=[lost["event"]] if lost.get("event") else [],
            ),
            Link(Role.IMPACT, "link.server_forgot_impact"),
            Link(Role.FIX, "fix.server_restore_backup"),
        ]
        return Finding(
            key=f"server:lost:{lost['since']}",
            rule=self.name,
            severity=Severity.PROBLEM,
            title="finding.server_forgot.title",
            params=params,
            started_at=since,
            chain=chain,
        )
