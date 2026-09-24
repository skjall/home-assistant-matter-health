"""Devices, border routers and radio links, from the Matter Server's websocket.

The Matter Server already tracks which devices are reachable, which border
routers announce themselves and how well every Thread device hears its
neighbours. This source only reads that; it never sends a command that
changes anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import time
from typing import Any, ClassVar

import aiohttp

from .. import kinds
from ..config import MATTER_SERVER_PORT, MATTER_SERVER_SLUG
from ..engine import SOURCES, Source
from ..supervisor import Supervisor

#: Border routers come and go with power; a minute is quick enough to tie a
#: disappearance to a switch that was turned off just before.
BORDER_ROUTER_POLL_S = 60.0

#: A border router missing from this many polls in a row is reported gone.
GONE_AFTER_ROUNDS = 2

#: Link quality changes slowly; the topology is also expensive for the server
#: to collect.
TOPOLOGY_POLL_S = 600.0

#: Answers to a read command; the topology of a large home takes a while.
COMMAND_TIMEOUT_S = 120.0

#: The only commands this source sends. All of them only read.
READ_COMMANDS = frozenset(
    {"start_listening", "get_thread_border_routers", "get_network_topology"}
)


def border_router_name(raw: dict[str, Any]) -> str:
    """Return a readable name for a border router announcement.

    Announcements carry the host name, e.g. ``Living-Room-Speaker.local.``;
    that is what the owner named the device, with dashes for spaces.
    """
    host = str(raw.get("hostname") or "").removesuffix(".").removesuffix(".local")
    if host:
        return host.replace("-", " ")
    return str(raw.get("modelName") or raw.get("vendorName") or "Border router")


def link_summary(topology: dict[str, Any]) -> list[dict[str, Any]]:
    """For every device, its best radio link.

    A device is only as well connected as its strongest neighbour: a battery
    sensor talks to exactly one parent, a mains device to several routers.
    """
    nodes = {node["id"]: node for node in topology.get("nodes", [])}
    best: dict[str, dict[str, Any]] = {}
    for link in topology.get("connections", []):
        for here, there, direction in (
            (link["source"], link["target"], "target_to_source"),
            (link["target"], link["source"], "source_to_target"),
        ):
            heard = link.get(direction) or {}
            rssi = heard.get("rssi")
            if rssi is None:
                continue
            current = best.get(here)
            if current is None or rssi > current["rssi"]:
                best[here] = {
                    "rssi": rssi,
                    "lqi": heard.get("lqi"),
                    "strength": heard.get("strength") or link.get("strength"),
                    "neighbour": there,
                }
    summary = []
    for node_id, link in best.items():
        node = nodes.get(node_id, {})
        neighbour = nodes.get(link["neighbour"], {})
        summary.append(
            {
                "subject": _subject(node) or f"thread:{node_id}",
                "neighbour": _subject(neighbour),
                "role": node.get("role"),
                "rssi": link["rssi"],
                "lqi": link["lqi"],
                "strength": link["strength"],
            }
        )
    return sorted(summary, key=lambda item: str(item["subject"]))


def _by_name(info: dict[str, Any]) -> str:
    return str(info.get("name", "")).lower()


def _subject(node: dict[str, Any]) -> str | None:
    if node.get("node_id") is not None:
        return f"node:{node['node_id']}"
    if node.get("ext_address"):
        return f"br:{str(node['ext_address']).lower()}"
    return None


@SOURCES.register("matter_server")
class MatterServerSource(Source):
    """Reachability of devices, border routers and radio links."""

    name: ClassVar[str] = "matter_server"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Start with nothing known."""
        super().__init__(*args, **kwargs)
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._available: dict[int, bool] = {}
        self._border_routers: dict[str, dict[str, Any]] = {}
        self._missing: dict[str, int] = {}
        self._first_round = True

    async def run(self) -> None:
        """Stay connected, listen for device changes and poll the rest."""
        async with aiohttp.ClientSession() as session:
            supervisor = Supervisor(session, self.ctx.options)
            base = await supervisor.addon_url(
                MATTER_SERVER_SLUG,
                MATTER_SERVER_PORT,
                "ws",
                self.ctx.options.matter_server_url,
            )
            if base is None:
                raise ConnectionError("Matter Server add-on not running")
            url = base if base.endswith("/ws") else f"{base}/ws"
            async with session.ws_connect(url, max_msg_size=0, heartbeat=55) as ws:
                await ws.receive_json()  # server_info comes first, unasked
                reader = asyncio.create_task(self._read(ws))
                try:
                    nodes = await self._command(ws, "start_listening")
                    await self.connected()
                    await self._nodes_known(nodes)
                    await self._poll(ws)
                finally:
                    reader.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await reader

    async def _poll(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        next_topology = 0.0
        while True:
            await self._border_routers_seen(
                await self._command(ws, "get_thread_border_routers")
            )
            if time.monotonic() >= next_topology:
                topology = await self._command(ws, "get_network_topology")
                await self.emit(kinds.THREAD_TOPOLOGY, devices=link_summary(topology))
                next_topology = time.monotonic() + TOPOLOGY_POLL_S
            await asyncio.sleep(BORDER_ROUTER_POLL_S)

    async def _command(self, ws: aiohttp.ClientWebSocketResponse, command: str) -> Any:
        if command not in READ_COMMANDS:
            raise ValueError(f"{command} is not a read-only command")
        message_id = str(next(self._ids))
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await ws.send_json({"message_id": message_id, "command": command})
            return await asyncio.wait_for(future, COMMAND_TIMEOUT_S)
        finally:
            self._pending.pop(message_id, None)

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for message in ws:
            if message.type is not aiohttp.WSMsgType.TEXT:
                break
            await self.handle_message(json.loads(message.data))
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Matter Server closed"))

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Route an answer to its command, or an event to its handler."""
        message_id = message.get("message_id")
        if message_id is not None and message_id in self._pending:
            future = self._pending[message_id]
            if "error_code" in message:
                future.set_exception(
                    RuntimeError(f"{message['error_code']}: {message.get('details')}")
                )
            else:
                future.set_result(message.get("result"))
            return
        event = message.get("event")
        data = message.get("data")
        if event in ("node_added", "node_updated") and isinstance(data, dict):
            await self._node_seen(data, added=event == "node_added")
        elif event == "node_removed" and data is not None:
            node_id = int(data)
            self._available.pop(node_id, None)
            await self._save_availability()
            await self.emit(
                kinds.MATTER_NODE_REMOVED,
                f"node:{node_id}",
                name=self.ctx.names.get(f"node:{node_id}"),
            )

    async def _nodes_known(self, nodes: list[dict[str, Any]]) -> None:
        for node in nodes or []:
            self._available[int(node["node_id"])] = bool(node.get("available"))
        await self._save_availability()

    async def _save_availability(self) -> None:
        """Keep the current picture for the overview page."""
        await self.ctx.store.set_state(
            "matter.nodes",
            {
                "total": len(self._available),
                "unavailable": sorted(
                    f"node:{node_id}"
                    for node_id, available in self._available.items()
                    if not available
                ),
            },
        )

    async def _node_seen(self, node: dict[str, Any], added: bool) -> None:
        node_id = int(node["node_id"])
        subject = f"node:{node_id}"
        available = bool(node.get("available"))
        name = self.ctx.names.get(subject)
        if added or node_id not in self._available:
            await self.emit(kinds.MATTER_NODE_ADDED, subject, name=name)
        elif self._available[node_id] != available:
            await self.emit(
                kinds.MATTER_NODE_AVAILABLE
                if available
                else kinds.MATTER_NODE_UNAVAILABLE,
                subject,
                name=name,
            )
        self._available[node_id] = available
        await self._save_availability()

    async def _border_routers_seen(self, announced: list[dict[str, Any]]) -> None:
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
                "name": name,
                "vendor": raw.get("vendorName"),
                "model": raw.get("modelName"),
            }
            self.ctx.names.set(f"br:{ext}", name)
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

    async def _border_router_event(self, kind: str, info: dict[str, Any]) -> None:
        details = {key: value for key, value in info.items() if key != "subject"}
        await self.emit(kind, info["subject"], **details)
