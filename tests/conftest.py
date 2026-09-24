"""Shared fakes: a clock that only moves when told, a store in a temp dir."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from pytest_aiohttp import AiohttpServer

from matter_health.config import Options
from matter_health.engine import Context, Engine, Rule, Source
from matter_health.model import Finding
from matter_health.store import Store

T0 = datetime(2030, 5, 4, 12, 0, tzinfo=UTC)


class Clock:
    """A clock for rules: time passes only when a test says so."""

    def __init__(self, start: datetime = T0) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> datetime:
        self.current += timedelta(**delta)
        return self.current

    def set(self, moment: datetime) -> None:
        self.current = moment


class Recorder:
    """Collects everything the engine tells its listeners."""

    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, topic: str, payload: dict[str, Any]) -> None:
        self.items.append((topic, payload))

    def findings(self) -> list[dict[str, Any]]:
        return [payload for topic, payload in self.items if topic == "finding"]

    def events(self) -> list[dict[str, Any]]:
        return [payload for topic, payload in self.items if topic == "event"]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def options(tmp_path: Path) -> Options:
    return Options(data_dir=tmp_path, supervisor_token="test-token")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def ctx(store: Store, options: Options, clock: Clock) -> Context:
    return Context(store=store, options=options, now=clock)


def make_engine(
    ctx: Context,
    rules: list[type[Rule]] | None = None,
    sources: list[type[Source]] | None = None,
) -> Engine:
    """Build an engine with only the plug-ins a test is about."""
    return Engine(ctx, sources=sources or [], rules=rules or [])


@pytest.fixture
async def engine(ctx: Context) -> AsyncIterator[Engine]:
    """An engine without plug-ins, so sources under test can emit."""
    built = make_engine(ctx)
    yield built
    await built.stop()


async def only_finding(store: Store) -> Finding:
    found = await store.findings()
    assert len(found) == 1, [f.key for f in found]
    return found[0]


def chain_keys(finding: Finding) -> list[str]:
    return [link.key for link in finding.chain]


class FakeSupervisor:
    """Answers the Supervisor endpoints the add-on reads.

    ``addons`` maps a slug to its ``data`` block; a missing slug is a 404.
    ``logs`` maps a slug to the lines its log stream sends before ending.
    ``paths`` maps any other path, such as ``/docker/info``, to its ``data``.
    """

    def __init__(
        self,
        addons: dict[str, dict[str, Any]] | None = None,
        logs: dict[str, list[str]] | None = None,
        paths: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.addons = addons or {}
        self.logs = logs or {}
        self.paths = paths or {}
        self.requests: list[tuple[str, dict[str, str]]] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/addons/{slug}/info", self.info)
        app.router.add_get("/addons/{slug}/logs/follow", self.follow)
        app.router.add_get("/{path:.*}", self.other)
        return app

    async def other(self, request: web.Request) -> web.Response:
        self.requests.append((request.path, dict(request.headers)))
        if request.path not in self.paths:
            return web.json_response({"result": "error"}, status=404)
        return web.json_response({"result": "ok", "data": self.paths[request.path]})

    def _record(self, request: web.Request) -> str:
        self.requests.append((request.path, dict(request.headers)))
        return request.match_info["slug"]

    async def info(self, request: web.Request) -> web.Response:
        slug = self._record(request)
        if slug not in self.addons:
            return web.json_response({"result": "error"}, status=404)
        return web.json_response({"result": "ok", "data": self.addons[slug]})

    async def follow(self, request: web.Request) -> web.StreamResponse:
        slug = self._record(request)
        if slug not in self.logs:
            raise web.HTTPNotFound()
        response = web.StreamResponse(headers={"Content-Type": "text/plain"})
        await response.prepare(request)
        for line in self.logs[slug]:
            await response.write(line.encode())
        await response.write_eof()
        return response


async def start_supervisor(
    aiohttp_server: AiohttpServer, supervisor: FakeSupervisor
) -> str:
    """Serve ``supervisor`` and return its base URL."""
    server = await aiohttp_server(supervisor.app())
    return str(server.make_url("")).rstrip("/")


def context_with(ctx: Context, **changes: Any) -> Context:
    """The same store, names and clock with some options changed."""
    return Context(
        store=ctx.store,
        options=replace(ctx.options, **changes),
        names=ctx.names,
        now=ctx.now,
    )


def source_for[S: Source](cls: type[S], ctx: Context, **changes: Any) -> S:
    """One source bound to an engine of its own, with changed options."""
    engine = make_engine(context_with(ctx, **changes), sources=[cls])
    source = engine.sources[0]
    assert isinstance(source, cls)
    return source


async def emit_at(
    ctx: Context,
    clock: Clock,
    minutes: float,
    kind: str,
    subject: str | None = None,
    **data: Any,
) -> None:
    """Emit an event ``minutes`` after T0, moving the clock there."""
    clock.set(T0 + timedelta(minutes=minutes))
    await ctx.emit(kind, "test", subject, **data)


async def tick_at(engine: Engine, clock: Clock, minutes: float) -> None:
    clock.set(T0 + timedelta(minutes=minutes))
    await engine.tick()


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)
