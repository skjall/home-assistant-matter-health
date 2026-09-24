"""The UniFi Network integration: which client is wired, which on which access point.

Read from Home Assistant: the integration's device trackers say per client
its address and, for a Wi-Fi client, the access point and network it uses;
a wired client has neither. The integration also tracks its own switches
and access points; they carry no client details, and their way into the
network is not told. Access points are named as the device registry names
them.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..engine import SOURCES
from ..sources.home_assistant import HomeAssistantApi
from . import Client, Enricher, Knowledge

#: The integration's domain in Home Assistant.
DOMAIN = "unifi"

#: Attributes only a client's tracker carries, not a UniFi device's.
CLIENT_ATTRIBUTES = frozenset({"host_name", "is_guest"})


@SOURCES.register("unifi")
class UnifiEnricher(Enricher):
    """Reads the UniFi Network integration."""

    name: ClassVar[str] = "unifi"

    async def read(self, api: HomeAssistantApi) -> Knowledge:
        """Read the integration's trackers, their states and the devices."""
        entities = await api.call({"type": "config/entity_registry/list"})
        trackers = {
            e["entity_id"]
            for e in entities
            if e.get("platform") == DOMAIN
            and str(e.get("entity_id", "")).startswith("device_tracker.")
        }
        if not trackers:
            return Knowledge([])
        states = await api.call({"type": "get_states"})
        devices = await api.call({"type": "config/device_registry/list"})
        return parse(trackers, states, devices)


def parse(
    trackers: set[str], states: list[dict[str, Any]], devices: list[dict[str, Any]]
) -> Knowledge:
    """Turn the integration's trackers into what every enricher says."""
    names: dict[str, str] = {}
    for device in devices:
        name = device.get("name_by_user") or device.get("name")
        for kind, value in device.get("connections") or []:
            if kind == "mac" and name:
                names[str(value).lower()] = name
    clients = []
    for state in states:
        attributes = state.get("attributes") or {}
        if (
            state.get("entity_id") not in trackers
            # Away, the tracker keeps where the client was, not where it is.
            or state.get("state") != "home"
            or not attributes.get("mac")
            or not CLIENT_ATTRIBUTES & attributes.keys()
        ):
            continue
        access_point = attributes.get("ap_mac")
        wireless = bool(access_point or attributes.get("essid"))
        clients.append(
            Client(
                mac=str(attributes["mac"]).lower(),
                ip=attributes.get("ip"),
                name=attributes.get("name") or attributes.get("host_name"),
                wired=not wireless,
                via=names.get(str(access_point or "").lower()),
                ssid=attributes.get("essid"),
            )
        )
    return Knowledge(clients)
