"""The Supervisor API: where the observed add-ons live and what they log."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import aiohttp

from .config import Options

_LOGGER = logging.getLogger(__name__)


class Supervisor:
    """Read-only access to add-on information and logs."""

    def __init__(self, session: aiohttp.ClientSession, options: Options) -> None:
        """Use ``session`` with the Supervisor token from ``options``."""
        self._session = session
        self._base = options.supervisor_url.rstrip("/")
        self._headers = (
            {"Authorization": f"Bearer {options.supervisor_token}"}
            if options.supervisor_token
            else {}
        )

    async def addon_info(self, slug: str) -> dict[str, Any] | None:
        """Return the Supervisor's description of an add-on, if installed."""
        async with self._session.get(
            f"{self._base}/addons/{slug}/info", headers=self._headers
        ) as response:
            if response.status != 200:
                return None
            body: dict[str, Any] = await response.json()
            data: dict[str, Any] = body.get("data") or {}
            return data if data.get("version") else None

    async def addon_url(
        self, slug: str, port: int, scheme: str, override: str | None
    ) -> str | None:
        """Where an add-on answers, preferring the user's explicit address.

        Add-ons on the host network are reached through their Supervisor host
        name, which resolves to the host; that is also what Home Assistant
        itself uses for the Matter Server.
        """
        if override:
            return override.rstrip("/")
        info = await self.addon_info(slug)
        if not info or info.get("state") != "started":
            return None
        hostname = info.get("hostname")
        if not hostname:
            raise ConnectionError(f"the Supervisor gave no host name for {slug}")
        return f"{scheme}://{hostname}:{port}"

    async def follow_logs(
        self, slug: str, on_open: Callable[[], Awaitable[None]] | None = None
    ) -> AsyncIterator[str]:
        """Yield an add-on's log lines as they are written, starting now.

        ``on_open`` runs once the Supervisor accepted the request, before the
        first line - a quiet log is still a working one.
        """
        headers = {**self._headers, "Accept": "text/plain"}
        timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
        async with self._session.get(
            f"{self._base}/addons/{slug}/logs/follow",
            headers=headers,
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            if on_open is not None:
                await on_open()
            async for raw in response.content:
                yield raw.decode("utf-8", errors="replace").rstrip("\r\n")
