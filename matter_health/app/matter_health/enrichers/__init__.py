"""What other systems know about how devices reach the home network.

Matter and Thread say nothing about the home network underneath: whether a
border router is on Wi-Fi or on a cable, which access point a Wi-Fi network
announced by a BSSID is. The network equipment knows. An enricher reads one
kind of network equipment - a UniFi controller, say - and answers in the
same few words as every other: which clients it sees, how each is connected
and through what, and which access point sends which BSSID.

Enrichers stand next to each other like transports. Each is a source that
runs only when the user configured it, polls its system read-only and keeps
what it learned in the store; the page merges what all of them know. Without
any, the picture simply does not say how a gateway is connected.
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

import aiohttp

from ..engine import SOURCES, Context, Source
from ..transports import quality

#: How a device is connected changes rarely.
POLL_S = 300.0

#: A Wi-Fi uplink is rated like a Matter device's Wi-Fi link.
STRONG_SIGNAL = -65
WEAK_SIGNAL = -75


@dataclass(frozen=True, slots=True)
class Client:
    """One device on the home network, as the network equipment sees it."""

    mac: str
    ip: str | None = None
    name: str | None = None
    #: True on a cable, False on Wi-Fi, None where the equipment does not say.
    wired: bool | None = None
    #: The switch or access point it is connected to.
    via: str | None = None
    #: The switch port, for a wired device.
    port: int | None = None
    ssid: str | None = None
    #: Signal in dBm as the access point hears it, for a Wi-Fi device.
    signal: int | None = None


@dataclass(slots=True)
class Knowledge:
    """What one or more enrichers know."""

    clients: list[Client]
    #: Access point BSSID -> the access point's own MAC, all lower case.
    access_points: dict[str, str]

    def dump(self) -> dict[str, Any]:
        """Return a form the store keeps."""
        return {
            "clients": [asdict(c) for c in self.clients],
            "access_points": self.access_points,
        }

    @classmethod
    def load(cls, raw: dict[str, Any]) -> Knowledge:
        """Read what ``dump`` stored."""
        return cls(
            [Client(**c) for c in raw.get("clients", [])],
            dict(raw.get("access_points", {})),
        )

    def merge(self, other: Knowledge) -> None:
        """Add what another enricher knows."""
        self.clients += other.clients
        self.access_points.update(other.access_points)

    def client(
        self, addresses: list[str] | None = None, mac: str | None = None
    ) -> Client | None:
        """Find a client by MAC, else by any of its IP addresses."""
        wanted = {a.lower() for a in addresses or []}
        mac = mac.lower() if mac else None
        by_ip = None
        for client in self.clients:
            if mac and client.mac == mac:
                return client
            if by_ip is None and client.ip and client.ip.lower() in wanted:
                by_ip = client
        return by_ip

    def uplink(
        self, addresses: list[str] | None = None, mac: str | None = None
    ) -> dict[str, Any] | None:
        """How a device reaches the home network, if anyone knows.

        A BSSID is resolved to its access point first: the access point is
        connected like any other client.
        """
        if mac and mac.lower() in self.access_points:
            mac = self.access_points[mac.lower()]
        client = self.client(addresses, mac)
        if client is None or client.wired is None:
            return None
        return {
            "wired": client.wired,
            "via": client.via,
            "port": client.port,
            "ssid": client.ssid,
            "signal": client.signal,
            "quality": None
            if client.wired
            else quality(client.signal, STRONG_SIGNAL, WEAK_SIGNAL),
        }

    def access_point_name(self, bssid: str) -> str | None:
        """Return what the owner named the access point sending ``bssid``."""
        mac = self.access_points.get(bssid.lower())
        client = self.client(mac=mac) if mac else None
        return client.name if client else None


class Enricher(Source):
    """A source that tells how devices are connected to the home network."""

    #: Where its options sit in the add-on options.
    option: ClassVar[str]

    @classmethod
    def settings(cls, options: Any) -> dict[str, Any]:
        """Return this enricher's own options."""
        value = options.extra.get(cls.option)
        return value if isinstance(value, dict) else {}

    async def run(self) -> None:
        """Poll the equipment until cancelled."""
        # Controllers are commonly reached by IP address, whose cookies the
        # default jar refuses.
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as session:
            while True:
                knowledge = await self.read(session)
                await self.ctx.store.set_state(state_key(self.name), knowledge.dump())
                await self.connected()
                await asyncio.sleep(POLL_S)

    @abstractmethod
    async def read(self, session: aiohttp.ClientSession) -> Knowledge:
        """Read what the equipment knows; raising means "try again later"."""


def state_key(name: str) -> str:
    """Where an enricher keeps what it learned."""
    return f"enrich.{name}"


async def known(ctx: Context) -> Knowledge:
    """Return what every enricher learned, merged."""
    merged = Knowledge([], {})
    for cls in SOURCES:
        if isinstance(cls, type) and issubclass(cls, Enricher):
            raw = await ctx.store.get_state(state_key(cls.name))
            if raw:
                merged.merge(Knowledge.load(raw))
    return merged


# Import the enrichers so they register themselves.
from . import unifi  # noqa: E402

__all__ = ["Client", "Enricher", "Knowledge", "known", "unifi"]
