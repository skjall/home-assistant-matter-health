"""Devices behind a Matter bridge.

A bridge makes devices of another network - Zigbee, Z-Wave, Bluetooth Mesh,
a vendor's own radio - look like Matter devices. It is one Matter node, on
whatever transport it uses; each device behind it is an endpoint of that
node carrying the Bridged Device Basic Information cluster. The bridge says
per endpoint whether it can still reach the device (``Reachable``).

A device behind a bridge can drop out while the bridge itself stays
reachable, so the node's own availability says nothing about it. Such a
device is therefore a subject of its own, ``node:<node id>:<endpoint>``, and
comes and goes like any other device. Bridging is not a transport: the
bridge hangs on its transport like every node, its devices hang on the
bridge.
"""

from __future__ import annotations

from typing import Any

#: The Bridged Device Basic Information cluster.
BRIDGED_INFO = 57

#: Its attributes this module reads.
VENDOR_NAME = 1
PRODUCT_NAME = 3
NODE_LABEL = 5
REACHABLE = 17

#: Where the bridged devices of all bridges are kept, by bridge subject.
BRIDGED = "matter.bridged"


def wanted(path: str) -> bool:
    """Whether an attribute describes a device behind a bridge."""
    parts = path.split("/")
    return len(parts) == 3 and parts[1] == str(BRIDGED_INFO) and parts[0] != "0"


def subject(node_id: int, endpoint: int) -> str:
    """Return the subject of a device behind a bridge."""
    return f"node:{node_id}:{endpoint}"


def is_bridged(subject: str) -> bool:
    """Whether a subject is a device behind a bridge rather than a node."""
    return subject.startswith("node:") and subject.count(":") == 2


def bridged(node_id: int, attributes: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the devices behind a node, in endpoint order; none if it bridges none.

    A device whose bridge does not say whether it is reachable counts as
    reachable: the attribute is mandatory, a bridge that leaves it out is
    faulty, not the device.
    """
    endpoints: dict[int, dict[int, Any]] = {}
    for path, value in attributes.items():
        if not wanted(path):
            continue
        raw_endpoint, _, raw_attribute = path.split("/")
        if raw_endpoint.isdigit() and raw_attribute.isdigit():
            endpoints.setdefault(int(raw_endpoint), {})[int(raw_attribute)] = value
    devices = []
    for endpoint, info in sorted(endpoints.items()):
        devices.append(
            {
                "subject": subject(node_id, endpoint),
                "endpoint": endpoint,
                "label": _text(info.get(NODE_LABEL)) or _text(info.get(PRODUCT_NAME)),
                "vendor": _text(info.get(VENDOR_NAME)),
                "reachable": info.get(REACHABLE) is not False,
            }
        )
    return devices


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


async def picture(store: Any, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return network picture entries for the devices behind bridges in ``nodes``.

    Each hangs on its bridge's entry and shares its transport, so it is drawn
    in the bridge's group; ``bridged`` marks that its own way to the bridge -
    a Zigbee or Z-Wave link, say - is not a Matter transport.
    """
    behind: dict[str, list[dict[str, Any]]] = await store.get_state(BRIDGED) or {}
    entries = []
    for node in nodes:
        devices = behind.get(str(node.get("subject")))
        if not devices:
            continue
        node["bridge"] = True
        for device in devices:
            entries.append(
                {
                    "id": f"{node['id']}/{device['endpoint']}",
                    "subject": device["subject"],
                    "transport": node["transport"],
                    "kind": "device",
                    "parent": node["id"],
                    "link": {},
                    "alternatives": 0,
                    "vendor": device.get("vendor"),
                    "bridged": True,
                }
            )
    return entries
