"""Start the add-on: storage, sources, rules and the web page."""

from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from . import enrichers, rules, sources, transports
from .config import Options, load_options
from .engine import Context, Engine
from .store import Store
from .web import create_app

_LOGGER = logging.getLogger("matter_health")

# Importing the packages is what registers every source, rule, transport and
# enricher.
PLUGINS = (sources, rules, transports, enrichers)


async def serve(options: Options) -> None:
    """Run until cancelled."""
    options.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(options.data_dir / "matter_health.db")
    engine = Engine(Context(store=store, options=options))
    engine.start()
    app = create_app(engine, trust_all=os.environ.get("MATTER_HEALTH_TRUST_ALL") == "1")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=options.port)
    await site.start()
    _LOGGER.info(
        "watching with %d sources and %d rules on port %d",
        len(engine.sources),
        len(engine.rules),
        options.port,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await engine.stop()
        store.close()


def main() -> None:
    """Entry point of ``python -m matter_health``."""
    options = load_options()
    logging.basicConfig(
        level=options.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    asyncio.run(serve(options))


if __name__ == "__main__":
    main()
