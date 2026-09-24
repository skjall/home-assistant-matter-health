"""Runs the sources and the rules, and connects them through the event bus.

Sources and rules never call each other. A source emits events; the engine
stores each one and hands it to every rule that listens for its kind; a rule
publishes findings. A source that fails is restarted with a growing delay, so
one unreachable add-on does not stop the others from being observed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from . import kinds
from .config import Options
from .model import Event, Finding
from .registry import Registry
from .store import Store

_LOGGER = logging.getLogger(__name__)

#: A failing source is retried after this many seconds, doubling up to the cap.
RETRY_FIRST_S = 5.0
RETRY_MAX_S = 300.0

#: How often rules get a chance to close situations that ended without an event.
TICK_S = 30.0

#: How often old history is removed.
HOUSEKEEPING_S = 3600.0


def utcnow() -> datetime:
    """Return the current time, timezone-aware."""
    return datetime.now(UTC)


class NameBook:
    """What the user calls each subject.

    Sources learn names from wherever they come from - the device registry,
    border router announcements - and rules use them when writing a finding.
    A subject without a known name falls back to a generic label the UI
    translates.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._names: dict[str, str] = {}
        self._devices: dict[str, str] = {}
        self._registry: dict[str, str] = {}

    def set(self, subject: str, name: str | None) -> None:
        """Remember ``name`` for ``subject``; empty names are ignored."""
        if name:
            self._names[subject] = name

    def get(self, subject: str | None, default: str | None = None) -> str | None:
        """Return the name for ``subject``, or ``default``."""
        if subject is None:
            return default
        return self._names.get(subject, default)

    def set_device(self, subject: str, device_id: str | None) -> None:
        """Remember which Home Assistant device ``subject`` is."""
        if device_id:
            self._registry[subject] = device_id

    def device(self, subject: str | None) -> str | None:
        """Return the Home Assistant device id of ``subject``, if known."""
        return self._registry.get(subject) if subject else None

    def know_device(self, name: str | None) -> None:
        """Remember a device name so a mangled copy of it can be recognised."""
        if name:
            self._devices.setdefault(_fold(name), name)

    def match(self, text: str) -> str | None:
        """Return the known device name ``text`` is a mangled copy of, if any.

        Host names are derived from device names with accents, spaces and
        punctuation dropped: "Kitchen Speaker (Left)" announces itself as
        "Kitchen-Speaker-Left". Comparing only letters and digits, without
        accents, finds the name the user actually gave.
        """
        return self._devices.get(_fold(text)) if _fold(text) else None

    def snapshot(self) -> dict[str, str]:
        """Return a copy of every known name."""
        return dict(self._names)


def _fold(text: str) -> str:
    """Reduce ``text`` to lower-case letters and digits without accents."""
    plain = unicodedata.normalize("NFKD", text)
    return "".join(c for c in plain.casefold() if c.isalnum() and c.isascii())


Listener = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class Context:
    """What sources and rules share: storage, names, options and the clock."""

    store: Store
    options: Options
    names: NameBook = field(default_factory=NameBook)
    now: Callable[[], datetime] = utcnow
    _engine: Engine | None = None

    async def emit(
        self,
        kind: str,
        source: str,
        subject: str | None = None,
        *,
        at: datetime | None = None,
        **data: Any,
    ) -> Event:
        """Record an event and hand it to the rules; ``at`` defaults to now."""
        event = Event(
            kind=kind, at=at or self.now(), source=source, subject=subject, data=data
        )
        if self._engine is None:
            raise RuntimeError("context is not attached to an engine")
        return await self._engine.dispatch(event)

    async def publish(self, finding: Finding) -> None:
        """Store a finding and tell the UI.

        Rules keep their memory in the process, so after a restart they find a
        lasting situation again as if it were new. While the stored finding is
        still open, it began when it was first seen, not when it was seen again.
        """
        known = await self.store.finding(finding.key)
        if (
            known is not None
            and known.ended_at is None
            and known.started_at < finding.started_at
        ):
            finding.started_at = known.started_at
        await self.store.put_finding(finding, self.now())
        if self._engine is not None:
            await self._engine.notify("finding", finding.as_dict())


class Source(ABC):
    """Observes one thing and turns what it sees into events."""

    name: ClassVar[str]

    def __init__(self, ctx: Context) -> None:
        """Keep the shared context."""
        self.ctx = ctx

    @classmethod
    def enabled(cls, options: Options) -> bool:
        """Whether this source should run with the given options."""
        return True

    async def emit(
        self,
        kind: str,
        subject: str | None = None,
        *,
        at: datetime | None = None,
        **data: Any,
    ) -> Event:
        """Emit an event from this source; ``at`` defaults to now."""
        return await self.ctx.emit(kind, self.name, subject, at=at, **data)

    async def connected(self) -> None:
        """Tell the engine this source reaches what it observes."""
        if self.ctx._engine is not None:
            await self.ctx._engine.set_status(self.name, ok=True)

    @abstractmethod
    async def run(self) -> None:
        """Observe until cancelled; raising means "retry later"."""


class Rule(ABC):
    """Draws conclusions from events of the kinds it listens for."""

    name: ClassVar[str]
    listens: ClassVar[frozenset[str]]

    #: Rules whose findings tell a larger story this rule's findings can be
    #: one consequence of, such as a failed pairing during a mesh split.
    part_of: ClassVar[frozenset[str]] = frozenset()

    #: For a rule that tells such a story: how long before its finding began
    #: and after it ended a consequence may start and still belong to it.
    story_margin: ClassVar[tuple[timedelta, timedelta] | None] = None

    def __init__(self, ctx: Context) -> None:
        """Keep the shared context."""
        self.ctx = ctx

    @classmethod
    def stories_for(cls, finding: Finding) -> frozenset[str]:
        """Rules whose story ``finding`` may belong to; by default ``part_of``."""
        return cls.part_of

    @abstractmethod
    async def on_event(self, event: Event) -> None:
        """React to one event."""

    async def on_tick(self) -> None:  # noqa: B027 - optional hook, empty on purpose
        """Close situations that simply stopped; runs every few seconds."""


SOURCES: Registry[type[Source]] = Registry("source")
RULES: Registry[type[Rule]] = Registry("rule")


class Engine:
    """Starts every enabled source and rule and moves events between them."""

    def __init__(
        self,
        ctx: Context,
        sources: list[type[Source]] | None = None,
        rules: list[type[Rule]] | None = None,
    ) -> None:
        """Bind to a context; by default use every registered plug-in."""
        self.ctx = ctx
        ctx._engine = self
        self.sources = [
            cls(ctx)
            for cls in (sources if sources is not None else list(SOURCES))
            if cls.enabled(ctx.options)
        ]
        self.rules = [cls(ctx) for cls in (rules if rules is not None else list(RULES))]
        self._listeners: list[Listener] = []
        self._tasks: list[asyncio.Task[None]] = []
        self.status: dict[str, dict[str, Any]] = {
            source.name: {"ok": None, "since": None, "detail": None}
            for source in self.sources
        }

    async def set_status(self, name: str, ok: bool, detail: str | None = None) -> None:
        """Record whether a source works; changes become events.

        Connecting for the first time after a start is expected and stays out
        of the timeline; failing to connect, losing a connection and getting
        it back are what the user may need to see.
        """
        previous = self.status.get(name, {})
        if previous.get("ok") == ok and previous.get("detail") == detail:
            return
        self.status[name] = {
            "ok": ok,
            "since": self.ctx.now().isoformat(),
            "detail": detail,
        }
        if previous.get("ok") == ok or (previous.get("ok") is None and ok):
            return
        await self.ctx.emit(
            kinds.SOURCE_STATUS, "engine", f"source:{name}", ok=ok, detail=detail
        )

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener(topic, payload)`` for every event and finding."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def notify(self, topic: str, payload: dict[str, Any]) -> None:
        """Tell every listener; a failing listener does not stop the others."""
        for listener in list(self._listeners):
            try:
                await listener(topic, payload)
            except Exception:
                _LOGGER.exception("listener failed on %s", topic)

    async def dispatch(self, event: Event) -> Event:
        """Store an event, then let every interested rule see it."""
        event_id = await self.ctx.store.add_event(event)
        stored = Event(
            kind=event.kind,
            at=event.at,
            source=event.source,
            subject=event.subject,
            data=event.data,
            id=event_id,
        )
        for rule in self.rules:
            if stored.kind in rule.listens:
                try:
                    await rule.on_event(stored)
                except Exception:
                    _LOGGER.exception("rule %s failed on %s", rule.name, stored.kind)
        await self.notify("event", event_payload(stored))
        return stored

    async def _keep_running(self, source: Source) -> None:
        delay = RETRY_FIRST_S
        while True:
            try:
                await source.run()
                # A stream that ends - Home Assistant restarting, an add-on
                # being updated - is a connection lost like any other.
                await self.set_status(source.name, ok=False, detail="connection closed")
                _LOGGER.info("source %s ended; reconnecting", source.name)
                delay = RETRY_FIRST_S
            except asyncio.CancelledError:
                raise
            except Exception as err:
                await self.set_status(
                    source.name, ok=False, detail=str(err) or type(err).__name__
                )
                _LOGGER.warning(
                    "source %s stopped: %s; retrying in %.0f s",
                    source.name,
                    err,
                    delay,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_S)

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            await self.tick()

    async def tick(self) -> None:
        """Give every rule its periodic look."""
        for rule in self.rules:
            try:
                await rule.on_tick()
            except Exception:
                _LOGGER.exception("rule %s failed on tick", rule.name)

    async def _housekeeping(self) -> None:
        while True:
            removed = await self.ctx.store.forget_older_than(
                self.ctx.now(), self.ctx.options.retention_days
            )
            if removed:
                _LOGGER.info("removed %d entries older than the retention", removed)
            await asyncio.sleep(HOUSEKEEPING_S)

    def start(self) -> None:
        """Start sources, the rule clock and housekeeping in the background."""
        for source in self.sources:
            self._tasks.append(
                asyncio.create_task(self._keep_running(source), name=source.name)
            )
        self._tasks.append(asyncio.create_task(self._tick(), name="tick"))
        self._tasks.append(asyncio.create_task(self._housekeeping(), name="house"))

    async def stop(self) -> None:
        """Cancel everything started by :meth:`start`."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()


def event_payload(event: Event) -> dict[str, Any]:
    """Serialise an event for the API."""
    return {
        "id": event.id,
        "kind": event.kind,
        "at": event.at.isoformat(),
        "source": event.source,
        "subject": event.subject,
        "data": event.data,
    }
