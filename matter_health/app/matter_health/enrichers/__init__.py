"""What Home Assistant's network integrations know about how devices are connected.

Matter and Thread say nothing about the home network underneath: whether a
border router is on Wi-Fi or on a cable, and through which access point.
A router or controller integration in Home Assistant often knows - its
device trackers carry it. An enricher reads one such integration and answers
in the same few words as every other: which clients it sees and how each is
connected.

Enrichers stand next to each other like transports. Each is a source that
reads Home Assistant only - never the network equipment itself - and keeps
what it learned in the store; the page merges what all of them know. Without
a matching integration, the picture simply does not say how a gateway is
connected.
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

import aiohttp

from ..engine import SOURCES, Context, Source
from ..sources.home_assistant import HomeAssistantApi

#: How a device is connected changes rarely.
POLL_S = 300.0


@dataclass(frozen=True, slots=True)
class Client:
    """One device on the home network, as an integration sees it."""

    mac: str
    ip: str | None = None
    name: str | None = None
    #: True on a cable, False on Wi-Fi.
    wired: bool = True
    #: The access point it is connected to, for a Wi-Fi device.
    via: str | None = None
    ssid: str | None = None


@dataclass(slots=True)
class Knowledge:
    """What one or more enrichers know."""

    clients: list[Client]

    def dump(self) -> list[dict[str, Any]]:
        """Return a form the store keeps."""
        return [asdict(c) for c in self.clients]

    @classmethod
    def load(cls, raw: list[dict[str, Any]]) -> Knowledge:
        """Read what ``dump`` stored."""
        return cls([Client(**c) for c in raw])

    def client(
        self, addresses: list[str] | None, mac: str | None = None
    ) -> Client | None:
        """Find a client by its MAC, else by any of its IP addresses."""
        wanted = {a.lower() for a in addresses or []}
        mac = mac.lower() if mac else None
        by_address = None
        for client in self.clients:
            if mac and client.mac == mac:
                return client
            if by_address is None and client.ip and client.ip.lower() in wanted:
                by_address = client
        return by_address

    def uplink(self, addresses: list[str] | None) -> dict[str, Any] | None:
        """How a device reaches the home network, if an integration knows."""
        client = self.client(addresses)
        if client is None:
            return None
        return {"wired": client.wired, "via": client.via, "ssid": client.ssid}

    def access_point(self, clients: list[dict[str, Any]]) -> str | None:
        """Name the access point that most of the given Wi-Fi clients are on.

        Each client is ``{"mac", "addresses"}``. Clients roam; the one most
        of them report wins.
        """
        seen: Counter[str] = Counter()
        for identity in clients:
            client = self.client(identity.get("addresses"), identity.get("mac"))
            if client is not None and not client.wired and client.via:
                seen[client.via] += 1
        return seen.most_common(1)[0][0] if seen else None


class Enricher(Source):
    """A source that tells how devices are connected, from an integration."""

    async def run(self) -> None:
        """Read Home Assistant now and then until cancelled."""
        token = self.ctx.options.supervisor_token or ""
        while True:
            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(self.ctx.options.core_websocket_url) as ws,
            ):
                api = HomeAssistantApi(ws)
                await api.authenticate(token)
                knowledge = await self.read(api)
            await self.ctx.store.set_state(state_key(self.name), knowledge.dump())
            # Without the integration there is nothing to be connected to,
            # and nothing to report missing either.
            if knowledge.clients:
                await self.connected()
            else:
                self.unused()
            await asyncio.sleep(POLL_S)

    @abstractmethod
    async def read(self, api: HomeAssistantApi) -> Knowledge:
        """Read what the integration knows; nothing when it is not set up."""


def state_key(name: str) -> str:
    """Where an enricher keeps what it learned."""
    return f"enrich.{name}"


async def known(ctx: Context) -> Knowledge:
    """Return what every enricher learned, merged."""
    merged = Knowledge([])
    for cls in SOURCES:
        if isinstance(cls, type) and issubclass(cls, Enricher):
            raw = await ctx.store.get_state(state_key(cls.name))
            if raw:
                merged.clients += Knowledge.load(raw).clients
    return merged


# Import the enrichers so they register themselves.
from . import unifi  # noqa: E402

__all__ = ["Client", "Enricher", "Knowledge", "known", "unifi"]
