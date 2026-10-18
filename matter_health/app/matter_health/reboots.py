"""How often a device restarts.

Every Matter device counts its restarts (General Diagnostics, ``0/51/1``),
over its whole life. That total says little: a device that once restarted
hundreds of times keeps its count after the cause is gone. What matters is
how often it restarts now, so the add-on keeps the last count of every
device and tells each rise as an event, with the reason the device gives
for its last restart (``0/51/4``). Rules count those events over a window.

A count lower than the one kept means the device was reset or paired anew;
it becomes the new starting point, not a restart.
"""

from __future__ import annotations

from typing import Any

#: The restart count and the reason for the last restart.
COUNT = "0/51/1"
REASON = "0/51/4"

#: The last count of every device, by subject.
COUNTS = "matter.reboot_counts"

#: The reasons a device gives, as the specification names them.
REASONS = {
    0: "unspecified",
    1: "power_on",
    2: "brown_out",
    3: "software_watchdog",
    4: "hardware_watchdog",
    5: "software_update",
    6: "software_reset",
}


def wanted(path: str) -> bool:
    """Whether an attribute tells about restarts."""
    return path in (COUNT, REASON)


def rises(
    known: dict[str, int], nodes: dict[int, dict[str, Any]]
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Compare the counts with those kept; return the new counts and the rises.

    A device seen for the first time only sets its starting point.
    """
    counts = dict(known)
    found = []
    for node_id, attributes in sorted(nodes.items()):
        count = attributes.get(COUNT)
        if not isinstance(count, int) or isinstance(count, bool):
            continue
        subject = f"node:{node_id}"
        before = counts.get(subject)
        counts[subject] = count
        if before is not None and count > before:
            reason = attributes.get(REASON)
            found.append(
                {
                    "subject": subject,
                    "count": count - before,
                    "total": count,
                    "reason": REASONS.get(reason) if isinstance(reason, int) else None,
                }
            )
    return counts, found
