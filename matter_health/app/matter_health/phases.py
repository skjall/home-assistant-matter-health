"""The stages of adding a Matter device, as a user would understand them.

matter.js names about twenty commissioning steps. For the user they fall into
seven stages, each with its own typical causes and remedies. A step name maps
to a stage by its prefix; an unknown step falls into the stage after the last
known one, which is where it most likely belongs.
"""

from __future__ import annotations

#: The stages in the order they happen.
PHASES: tuple[str, ...] = (
    "first_contact",  # PASE: phone or server proves it knows the setup code
    "prepare",  # fail-safe, regional settings, time
    "authenticity",  # device attestation: is this a certified product
    "keys",  # operational certificate and access rights for this home
    "network",  # Thread or Wi-Fi credentials, joining the network
    "find_again",  # reaching the device on its operational address (CASE)
    "finish",  # commissioning complete, label written
)

#: matter.js step name (or its prefix before the first dot) -> stage.
STEP_PHASE: dict[str, str] = {
    "GetInitialData": "prepare",
    "GeneralCommissioning.ArmFailsafe": "prepare",
    "GeneralCommissioning.ConfigureRegulatoryInformation": "prepare",
    "TimeSynchronization": "prepare",
    "OperationalCredentials.DeviceAttestation": "authenticity",
    "OperationalCredentials.Certificates": "keys",
    "AccessControl": "keys",
    "NetworkCommissioning": "network",
    "ThreadNetworkSetup": "network",
    "WifiNetworkSetup": "network",
    "Reconnect": "find_again",
    "GeneralCommissioning.Complete": "finish",
    "OperationalCredentials.UpdateFabricLabel": "finish",
}


def phase_of(step_name: str | None) -> str:
    """Return the stage of a matter.js step; unknown steps count as "prepare"."""
    if not step_name:
        return "first_contact"
    if step_name in STEP_PHASE:
        return STEP_PHASE[step_name]
    prefix = step_name.split(".", 1)[0]
    return STEP_PHASE.get(prefix, "prepare")


def later(first: str, second: str) -> str:
    """Whichever of two stages comes later."""
    return first if PHASES.index(first) >= PHASES.index(second) else second
