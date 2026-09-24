"""Home Assistant's own Thread radio: interference, a failing stick, no network.

Three things can go wrong with the radio behind Home Assistant's border router:

- The channel is busy. Before sending, a Thread radio listens; when it keeps
  hearing others on the channel - Wi-Fi, Zigbee, a microwave oven - it gives
  up. Now and then that is normal; many times in a few minutes means the
  channel is crowded and messages get lost.
- The border router loses contact with its radio stick or module: the USB
  connection hiccups, the firmware does not match, or the stick runs out of
  memory. While that lasts, nothing goes through it.
- The border router is not part of the Thread network at all: detached means
  it lost its neighbours, disabled means it has no network to join.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from ... import kinds
from ...engine import RULES, Context, Rule
from ...model import Confidence, Event, Finding, Link, Role, Severity

#: The log reports a crowded channel at most every 30 minutes while it lasts;
#: without a report for this long it is over.
BUSY_OVER_AFTER = timedelta(minutes=45)

#: Radio faults that alone mean the stick was lost ...
SEVERE_FAULTS = frozenset({"RCP failure detected", "Failed to communicate with RCP"})

#: ... others happen once in a while and count only when repeated.
FAULTS_AFTER = 3
FAULT_WINDOW = timedelta(minutes=30)

#: A radio without faults for this long is fine again.
FAULT_OVER_AFTER = timedelta(minutes=30)

#: Roles in which the border router takes no part in the network ...
OUTSIDE_ROLES = frozenset({"detached", "disabled"})

#: ... for longer than a new leader takes to be elected.
OUTSIDE_FOR = timedelta(minutes=3)

STATE = "radio.state"


@RULES.register("radio")
class RadioRule(Rule):
    """Reports trouble with Home Assistant's own Thread radio."""

    name: ClassVar[str] = "radio"
    part_of: ClassVar[frozenset[str]] = frozenset({"mesh"})
    listens: ClassVar[frozenset[str]] = frozenset(
        {kinds.THREAD_CHANNEL_BUSY, kinds.THREAD_RADIO_FAULT, kinds.THREAD_STATE}
    )

    def __init__(self, ctx: Context) -> None:
        """Read what is open from the store on first use."""
        super().__init__(ctx)
        self._state: dict[str, Any] | None = None

    async def _load(self) -> dict[str, Any]:
        if self._state is None:
            self._state = dict(await self.ctx.store.get_state(STATE) or {})
        return self._state

    async def _save(self) -> None:
        await self.ctx.store.set_state(STATE, await self._load())

    async def on_event(self, event: Event) -> None:
        """Open or extend a finding."""
        state = await self._load()
        if event.kind == kinds.THREAD_CHANNEL_BUSY:
            busy = state.setdefault("busy", {"since": event.at.isoformat()})
            busy["last"] = event.at.isoformat()
            busy["count"] = int(event.data.get("count", 0))
            busy["minutes"] = int(event.data.get("minutes", 0))
            busy.setdefault("evidence", []).append(event.id)
            await self.ctx.publish(self.busy(busy))
        elif event.kind == kinds.THREAD_RADIO_FAULT:
            await self._fault(state, event)
        else:
            await self._role(state, event)
        await self._save()

    async def _fault(self, state: dict[str, Any], event: Event) -> None:
        reason = str(event.data.get("reason") or "")
        recent = [
            item
            for item in state.get("faults", [])
            if event.at - datetime.fromisoformat(item["at"]) < FAULT_WINDOW
        ]
        recent.append({"at": event.at.isoformat(), "reason": reason, "id": event.id})
        state["faults"] = recent
        fault = state.get("fault")
        if fault is None and (reason in SEVERE_FAULTS or len(recent) >= FAULTS_AFTER):
            fault = state["fault"] = {"since": recent[0]["at"], "evidence": []}
        if fault is not None:
            fault["last"] = event.at.isoformat()
            fault["reason"] = reason
            fault["evidence"] = sorted({*fault["evidence"], *(i["id"] for i in recent)})
            await self.ctx.publish(self.fault(fault))

    async def _role(self, state: dict[str, Any], event: Event) -> None:
        role = event.data.get("role")
        outside = state.get("outside")
        if role in OUTSIDE_ROLES:
            if outside is None or outside["role"] != role:
                state["outside"] = {
                    "since": event.at.isoformat(),
                    "role": role,
                    "id": event.id,
                }
        elif outside is not None:
            state.pop("outside")
            if outside.get("reported"):
                finding = self.outside(outside)
                finding.ended_at = event.at
                await self.ctx.publish(finding)

    async def on_tick(self) -> None:
        """Report a lasting role outside the network; close what is over."""
        state = await self._load()
        now = self.ctx.now()
        busy = state.get("busy")
        if busy and now - datetime.fromisoformat(busy["last"]) >= BUSY_OVER_AFTER:
            state.pop("busy")
            finding = self.busy(busy)
            finding.ended_at = now
            await self.ctx.publish(finding)
        fault = state.get("fault")
        if fault and now - datetime.fromisoformat(fault["last"]) >= FAULT_OVER_AFTER:
            state.pop("fault")
            state.pop("faults", None)
            finding = self.fault(fault)
            finding.ended_at = now
            await self.ctx.publish(finding)
        outside = state.get("outside")
        if (
            outside
            and not outside.get("reported")
            and now - datetime.fromisoformat(outside["since"]) >= OUTSIDE_FOR
        ):
            outside["reported"] = True
            await self.ctx.publish(self.outside(outside))
        await self._save()

    def busy(self, busy: dict[str, Any]) -> Finding:
        """Build the finding for a crowded channel."""
        since = datetime.fromisoformat(busy["since"])
        params = {"count": busy.get("count"), "minutes": busy.get("minutes")}
        return Finding(
            key=f"radio:busy:{busy['since']}",
            rule=self.name,
            severity=Severity.WARNING,
            title="finding.channel_busy.title",
            params=params,
            started_at=since,
            chain=[
                Link(
                    Role.CAUSE,
                    "link.channel_busy_cause",
                    confidence=Confidence.LIKELY,
                ),
                Link(
                    Role.EFFECT,
                    "link.channel_busy",
                    params,
                    at=since,
                    evidence=[i for i in busy.get("evidence", []) if i],
                ),
                Link(Role.IMPACT, "link.channel_busy_impact"),
                Link(Role.FIX, "fix.channel_busy"),
            ],
        )

    def fault(self, fault: dict[str, Any]) -> Finding:
        """Build the finding for a radio the border router keeps losing."""
        since = datetime.fromisoformat(fault["since"])
        return Finding(
            key=f"radio:fault:{fault['since']}",
            rule=self.name,
            severity=Severity.PROBLEM,
            title="finding.radio_fault.title",
            params={"reason": fault.get("reason")},
            started_at=since,
            chain=[
                Link(
                    Role.EFFECT,
                    "link.radio_fault",
                    at=since,
                    evidence=[i for i in fault.get("evidence", []) if i],
                ),
                Link(Role.IMPACT, "link.radio_fault_impact"),
                Link(Role.FIX, "fix.radio_fault"),
            ],
        )

    def outside(self, outside: dict[str, Any]) -> Finding:
        """Build the finding for a border router outside the network."""
        since = datetime.fromisoformat(outside["since"])
        role = str(outside["role"])
        return Finding(
            key=f"radio:role:{outside['since']}",
            rule=self.name,
            severity=Severity.PROBLEM,
            title=f"finding.otbr_{role}.title",
            started_at=since,
            chain=[
                Link(
                    Role.EFFECT,
                    f"link.otbr_{role}",
                    at=since,
                    evidence=[outside["id"]] if outside.get("id") else [],
                ),
                Link(Role.IMPACT, "link.otbr_outside_impact"),
                Link(Role.FIX, f"fix.otbr_{role}"),
            ],
        )
