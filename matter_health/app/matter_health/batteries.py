"""What a battery device last said about its battery.

The Power Source cluster, on whichever endpoint a device puts it, tells the
battery's voltage and, where the device knows it, how many cells it holds.
The Matter Server keeps the last value of a device that went away, so the
voltage is known even for a device that no longer answers - which is when it
matters: a battery that sagged under load stops a device as surely as a
network that lost it, and taking the battery out is the moment to put a
fresh one in.
"""

from __future__ import annotations

from typing import Any

#: The Power Source cluster.
POWER_SOURCE = 47

#: Its attributes this module reads.
VOLTAGE = 11
QUANTITY = 27

#: Where the batteries of all devices are kept, by subject.
BATTERIES = "matter.batteries"

#: A single cell below this, in millivolts, is worn: a fresh one gives about
#: 1500 mV, and many devices stop working under load well before empty.
WEAK_CELL_MV = 1300

#: More than this can only be several cells or a coin cell of about 3 V.
ONE_CELL_MAX_MV = 1800


def wanted(path: str) -> bool:
    """Whether an attribute tells about a battery."""
    parts = path.split("/")
    return (
        len(parts) == 3
        and parts[1] == str(POWER_SOURCE)
        and parts[2] in (str(VOLTAGE), str(QUANTITY))
    )


def battery(attributes: dict[str, Any]) -> dict[str, Any] | None:
    """Return a device's battery: its voltage and, where told, per cell.

    Without a cell count, a voltage low enough for one cell is taken as one;
    a higher one stays unsplit, since two cells and a coin cell look alike.
    """
    millivolts = None
    cells = None
    for path, value in attributes.items():
        if not wanted(path) or not isinstance(value, int) or value <= 0:
            continue
        if path.endswith(f"/{VOLTAGE}"):
            millivolts = value
        else:
            cells = value
    if millivolts is None:
        return None
    if cells is None and millivolts <= ONE_CELL_MAX_MV:
        cells = 1
    per_cell = millivolts // cells if cells else None
    return {
        "millivolts": millivolts,
        "cells": cells,
        "cell_millivolts": per_cell,
        "weak": per_cell is not None and per_cell < WEAK_CELL_MV,
    }


def batteries(nodes: dict[int, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return the battery of every device that reports one, by subject."""
    found = {}
    for node_id, attributes in sorted(nodes.items()):
        known = battery(attributes)
        if known is not None:
            found[f"node:{node_id}"] = known
    return found
