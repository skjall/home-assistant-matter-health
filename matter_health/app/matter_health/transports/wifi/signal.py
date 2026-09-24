"""Wi-Fi devices far from their access point.

A weak Wi-Fi link makes a device slow to answer and drop off now and then;
it reconnects on its own, so it looks flaky rather than broken. What helps is
an access point closer to it, or the device moved.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ... import kinds
from ...engine import RULES
from ...rules.weak_link import WeakLinkRule


@RULES.register("wifi_signal")
class WifiSignalRule(WeakLinkRule):
    """A finding per Wi-Fi device with a weak link to its access point."""

    name: ClassVar[str] = "wifi_signal"
    listens: ClassVar[frozenset[str]] = frozenset({kinds.WIFI_LINKS})
    effect: ClassVar[str] = "link.weak_wifi"
    fix: ClassVar[str] = "fix.weak_wifi"

    def is_weak(self, link: dict[str, Any]) -> bool:
        """Weak as the transport rated it."""
        return link.get("quality") == "weak"
