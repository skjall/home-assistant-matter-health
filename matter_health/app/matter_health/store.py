"""Durable history of events and findings.

The system journal on Home Assistant OS keeps only hours; a pairing problem is
usually noticed and reported days later. Everything the add-on observes and
concludes is therefore kept here, in SQLite under ``/data``, for as long as the
user configured.

SQLite calls are blocking; they run in a worker thread so the event loop keeps
serving the UI and the sources while a query runs.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .model import Event, Finding

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    subject TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
CREATE INDEX IF NOT EXISTS events_kind_at ON events (kind, at);
CREATE TABLE IF NOT EXISTS findings (
    key TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS findings_started ON findings (started_at);
CREATE TABLE IF NOT EXISTS state (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    """Events, findings and small pieces of state in one SQLite file."""

    def __init__(self, path: Path | str) -> None:
        """Open (and if needed create) the database at ``path``."""
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = asyncio.Lock()

    async def _run[R](self, work: Any, *args: Any) -> R:
        async with self._lock:
            result: R = await asyncio.to_thread(work, *args)
            return result

    def close(self) -> None:
        """Close the database."""
        self._db.close()

    # --- events ---------------------------------------------------------

    def _add_event(self, event: Event) -> int:
        cursor = self._db.execute(
            "INSERT INTO events (at, kind, source, subject, data)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                event.at.isoformat(),
                event.kind,
                event.source,
                event.subject,
                json.dumps(event.data, default=str),
            ),
        )
        self._db.commit()
        return int(cursor.lastrowid or 0)

    async def add_event(self, event: Event) -> int:
        """Store an event and return its id."""
        return await self._run(self._add_event, event)

    def _events(
        self,
        kinds: tuple[str, ...],
        since: datetime | None,
        until: datetime | None,
        subject: str | None,
        limit: int,
    ) -> list[Event]:
        clauses: list[str] = []
        args: list[str] = []
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            args.extend(kinds)
        if since:
            clauses.append("at >= ?")
            args.append(since.isoformat())
        if until:
            clauses.append("at <= ?")
            args.append(until.isoformat())
        if subject:
            clauses.append("subject = ?")
            args.append(subject)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.execute(
            "SELECT id, at, kind, source, subject, data FROM events"
            f" {where} ORDER BY at DESC, id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [
            Event(
                id=row[0],
                at=datetime.fromisoformat(row[1]),
                kind=row[2],
                source=row[3],
                subject=row[4],
                data=json.loads(row[5]),
            )
            for row in reversed(rows)
        ]

    async def events(
        self,
        kinds: tuple[str, ...] = (),
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        subject: str | None = None,
        limit: int = 1000,
    ) -> list[Event]:
        """Events in time order, newest ``limit`` of those matching."""
        return await self._run(self._events, kinds, since, until, subject, limit)

    # --- findings -------------------------------------------------------

    def _put_finding(self, finding: Finding, now: datetime) -> None:
        self._db.execute(
            "INSERT INTO findings (key, started_at, updated_at, payload)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
            " updated_at = excluded.updated_at, payload = excluded.payload",
            (
                finding.key,
                finding.started_at.isoformat(),
                now.isoformat(),
                json.dumps(finding.as_dict(), default=str),
            ),
        )
        self._db.commit()

    async def put_finding(self, finding: Finding, now: datetime) -> None:
        """Add a finding, or replace the one with the same key."""
        await self._run(self._put_finding, finding, now)

    def _findings(self, since: datetime | None) -> list[Finding]:
        if since:
            rows = self._db.execute(
                "SELECT payload FROM findings WHERE started_at >= ?"
                " OR json_extract(payload, '$.ended_at') IS NULL"
                " ORDER BY started_at DESC",
                (since.isoformat(),),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT payload FROM findings ORDER BY started_at DESC"
            ).fetchall()
        return [Finding.from_dict(json.loads(row[0])) for row in rows]

    async def findings(self, since: datetime | None = None) -> list[Finding]:
        """Return findings newest first; open ones are always included."""
        return await self._run(self._findings, since)

    def _finding(self, key: str) -> Finding | None:
        row = self._db.execute(
            "SELECT payload FROM findings WHERE key = ?", (key,)
        ).fetchone()
        return Finding.from_dict(json.loads(row[0])) if row else None

    async def finding(self, key: str) -> Finding | None:
        """Return the finding stored under ``key``, if any."""
        return await self._run(self._finding, key)

    # --- state ----------------------------------------------------------

    def _get_state(self, name: str) -> Any:
        row = self._db.execute(
            "SELECT value FROM state WHERE name = ?", (name,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    async def get_state(self, name: str) -> Any:
        """Return a value kept across restarts, or None."""
        return await self._run(self._get_state, name)

    def _set_state(self, name: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO state (name, value) VALUES (?, ?)"
            " ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, json.dumps(value, default=str)),
        )
        self._db.commit()

    async def set_state(self, name: str, value: Any) -> None:
        """Keep a value across restarts."""
        await self._run(self._set_state, name, value)

    # --- housekeeping ---------------------------------------------------

    def _forget_before(self, cutoff: datetime) -> int:
        removed = self._db.execute(
            "DELETE FROM events WHERE at < ?", (cutoff.isoformat(),)
        ).rowcount
        removed += self._db.execute(
            "DELETE FROM findings WHERE updated_at < ?"
            " AND json_extract(payload, '$.ended_at') IS NOT NULL",
            (cutoff.isoformat(),),
        ).rowcount
        self._db.commit()
        return removed

    async def forget_older_than(self, now: datetime, days: int) -> int:
        """Drop history older than ``days``; open findings stay."""
        return await self._run(self._forget_before, now - timedelta(days=days))
