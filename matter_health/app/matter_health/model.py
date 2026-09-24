"""The two things everything else passes around: events and findings.

An event is a fact a source observed at a point in time. A finding is what a
rule concluded from events, told as a chain a user can follow from cause to
fix. Neither carries user-facing words: a finding holds translation keys and
the values to put into them, and the UI does the wording in the user's
language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """How much a finding asks of the user."""

    INFO = "info"
    WARNING = "warning"
    PROBLEM = "problem"


class Confidence(StrEnum):
    """How sure a rule is that a link in the chain is what really happened.

    A cause is often inferred from timing alone - something switched off and a
    moment later a device vanished. The UI says "probably" for those instead of
    presenting a guess as a fact.
    """

    CERTAIN = "certain"
    LIKELY = "likely"
    POSSIBLE = "possible"


class Role(StrEnum):
    """The part a link plays in the story of a finding."""

    CAUSE = "cause"
    EFFECT = "effect"
    IMPACT = "impact"
    FIX = "fix"


@dataclass(frozen=True, slots=True)
class Event:
    """Something a source observed.

    ``subject`` identifies what the event is about in a form that stays the
    same across restarts - ``node:<id>``, ``br:<extended address>``,
    ``entity:<entity_id>`` - so rules can follow one thing through time.
    """

    kind: str
    at: datetime
    source: str
    subject: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


@dataclass(slots=True)
class Link:
    """One step of a finding's chain.

    ``key`` names a translation; ``params`` fills its placeholders. Names in
    ``params`` are already resolved to what the user calls the thing.
    ``evidence`` points at the events the link was drawn from, so the UI can
    show the raw facts behind every sentence.
    """

    role: Role
    key: str
    params: dict[str, Any] = field(default_factory=dict)
    at: datetime | None = None
    confidence: Confidence = Confidence.CERTAIN
    evidence: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage and the API."""
        return {
            "role": self.role.value,
            "key": self.key,
            "params": self.params,
            "at": self.at.isoformat() if self.at else None,
            "confidence": self.confidence.value,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Link:
        """Rebuild a link written by :meth:`as_dict`."""
        return cls(
            role=Role(raw["role"]),
            key=raw["key"],
            params=raw.get("params", {}),
            at=datetime.fromisoformat(raw["at"]) if raw.get("at") else None,
            confidence=Confidence(raw.get("confidence", Confidence.CERTAIN)),
            evidence=list(raw.get("evidence", [])),
        )


@dataclass(slots=True)
class Finding:
    """What a rule concluded, told from cause to fix.

    ``key`` makes a finding unique: a rule that sees the same situation again
    updates its finding instead of adding a second one. ``ended_at`` is empty
    while the situation lasts.
    """

    key: str
    rule: str
    severity: Severity
    title: str
    started_at: datetime
    chain: list[Link]
    params: dict[str, Any] = field(default_factory=dict)
    subjects: list[str] = field(default_factory=list)
    ended_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage and the API."""
        return {
            "key": self.key,
            "rule": self.rule,
            "severity": self.severity.value,
            "title": self.title,
            "params": self.params,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "subjects": self.subjects,
            "chain": [link.as_dict() for link in self.chain],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Finding:
        """Rebuild a finding written by :meth:`as_dict`."""
        return cls(
            key=raw["key"],
            rule=raw["rule"],
            severity=Severity(raw["severity"]),
            title=raw["title"],
            params=raw.get("params", {}),
            started_at=datetime.fromisoformat(raw["started_at"]),
            ended_at=(
                datetime.fromisoformat(raw["ended_at"]) if raw.get("ended_at") else None
            ),
            subjects=list(raw.get("subjects", [])),
            chain=[Link.from_dict(link) for link in raw.get("chain", [])],
        )
