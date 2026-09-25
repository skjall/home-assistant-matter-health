from datetime import UTC, datetime, timedelta

from matter_health.model import Finding, Severity
from matter_health.rules.offline import OfflineRule
from matter_health.rules.pairing import PairingRule
from matter_health.stories import stories
from matter_health.transports.thread.border_router import BorderRouterRule
from matter_health.transports.thread.mesh import MeshRule
from matter_health.transports.thread.partitions import PartitionRule
from matter_health.transports.thread.signal import SignalRule

T0 = datetime(2030, 1, 1, 20, 0, tzinfo=UTC)
RULES = (
    MeshRule,
    BorderRouterRule,
    PairingRule,
    OfflineRule,
    SignalRule,
    PartitionRule,
)


def finding(
    key: str,
    rule: str,
    minutes: float,
    severity: Severity = Severity.WARNING,
    lasted: float | None = 1,
) -> Finding:
    start = T0 + timedelta(minutes=minutes)
    return Finding(
        key=key,
        rule=rule,
        severity=severity,
        title="t",
        started_at=start,
        chain=[],
        ended_at=start + timedelta(minutes=lasted) if lasted is not None else None,
    )


def test_consequences_join_the_mesh_story() -> None:
    mesh = finding("mesh:a", "mesh", 0)
    found = [
        mesh,
        finding("border_router:tv", "border_router", 3),
        finding("pairing:a", "pairing", -1, Severity.PROBLEM),
        finding("offline:plug", "offline", 5.5),
        # Outside the reach of the story.
        finding("pairing:b", "pairing", -4, Severity.PROBLEM),
        finding("offline:late", "offline", 7),
        # Not consequences, whatever their time.
        finding("pairing:ok", "pairing", 0, Severity.INFO),
        finding("switched_border_router:tv:entity:x", "border_router", 3),
        finding("signal:node:7", "signal", 0),
        finding("unknown:x", "retired_rule", 0),
    ]

    assert stories(found, RULES, T0 + timedelta(hours=1)) == {
        "border_router:tv": "mesh:a",
        "pairing:a": "mesh:a",
        "offline:plug": "mesh:a",
    }


def test_an_open_story_reaches_until_now() -> None:
    found = [
        finding("mesh:a", "mesh", 0, lasted=None),
        finding("offline:plug", "offline", 30),
    ]

    assert stories(found, RULES, T0 + timedelta(minutes=40)) == {
        "offline:plug": "mesh:a"
    }


def test_the_earliest_matching_story_wins() -> None:
    found = [
        finding("mesh:b", "mesh", 2),
        finding("mesh:a", "mesh", 0),
        finding("border_router:tv", "border_router", 2.5),
    ]

    assert stories(found, RULES, T0) == {"border_router:tv": "mesh:a"}


def test_a_story_within_a_larger_one_hands_its_parts_up() -> None:
    found = [
        finding("partitions:a", "partitions", 0, Severity.PROBLEM, lasted=None),
        finding("mesh:a", "mesh", -1),
        finding("border_router:tv", "border_router", 1),
    ]

    assert stories(found, RULES, T0 + timedelta(hours=1)) == {
        "mesh:a": "partitions:a",
        "border_router:tv": "partitions:a",
    }
