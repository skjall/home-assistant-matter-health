"""The OpenThread Border Router's log: signs of trouble only the log shows.

Role and partition of the border router itself are polled from its REST API.
Some facts are only in the log:

- the leader stopped answering, or a router of *another* partition is in
  range - the mesh is split while the border router itself still looks fine;
- the radio could not send because the channel was busy - once in a while is
  normal, many times in a few minutes means interference;
- the border router lost contact with its radio stick;
- the start script found IPv6 forwarding switched off, so devices in the mesh
  cannot answer anything outside it.

OpenThread repeats most of these every second while the condition lasts; a
line is reported once and again only after it stopped for a while.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable

from ... import kinds
from ...parsers import PARSERS, LineParser, Parsed, clean

#: A message repeated within this many seconds belongs to the same episode.
REPEAT_WINDOW_S = 30.0

#: Busy-channel failures are counted over this many seconds ...
BUSY_WINDOW_S = 600.0

#: ... and this many of them in that time mean interference. Occasional
#: failures are normal; several per minute for minutes are not.
BUSY_AFTER = 20

#: A busy channel is reported again at most this often while it lasts.
BUSY_REPEAT_S = 1800.0

#: One failed transmission writes three lines; only this one is counted.
CHANNEL_BUSY = re.compile(r"Handle transmit done failed: ChannelAccessFailure")

#: The border router lost its radio, or its radio ran out of buffers.
RADIO_FAULT = re.compile(
    r"(RCP failure detected|Failed to communicate with RCP|radio tx timeout"
    r"|Wait for response timeout|NoBufs)"
)

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Mle-+: Leader age timeout"), kinds.THREAD_LEADER_LOST),
    (re.compile(r"Mle-+: Different partition"), kinds.THREAD_FOREIGN_PARTITION),
    (
        re.compile(r"IPv6 routing/forwarding is not enabled"),
        kinds.HOST_FORWARDING_OFF,
    ),
    (re.compile(r"Starting otbr-agent"), kinds.THREAD_AGENT_STARTED),
]


@PARSERS.register("openthread")
class OpenThreadParser(LineParser):
    """Mesh, radio and host trouble the border router writes to its log."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Use ``clock`` to tell repeats from new episodes."""
        self._clock = clock
        self._last_seen: dict[str, float] = {}
        self._busy: deque[float] = deque()
        self._busy_reported: float | None = None

    def parse(self, line: str) -> list[Parsed]:
        """Return the event this line reports, unless it is a repeat."""
        text = clean(line)
        if CHANNEL_BUSY.search(text):
            return self._channel_busy()
        fault = RADIO_FAULT.search(text)
        if fault:
            return self._once(kinds.THREAD_RADIO_FAULT, reason=fault.group(1))
        for pattern, kind in PATTERNS:
            if pattern.search(text):
                return self._once(kind)
        return []

    def _once(self, kind: str, **data: str) -> list[Parsed]:
        now = self._clock()
        last = self._last_seen.get(kind)
        self._last_seen[kind] = now
        if last is not None and now - last < REPEAT_WINDOW_S:
            return []
        return [Parsed(kind, data=dict(data))]

    def _channel_busy(self) -> list[Parsed]:
        now = self._clock()
        self._busy.append(now)
        while self._busy and now - self._busy[0] > BUSY_WINDOW_S:
            self._busy.popleft()
        if len(self._busy) < BUSY_AFTER:
            return []
        if self._busy_reported is not None and now - self._busy_reported < (
            BUSY_REPEAT_S
        ):
            return []
        self._busy_reported = now
        return [
            Parsed(
                kinds.THREAD_CHANNEL_BUSY,
                data={"count": len(self._busy), "minutes": int(BUSY_WINDOW_S // 60)},
            )
        ]
