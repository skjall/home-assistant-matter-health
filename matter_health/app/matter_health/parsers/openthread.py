"""The OpenThread Border Router's log: signs that the mesh fell apart.

Role and partition of the border router itself are polled from its REST API.
Two facts are only in the log: that the leader stopped answering, and that a
router of *another* partition is in range - the mesh is split while the border
router itself still looks fine.

OpenThread repeats both messages every second while the condition lasts; a
line is reported once and again only after it stopped for a while.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from .. import kinds
from . import PARSERS, LineParser, Parsed, clean

#: A message repeated within this many seconds belongs to the same episode.
REPEAT_WINDOW_S = 30.0

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Mle-+: Leader age timeout"), kinds.THREAD_LEADER_LOST),
    (re.compile(r"Mle-+: Different partition"), kinds.THREAD_FOREIGN_PARTITION),
]


@PARSERS.register("openthread")
class OpenThreadParser(LineParser):
    """Mesh trouble the border router notices before its own role changes."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Use ``clock`` to tell repeats from new episodes."""
        self._clock = clock
        self._last_seen: dict[str, float] = {}

    def parse(self, line: str) -> list[Parsed]:
        """Return the mesh event this line reports, unless it is a repeat."""
        text = clean(line)
        for pattern, kind in PATTERNS:
            if not pattern.search(text):
                continue
            now = self._clock()
            last = self._last_seen.get(kind)
            self._last_seen[kind] = now
            if last is not None and now - last < REPEAT_WINDOW_S:
                return []
            return [Parsed(kind)]
        return []
