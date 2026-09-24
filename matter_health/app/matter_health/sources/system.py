"""The system Home Assistant runs on, as the Supervisor describes it.

Two kinds of facts come from here. Versions: an update of Home Assistant, its
operating system, the Matter Server or the border router add-on is a common
reason for devices to start misbehaving the next day, and the user rarely
connects the two. And the host's IPv6 settings: Thread devices can only answer
Home Assistant if the host forwards IPv6 between its network and the mesh, and
Home Assistant OS does that only with IPv6 enabled for Docker.

Everything here is read; nothing is changed.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import aiohttp

from .. import kinds
from ..config import MATTER_SERVER_SLUG, OTBR_SLUG
from ..engine import SOURCES, Source
from ..supervisor import Supervisor

#: Updates and settings change rarely; a few minutes is quick enough to tie an
#: update to what went wrong after it.
POLL_S = 300.0

#: Software whose version is followed: (key, Supervisor path, readable name).
SOFTWARE: tuple[tuple[str, str, str], ...] = (
    ("core", "/core/info", "Home Assistant"),
    ("os", "/os/info", "Home Assistant OS"),
    (MATTER_SERVER_SLUG, f"/addons/{MATTER_SERVER_SLUG}/info", "Matter Server"),
    (OTBR_SLUG, f"/addons/{OTBR_SLUG}/info", "OpenThread Border Router"),
)

#: The last versions seen, kept across restarts so an update made while the
#: add-on was stopped is noticed too.
VERSIONS = "system.versions"

#: The last network settings seen, for the overview and the rules.
NETWORK = "system.network"


@SOURCES.register("system")
class SystemSource(Source):
    """Follows versions and the host's IPv6 settings."""

    name: ClassVar[str] = "system"

    async def run(self) -> None:
        """Poll until cancelled."""
        async with aiohttp.ClientSession() as session:
            supervisor = Supervisor(session, self.ctx.options)
            while True:
                await self.poll(supervisor)
                await self.connected()
                await asyncio.sleep(POLL_S)

    async def poll(self, supervisor: Supervisor) -> None:
        """Read versions and network settings once and report changes."""
        await self._versions(supervisor)
        await self._network(supervisor)

    async def _versions(self, supervisor: Supervisor) -> None:
        known: dict[str, str] = dict(await self.ctx.store.get_state(VERSIONS) or {})
        for key, path, name in SOFTWARE:
            info = await supervisor.data(path)
            version = str((info or {}).get("version") or "")
            if not version:
                continue
            previous = known.get(key)
            if previous and previous != version:
                await self.emit(
                    kinds.SYSTEM_UPDATED,
                    f"software:{key}",
                    name=name,
                    previous=previous,
                    current=version,
                )
            known[key] = version
        await self.ctx.store.set_state(VERSIONS, known)

    async def _network(self, supervisor: Supervisor) -> None:
        docker = await supervisor.data("/docker/info") or {}
        network = await supervisor.data("/network/info") or {}
        os_info = await supervisor.data("/os/info")
        primary: dict[str, Any] = next(
            (i for i in network.get("interfaces", []) if i.get("primary")), {}
        )
        current: dict[str, Any] = {
            "docker_ipv6": docker.get("enable_ipv6"),
            "ipv6_method": (primary.get("ipv6") or {}).get("method"),
            "interface": primary.get("interface"),
            "haos": bool(os_info and os_info.get("version")),
        }
        if current != await self.ctx.store.get_state(NETWORK):
            await self.ctx.store.set_state(NETWORK, current)
            await self.emit(kinds.SYSTEM_NETWORK, None, **current)
