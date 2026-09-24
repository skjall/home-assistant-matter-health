"""Devices and how they reach Home Assistant, from the Matter Server's websocket.

The Matter Server already tracks which devices are reachable and keeps every
attribute they report. This source only reads that; it never sends a command
that changes anything. What a device's transport means - border routers,
access points, radio links - is the transports' business: this source hands
each one the attributes of its devices and lets it ask the server the
read-only questions it declares.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from typing import Any, ClassVar

import aiohttp

from .. import kinds
from ..config import MATTER_SERVER_PORT, MATTER_SERVER_SLUG
from ..engine import SOURCES, Source
from ..supervisor import Supervisor
from ..transports import FEATURE_MAP, TRANSPORTS, Transport, transport_of

#: How often the transports may ask the server something. Border routers come
#: and go with power; a minute is quick enough to tie a disappearance to a
#: switch that was turned off just before.
POLL_S = 60.0

#: Answers to a read command; the topology of a large home takes a while.
COMMAND_TIMEOUT_S = 120.0


def read_commands() -> frozenset[str]:
    """Return the only commands this source sends: its own and the transports'."""
    commands = {"start_listening"}
    for transport in TRANSPORTS:
        commands |= transport.commands
    return frozenset(commands)


def wanted(path: str) -> bool:
    """Whether an attribute tells a transport anything."""
    return path == FEATURE_MAP or any(
        path.startswith(prefix)
        for transport in TRANSPORTS
        for prefix in transport.clusters
    )


@SOURCES.register("matter_server")
class MatterServerSource(Source):
    """Reachability of devices, and what their transports need to know."""

    name: ClassVar[str] = "matter_server"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Start with nothing known."""
        super().__init__(*args, **kwargs)
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._available: dict[int, bool] = {}
        self._attributes: dict[int, dict[str, Any]] = {}
        self.transports: dict[str, Transport] = {
            name: TRANSPORTS.get(name)(self.ctx) for name in TRANSPORTS.names()
        }

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
        async def ask(command: str) -> Any:
            return await self._command(ws, command)

        while True:
            for transport in self.transports.values():
                await transport.poll(ask)
            await asyncio.sleep(POLL_S)

    async def _command(self, ws: aiohttp.ClientWebSocketResponse, command: str) -> Any:
        if command not in read_commands():
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
        elif event == "attribute_updated" and isinstance(data, list) and len(data) == 3:
            node_id, path, value = data
            if wanted(str(path)):
                self._attributes.setdefault(int(node_id), {})[str(path)] = value
                await self._tell_transports()
        elif event == "node_removed" and data is not None:
            node_id = int(data)
            self._available.pop(node_id, None)
            self._attributes.pop(node_id, None)
            await self._save_availability()
            await self._tell_transports()
            await self.emit(
                kinds.MATTER_NODE_REMOVED,
                f"node:{node_id}",
                name=self.ctx.names.get(f"node:{node_id}"),
            )

    def _remember(self, node: dict[str, Any]) -> None:
        """Keep the attributes of a node that tell a transport anything."""
        attributes = node.get("attributes") or {}
        self._attributes[int(node["node_id"])] = {
            path: value for path, value in attributes.items() if wanted(path)
        }

    async def _tell_transports(self) -> None:
        """Say which transport each device uses and hand each its devices."""
        by_transport: dict[str, dict[str, dict[str, Any]]] = {
            name: {} for name in self.transports
        }
        mine: dict[str, str] = {}
        for node_id, attributes in sorted(self._attributes.items()):
            name = transport_of(attributes)
            if name is None:
                continue
            subject = f"node:{node_id}"
            mine[subject] = name
            by_transport[name][subject] = attributes
        await self.ctx.store.set_state("matter.transports", mine)
        for name, transport in self.transports.items():
            await transport.devices(by_transport[name])

    async def _nodes_known(self, nodes: list[dict[str, Any]]) -> None:
        before = await self.ctx.store.get_state("matter.nodes") or {}
        if not nodes and before.get("total"):
            # A server that forgot every device lost its storage; the
            # devices themselves are still out there, paired to nobody.
            await self.emit(kinds.MATTER_NODES_LOST, previous=before["total"])
        for node in nodes or []:
            self._available[int(node["node_id"])] = bool(node.get("available"))
            self._remember(node)
        await self._save_availability()
        await self._tell_transports()

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
        if "attributes" in node:
            self._remember(node)
            await self._tell_transports()
