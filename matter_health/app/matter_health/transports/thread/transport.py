"""Thread: devices in a radio mesh, connected to the home by border routers.

The Matter Server already knows which border routers announce themselves and
how well every Thread device hears its neighbours. This transport reads both:
the border routers every minute, so their disappearance can be tied to a
switch turned off just before, and the radio links every ten minutes, since
link quality changes slowly and the server takes a while to collect it.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable
from typing import Any, ClassVar

from ... import kinds
from .. import TRANSPORTS, Ask, Transport
from .tree import build_tree, remember_parents

#: A border router missing from this many polls in a row is reported gone.
GONE_AFTER_ROUNDS = 2

#: Link quality changes slowly; the topology is also expensive for the server
#: to collect.
TOPOLOGY_POLL_S = 600.0

#: Thread roles that pass messages on for others.
RELAYING_ROLES = frozenset({"router", "leader"})

#: Devices that report no neighbour table of their own; their parent's entry
#: is the one link they have.
CHILD_ROLES = frozenset({"sleepy_end_device", "end_device", "reed", "child"})

#: What the tree calls a node, in the words every transport uses.
KIND = {
    "border_router": "gateway",
    "router": "relay",
    "end_device": "device",
    "sleepy": "sleepy",
    "unknown": "unknown",
}

#: The link quality Thread reports, 0-3, in the words every transport uses.
QUALITY = {3: "strong", 2: "medium", 1: "weak", 0: "weak"}

#: Roles in which the border router takes part in the mesh.
CONNECTED_ROLES = frozenset({"router", "leader", "child"})


def border_router_name(raw: dict[str, Any]) -> str:
    """Return a readable name for a border router announcement.

    Announcements carry the host name, e.g. ``Living-Room-Speaker.local.``;
    that is what the owner named the device, with dashes for spaces.
    """
    host = str(raw.get("hostname") or "").removesuffix(".").removesuffix(".local")
    if host:
        return host.replace("-", " ")
    return str(raw.get("modelName") or raw.get("vendorName") or "Border router")


def display_name(raw: dict[str, Any]) -> str | None:
    """Return a better name than the host name, where the announcement implies one.

    Home Assistant's own border router announces itself with the add-on's
    host name, which reads like a technical label; it is Home Assistant's.
    """
    if raw.get("vendorName") == "Home Assistant":
        return "Home Assistant"
    return None


def own_part(topology: dict[str, Any]) -> dict[str, Any]:
    """Return the Thread part of the Matter Server's topology.

    The server reports every network its devices use in one picture: Wi-Fi
    devices and their access points too. Each node and link says which
    network it belongs to; one that says nothing is taken for Thread.
    """

    def thread(value: Any) -> bool:
        return value in (None, "thread")

    return {
        "nodes": [
            n for n in topology.get("nodes", []) if thread(n.get("network_type"))
        ],
        "connections": [
            c for c in topology.get("connections", []) if thread(c.get("network"))
        ],
    }


def link_summary(topology: dict[str, Any]) -> list[dict[str, Any]]:
    """For every device, its best link to a neighbour that relays.

    A device is only as well connected as its strongest way into the mesh.
    Only routers and border routers pass messages on; a strong signal from a
    battery sensor next door helps nobody.

    Each connection carries what the ``source`` device measured of the
    ``target`` (``source_to_target``) and, if the target reports too, the
    other way round. A device's own measurement counts first; what a
    neighbour heard of it stands in only where the device measured nothing.
    A router or border router that reports no measurements at all is left
    out: nothing can be said about its reception.
    """
    nodes = {node["id"]: node for node in topology.get("nodes", [])}
    own: dict[str, dict[str, dict[str, Any]]] = {}
    heard: dict[str, dict[str, dict[str, Any]]] = {}
    for link in topology.get("connections", []):
        source, target = link["source"], link["target"]
        for measurer, measured, direction in (
            (source, target, "source_to_target"),
            (target, source, "target_to_source"),
        ):
            value = link.get(direction) or {}
            if value.get("rssi") is None:
                continue
            own.setdefault(measurer, {})[measured] = value
            heard.setdefault(measured, {})[measurer] = value

    def relays(node_id: str) -> bool:
        node = nodes.get(node_id, {})
        return node.get("kind") == "border_router" or node.get("role") in (
            RELAYING_ROLES
        )

    summary = []
    for node_id, node in nodes.items():
        child = node.get("role") in CHILD_ROLES
        if not own.get(node_id) and not child:
            continue
        candidates = {**heard.get(node_id, {}), **own.get(node_id, {})}
        options = [
            (value, neighbour)
            for neighbour, value in candidates.items()
            if relays(neighbour)
        ]
        if not options:
            continue
        value, neighbour = max(options, key=lambda item: item[0]["rssi"])
        summary.append(
            {
                "subject": _subject(node) or f"thread:{node_id}",
                "neighbour": _subject(nodes.get(neighbour, {})),
                "role": node.get("role"),
                "rssi": value["rssi"],
                "lqi": value.get("lqi"),
                "strength": value.get("strength"),
            }
        )
    return sorted(summary, key=lambda item: str(item["subject"]))


def link_quality(link: dict[str, Any]) -> str | None:
    """Rate a link by what the Matter Server says of it, else by its quality."""
    strength = link.get("strength")
    if strength in ("strong", "medium", "weak"):
        return str(strength)
    lqi = link.get("lqi")
    return QUALITY.get(lqi) if isinstance(lqi, int) else None


def _by_name(info: dict[str, Any]) -> str:
    return str(info.get("name", "")).lower()


def _subject(node: dict[str, Any]) -> str | None:
    if node.get("node_id") is not None:
        return f"node:{node['node_id']}"
    if node.get("ext_address"):
        return f"br:{str(node['ext_address']).lower()}"
    return None


@TRANSPORTS.register("thread")
class ThreadTransport(Transport):
    """Border routers, the mesh behind them and every device's way in."""

    name: ClassVar[str] = "thread"
    feature: ClassVar[int] = 0b10
    commands: ClassVar[frozenset[str]] = frozenset(
        {"get_thread_border_routers", "get_network_topology"}
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Start with nothing known."""
        super().__init__(*args, **kwargs)
        self._border_routers: dict[str, dict[str, Any]] = {}
        self._missing: dict[str, int] = {}
        self._first_round = True
        self._next_topology = 0.0

    async def poll(self, ask: Ask) -> None:
        """Read the border routers, and now and then the radio links."""
        await self.border_routers_seen(await ask("get_thread_border_routers"))
        if time.monotonic() >= self._next_topology:
            topology = own_part(await ask("get_network_topology") or {})
            links = link_summary(topology)
            # Kept for rules that ask later who hung on whom.
            await self.ctx.store.set_state("thread.links", links)
            await self.tree(topology)
            await self.ctx.emit(kinds.THREAD_TOPOLOGY, "matter_server", devices=links)
            self._next_topology = time.monotonic() + TOPOLOGY_POLL_S

    async def tree(self, topology: dict[str, Any]) -> None:
        """Keep the mesh as a tree for the page, and who hung on whom."""
        home = await self.home_network(self._border_routers.values())
        tree = build_tree(topology, home)
        await self.ctx.store.set_state(
            "thread.tree", {"at": self.ctx.now().isoformat(), "nodes": tree}
        )
        known = await self.ctx.store.get_state("thread.parents") or {}
        await self.ctx.store.set_state("thread.parents", remember_parents(tree, known))

    async def border_routers_seen(self, announced: list[dict[str, Any]]) -> None:
        """Compare this round's announcements with what was there before.

        Border routers are told apart by host name: some regenerate their
        Thread extended address on every restart, the host name stays. A
        router counts as gone only when it is missing from two rounds in a
        row, so one incomplete answer does not report the whole house as lost.
        """
        current: dict[str, dict[str, Any]] = {}
        for raw in announced or []:
            ext = str(raw.get("extAddressHex") or "").lower()
            if not ext:
                continue
            name = border_router_name(raw)
            current[name] = {
                "subject": f"br:{ext}",
                # Keyed by the announced name, shown by the one the user gave.
                "name": self.ctx.names.match(name) or display_name(raw) or name,
                "vendor": raw.get("vendorName"),
                "model": raw.get("modelName"),
                "network": raw.get("networkName"),
                "pan": str(raw.get("extendedPanIdHex") or "").lower() or None,
                # Where it is on the home network, to find it there.
                "addresses": [
                    a for a in raw.get("addresses") or [] if isinstance(a, str)
                ],
            }
            self.ctx.names.set(f"br:{ext}", current[name]["name"])
        home = await self.home_network(current.values())
        for info in current.values():
            # A router whose network is not announced is given the benefit of
            # the doubt; wrongly hiding a real bridge would be worse.
            info["own"] = home is None or info["pan"] in (None, home)
        # Only the very first answer is a baseline. Deciding by an empty list
        # instead would swallow the return of the last router that went away.
        first_round, self._first_round = self._first_round, False
        for name, info in current.items():
            self._missing.pop(name, None)
            if not first_round and name not in self._border_routers:
                await self._border_router_event(kinds.BORDER_ROUTER_APPEARED, info)
            self._border_routers[name] = info
        for name in list(self._border_routers.keys() - current.keys()):
            self._missing[name] = self._missing.get(name, 0) + 1
            if self._missing[name] >= GONE_AFTER_ROUNDS:
                info = self._border_routers.pop(name)
                self._missing.pop(name)
                await self._border_router_event(kinds.BORDER_ROUTER_GONE, info)
        await self.ctx.store.set_state(
            "border_routers", sorted(self._border_routers.values(), key=_by_name)
        )

    async def home_network(self, routers: Iterable[dict[str, Any]]) -> str | None:
        """Return the Extended PAN ID of the Thread network Home Assistant uses.

        Other products - some hubs, for instance - run a Thread network of
        their own next to it. Their border routers are announced alike but
        carry nothing for the devices Home Assistant controls. The network
        of Home Assistant's own border router decides; without one, the
        network most border routers belong to.
        """
        routers = [r for r in routers if r["pan"]]
        node = await self.ctx.store.get_state("otbr.node") or {}
        own_name = node.get("network_name")
        for router in routers:
            if own_name and router["network"] == own_name:
                return str(router["pan"])
        counted = Counter(str(r["pan"]) for r in routers)
        return counted.most_common(1)[0][0] if counted else None

    async def _border_router_event(self, kind: str, info: dict[str, Any]) -> None:
        details = {
            key: value
            for key, value in info.items()
            if key not in ("subject", "addresses")
        }
        await self.ctx.emit(kind, "matter_server", info["subject"], **details)

    async def picture(self, away: set[str]) -> list[dict[str, Any]]:
        """Return the mesh as a tree; devices away under the parent they had."""
        tree = await self.ctx.store.get_state("thread.tree") or {}
        parents: dict[str, str] = await self.ctx.store.get_state("thread.parents") or {}
        mine: dict[str, str] = await self.ctx.store.get_state("matter.transports") or {}
        routers = await self.ctx.store.get_state("border_routers") or []
        addresses = {r["subject"]: r.get("addresses") or [] for r in routers}
        entries = []
        for raw in tree.get("nodes", []):
            entry = dict(raw)
            entry["kind"] = KIND.get(str(raw.get("kind")), "unknown")
            if entry["kind"] == "gateway":
                entry["addresses"] = addresses.get(str(raw.get("subject")), [])
            link = dict(raw.get("link") or {})
            link["quality"] = link_quality(link)
            entry["link"] = link
            entries.append(entry)
        by_subject = {e["subject"]: e["id"] for e in entries if e.get("subject")}
        for subject in sorted(away - by_subject.keys()):
            if mine.get(subject, self.name) != self.name:
                continue
            entries.append(
                {
                    "id": subject,
                    "subject": subject,
                    "kind": "device",
                    "parent": by_subject.get(parents.get(subject, "")),
                    "link": {},
                    "alternatives": 0,
                    "vendor": None,
                    "missing": True,
                }
            )
        return entries

    async def summary(self, devices: int) -> dict[str, Any]:
        """Border routers of the home network, and whether ours takes part."""
        routers = await self.ctx.store.get_state("border_routers") or []
        node = await self.ctx.store.get_state("otbr.node")
        role = (node or {}).get("role")
        return {
            "gateways": sum(1 for r in routers if r.get("own") is not False),
            "connected": None if node is None else role in CONNECTED_ROLES,
            "foreign": [r for r in routers if r.get("own") is False],
        }
