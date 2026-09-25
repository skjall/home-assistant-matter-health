"""Thread: devices in a radio mesh, connected to the home by border routers.

The Matter Server already knows which border routers announce themselves and
how well every Thread device hears its neighbours. This transport reads both:
the border routers every minute, so their disappearance can be tied to a
switch turned off just before, and the radio links every ten minutes, since
link quality changes slowly and the server takes a while to collect it.

Border routers say more in their announcements than their name: their role
in the mesh and the partition they belong to. Border routers of one network
in different partitions mean the mesh has fallen apart.

Every ten minutes the transport also reads the radio counters of the devices
that stay awake. How often each found the channel busy since the last
reading shows where on the channel something else is sending: everywhere
alike, or around a few devices.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta
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

#: A border router's role in its announcement's state bitmap (bits 9-10).
ANNOUNCED_ROLES = {0: "disabled", 1: "child", 2: "router", 3: "leader"}

#: A border router that has not announced itself for this much longer than
#: the others is gone, even if the Matter Server still lists it.
SILENT_AFTER = timedelta(minutes=30)

#: The radio counters of Thread Network Diagnostics, by what they count.
COUNTERS = {"tx": "0/53/22", "retry": "0/53/33", "cca": "0/53/36", "busy": "0/53/38"}

#: The cluster's routing role, and its feature map: bit 3 means the device
#: keeps MAC counters.
ROUTING_ROLE = "0/53/1"
#: The channel the device's Thread network uses.
CHANNEL = "0/53/0"
DIAGNOSTICS_FEATURES = "0/53/65532"
MAC_COUNTERS = 0b1000

#: Routing roles of devices that stay awake: end device, router-eligible,
#: router, leader. A sleepy device is not asked; it would have to wake up.
AWAKE_ROLES = frozenset({3, 4, 5, 6})
LEADER_ROLE = 6

#: One device's answer may take this long; a silent one is skipped.
READ_TIMEOUT_S = 15.0


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


def announced_role(raw: dict[str, Any]) -> str | None:
    """Return the role a border router announces, from its state bitmap."""
    try:
        bitmap = int(str(raw.get("stateBitmapHex") or ""), 16)
    except ValueError:
        return None
    return ANNOUNCED_ROLES[(bitmap >> 9) & 0b11]


def partitions(routers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the border routers of the home network by the partition they are in.

    Home Assistant's own border router's partition comes first, then the
    others by size. Each part names its leader, where one of its border
    routers leads it.
    """
    parts: dict[str, list[dict[str, Any]]] = {}
    for router in routers:
        if router.get("own") is not False and router.get("partition"):
            parts.setdefault(str(router["partition"]), []).append(router)

    def order(item: tuple[str, list[dict[str, Any]]]) -> tuple[bool, int, str]:
        ours = any(r.get("vendor") == "Home Assistant" for r in item[1])
        return (not ours, -len(item[1]), item[0])

    return [
        {
            "partition": partition,
            "leader": next(
                (r["name"] for r in members if r.get("role") == "leader"), None
            ),
            "border_routers": [
                {"subject": r["subject"], "name": r["name"], "role": r.get("role")}
                for r in sorted(members, key=_by_name)
            ],
        }
        for partition, members in sorted(parts.items(), key=order)
    ]


def rates(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, float | int | None] | None:
    """Return what a device's counters did between two readings, per hour.

    None when there is nothing to compare: readings at the same moment, or
    counters that went back because the device restarted.
    """
    hours = (
        datetime.fromisoformat(after["at"]) - datetime.fromisoformat(before["at"])
    ).total_seconds() / 3600
    deltas = {name: after[name] - before[name] for name in COUNTERS}
    if hours <= 0 or any(delta < 0 for delta in deltas.values()):
        return None
    return {
        "cca_per_hour": round(deltas["cca"] / hours),
        "busy_per_hour": round(deltas["busy"] / hours),
        "retries_per_frame": round(deltas["retry"] / deltas["tx"], 3)
        if deltas["tx"]
        else None,
    }


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
        {"get_thread_border_routers", "get_network_topology", "read_attribute"}
    )
    clusters: ClassVar[tuple[str, ...]] = (CHANNEL, ROUTING_ROLE, DIAGNOSTICS_FEATURES)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Start with nothing known."""
        super().__init__(*args, **kwargs)
        self._border_routers: dict[str, dict[str, Any]] = {}
        self._missing: dict[str, int] = {}
        self._first_round = True
        self._next_topology = 0.0
        self._parts: list[dict[str, Any]] | None = None
        #: Devices whose radio counters are read, by subject.
        self._measured: list[str] = []

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
            await self.measure(ask)
            self._next_topology = time.monotonic() + TOPOLOGY_POLL_S

    async def devices(self, attributes: dict[str, dict[str, Any]]) -> None:
        """Note each device's routing role, and which keep radio counters."""
        roles = {
            subject: attrs[ROUTING_ROLE]
            for subject, attrs in attributes.items()
            if isinstance(attrs.get(ROUTING_ROLE), int)
        }
        await self.ctx.store.set_state("thread.roles", roles)
        channels = Counter(
            attrs[CHANNEL]
            for attrs in attributes.values()
            if isinstance(attrs.get(CHANNEL), int) and 11 <= attrs[CHANNEL] <= 26
        )
        if channels:
            await self.ctx.store.set_state(
                "thread.channel", channels.most_common(1)[0][0]
            )
        self._measured = sorted(
            subject
            for subject, attrs in attributes.items()
            if roles.get(subject) in AWAKE_ROLES
            and isinstance(attrs.get(DIAGNOSTICS_FEATURES), int)
            and attrs[DIAGNOSTICS_FEATURES] & MAC_COUNTERS
        )

    async def measure(self, ask: Ask) -> None:
        """Read the radio counters of the devices that stay awake.

        What they counted since the last reading, per hour, is kept for the
        page and reported to the rules.
        """
        nodes = await self.ctx.store.get_state("matter.nodes") or {}
        away = set(nodes.get("unavailable", []))
        readings: dict[str, dict[str, Any]] = dict(
            await self.ctx.store.get_state("thread.counters") or {}
        )
        now = self.ctx.now().isoformat()
        measured: dict[str, dict[str, Any]] = {}
        for subject in self._measured:
            if subject in away:
                continue
            try:
                answer = await asyncio.wait_for(
                    ask(
                        "read_attribute",
                        node_id=int(subject.split(":")[1]),
                        attribute_path=list(COUNTERS.values()),
                    ),
                    READ_TIMEOUT_S,
                )
            except TimeoutError, RuntimeError:
                continue
            values = {name: (answer or {}).get(path) for name, path in COUNTERS.items()}
            if not all(isinstance(v, int) for v in values.values()):
                continue
            reading = {**values, "at": now}
            if subject in readings and (rated := rates(readings[subject], reading)):
                measured[subject] = rated
            readings[subject] = reading
        await self.ctx.store.set_state("thread.counters", readings)
        await self.ctx.store.set_state(
            "thread.interference", {"at": now, "devices": measured}
        )
        if measured:
            await self.ctx.emit(
                kinds.THREAD_INTERFERENCE,
                "matter_server",
                devices=[{"subject": s, **r} for s, r in sorted(measured.items())],
            )

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
        seen = [
            raw["lastSeen"]
            for raw in announced or []
            if isinstance(raw.get("lastSeen"), int)
        ]
        newest = max(seen, default=None)
        for raw in announced or []:
            ext = str(raw.get("extAddressHex") or "").lower()
            if not ext:
                continue
            last = raw.get("lastSeen")
            if (
                newest is not None
                and isinstance(last, int)
                and newest - last > SILENT_AFTER.total_seconds() * 1000
            ):
                # Unplugged: it stopped announcing itself, the list keeps it.
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
                "role": announced_role(raw),
                "partition": str(raw.get("partitionIdHex") or "").lower() or None,
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
        parts = partitions(self._border_routers.values())
        await self.ctx.store.set_state("thread.partitions", parts)
        if parts != self._parts:
            self._parts = parts
            await self.ctx.emit(kinds.THREAD_PARTITIONS, "matter_server", parts=parts)

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
            if key not in ("subject", "addresses", "role", "partition")
        }
        await self.ctx.emit(kind, "matter_server", info["subject"], **details)

    async def picture(self, away: set[str]) -> list[dict[str, Any]]:
        """Return the mesh as a tree; devices away under the parent they had."""
        tree = await self.ctx.store.get_state("thread.tree") or {}
        parents: dict[str, str] = await self.ctx.store.get_state("thread.parents") or {}
        mine: dict[str, str] = await self.ctx.store.get_state("matter.transports") or {}
        routers = await self.ctx.store.get_state("border_routers") or []
        addresses = {r["subject"]: r.get("addresses") or [] for r in routers}
        announced = {r["subject"]: r for r in routers}
        parts = await self.ctx.store.get_state("thread.partitions") or []
        main = parts[0]["partition"] if len(parts) > 1 else None
        roles: dict[str, int] = await self.ctx.store.get_state("thread.roles") or {}
        radio = await self.ctx.store.get_state("thread.interference") or {}
        busy = {s: r.get("cca_per_hour") for s, r in radio.get("devices", {}).items()}
        entries = []
        for raw in tree.get("nodes", []):
            entry = dict(raw)
            entry["kind"] = KIND.get(str(raw.get("kind")), "unknown")
            subject = str(raw.get("subject"))
            if entry["kind"] == "gateway":
                entry["addresses"] = addresses.get(subject, [])
                router = announced.get(subject, {})
                entry["role"] = router.get("role")
                # In another partition than Home Assistant's border router.
                entry["apart"] = main is not None and router.get("partition") not in (
                    None,
                    main,
                )
            else:
                entry["role"] = "leader" if roles.get(subject) == LEADER_ROLE else None
            if busy.get(subject) is not None:
                entry["channel_busy_per_hour"] = busy[subject]
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
