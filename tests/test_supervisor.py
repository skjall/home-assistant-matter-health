from datetime import UTC, datetime

import aiohttp
import pytest
from conftest import FakeSupervisor, start_supervisor
from pytest_aiohttp import AiohttpServer

from matter_health.config import Options
from matter_health.supervisor import LogLine, Supervisor, journal_line

ADDONS = {
    "running": {"version": "1.0", "state": "started", "hostname": "matter-host"},
    "stopped": {"version": "1.0", "state": "stopped", "hostname": "otbr-host"},
    "unversioned": {"state": "started"},
    "nameless": {"version": "1.0", "state": "started"},
}


async def test_addon_info_and_url(aiohttp_server: AiohttpServer) -> None:
    fake = FakeSupervisor(ADDONS)
    base = await start_supervisor(aiohttp_server, fake)
    options = Options(supervisor_url=base + "/", supervisor_token="secret")

    async with aiohttp.ClientSession() as session:
        supervisor = Supervisor(session, options)
        assert await supervisor.addon_info("running") == ADDONS["running"]
        assert await supervisor.addon_info("unversioned") is None
        assert await supervisor.addon_info("absent") is None
        assert (
            await supervisor.addon_url("running", 5580, "ws", None)
            == "ws://matter-host:5580"
        )
        assert await supervisor.addon_url("stopped", 8081, "http", None) is None
        with pytest.raises(ConnectionError, match="no host name for nameless"):
            await supervisor.addon_url("nameless", 5580, "ws", None)
        assert await supervisor.addon_url("absent", 8081, "http", None) is None
        assert (
            await supervisor.addon_url("absent", 8081, "http", "http://192.0.2.10/")
            == "http://192.0.2.10"
        )

    assert all(
        headers.get("Authorization") == "Bearer secret" for _, headers in fake.requests
    )


async def test_follow_logs_yields_lines_after_opening(
    aiohttp_server: AiohttpServer,
) -> None:
    fake = FakeSupervisor(logs={"running": ["first\r\n", "second\n", "third"]})
    base = await start_supervisor(aiohttp_server, fake)
    opened: list[str] = []

    async def on_open() -> None:
        opened.append("open")

    async with aiohttp.ClientSession() as session:
        supervisor = Supervisor(session, Options(supervisor_url=base))
        lines = [line async for line in supervisor.follow_logs("running", on_open)]
        # Works without a callback too.
        again = [line async for line in supervisor.follow_logs("running")]

    assert opened == ["open"]
    assert [line.text for line in lines] == ["first", "second", "third"]
    assert lines == again
    path, headers = fake.requests[0]
    assert path == "/addons/running/logs/follow"
    assert headers["Accept"] == "text/x-log"
    assert "Authorization" not in headers


async def test_follow_logs_raises_when_refused(aiohttp_server: AiohttpServer) -> None:
    base = await start_supervisor(aiohttp_server, FakeSupervisor())

    async with aiohttp.ClientSession() as session:
        supervisor = Supervisor(session, Options(supervisor_url=base))
        with pytest.raises(aiohttp.ClientResponseError):
            async for _ in supervisor.follow_logs("absent"):
                pass


def test_journal_line_takes_the_time_from_the_prefix() -> None:
    line = journal_line(
        "2030-01-02 03:04:05.678 host addon_example[42]: INFO something happened"
    )

    assert line.text == "INFO something happened"
    assert line.at == datetime(2030, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)


def test_journal_line_without_prefix_keeps_the_text() -> None:
    assert journal_line("INFO no prefix") == LogLine("INFO no prefix")
