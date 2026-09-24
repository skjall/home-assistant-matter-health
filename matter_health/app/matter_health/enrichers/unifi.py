"""A UniFi Network controller: which client is wired, which on which access point.

Everything is read from the controller's device and client lists; nothing is
changed. A UniFi OS console (Cloud Key, Dream Machine, Cloud Gateway)
accepts an API key or a login; a standalone Network application only a
login. An account with the read-only role is enough.
"""

from __future__ import annotations

from typing import Any, ClassVar

import aiohttp

from ..engine import SOURCES
from . import Client, Enricher, Knowledge

#: Where a UniFi OS console serves the Network application.
UNIFI_OS_PREFIX = "/proxy/network"

TIMEOUT = aiohttp.ClientTimeout(total=30)


@SOURCES.register("unifi")
class UnifiEnricher(Enricher):
    """Reads a UniFi Network controller."""

    name: ClassVar[str] = "unifi"
    option: ClassVar[str] = "unifi"

    @classmethod
    def enabled(cls, options: Any) -> bool:
        """Run only when a controller is configured."""
        return bool(cls.settings(options).get("url"))

    async def read(self, session: aiohttp.ClientSession) -> Knowledge:
        """Log in, then read devices and clients of the site."""
        settings = self.settings(self.ctx.options)
        base = str(settings["url"]).rstrip("/")
        ssl = bool(settings.get("verify_ssl", False))
        prefix, headers = await login(session, base, settings, ssl)
        site = settings.get("site") or "default"

        async def get(path: str) -> list[dict[str, Any]]:
            url = f"{base}{prefix}/api/s/{site}/{path}"
            async with session.get(
                url, headers=headers, ssl=ssl, timeout=TIMEOUT
            ) as response:
                response.raise_for_status()
                return list((await response.json()).get("data", []))

        return parse(await get("stat/device"), await get("stat/sta"))


async def login(
    session: aiohttp.ClientSession, base: str, settings: dict[str, Any], ssl: bool
) -> tuple[str, dict[str, str]]:
    """Return the path prefix of the Network application and the headers to send.

    An API key is sent with every request. A login leaves a cookie in the
    session: UniFi OS takes it at ``/api/auth/login``, the standalone
    application at ``/api/login``.
    """
    if settings.get("api_key"):
        return UNIFI_OS_PREFIX, {"X-API-KEY": str(settings["api_key"])}
    body = {"username": settings.get("username"), "password": settings.get("password")}
    for path, prefix in (("/api/auth/login", UNIFI_OS_PREFIX), ("/api/login", "")):
        async with session.post(
            f"{base}{path}", json=body, ssl=ssl, timeout=TIMEOUT
        ) as response:
            if response.status in (404, 405):
                continue
            if response.status in (401, 403):
                raise PermissionError("UniFi refused the login")
            response.raise_for_status()
            return prefix, {}
    raise ConnectionError("No UniFi Network application found")


def parse(devices: list[dict[str, Any]], stations: list[dict[str, Any]]) -> Knowledge:
    """Turn the controller's lists into what every enricher says."""
    names = {
        str(d["mac"]).lower(): d.get("name") or d.get("model")
        for d in devices
        if d.get("mac")
    }
    access_points = {
        str(vap["bssid"]).lower(): str(device["mac"]).lower()
        for device in devices
        if device.get("mac")
        for vap in device.get("vap_table") or []
        if vap.get("bssid")
    }
    clients = []
    for device in devices:
        if not device.get("mac"):
            continue
        uplink = device.get("uplink") or {}
        kind = uplink.get("type")
        clients.append(
            Client(
                mac=str(device["mac"]).lower(),
                ip=device.get("ip"),
                name=names[str(device["mac"]).lower()],
                wired=None if kind is None else kind == "wire",
                via=uplink.get("uplink_device_name")
                or names.get(str(uplink.get("uplink_mac") or "").lower()),
                port=uplink.get("uplink_remote_port"),
            )
        )
    for station in stations:
        if not station.get("mac"):
            continue
        wired = bool(station.get("is_wired"))
        via = station.get("sw_mac") if wired else station.get("ap_mac")
        clients.append(
            Client(
                mac=str(station["mac"]).lower(),
                ip=station.get("ip") or station.get("last_ip"),
                name=station.get("name") or station.get("hostname"),
                wired=wired,
                via=names.get(str(via or "").lower()),
                port=station.get("sw_port") if wired else None,
                ssid=None if wired else station.get("essid"),
                signal=None if wired else station.get("signal"),
            )
        )
    return Knowledge(clients, access_points)
