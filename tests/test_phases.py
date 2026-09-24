import pytest

from matter_health.phases import PHASES, later, phase_of


@pytest.mark.parametrize(
    ("step", "phase"),
    [
        (None, "first_contact"),
        ("", "first_contact"),
        ("GetInitialData", "prepare"),
        ("OperationalCredentials.DeviceAttestation", "authenticity"),
        ("OperationalCredentials.Certificates", "keys"),
        ("AccessControl", "keys"),
        # Known by prefix only.
        ("ThreadNetworkSetup.Connect", "network"),
        ("Reconnect.Operational", "find_again"),
        ("GeneralCommissioning.Complete", "finish"),
        # An unknown general step is not "finish".
        ("GeneralCommissioning.Unknown", "prepare"),
        ("SomethingNew", "prepare"),
    ],
)
def test_phase_of(step: str | None, phase: str) -> None:
    assert phase_of(step) == phase


def test_later_picks_the_stage_further_along() -> None:
    assert later("keys", "prepare") == "keys"
    assert later("prepare", "network") == "network"
    assert later("finish", "finish") == "finish"
    assert PHASES[0] == "first_contact"
    assert PHASES[-1] == "finish"
