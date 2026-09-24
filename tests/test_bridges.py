from typing import Any

import pytest

from matter_health import bridges
from matter_health.store import Store


@pytest.mark.parametrize(
    ("path", "wanted"),
    [("3/57/17", True), ("0/57/17", False), ("3/6/0", False), ("57/17", False)],
)
def test_only_bridged_device_information_is_wanted(path: str, wanted: bool) -> None:
    assert bridges.wanted(path) is wanted


@pytest.mark.parametrize(
    ("subject", "bridged"),
    [("node:5:3", True), ("node:5", False), ("br:0a1b", False)],
)
def test_a_bridged_device_is_told_by_its_subject(subject: str, bridged: bool) -> None:
    assert bridges.is_bridged(subject) is bridged


def test_what_a_bridge_says_of_its_devices() -> None:
    attributes: dict[str, Any] = {
        "0/40/5": "The bridge",
        "4/57/5": "  ",
        "4/57/3": "Door Sensor",
        "4/57/17": False,
        "3/57/5": "Balcony Light",
        "3/57/1": "Acme",
        "x/57/5": "ignored",
        "6/6/0": True,
    }

    assert bridges.bridged(5, attributes) == [
        {
            "subject": "node:5:3",
            "endpoint": 3,
            "label": "Balcony Light",
            "vendor": "Acme",
            "reachable": True,
        },
        {
            "subject": "node:5:4",
            "endpoint": 4,
            "label": "Door Sensor",
            "vendor": None,
            "reachable": False,
        },
    ]


def test_a_node_that_bridges_nothing() -> None:
    assert bridges.bridged(5, {"0/49/65532": 2, "1/6/0": True}) == []


async def test_bridged_devices_hang_on_their_bridge(store: Store) -> None:
    await store.set_state(
        bridges.BRIDGED, {"node:5": bridges.bridged(5, {"3/57/5": "Lamp"})}
    )
    nodes: list[dict[str, Any]] = [
        {"id": "ethernet:node:5", "subject": "node:5", "transport": "ethernet"},
        {"id": "ethernet:node:6", "subject": "node:6", "transport": "ethernet"},
    ]

    entries = await bridges.picture(store, nodes)

    assert nodes[0]["bridge"] is True
    assert "bridge" not in nodes[1]
    assert entries == [
        {
            "id": "ethernet:node:5/3",
            "subject": "node:5:3",
            "transport": "ethernet",
            "kind": "device",
            "parent": "ethernet:node:5",
            "link": {},
            "alternatives": 0,
            "vendor": None,
            "bridged": True,
        }
    ]
