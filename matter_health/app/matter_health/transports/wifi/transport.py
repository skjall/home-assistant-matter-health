"""Wi-Fi: devices on an access point of the home network.

A Wi-Fi device reports its own link in the Wi-Fi Network Diagnostics cluster
(``0/54``): the access point it is associated with (BSSID), the channel and
the signal strength. Nothing needs polling; the Matter Server passes the
attributes on as they change.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, ClassVar

from ... import kinds
from .. import ROOT, TRANSPORTS, Transport, quality

#: Wi-Fi Network Diagnostics on the root endpoint.
BSSID = "0/54/0"
SECURITY = "0/54/1"
VERSION = "0/54/2"
CHANNEL = "0/54/3"
RSSI = "0/54/4"
#: The networks a device knows, each with its SSID and whether it is on it.
NETWORKS = "0/49/1"

#: Wi-Fi signal, in dBm: above -65 there is room to spare; at -75 and below
#: throughput drops and a device loses its association now and then.
STRONG_RSSI = -65
WEAK_RSSI = -75

SECURITY_NAMES = {1: "none", 2: "WEP", 3: "WPA", 4: "WPA2", 5: "WPA3"}
VERSION_NAMES = {0: "a", 1: "b", 2: "g", 3: "n", 4: "ac", 5: "ax", 6: "ah"}

STATE = "wifi.devices"


def _mac(value: Any) -> str | None:
    """Return a BSSID, reported as base64 bytes, as colon-separated hex."""
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except binascii.Error, ValueError:
        return None
    return ":".join(f"{byte:02x}" for byte in raw) if len(raw) == 6 else None


def _ssid(networks: Any) -> str | None:
    """Return the name of the network a device is connected to."""
    for network in networks if isinstance(networks, list) else []:
        if isinstance(network, dict) and network.get("1") and network.get("0"):
            try:
                raw = base64.b64decode(str(network["0"]), validate=True)
            except binascii.Error, ValueError:
                return None
            return raw.decode("utf-8", "replace") or None
    return None


def describe(attributes: dict[str, Any]) -> dict[str, Any]:
    """Return what a device's attributes say about its Wi-Fi link."""
    rssi = attributes.get(RSSI)
    return {
        "bssid": _mac(attributes.get(BSSID)),
        "ssid": _ssid(attributes.get(NETWORKS)),
        "channel": attributes.get(CHANNEL),
        "rssi": rssi if isinstance(rssi, int) else None,
        "security": SECURITY_NAMES.get(attributes.get(SECURITY) or 0),
        "standard": VERSION_NAMES.get(attributes.get(VERSION) or -1),
    }


@TRANSPORTS.register("wifi")
class WifiTransport(Transport):
    """Access points and the devices associated with them."""

    name: ClassVar[str] = "wifi"
    feature: ClassVar[int] = 0b1
    clusters: ClassVar[tuple[str, ...]] = ("0/54/", NETWORKS)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Nothing rated yet."""
        super().__init__(*args, **kwargs)
        self._rated: dict[str, str | None] = {}

    async def devices(self, attributes: dict[str, dict[str, Any]]) -> None:
        """Keep each device's link; report the links when a rating changed.

        A device keeps its last access point while it is away, so the page
        shows it where it was.
        """
        stored: dict[str, dict[str, Any]] = dict(
            await self.ctx.store.get_state(STATE) or {}
        )
        for subject, attrs in attributes.items():
            link = describe(attrs)
            if link["bssid"] is None and subject in stored:
                link = {**stored[subject], "rssi": None}
            stored[subject] = link
        await self.ctx.store.set_state(STATE, stored)
        rated = {
            subject: quality(link["rssi"], STRONG_RSSI, WEAK_RSSI)
            for subject, link in stored.items()
        }
        if rated != self._rated:
            self._rated = rated
            await self.ctx.emit(
                kinds.WIFI_LINKS,
                "matter_server",
                devices=[
                    {
                        "subject": subject,
                        "neighbour": f"ap:{link['bssid']}" if link["bssid"] else None,
                        "rssi": link["rssi"],
                        "quality": rated[subject],
                    }
                    for subject, link in sorted(stored.items())
                ],
            )

    async def picture(self, away: set[str]) -> list[dict[str, Any]]:
        """Access points on the home network, each device on its own."""
        stored: dict[str, dict[str, Any]] = await self.ctx.store.get_state(STATE) or {}
        entries: list[dict[str, Any]] = []
        points: dict[str, dict[str, Any]] = {}
        for subject, link in sorted(stored.items()):
            bssid = link.get("bssid")
            if bssid and bssid not in points:
                points[bssid] = {
                    "id": f"ap:{bssid}",
                    "subject": f"ap:{bssid}",
                    "kind": "gateway",
                    "parent": ROOT,
                    # The access point is known on the home network by it.
                    "mac": bssid,
                    "link": {},
                    "alternatives": 0,
                    "vendor": None,
                    "detail": {
                        "address": bssid[-8:],
                        "ssid": link.get("ssid"),
                        "channel": link.get("channel"),
                    },
                }
            entries.append(
                {
                    "id": subject,
                    "subject": subject,
                    "kind": "device",
                    "parent": f"ap:{bssid}" if bssid else None,
                    "link": {
                        "rssi": link.get("rssi"),
                        "quality": quality(link.get("rssi"), STRONG_RSSI, WEAK_RSSI),
                    },
                    "alternatives": 0,
                    "vendor": None,
                    "detail": {
                        "channel": link.get("channel"),
                        "security": link.get("security"),
                        "standard": link.get("standard"),
                    },
                }
            )
        # A device that reported no link yet has no way in to show.
        mine: dict[str, str] = await self.ctx.store.get_state("matter.transports") or {}
        for subject, transport in sorted(mine.items()):
            if transport == self.name and subject not in stored:
                entries.append(
                    {
                        "id": subject,
                        "subject": subject,
                        "kind": "device",
                        "parent": None,
                        "link": {},
                        "alternatives": 0,
                        "vendor": None,
                    }
                )
        del away  # a device away keeps its last access point
        return [*points.values(), *entries]

    async def summary(self, devices: int) -> dict[str, Any]:
        """How many access points the devices are spread over."""
        del devices
        stored: dict[str, dict[str, Any]] = await self.ctx.store.get_state(STATE) or {}
        points = {link.get("bssid") for link in stored.values() if link.get("bssid")}
        return {"gateways": len(points), "connected": None}
