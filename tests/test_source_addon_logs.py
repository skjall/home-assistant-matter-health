from datetime import UTC, datetime

import aiohttp
import pytest
from conftest import FakeSupervisor, source_for, start_supervisor
from pytest_aiohttp import AiohttpServer

from matter_health import kinds
from matter_health.config import MATTER_SERVER_SLUG, OTBR_SLUG
from matter_health.engine import Context
from matter_health.sources.addon_logs import MatterServerLog
from matter_health.store import Store
from matter_health.transports.thread.otbr import OtbrLog


async def test_matter_server_log_becomes_commissioning_events(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeSupervisor(
        logs={
            MATTER_SERVER_SLUG: [
                "INFO PaseClient Establish PASE to device fd00:db8::1\n",
                "INFO something unrelated\n",
                "INFO Start commissioning of node @1:2a into fabric index 1\n",
                "INFO Commissioned peer as @1:2a\n",
            ]
        }
    )
    base = await start_supervisor(aiohttp_server, fake)
    source = source_for(MatterServerLog, ctx, supervisor_url=base)

    await source.run()

    found = [(e.kind, e.subject, e.source) for e in await store.events()]
    assert found == [
        (kinds.COMMISSIONING_CONTACT, None, "matter_server_log"),
        (kinds.COMMISSIONING_STARTED, "node:42", "matter_server_log"),
        (kinds.COMMISSIONING_COMPLETED, "node:42", "matter_server_log"),
    ]
    assert source.ctx._engine is not None
    assert source.ctx._engine.status["matter_server_log"]["ok"] is True
    assert fake.requests[0][1]["Authorization"] == "Bearer test-token"


async def test_otbr_log_reports_a_split_mesh_once(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    lost = "[W] Mle-----------: Leader age timeout\n"
    base = await start_supervisor(
        aiohttp_server, FakeSupervisor(logs={OTBR_SLUG: [lost, lost, lost]})
    )
    source = source_for(OtbrLog, ctx, supervisor_url=base)

    await source.run()

    assert [e.kind for e in await store.events()] == [kinds.THREAD_LEADER_LOST]


async def test_a_refused_log_is_an_error(
    ctx: Context, aiohttp_server: AiohttpServer
) -> None:
    base = await start_supervisor(aiohttp_server, FakeSupervisor())
    source = source_for(OtbrLog, ctx, supervisor_url=base)

    with pytest.raises(aiohttp.ClientResponseError):
        await source.run()

    assert source.ctx._engine is not None
    assert source.ctx._engine.status["otbr_log"]["ok"] is None


def journal(second: int, text: str) -> str:
    return f"2030-01-02 03:04:{second:02d}.000 host addon_example[1]: {text}\n"


PAIRING = [
    journal(1, "INFO PaseClient Establish PASE to device fd00:db8::1"),
    journal(2, "INFO Start commissioning of node @1:2a into fabric index 1"),
    journal(2, "INFO Commissioned peer as @1:2a"),
]


async def test_events_carry_the_time_the_line_was_written(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    base = await start_supervisor(
        aiohttp_server, FakeSupervisor(logs={MATTER_SERVER_SLUG: PAIRING})
    )

    await source_for(MatterServerLog, ctx, supervisor_url=base).run()

    assert [e.at for e in await store.events()] == [
        datetime(2030, 1, 2, 3, 4, second, tzinfo=UTC) for second in (1, 2, 2)
    ]


async def test_lines_repeated_on_reconnect_are_read_once(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeSupervisor(logs={MATTER_SERVER_SLUG: PAIRING})
    base = await start_supervisor(aiohttp_server, fake)
    await source_for(MatterServerLog, ctx, supervisor_url=base).run()

    # The Supervisor starts every connection with the lines it already sent.
    fake.logs[MATTER_SERVER_SLUG] = [
        *PAIRING,
        journal(3, "INFO PaseClient Establish PASE to device fd00:db8::2"),
    ]
    await source_for(MatterServerLog, ctx, supervisor_url=base).run()

    assert [e.kind for e in await store.events()] == [
        kinds.COMMISSIONING_CONTACT,
        kinds.COMMISSIONING_STARTED,
        kinds.COMMISSIONING_COMPLETED,
        kinds.COMMISSIONING_CONTACT,
    ]


async def test_a_line_sharing_the_last_time_is_still_new(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeSupervisor(logs={MATTER_SERVER_SLUG: PAIRING[:2]})
    base = await start_supervisor(aiohttp_server, fake)
    await source_for(MatterServerLog, ctx, supervisor_url=base).run()

    fake.logs[MATTER_SERVER_SLUG] = PAIRING
    await source_for(MatterServerLog, ctx, supervisor_url=base).run()

    assert [e.kind for e in await store.events()][-1] == kinds.COMMISSIONING_COMPLETED
    assert len(await store.events()) == 3


async def test_the_position_is_kept_while_nothing_is_found(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    quiet = [journal(second, "INFO nothing to see") for second in (0, 5, 11)]
    base = await start_supervisor(
        aiohttp_server, FakeSupervisor(logs={MATTER_SERVER_SLUG: quiet})
    )

    await source_for(MatterServerLog, ctx, supervisor_url=base).run()

    stored = await store.get_state("log.matter_server_log")
    assert stored == {"at": "2030-01-02T03:04:11+00:00", "count": 1}
