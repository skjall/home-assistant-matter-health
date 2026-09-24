"""Adding a device: how far it got, why it stopped, what to try.

The Matter Server logs every commissioning step. The user only sees "failed"
on the phone. This rule collects the steps of one attempt, notes the stage the
attempt reached and, when it failed, explains that stage in everyday words.

A failure during a disturbed mesh is almost never the device's fault: it joined
a network that at that moment could not carry it. That cause wins over the
stage-specific guesses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar

from .. import kinds
from ..engine import RULES, Context, Rule
from ..model import Confidence, Event, Finding, Link, Role, Severity
from ..phases import phase_of
from .common import mesh_trouble, seconds

#: Commissioning lines further apart than this belong to different attempts.
ATTEMPT_GAP = timedelta(minutes=2)

#: An attempt without a line for this long has ended, whether or not the log
#: said so - the phone gives up after about this long.
GIVEN_UP_AFTER = timedelta(minutes=3)

#: The Matter Server writes a step's failure and then the overall result; both
#: belong to the same attempt, so a failed attempt stays open this long.
FAILURE_SETTLES = timedelta(seconds=30)

#: Mesh trouble this close to an attempt counts as the likely reason.
MESH_WINDOW = timedelta(minutes=2)


@dataclass
class Attempt:
    """One try at adding a device."""

    start: datetime
    last: datetime
    phase: str = "first_contact"
    step: str | None = None
    node_id: int | None = None
    reasons: list[str] = field(default_factory=list)
    retries: int = 0
    failed: bool = False
    attestation: bool = False
    completed: bool = False
    evidence: list[int] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable identity of the finding for this attempt."""
        return f"pairing:{self.start.isoformat()}"


@RULES.register("pairing")
class PairingRule(Rule):
    """Follows each attempt to add a device and explains failures."""

    name: ClassVar[str] = "pairing"
    listens: ClassVar[frozenset[str]] = frozenset(
        {
            kinds.COMMISSIONING_CONTACT,
            kinds.COMMISSIONING_CONTACT_OK,
            kinds.COMMISSIONING_STARTED,
            kinds.COMMISSIONING_STEP,
            kinds.COMMISSIONING_RETRY,
            kinds.COMMISSIONING_FAILED,
            kinds.COMMISSIONING_ATTESTATION_REJECTED,
            kinds.COMMISSIONING_COMPLETED,
        }
    )

    def __init__(self, ctx: Context) -> None:
        """No attempt is open at start."""
        super().__init__(ctx)
        self.attempt: Attempt | None = None

    async def on_event(self, event: Event) -> None:
        """Add the event to its attempt; report when the attempt ends."""
        attempt = self.attempt
        starts_new = event.kind == kinds.COMMISSIONING_CONTACT and (
            attempt is None or attempt.failed or attempt.completed
        )
        if attempt is None or starts_new or event.at - attempt.last > ATTEMPT_GAP:
            if attempt and not attempt.completed and not attempt.failed:
                attempt.failed = True
                await self.report(attempt)
            attempt = self.attempt = Attempt(start=event.at, last=event.at)
        attempt.last = event.at
        if event.id:
            attempt.evidence.append(event.id)
        self.absorb(attempt, event)
        if attempt.completed or attempt.failed:
            await self.report(attempt)
        if attempt.completed:
            self.attempt = None

    def absorb(self, attempt: Attempt, event: Event) -> None:
        """Update the attempt with what one event says."""
        data: dict[str, Any] = event.data
        if data.get("node_id") is not None:
            attempt.node_id = int(data["node_id"])
        if event.kind in (kinds.COMMISSIONING_STEP, kinds.COMMISSIONING_FAILED) and (
            data.get("name")
        ):
            attempt.step = str(data["name"])
            attempt.phase = phase_of(attempt.step)
        elif event.kind == kinds.COMMISSIONING_CONTACT_OK:
            attempt.phase = "prepare"
        if data.get("reason"):
            attempt.reasons.append(str(data["reason"]))
        if event.kind == kinds.COMMISSIONING_RETRY:
            attempt.retries += 1
        elif event.kind == kinds.COMMISSIONING_ATTESTATION_REJECTED:
            attempt.attestation = True
            attempt.phase = "authenticity"
        elif event.kind == kinds.COMMISSIONING_FAILED:
            attempt.failed = True
        elif event.kind == kinds.COMMISSIONING_COMPLETED:
            attempt.completed = True
            attempt.phase = "finish"

    async def on_tick(self) -> None:
        """Close an attempt that went silent, or a failure that settled."""
        attempt = self.attempt
        if attempt is None:
            return
        quiet = self.ctx.now() - attempt.last
        if attempt.failed and quiet >= FAILURE_SETTLES:
            self.attempt = None
        elif not attempt.failed and quiet >= GIVEN_UP_AFTER:
            attempt.failed = True
            await self.report(attempt)
            self.attempt = None

    async def report(self, attempt: Attempt) -> None:
        """Publish the finding for an attempt."""
        await self.ctx.publish(await self.describe(attempt))

    async def describe(self, attempt: Attempt) -> Finding:
        """Build the finding for an attempt, success or failure."""
        device = (
            self.ctx.names.get(f"node:{attempt.node_id}")
            if attempt.node_id is not None
            else None
        )
        details: dict[str, Any] = {
            "phase": attempt.phase,
            "step": attempt.step,
            "node_id": attempt.node_id,
            "reasons": attempt.reasons,
            "retries": attempt.retries,
        }
        if attempt.completed:
            return Finding(
                key=attempt.key,
                rule=self.name,
                severity=Severity.INFO,
                title="finding.pairing_ok.title",
                params={"device": device, **details},
                started_at=attempt.start,
                ended_at=attempt.last,
                chain=[
                    Link(
                        Role.EFFECT,
                        "link.pairing_ok",
                        {
                            "device": device,
                            "seconds": seconds(attempt.start, attempt.last),
                        },
                        at=attempt.last,
                        evidence=list(attempt.evidence),
                    )
                ],
                subjects=[f"node:{attempt.node_id}"] if attempt.node_id else [],
            )

        trouble = await mesh_trouble(
            self.ctx, attempt.start - MESH_WINDOW, attempt.last + MESH_WINDOW
        )
        chain: list[Link] = []
        if trouble:
            chain.append(
                Link(
                    Role.CAUSE,
                    "link.mesh_disturbed_during_pairing",
                    at=trouble[0].at,
                    confidence=Confidence.LIKELY,
                    evidence=[event.id for event in trouble if event.id],
                )
            )
        else:
            chain.append(
                Link(
                    Role.CAUSE,
                    f"cause.pairing.{attempt.phase}",
                    confidence=Confidence.POSSIBLE,
                )
            )
        chain.append(
            Link(
                Role.EFFECT,
                "link.pairing_stopped",
                {"phase": attempt.phase},
                at=attempt.last,
                evidence=list(attempt.evidence),
            )
        )
        chain.append(Link(Role.IMPACT, "link.pairing_failed_impact"))
        if trouble:
            chain.append(Link(Role.FIX, "fix.pairing_wait_for_mesh"))
        chain.append(Link(Role.FIX, f"fix.pairing.{attempt.phase}"))
        chain.append(Link(Role.FIX, "fix.pairing_reset_and_retry"))
        return Finding(
            key=attempt.key,
            rule=self.name,
            severity=Severity.PROBLEM,
            title="finding.pairing_failed.title",
            params={"device": device, **details},
            started_at=attempt.start,
            ended_at=attempt.last,
            chain=chain,
            subjects=[f"node:{attempt.node_id}"] if attempt.node_id else [],
        )
