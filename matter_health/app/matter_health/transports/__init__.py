"""How Matter devices reach Home Assistant: one module per transport.

Matter runs over Thread, Wi-Fi and Ethernet, and almost everything that can
go wrong differs between them. A Thread device hangs on a parent in a mesh
that border routers connect to the home network; a Wi-Fi device hangs on an
access point; a wired one on the home network itself. Each transport is
therefore a module of its own, next to the others and built alike: it reads
what it needs from the Matter Server, brings its own sources and rules, and
draws its part of the network picture. The rest of the add-on - devices
coming and going, pairing, names - knows none of them.

A device says which transport it uses in the feature map of its Network
Commissioning cluster (attribute ``0/49/65532``), one bit per transport.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from ..engine import Context
from ..registry import Registry

#: The Network Commissioning cluster's feature map on the root endpoint.
FEATURE_MAP = "0/49/65532"

#: Where each device hangs in the network picture while nothing is known.
ROOT = "home"

#: What a node in the network picture is, whatever the transport:
#: ``gateway`` connects the transport to the home network (a Thread border
#: router, a Wi-Fi access point), ``relay`` passes messages on for others,
#: ``device`` and ``sleepy`` (a battery device that wakes now and then) only
#: speak for themselves, ``unknown`` is something the transport sees but
#: Home Assistant does not control.
KINDS = frozenset({"gateway", "relay", "device", "sleepy", "unknown"})

#: A read-only question to the Matter Server: command in, answer out.
Ask = Callable[[str], Awaitable[Any]]


class Transport(ABC):
    """One way a Matter device reaches Home Assistant.

    The Matter Server source holds one instance per transport while it is
    connected: it hands each transport the attributes of its devices and lets
    it poll the server. The web API makes its own instances, which only read
    what the polling ones stored; everything a transport shows must therefore
    come from the store.
    """

    name: ClassVar[str]
    #: The transport's bit in the Network Commissioning feature map.
    feature: ClassVar[int]
    #: Matter Server commands the transport polls with; they must only read.
    commands: ClassVar[frozenset[str]] = frozenset()
    #: Attribute path prefixes of its devices the transport wants to see,
    #: such as ``"0/54/"`` for Wi-Fi diagnostics.
    clusters: ClassVar[tuple[str, ...]] = ()

    def __init__(self, ctx: Context) -> None:
        """Share the add-on's context."""
        self.ctx = ctx

    async def poll(self, ask: Ask) -> None:  # noqa: B027 - most transports don't
        """Read from the Matter Server what devices do not report themselves."""

    async def devices(self, attributes: dict[str, dict[str, Any]]) -> None:  # noqa: B027
        """Take note of the attributes of this transport's devices, by subject."""

    @abstractmethod
    async def picture(self, away: set[str]) -> list[dict[str, Any]]:
        """Return this transport's part of the network picture.

        One entry per node: ``id`` (unique across transports), ``subject``,
        ``kind`` (see ``KINDS``), ``parent`` (an id, ``ROOT`` or None for a
        device without a way in), ``link`` with ``rssi`` and ``quality``
        (strong, medium, weak or None), ``alternatives`` (other relaying
        neighbours a relay could switch to), ``vendor`` and ``detail`` (what
        the transport has to add, shown as it is). Devices in ``away`` are
        included where they were last seen.
        """

    @abstractmethod
    async def summary(self, devices: int) -> dict[str, Any]:
        """Return what the overview says about this transport.

        ``devices`` is how many devices use it. The answer has ``gateways``
        (how many connect it to the home network, None where it has none)
        and ``connected`` (whether it works as a whole; None where there is
        nothing to tell).
        """


TRANSPORTS: Registry[type[Transport]] = Registry("transport")


def transport_of(attributes: dict[str, Any]) -> str | None:
    """Return the transport a device uses, from its Network Commissioning features."""
    features = attributes.get(FEATURE_MAP)
    if not isinstance(features, int):
        return None
    for name in TRANSPORTS.names():
        if features & TRANSPORTS.get(name).feature:
            return name
    return None


def quality(rssi: int | None, strong: int, weak: int) -> str | None:
    """Rate a signal: strong at or above ``strong`` dBm, weak at or below ``weak``."""
    if rssi is None:
        return None
    if rssi >= strong:
        return "strong"
    return "weak" if rssi <= weak else "medium"


# Import the transports so they register themselves.
from . import ethernet, thread, wifi  # noqa: E402

__all__ = [
    "KINDS",
    "ROOT",
    "TRANSPORTS",
    "Ask",
    "Transport",
    "ethernet",
    "quality",
    "thread",
    "transport_of",
    "wifi",
]
