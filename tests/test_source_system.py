from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from conftest import FakeSupervisor, source_for, start_supervisor
from pytest_aiohttp import AiohttpServer

from matter_health import kinds
from matter_health.config import MATTER_SERVER_SLUG, OTBR_SLUG
from matter_health.engine import Context
from matter_health.sources import system
from matter_health.sources.system import SystemSource
from matter_health.store import Store
from matter_health.supervisor import Supervisor


def paths(core: str = "2030.5.0", ipv6: bool | None = True) -> dict[str, Any]:
    return {
        "/core/info": {"version": core},
        "/os/info": {"version": "20.1"},
        "/docker/info": {"version": "29.0", "enable_ipv6": ipv6},
        "/network/info": {
            "interfaces": [
                {"interface": "wlan0", "primary": False, "ipv6": {"method": "auto"}},
                {"interface": "eth0", "primary": True, "ipv6": {"method": "auto"}},
            ]
        },
    }


async def poll(
    ctx: Context, fake: FakeSupervisor, aiohttp_server: AiohttpServer
) -> None:
    base = await start_supervisor(aiohttp_server, fake)
    source = source_for(SystemSource, ctx, supervisor_url=base)
    async with aiohttp.ClientSession() as session:
        await source.poll(Supervisor(session, source.ctx.options))


async def test_an_update_is_reported_with_both_versions(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeSupervisor(
        addons={MATTER_SERVER_SLUG: {"version": "8.0", "state": "started"}},
        paths=paths(),
    )

    await poll(ctx, fake, aiohttp_server)
    # The first look only learns the versions.
    assert [e.kind for e in await store.events()] == [kinds.SYSTEM_NETWORK]
    assert await store.get_state("system.versions") == {
        "core": "2030.5.0",
        "os": "20.1",
        MATTER_SERVER_SLUG: "8.0",
    }

    fake.paths = paths(core="2030.6.0")
    fake.addons[MATTER_SERVER_SLUG]["version"] = "9.0"
    await poll(ctx, fake, aiohttp_server)

    updates = [(e.subject, e.data) for e in await store.events((kinds.SYSTEM_UPDATED,))]
    assert updates == [
        (
            "software:core",
            {"name": "Home Assistant", "previous": "2030.5.0", "current": "2030.6.0"},
        ),
        (
            f"software:{MATTER_SERVER_SLUG}",
            {"name": "Matter Server", "previous": "8.0", "current": "9.0"},
        ),
    ]
    assert OTBR_SLUG not in await store.get_state("system.versions")


async def test_network_settings_are_reported_when_they_change(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    fake = FakeSupervisor(paths=paths(ipv6=None))

    await poll(ctx, fake, aiohttp_server)
    await poll(ctx, fake, aiohttp_server)

    [event] = await store.events((kinds.SYSTEM_NETWORK,))
    expected = {
        "docker_ipv6": None,
        "ipv6_method": "auto",
        "interface": "eth0",
        "haos": True,
    }
    assert event.data == expected
    assert await store.get_state("system.network") == expected

    fake.paths = paths(ipv6=True)
    await poll(ctx, fake, aiohttp_server)
    assert len(await store.events((kinds.SYSTEM_NETWORK,))) == 2


async def test_a_host_without_home_assistant_os_or_answers(
    ctx: Context, store: Store, aiohttp_server: AiohttpServer
) -> None:
    await poll(ctx, FakeSupervisor(), aiohttp_server)

    assert await store.get_state("system.network") == {
        "docker_ipv6": None,
        "ipv6_method": None,
        "interface": None,
        "haos": False,
    }
    assert await store.get_state("system.versions") == {}


async def test_run_polls_and_reports_itself_connected(
    ctx: Context,
    store: Store,
    aiohttp_server: AiohttpServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = await start_supervisor(aiohttp_server, FakeSupervisor(paths=paths()))
    source = source_for(SystemSource, ctx, supervisor_url=base)

    class Stop(Exception):
        pass

    async def stop(_: float) -> None:
        raise Stop

    monkeypatch.setattr(system, "asyncio", SimpleNamespace(sleep=stop))
    with pytest.raises(Stop):
        await source.run()

    assert source.ctx._engine is not None
    assert source.ctx._engine.status["system"]["ok"] is True
    assert await store.get_state("system.versions")
