"""Thread devices that hear their nearest relaying neighbour only faintly.

A battery device drains faster on a weak link, because it repeats itself.
The fix is almost always physical: another mains-powered Thread device in
between, or the device a little closer.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ... import kinds
from ...engine import RULES
from ...model import Link, Role
from ...rules.weak_link import WeakLinkRule

#: Below this a link is unreliable in practice; Thread radios stop decoding
#: around -100 dBm and need a margin for interference.
WEAK_RSSI = -85

#: Link quality 0-3 as Thread reports it; 1 means most frames need repeats.
WEAK_LQI = 1

#: Battery devices that only wake up now and then.
SLEEPY_ROLES = frozenset({"sleepy_end_device", "end_device"})


def is_weak(link: dict[str, Any]) -> bool:
    """Whether a device's best link is too weak to rely on."""
    if link.get("strength") == "weak":
        return True
    rssi, lqi = link.get("rssi"), link.get("lqi")
    return (rssi is not None and rssi <= WEAK_RSSI) or (
        lqi is not None and lqi <= WEAK_LQI
    )


@RULES.register("signal")
class SignalRule(WeakLinkRule):
    """A finding per Thread device with a weak way into the mesh."""

    name: ClassVar[str] = "signal"
    listens: ClassVar[frozenset[str]] = frozenset({kinds.THREAD_TOPOLOGY})
    fix: ClassVar[str] = "fix.weak_signal"

    def is_weak(self, link: dict[str, Any]) -> bool:
        """Weak by the Matter Server's rating, the signal or the link quality."""
        return is_weak(link)

    def impacts(self, link: dict[str, Any]) -> list[Link]:
        """Add that a battery device also runs down faster."""
        if link.get("role") in SLEEPY_ROLES:
            return [Link(Role.IMPACT, "link.weak_signal_battery")]
        return []
