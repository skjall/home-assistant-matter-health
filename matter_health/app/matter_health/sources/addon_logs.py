"""Follow the logs of the observed add-ons and hand each line to its parser."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar

import aiohttp

from ..config import MATTER_SERVER_SLUG
from ..engine import SOURCES, Source
from ..parsers import PARSERS, LineParser
from ..supervisor import LogLine, Supervisor

#: How often the reading position is stored while nothing is found. Lines
#: read again after a restart within that time find nothing new either.
SAVE_EVERY = timedelta(seconds=10)


@dataclass
class Position:
    """The last line read: its journal time and how many lines had that time.

    The Supervisor repeats the last lines of a log on every connection. A
    pairing read twice is a pairing reported twice, so lines up to here are
    skipped. Several lines can share a journal time - a message split over
    lines - hence the count.
    """

    at: datetime | None = None
    count: int = 0

    @classmethod
    def load(cls, stored: dict[str, Any] | None) -> Position:
        """Read a stored position; nothing stored reads everything."""
        if not stored or not stored.get("at"):
            return cls()
        return cls(datetime.fromisoformat(stored["at"]), int(stored.get("count", 0)))

    def dump(self) -> dict[str, Any]:
        """Return the position in a form the store keeps."""
        return {"at": self.at.isoformat() if self.at else None, "count": self.count}

    def advance(self, line: LogLine, seen: dict[datetime, int]) -> bool:
        """Move past ``line``; return whether it is new.

        ``seen`` counts the lines of this connection by journal time, since
        the lines repeated on connecting are those with the stored time too.
        """
        if line.at is None:
            return True
        seen[line.at] = seen.get(line.at, 0) + 1
        if self.at is not None and (
            line.at < self.at or (line.at == self.at and seen[line.at] <= self.count)
        ):
            return False
        self.at, self.count = line.at, seen[line.at]
        return True


class AddonLogSource(Source):
    """Reads one add-on's log from where it stopped; subclasses name the add-on."""

    slug: ClassVar[str]
    parser: ClassVar[str]

    def make_parser(self) -> LineParser:
        """Return a fresh parser; parsers may keep state between lines."""
        return PARSERS.get(self.parser)()

    async def run(self) -> None:
        """Follow the log until cancelled or the stream ends."""
        parser = self.make_parser()
        key = f"log.{self.name}"
        position = Position.load(await self.ctx.store.get_state(key))
        seen: dict[datetime, int] = {}
        saved = position.at
        async with aiohttp.ClientSession() as session:
            supervisor = Supervisor(session, self.ctx.options)
            async for line in supervisor.follow_logs(self.slug, self.connected):
                if not position.advance(line, seen):
                    continue
                found = parser.parse(line.text)
                for parsed in found:
                    await self.emit(
                        parsed.kind, parsed.subject, at=line.at, **parsed.data
                    )
                if line.at and (
                    found or saved is None or line.at - saved >= SAVE_EVERY
                ):
                    await self.ctx.store.set_state(key, position.dump())
                    saved = line.at


@SOURCES.register("matter_server_log")
class MatterServerLog(AddonLogSource):
    """Commissioning steps from the Matter Server add-on."""

    name: ClassVar[str] = "matter_server_log"
    slug: ClassVar[str] = MATTER_SERVER_SLUG
    parser: ClassVar[str] = "matter_js"
