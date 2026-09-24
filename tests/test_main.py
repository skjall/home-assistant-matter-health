import asyncio
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from matter_health import __main__ as entry
from matter_health.config import Options


async def test_serve_runs_until_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MATTER_HEALTH_TRUST_ALL", "1")
    options = Options(
        data_dir=tmp_path / "data",
        port=0,
        # Nothing answers here; every source fails and waits to retry.
        supervisor_url="http://127.0.0.1:9",
    )
    task = asyncio.create_task(entry.serve(options))
    for _ in range(100):
        if (options.data_dir / "matter_health.db").exists():
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (options.data_dir / "matter_health.db").exists()


def test_main_reads_options_and_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    options = Options(log_level="warning")
    served: list[Options] = []

    async def serve(given: Options) -> None:
        served.append(given)

    def run(coroutine: Coroutine[Any, Any, None]) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coroutine)
        finally:
            loop.close()

    monkeypatch.setattr(entry, "load_options", lambda: options)
    monkeypatch.setattr(entry, "serve", serve)
    monkeypatch.setattr(asyncio, "run", run)

    entry.main()

    assert served == [options]
    assert logging.getLogger("aiohttp.access").level == logging.WARNING
