import asyncio
import logging
import types
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, ClassVar

import pytest
from conftest import T0, Clock, Recorder, make_engine

from matter_health import engine as engine_module
from matter_health import kinds
from matter_health.config import Options
from matter_health.engine import (
    Context,
    Engine,
    NameBook,
    Rule,
    Source,
    event_payload,
)
from matter_health.model import Event, Finding, Severity
from matter_health.store import Store


class Tracing(Rule):
    """Records the order in which rules see events."""

    name: ClassVar[str] = "tracing"
    listens: ClassVar[frozenset[str]] = frozenset({"test.ping"})
    seen: ClassVar[list[str]] = []

    async def on_event(self, event: Event) -> None:
        self.seen.append(f"{self.name}:{event.kind}:{event.id}")

    async def on_tick(self) -> None:
        self.seen.append(f"{self.name}:tick")


class Broken(Tracing):
    name: ClassVar[str] = "broken"

    async def on_event(self, event: Event) -> None:
        self.seen.append(f"{self.name}:{event.kind}")
        raise RuntimeError("rule bug")

    async def on_tick(self) -> None:
        raise RuntimeError("tick bug")


class Second(Tracing):
    name: ClassVar[str] = "second"


class Deaf(Tracing):
    name: ClassVar[str] = "deaf"
    listens: ClassVar[frozenset[str]] = frozenset({"test.other"})


class Scripted(Source):
    """A source that follows a script of outcomes, one per run."""

    name: ClassVar[str] = "scripted"
    script: ClassVar[list[BaseException | None]] = []

    async def run(self) -> None:
        outcome = self.script.pop(0)
        if outcome is not None:
            raise outcome


class Disabled(Scripted):
    name: ClassVar[str] = "disabled"

    @classmethod
    def enabled(cls, options: Options) -> bool:
        return False


class Waiting(Source):
    name: ClassVar[str] = "waiting"
    started: ClassVar[asyncio.Event]

    async def run(self) -> None:
        await self.connected()
        self.started.set()
        await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def clear_trace() -> None:
    Tracing.seen.clear()


def test_name_book() -> None:
    names = NameBook()
    names.set("node:1", "Living Room Plug")
    names.set("node:2", None)
    names.set("node:3", "")

    assert names.get("node:1") == "Living Room Plug"
    assert names.get("node:2") is None
    assert names.get("node:2", "fallback") == "fallback"
    assert names.get(None, "fallback") == "fallback"
    assert names.snapshot() == {"node:1": "Living Room Plug"}


def test_name_book_recognises_names_turned_into_host_names() -> None:
    names = NameBook()
    names.know_device("Küche Speaker (Links)")
    names.know_device("Küche Speaker (Links)!")  # the first name stays
    names.know_device(None)

    assert names.match("Kuche Speaker Links") == "Küche Speaker (Links)"
    assert names.match("kuche-speaker-links") == "Küche Speaker (Links)"
    assert names.match("Kitchen Speaker") is None
    assert names.match("---") is None


async def test_emit_needs_an_engine(ctx: Context) -> None:
    with pytest.raises(RuntimeError, match="not attached"):
        await ctx.emit("test.ping", "test")


async def test_publish_without_engine_only_stores(ctx: Context, store: Store) -> None:
    finding = Finding(
        key="k", rule="r", severity=Severity.INFO, title="t", started_at=T0, chain=[]
    )

    await ctx.publish(finding)

    assert await store.finding("k") == finding


async def test_connected_without_engine_does_nothing(ctx: Context) -> None:
    await Waiting(ctx).connected()


async def test_dispatch_stores_then_hands_to_listening_rules_in_order(
    ctx: Context, store: Store
) -> None:
    engine = make_engine(ctx, rules=[Tracing, Broken, Deaf, Second])
    recorder = Recorder()
    engine.subscribe(recorder)

    stored = await ctx.emit("test.ping", "test", "node:1", value=1)

    assert stored.id is not None
    assert Tracing.seen == [
        f"tracing:test.ping:{stored.id}",
        "broken:test.ping",
        f"second:test.ping:{stored.id}",
    ]
    assert (await store.events())[0] == stored
    assert recorder.events() == [event_payload(stored)]


async def test_a_failing_listener_does_not_stop_the_others(ctx: Context) -> None:
    engine = make_engine(ctx)
    recorder = Recorder()

    async def failing(topic: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("listener bug")

    unsubscribe = engine.subscribe(failing)
    engine.subscribe(recorder)
    await engine.notify("event", {"n": 1})
    unsubscribe()
    await engine.notify("event", {"n": 2})

    assert recorder.items == [("event", {"n": 1}), ("event", {"n": 2})]


async def test_publish_tells_listeners(ctx: Context) -> None:
    engine = make_engine(ctx)
    recorder = Recorder()
    engine.subscribe(recorder)
    finding = Finding(
        key="k", rule="r", severity=Severity.INFO, title="t", started_at=T0, chain=[]
    )

    await ctx.publish(finding)

    assert recorder.findings() == [finding.as_dict()]


def signal_finding(minutes: float, ended: float | None = None) -> Finding:
    return Finding(
        key="signal:node:1",
        rule="signal",
        severity=Severity.WARNING,
        title="finding.weak_signal.title",
        started_at=T0 + timedelta(minutes=minutes),
        ended_at=T0 + timedelta(minutes=ended) if ended is not None else None,
        chain=[],
    )


async def test_publish_keeps_the_start_of_a_finding_still_open(
    ctx: Context, store: Store
) -> None:
    await ctx.publish(signal_finding(0))

    # A rule that forgot the situation (e.g. after a restart) sees it again.
    again = signal_finding(10)
    await ctx.publish(again)
    assert again.started_at == T0
    assert (await store.finding("signal:node:1")) == signal_finding(0)

    # An earlier start is taken as it is.
    earlier = signal_finding(-5)
    await ctx.publish(earlier)
    assert earlier.started_at == T0 - timedelta(minutes=5)


async def test_publish_starts_anew_after_a_closed_finding(
    ctx: Context, store: Store
) -> None:
    await ctx.publish(signal_finding(0, ended=5))

    await ctx.publish(signal_finding(10))

    stored = await store.finding("signal:node:1")
    assert stored == signal_finding(10)


async def test_set_status_emits_changes_but_not_the_first_connect(
    ctx: Context, store: Store, clock: Clock
) -> None:
    engine = make_engine(ctx, sources=[Scripted, Disabled])
    assert engine.status == {"scripted": {"ok": None, "since": None, "detail": None}}

    async def history() -> list[dict[str, Any]]:
        return [e.data for e in await store.events((kinds.SOURCE_STATUS,))]

    await engine.set_status("scripted", ok=True)
    connected_at = clock().isoformat()
    assert await history() == []
    assert engine.status["scripted"]["since"] == connected_at

    # Nothing changed: not even "since" moves.
    clock.advance(seconds=5)
    await engine.set_status("scripted", ok=True)
    assert engine.status["scripted"]["since"] == connected_at

    await engine.set_status("scripted", ok=False, detail="refused")
    # A new reason while still down updates the status, not the timeline.
    clock.advance(seconds=5)
    await engine.set_status("scripted", ok=False, detail="timeout")
    assert engine.status["scripted"] == {
        "ok": False,
        "since": clock().isoformat(),
        "detail": "timeout",
    }
    await engine.set_status("scripted", ok=True)

    assert await history() == [
        {"ok": False, "detail": "refused"},
        {"ok": True, "detail": None},
    ]
    assert [e.subject for e in await store.events()] == ["source:scripted"] * 2


async def test_set_status_reports_a_source_that_never_connected(
    ctx: Context, store: Store
) -> None:
    engine = make_engine(ctx, sources=[Scripted])

    await engine.set_status("scripted", ok=False, detail="refused")

    found = await store.events((kinds.SOURCE_STATUS,))
    assert [e.data for e in found] == [{"ok": False, "detail": "refused"}]


def fake_sleep(monkeypatch: pytest.MonkeyPatch, stop_after: int) -> list[float]:
    """Replace the engine's sleep; the ``stop_after``-th call cancels."""
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) >= stop_after:
            raise asyncio.CancelledError

    patched = types.SimpleNamespace(**vars(asyncio))
    patched.sleep = sleep
    monkeypatch.setattr(engine_module, "asyncio", patched)
    return delays


async def test_keep_running_backs_off_and_resets_after_success(
    ctx: Context, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(ctx, sources=[Scripted])
    Scripted.script = [ValueError("refused"), RuntimeError(), None, ValueError("x")]
    delays = fake_sleep(monkeypatch, stop_after=4)

    with pytest.raises(asyncio.CancelledError):
        await engine._keep_running(engine.sources[0])

    assert delays == [5.0, 10.0, 5.0, 10.0]
    details = [e.data for e in await store.events((kinds.SOURCE_STATUS,))]
    # Only the change to "not ok" is an event; the second failure is not.
    assert details == [{"ok": False, "detail": "refused"}]
    assert engine.status["scripted"]["detail"] == "x"


async def test_keep_running_reports_a_stream_that_ended(
    ctx: Context, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(ctx, sources=[Scripted])
    await engine.set_status("scripted", ok=True)
    Scripted.script = [None]
    fake_sleep(monkeypatch, stop_after=1)

    with pytest.raises(asyncio.CancelledError):
        await engine._keep_running(engine.sources[0])

    details = [e.data for e in await store.events((kinds.SOURCE_STATUS,))]
    assert details == [{"ok": False, "detail": "connection closed"}]


async def test_keep_running_caps_the_delay(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(ctx, sources=[Scripted])
    Scripted.script = [RuntimeError("down")] * 8
    delays = fake_sleep(monkeypatch, stop_after=8)

    with pytest.raises(asyncio.CancelledError):
        await engine._keep_running(engine.sources[0])

    assert delays == [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0]
    assert engine.status["scripted"]["detail"] == "down"


async def test_keep_running_passes_cancellation_through(ctx: Context) -> None:
    engine = make_engine(ctx, sources=[Scripted])
    Scripted.script = [asyncio.CancelledError()]

    with pytest.raises(asyncio.CancelledError):
        await engine._keep_running(engine.sources[0])


async def test_tick_survives_a_failing_rule(
    ctx: Context, caplog: pytest.LogCaptureFixture
) -> None:
    engine = make_engine(ctx, rules=[Broken, Tracing])

    await engine.tick()

    assert Tracing.seen == ["tracing:tick"]
    assert "rule broken failed on tick" in caplog.text


async def run_engine_briefly(
    engine: Engine, until: Callable[[], Awaitable[bool]]
) -> None:
    engine.start()
    try:
        for _ in range(200):
            if await until():
                return
            await asyncio.sleep(0.01)
        pytest.fail("engine did not get there")
    finally:
        await engine.stop()


async def test_start_runs_sources_ticks_and_housekeeping(
    ctx: Context,
    store: Store,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(engine_module, "TICK_S", 0.01)
    Waiting.started = asyncio.Event()
    engine = make_engine(ctx, rules=[Tracing], sources=[Waiting])
    old = Event(kind="test.old", at=T0 - timedelta(days=40), source="test")
    await store.add_event(old)

    async def ticked() -> bool:
        # Housekeeping runs in its own task; wait for its report too.
        return (
            Waiting.started.is_set()
            and "tracing:tick" in Tracing.seen
            and "removed 1 entries" in caplog.text
        )

    await run_engine_briefly(engine, ticked)

    assert engine.status["waiting"]["ok"] is True
    assert await store.events(("test.old",)) == []
    assert "removed 1 entries" in caplog.text
    assert engine._tasks == []


async def test_housekeeping_with_nothing_to_remove(
    ctx: Context, store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    engine = make_engine(ctx)
    await store.add_event(Event(kind="test.new", at=T0, source="test"))

    async def housekept() -> bool:
        # Housekeeping runs first thing; one pass through the loop suffices.
        await asyncio.sleep(0.05)
        return True

    await run_engine_briefly(engine, housekept)

    assert len(await store.events(("test.new",))) == 1
    assert "removed" not in caplog.text


def test_event_payload() -> None:
    event = Event(
        kind="test.ping", at=T0, source="test", subject="node:1", data={"a": 1}, id=9
    )

    assert event_payload(event) == {
        "id": 9,
        "kind": "test.ping",
        "at": T0.isoformat(),
        "source": "test",
        "subject": "node:1",
        "data": {"a": 1},
    }
