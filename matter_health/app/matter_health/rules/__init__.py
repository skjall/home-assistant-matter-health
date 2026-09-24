"""Everything the add-on concludes. Each module registers one rule."""

from . import (
    border_router,
    flaky,
    host,
    mesh,
    offline,
    pairing,
    radio,
    relay,
    server,
    signal,
    wave,
)

__all__ = [
    "border_router",
    "flaky",
    "host",
    "mesh",
    "offline",
    "pairing",
    "radio",
    "relay",
    "server",
    "signal",
    "wave",
]
