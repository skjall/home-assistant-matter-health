"""Who hangs on whom: the Thread mesh reduced to a tree a person can read.

The Matter Server reports every radio link it knows of. In a home with a few
dozen mains-powered devices that is hundreds of links, most of them between
routers that could all reach each other. Drawn as they are, they make a ball
of lines that says nothing.

What a person needs is the way each device takes into the network:

- A battery device talks only to its parent, one mains-powered device nearby.
  That link is reported with a path cost of 0.
- A device that relays for others picks the cheapest way to a border router.
  Thread prices a link by its quality: a good one costs 1, a poor one up to 6.
  The same sum over the links gives each relaying device its path.

Every device then has exactly one line upwards, and the lines that were left
out are not lost: each relaying device keeps the number of other relaying
neighbours it could switch to. A device with none has no way around a failure.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any

#: Thread's cost of a link by its quality (0 = unusable, 3 = good). Unknown
#: quality counts like the poorest usable link.
LINK_COST = {3: 1, 2: 2, 1: 4, 0: 6}
UNKNOWN_COST = 6

#: A neighbour worth switching to has at least this link quality.
USABLE_LQI = 2

#: Home Assistant itself: the root every border router hangs on, reached
#: through the home network rather than by radio.
ROOT = "home"


@dataclass
class Link:
    """One radio link as the two ends measured it."""

    parent_link: bool = False
    router_link: bool = False
    #: Measurements by each end of the other, keyed by the measuring end.
    measured: dict[str, dict[str, Any]] = field(default_factory=dict)

    def seen_by(self, node: str) -> dict[str, Any]:
        """Return what ``node`` measured of the other end, else what the other saw."""
        if node in self.measured:
            return self.measured[node]
        return next(iter(self.measured.values()), {})

    @property
    def cost(self) -> int:
        """Thread's price of this link; the worse direction decides."""
        qualities = [m.get("lqi") for m in self.measured.values()]
        known = [q for q in qualities if isinstance(q, int)]
        return LINK_COST.get(min(known), UNKNOWN_COST) if known else UNKNOWN_COST


def _links(topology: dict[str, Any]) -> dict[str, dict[str, Link]]:
    """Every node's links, by neighbour."""
    links: dict[str, dict[str, Link]] = {}
    for raw in topology.get("connections", []):
        source, target = str(raw["source"]), str(raw["target"])
        link = links.setdefault(source, {}).get(target) or Link()
        cost = raw.get("path_cost")
        link.parent_link = link.parent_link or cost == 0
        link.router_link = link.router_link or (isinstance(cost, int) and cost > 0)
        if raw.get("source_to_target"):
            link.measured[source] = raw["source_to_target"]
        if raw.get("target_to_source"):
            link.measured[target] = raw["target_to_source"]
        links.setdefault(source, {})[target] = link
        links.setdefault(target, {})[source] = link
    return links


def _relaying(
    nodes: dict[str, dict[str, Any]], links: dict[str, dict[str, Link]]
) -> set[str]:
    """Nodes that pass messages on: border routers and routers.

    The role a device reports lags behind: a device that became a router may
    still call itself router-eligible. Having links priced as router links is
    what counts. A sleepy device never relays, whatever its links say.
    """
    relaying = set()
    for node_id, node in nodes.items():
        role = node.get("role")
        routes = any(link.router_link for link in links.get(node_id, {}).values())
        if (
            node.get("kind") == "border_router"
            or role in ("router", "leader")
            or (role != "sleepy_end_device" and routes)
        ):
            relaying.add(node_id)
    return relaying


def build_tree(
    topology: dict[str, Any], own_network: str | None = None
) -> list[dict[str, Any]]:
    """Reduce the Matter Server's topology to one parent per device.

    Returns one entry per node: ``id``, ``subject``, ``kind`` (border_router,
    router, end_device, sleepy, unknown), ``parent`` (a node id, ``ROOT``, or
    None for a device without a way in), ``link`` (what the device measured
    of its parent) and ``alternatives`` (other relaying neighbours with a
    usable link). Border routers of other Thread networks are left out; they
    carry nothing for this one.
    """
    nodes = {
        str(n["id"]): n
        for n in topology.get("nodes", [])
        if own_network is None
        or n.get("kind") != "border_router"
        or str(n.get("ext_pan_id", "")).lower() in ("", own_network.lower())
    }
    links = _links(topology)
    relaying = _relaying(nodes, links)
    borders = [i for i in nodes if nodes[i].get("kind") == "border_router"]

    # Cheapest way from every relaying node to any border router.
    best: dict[str, int] = dict.fromkeys(borders, 0)
    parent: dict[str, str] = dict.fromkeys(borders, ROOT)
    queue = [(0, node_id) for node_id in sorted(borders)]
    while queue:
        cost, node_id = heapq.heappop(queue)
        if cost > best.get(node_id, cost):
            continue
        for neighbour, link in sorted(links.get(node_id, {}).items()):
            if neighbour not in relaying or neighbour not in nodes:
                continue
            total = cost + link.cost
            if total < best.get(neighbour, total + 1):
                best[neighbour] = total
                parent[neighbour] = node_id
                heapq.heappush(queue, (total, neighbour))

    tree = []
    for node_id, node in sorted(nodes.items()):
        mine = links.get(node_id, {})
        if node_id in relaying:
            up = parent.get(node_id)
        else:
            up = _parent_of_child(node_id, mine, relaying, nodes)
        seen = mine[up].seen_by(node_id) if up in mine else {}
        tree.append(
            {
                "id": node_id,
                "subject": _subject(node),
                "kind": _kind(node, node_id in relaying),
                "parent": up,
                "link": {
                    "rssi": seen.get("rssi"),
                    "lqi": seen.get("lqi"),
                    "strength": seen.get("strength"),
                },
                "alternatives": sum(
                    1
                    for other, candidate in mine.items()
                    if other != up
                    and other in relaying
                    and (candidate.seen_by(node_id).get("lqi") or 0) >= USABLE_LQI
                )
                if node_id in relaying
                else 0,
                "vendor": node.get("vendor_name"),
            }
        )
    return tree


def _parent_of_child(
    node_id: str,
    mine: dict[str, Link],
    relaying: set[str],
    nodes: dict[str, dict[str, Any]],
) -> str | None:
    """Return a non-relaying device's parent: its parent link, else its best."""
    candidates = [n for n in mine if n in relaying and n in nodes]
    if not candidates:
        return None
    parents = [n for n in candidates if mine[n].parent_link]

    def strength(neighbour: str) -> int:
        rssi = mine[neighbour].seen_by(node_id).get("rssi")
        return rssi if isinstance(rssi, int) else -200

    return max(parents or candidates, key=lambda n: (strength(n), n))


def _kind(node: dict[str, Any], relays: bool) -> str:
    if node.get("kind") == "border_router":
        return "border_router"
    if node.get("kind") == "thread_unknown":
        return "unknown"
    if relays:
        return "router"
    if node.get("role") == "sleepy_end_device":
        return "sleepy"
    return "end_device"


def _subject(node: dict[str, Any]) -> str | None:
    if node.get("node_id") is not None:
        return f"node:{node['node_id']}"
    if node.get("ext_address"):
        return f"br:{str(node['ext_address']).lower()}"
    return None


def remember_parents(
    tree: list[dict[str, Any]], known: dict[str, str]
) -> dict[str, str]:
    """Update the last known parent subject of every device.

    A device that went away is missing from the next topology; the page still
    shows it, under the parent it had.
    """
    subjects = {entry["id"]: entry["subject"] for entry in tree}
    updated = dict(known)
    for entry in tree:
        parent = subjects.get(entry["parent"]) if entry["parent"] else None
        if entry["subject"] and parent:
            updated[entry["subject"]] = parent
    return updated
