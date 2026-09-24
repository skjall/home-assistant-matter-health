import aiohttp
import pytest
from conftest import FakeSupervisor, source_for, start_supervisor
from pytest_aiohttp import AiohttpServer

from matter_health import kinds
from matter_health.config import MATTER_SERVER_SLUG, OTBR_SLUG
from matter_health.engine import Context
from matter_health.sources.addon_logs import MatterServerLog, OtbrLog
from matter_health.store import Store


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
