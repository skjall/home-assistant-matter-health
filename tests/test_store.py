from datetime import timedelta

from conftest import T0

from matter_health.model import Event, Finding, Link, Role, Severity
from matter_health.store import Store


def event(kind: str, minutes: float, subject: str | None = None) -> Event:
    return Event(
        kind=kind,
        at=T0 + timedelta(minutes=minutes),
        source="test",
        subject=subject,
        data={"name": "Living Room Plug", "when": T0},
    )


def finding(key: str, minutes: float, ended: float | None = None) -> Finding:
    return Finding(
        key=key,
        rule="test",
        severity=Severity.WARNING,
        title="finding.test.title",
        started_at=T0 + timedelta(minutes=minutes),
        ended_at=T0 + timedelta(minutes=ended) if ended is not None else None,
        chain=[Link(Role.CAUSE, "link.cause_unknown")],
    )


async def test_events_are_returned_in_time_order_with_ids(store: Store) -> None:
    second = await store.add_event(event("b", 2, "node:1"))
    first = await store.add_event(event("a", 1))

    found = await store.events()

    assert [e.kind for e in found] == ["a", "b"]
    assert [e.id for e in found] == [first, second]
    assert found[1].subject == "node:1"
    # Values JSON cannot hold are kept as text.
    assert found[0].data == {"name": "Living Room Plug", "when": str(T0)}


async def test_event_filters(store: Store) -> None:
    for minute, kind, subject in [
        (0, "a", "node:1"),
        (1, "b", "node:1"),
        (2, "a", "node:2"),
        (3, "a", "node:1"),
    ]:
        await store.add_event(event(kind, minute, subject))

    def minutes(found: list[Event]) -> list[float]:
        return [(e.at - T0).total_seconds() / 60 for e in found]

    assert minutes(await store.events(("a",))) == [0, 2, 3]
    assert minutes(await store.events(("a", "b"), subject="node:1")) == [0, 1, 3]
    assert minutes(
        await store.events(
            since=T0 + timedelta(minutes=1), until=T0 + timedelta(minutes=2)
        )
    ) == [1, 2]
    # The limit keeps the newest, still in time order.
    assert minutes(await store.events(limit=2)) == [2, 3]


async def test_findings_upsert_and_lookup(store: Store) -> None:
    await store.put_finding(finding("one", 0), T0)
    updated = finding("one", 0, ended=5)
    await store.put_finding(updated, T0 + timedelta(minutes=5))

    assert await store.finding("one") == updated
    assert await store.finding("absent") is None
    assert await store.findings() == [updated]


async def test_findings_since_keeps_open_ones(store: Store) -> None:
    await store.put_finding(finding("old-closed", 0, ended=1), T0)
    await store.put_finding(finding("old-open", 1), T0)
    await store.put_finding(finding("new-closed", 60, ended=61), T0)

    everything = await store.findings()
    recent = await store.findings(T0 + timedelta(minutes=30))

    assert [f.key for f in everything] == ["new-closed", "old-open", "old-closed"]
    assert [f.key for f in recent] == ["new-closed", "old-open"]


async def test_state_survives_as_json(store: Store) -> None:
    assert await store.get_state("otbr.node") is None

    await store.set_state("otbr.node", {"role": "leader"})
    await store.set_state("otbr.node", {"role": "router", "at": T0})

    assert await store.get_state("otbr.node") == {"role": "router", "at": str(T0)}


async def test_forget_older_than_keeps_open_findings(store: Store) -> None:
    await store.add_event(event("a", 0))
    await store.add_event(event("a", 3 * 24 * 60))
    await store.put_finding(finding("closed", 0, ended=1), T0)
    await store.put_finding(finding("open", 0), T0)
    now = T0 + timedelta(days=3)

    removed = await store.forget_older_than(now, days=2)

    assert removed == 2
    assert len(await store.events()) == 1
    assert [f.key for f in await store.findings()] == ["open"]
    assert await store.forget_older_than(now, days=2) == 0
