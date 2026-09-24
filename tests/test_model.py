from datetime import timedelta

from conftest import T0

from matter_health.model import Confidence, Finding, Link, Role, Severity


def test_link_round_trip_keeps_every_field() -> None:
    link = Link(
        Role.CAUSE,
        "link.power_off",
        {"switch": "Media Plug"},
        at=T0,
        confidence=Confidence.LIKELY,
        evidence=[3, 4],
    )

    raw = link.as_dict()

    assert raw["role"] == "cause"
    assert raw["at"] == T0.isoformat()
    assert Link.from_dict(raw) == link


def test_link_from_minimal_dict_uses_defaults() -> None:
    link = Link.from_dict({"role": "fix", "key": "fix.weak_signal"})

    assert link.params == {}
    assert link.at is None
    assert link.confidence is Confidence.CERTAIN
    assert link.evidence == []
    assert link.as_dict()["at"] is None


def test_finding_round_trip_open_and_closed() -> None:
    finding = Finding(
        key="offline:node:7",
        rule="offline",
        severity=Severity.WARNING,
        title="finding.device_unreachable.title",
        started_at=T0,
        chain=[Link(Role.EFFECT, "link.device_unreachable", at=T0)],
        params={"device": "Living Room Plug"},
        subjects=["node:7"],
    )

    assert Finding.from_dict(finding.as_dict()) == finding

    finding.ended_at = T0 + timedelta(minutes=12)
    raw = finding.as_dict()
    assert raw["ended_at"] == finding.ended_at.isoformat()
    assert Finding.from_dict(raw) == finding


def test_finding_from_minimal_dict() -> None:
    finding = Finding.from_dict(
        {
            "key": "k",
            "rule": "r",
            "severity": "info",
            "title": "t",
            "started_at": T0.isoformat(),
        }
    )

    assert finding.chain == []
    assert finding.subjects == []
    assert finding.params == {}
    assert finding.ended_at is None
