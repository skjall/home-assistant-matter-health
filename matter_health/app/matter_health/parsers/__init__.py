"""Turn log lines of the observed add-ons into events.

Some facts exist only in a log: the steps of adding a device, or that the
border router heard a router of a second partition. A parser reads one add-on's
lines and returns what they mean, one parser per log format. Adding a pattern
is adding a line to a parser's table.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..registry import Registry

#: Terminal colour codes some add-ons write into their log.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True, slots=True)
class Parsed:
    """What a log line means: an event kind, its subject and data."""

    kind: str
    subject: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class LineParser(ABC):
    """Reads the lines of one add-on's log."""

    @abstractmethod
    def parse(self, line: str) -> list[Parsed]:
        """Everything this line says; usually nothing."""


PARSERS: Registry[type[LineParser]] = Registry("parser")


def clean(line: str) -> str:
    """Return the line without colour codes."""
    return ANSI.sub("", line)


def peer_node_id(peer: str) -> int:
    """Return the node id from matter.js' ``@<fabric>:<node id in hex>``."""
    return int(peer.split(":", 1)[1], 16)


# Import the parsers so they register themselves.
from . import matter_js, openthread  # noqa: E402

__all__ = ["PARSERS", "LineParser", "Parsed", "clean", "matter_js", "openthread"]
