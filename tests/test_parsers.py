import pytest

from matter_health import kinds
from matter_health.parsers import PARSERS, Parsed, clean, peer_node_id
from matter_health.parsers.matter_js import MatterJsParser
from matter_health.parsers.openthread import REPEAT_WINDOW_S, OpenThreadParser

PREFIX = "2030-05-04 12:00:00.123 INFO ControllerCommissioningFlow "


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "PaseClient Establish PASE to device fd00:db8::1 port 5540",
            Parsed(kinds.COMMISSIONING_CONTACT),
        ),
        (
            "PaseClient Paired successfully with fd00:db8::1",
            Parsed(kinds.COMMISSIONING_CONTACT_OK),
        ),
        (
            "Start commissioning of node @1:2a into fabric index 1",
            Parsed(kinds.COMMISSIONING_STARTED, "node:42", {"node_id": 42}),
        ),
        (
            "Commissioning step 11.1: ThreadNetworkSetup failed with recoverable "
            "error: Timeout",
            Parsed(
                kinds.COMMISSIONING_RETRY,
                data={
                    "step": "11.1",
                    "name": "ThreadNetworkSetup",
                    "reason": "Timeout",
                },
            ),
        ),
        (
            "Commissioning step 12: Reconnect failed with error: No response",
            Parsed(
                kinds.COMMISSIONING_FAILED,
                data={"step": "12", "name": "Reconnect", "reason": "No response"},
            ),
        ),
        (
            # A failure without a reason carries none.
            "Commissioning step 12: Reconnect failed with error",
            Parsed(
                kinds.COMMISSIONING_FAILED,
                data={"step": "12", "name": "Reconnect"},
            ),
        ),
        (
            "Commissioning step 6.2: OperationalCredentials.Certificates succeeded, "
            "but took too long",
            Parsed(
                kinds.COMMISSIONING_FAILED,
                data={"step": "6.2", "name": "OperationalCredentials.Certificates"},
            ),
        ),
        (
            "Executing commissioning step 1: GetInitialData",
            Parsed(
                kinds.COMMISSIONING_STEP,
                data={"step": "1", "name": "GetInitialData"},
            ),
        ),
        (
            "Attestation finding, rejecting: certificate not trusted",
            Parsed(
                kinds.COMMISSIONING_ATTESTATION_REJECTED,
                data={"reason": "certificate not trusted"},
            ),
        ),
        (
            "Commission failed: Error: Commissioning timed out",
            Parsed(
                kinds.COMMISSIONING_FAILED,
                data={"reason": "Error: Commissioning timed out"},
            ),
        ),
        (
            "Commissioned peer5 as @1:ff",
            Parsed(kinds.COMMISSIONING_COMPLETED, "node:255", {"node_id": 255}),
        ),
        ("Subscription to @1:1e established", None),
    ],
)
def test_matter_js_lines(line: str, expected: Parsed | None) -> None:
    parsed = MatterJsParser().parse(PREFIX + line)

    assert parsed == ([expected] if expected else [])


def test_matter_js_ignores_colour_codes() -> None:
    line = "\x1b[32mINFO\x1b[0m Executing commissioning step \x1b[1m2\x1b[0m: Reconnect"

    assert MatterJsParser().parse(line) == [
        Parsed(kinds.COMMISSIONING_STEP, data={"step": "2", "name": "Reconnect"})
    ]


def test_helpers() -> None:
    assert clean("\x1b[1;31mred\x1b[0m text") == "red text"
    assert peer_node_id("@2:1e") == 30
    assert PARSERS.get("openthread") is OpenThreadParser


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_openthread_reports_each_episode_once() -> None:
    clock = FakeClock()
    parser = OpenThreadParser(clock)
    lost = "\x1b[33m[W] Mle-----------: Leader age timeout\x1b[0m"
    foreign = "[I] Mle-----------: Different partition (peer:2, local:1)"

    assert parser.parse(lost) == [Parsed(kinds.THREAD_LEADER_LOST)]
    clock.now += 1
    assert parser.parse(lost) == []
    # Another kind is its own episode.
    assert parser.parse(foreign) == [Parsed(kinds.THREAD_FOREIGN_PARTITION)]
    # Repeats keep the episode alive: each one moves the window.
    clock.now += REPEAT_WINDOW_S - 1
    assert parser.parse(lost) == []
    clock.now += REPEAT_WINDOW_S
    assert parser.parse(lost) == [Parsed(kinds.THREAD_LEADER_LOST)]
    assert parser.parse("[I] Mle-----------: Role router -> leader") == []


def test_openthread_default_clock() -> None:
    assert OpenThreadParser().parse("Mle-: Leader age timeout") == [
        Parsed(kinds.THREAD_LEADER_LOST)
    ]
