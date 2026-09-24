import json
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from conftest import FakeSupervisor, source_for, start_supervisor
from pytest_aiohttp import AiohttpServer

from matter_health import kinds
from matter_health.engine import Context
from matter_health.store import Store
from matter_health.transports.thread import otbr
from matter_health.transports.thread.otbr import OtbrSource


def node(state: str, partition: int, leader: int, routers: int = 3) -> dict[str, Any]:
    return {
        "state": state,
        "leaderData": {"partitionId": partition, "leaderRouterId": leader},
        "routerCount": routers,
        "networkName": "TestNet",
        "extAddress": "0a1b2c3d4e5f6071",
    }


class FakeOtbr:
    """Answers ``/node`` from a script, then fails so the source stops."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.paths: list[str] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/{tail:.*}", self.handle)
        return app

    async def handle(self, request: web.Request) -> web.Response:
        self.paths.append(request.path)
        if request.path != "/node" or not self.answers:
            return web.Response(status=503)
        # The real API answers with a JSON body under a text content type.
        return web.Response(
            text=json.dumps(self.answers.pop(0)),
            content_type="text/plain",
        )


async def test_reports_state_and_every_change(
    ctx: Context,
    store: Store,
    aiohttp_server: AiohttpServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(otbr, "POLL_S", 0)
    fake = FakeOtbr(
        [
            node("leader", 100, 5),
            node("leader", 100, 5),  # unchanged: no event
            node("router", 200, 9),
            node("router", 200, 9, routers=4),
        ]
    )
    server = await aiohttp_server(fake.app())
    # A trailing slash in the user's address is fine.
    source = source_for(OtbrSource, ctx, otbr_url=str(server.make_url("/")))

    with pytest.raises(aiohttp.ClientResponseError):
        await source.run()

    found = [(e.kind, e.data) for e in await store.events()]
    assert found == [
        (
            kinds.THREAD_STATE,
            {
                "role": "leader",
                "partition_id": 100,
                "leader_router_id": 5,
                "router_count": 3,
                "network_name": "TestNet",
            },
        ),
        (
            kinds.THREAD_STATE,
            {
                "role": "router",
                "partition_id": 200,
                "leader_router_id": 9,
                "router_count": 3,
                "network_name": "TestNet",
            },
        ),
        (kinds.THREAD_ROLE_CHANGED, {"previous": "leader", "current": "router"}),
        (kinds.THREAD_PARTITION_CHANGED, {"previous": 100, "current": 200}),
        (kinds.THREAD_LEADER_CHANGED, {"previous": 5, "current": 9}),
        (
            kinds.THREAD_STATE,
            {
                "role": "router",
                "partition_id": 200,
                "leader_router_id": 9,
                "router_count": 4,
                "network_name": "TestNet",
            },
        ),
    ]
    assert (await store.get_state("otbr.node"))["router_count"] == 4
    assert source.ctx._engine is not None
    assert source.ctx._engine.status["otbr"]["ok"] is True
    # Only /node is ever read; never a dataset with the network key.
    assert set(fake.paths) == {"/node"}


async def test_remembers_the_last_state_across_restarts(
    ctx: Context,
    store: Store,
    aiohttp_server: AiohttpServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(otbr, "POLL_S", 0)
    await store.set_state(
        "otbr.node",
        {
            "role": "leader",
            "partition_id": 100,
            "leader_router_id": 5,
            "router_count": 3,
            "network_name": "TestNet",
        },
    )
    fake = FakeOtbr([node("leader", 100, 7), {}])
    server = await aiohttp_server(fake.app())
    source = source_for(OtbrSource, ctx, otbr_url=str(server.make_url("")))

    with pytest.raises(aiohttp.ClientResponseError):
        await source.run()

    found = [e.kind for e in await store.events()]
    assert found == [
        kinds.THREAD_STATE,
        kinds.THREAD_LEADER_CHANGED,
        # An empty answer is a change too: everything is unknown now.
        kinds.THREAD_STATE,
        kinds.THREAD_ROLE_CHANGED,
        kinds.THREAD_PARTITION_CHANGED,
        kinds.THREAD_LEADER_CHANGED,
    ]


async def test_fails_when_the_addon_is_not_running(
    ctx: Context, aiohttp_server: AiohttpServer
) -> None:
    base = await start_supervisor(aiohttp_server, FakeSupervisor())
    source = source_for(OtbrSource, ctx, supervisor_url=base)

    with pytest.raises(ConnectionError, match="not running"):
        await source.run()
