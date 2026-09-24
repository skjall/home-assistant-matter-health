"""The Thread network as the OpenThread Border Router add-on sees it and logs it.

The border router's REST API reports its role, the partition it belongs to and
which router leads that partition. A change of any of these is how a mesh
problem shows up first: the leader disappears, routers elect a new one, and for
a while the network is split into partitions that cannot reach each other.

Only ``/node`` is read. The dataset endpoints return the network key in clear
text; this add-on has no use for it and never asks.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import aiohttp

from ... import kinds
from ...config import OTBR_REST_PORT, OTBR_SLUG
from ...engine import SOURCES, Source
from ...sources.addon_logs import AddonLogSource
from ...supervisor import Supervisor

#: Partitions typically settle within a minute; polling every few seconds
#: catches even short splits.
POLL_S = 5.0

#: The only endpoint read. Kept as a constant so a test can prove it.
NODE_PATH = "/node"


@SOURCES.register("otbr")
class OtbrSource(Source):
    """Polls the border router and reports role, partition and leader changes."""

    name: ClassVar[str] = "otbr"

    async def run(self) -> None:
        """Poll until cancelled; raise when the border router cannot be reached."""
        async with aiohttp.ClientSession() as session:
            supervisor = Supervisor(session, self.ctx.options)
            base = await supervisor.addon_url(
                OTBR_SLUG, OTBR_REST_PORT, "http", self.ctx.options.otbr_url
            )
            if base is None:
                raise ConnectionError("OpenThread Border Router add-on not running")
            previous: dict[str, Any] | None = await self.ctx.store.get_state(
                "otbr.node"
            )
            while True:
                node = await self._read_node(session, base)
                await self.connected()
                if node != previous:
                    await self._report(previous, node)
                    await self.ctx.store.set_state("otbr.node", node)
                    previous = node
                await asyncio.sleep(POLL_S)

    async def _read_node(
        self, session: aiohttp.ClientSession, base: str
    ) -> dict[str, Any]:
        async with session.get(
            f"{base}{NODE_PATH}",
            headers={"Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            response.raise_for_status()
            raw: dict[str, Any] = await response.json(content_type=None)
        leader = raw.get("leaderData") or {}
        return {
            "role": raw.get("state"),
            "partition_id": leader.get("partitionId"),
            "leader_router_id": leader.get("leaderRouterId"),
            "router_count": raw.get("routerCount"),
            "network_name": raw.get("networkName"),
        }

    async def _report(
        self, previous: dict[str, Any] | None, node: dict[str, Any]
    ) -> None:
        await self.emit(kinds.THREAD_STATE, **node)
        if previous is None:
            return
        if previous["role"] != node["role"]:
            await self.emit(
                kinds.THREAD_ROLE_CHANGED,
                previous=previous["role"],
                current=node["role"],
            )
        if previous["partition_id"] != node["partition_id"]:
            await self.emit(
                kinds.THREAD_PARTITION_CHANGED,
                previous=previous["partition_id"],
                current=node["partition_id"],
            )
        if previous["leader_router_id"] != node["leader_router_id"]:
            await self.emit(
                kinds.THREAD_LEADER_CHANGED,
                previous=previous["leader_router_id"],
                current=node["leader_router_id"],
            )


@SOURCES.register("otbr_log")
class OtbrLog(AddonLogSource):
    """Mesh splits, radio trouble and IPv6 forwarding from the add-on's log."""

    name: ClassVar[str] = "otbr_log"
    slug: ClassVar[str] = OTBR_SLUG
    parser: ClassVar[str] = "openthread"
