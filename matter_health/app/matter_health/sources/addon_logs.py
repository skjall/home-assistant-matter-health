"""Follow the logs of the observed add-ons and hand each line to its parser."""

from __future__ import annotations

from typing import ClassVar

import aiohttp

from ..config import MATTER_SERVER_SLUG, OTBR_SLUG
from ..engine import SOURCES, Source
from ..parsers import PARSERS, LineParser
from ..supervisor import Supervisor


class AddonLogSource(Source):
    """Reads one add-on's log from now on; subclasses name add-on and parser."""

    slug: ClassVar[str]
    parser: ClassVar[str]

    def make_parser(self) -> LineParser:
        """Return a fresh parser; parsers may keep state between lines."""
        return PARSERS.get(self.parser)()

    async def run(self) -> None:
        """Follow the log until cancelled or the stream ends."""
        parser = self.make_parser()
        async with aiohttp.ClientSession() as session:
            supervisor = Supervisor(session, self.ctx.options)
            async for line in supervisor.follow_logs(self.slug, self.connected):
                for parsed in parser.parse(line):
                    await self.emit(parsed.kind, parsed.subject, **parsed.data)


@SOURCES.register("matter_server_log")
class MatterServerLog(AddonLogSource):
    """Commissioning steps from the Matter Server add-on."""

    name: ClassVar[str] = "matter_server_log"
    slug: ClassVar[str] = MATTER_SERVER_SLUG
    parser: ClassVar[str] = "matter_js"


@SOURCES.register("otbr_log")
class OtbrLog(AddonLogSource):
    """Mesh splits from the OpenThread Border Router add-on."""

    name: ClassVar[str] = "otbr_log"
    slug: ClassVar[str] = OTBR_SLUG
    parser: ClassVar[str] = "openthread"
