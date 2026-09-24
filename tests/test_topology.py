from typing import Any

from matter_health.transports.thread.tree import ROOT, build_tree, remember_parents


def node(ident: str, **fields: Any) -> dict[str, Any]:
    return {"id": ident, **fields}


def link(
    source: str,
    target: str,
    cost: int | None,
    lqi: int | None = 3,
    rssi: int = -60,
    back: bool = False,
) -> dict[str, Any]:
    measured = {"lqi": lqi, "rssi": rssi, "strength": "strong"}
    raw: dict[str, Any] = {"source": source, "target": target, "path_cost": cost}
    raw["target_to_source" if back else "source_to_target"] = measured
    return raw


HOME_PAN = "aaaa"

TOPOLOGY: dict[str, Any] = {
    "nodes": [
        node("br_A", kind="border_router", ext_address="A1", ext_pan_id="AAAA"),
        node("br_B", kind="border_router", ext_address="B1", ext_pan_id="AAAA"),
        node("br_X", kind="border_router", ext_address="C1", ext_pan_id="FFFF"),
        node("1", kind="matter", node_id=1, role="router"),
        node("2", kind="matter", node_id=2, role="reed"),
        node("3", kind="matter", node_id=3, role="sleepy_end_device"),
        node("4", kind="matter", node_id=4, role="sleepy_end_device"),
        node("5", kind="matter", node_id=5),
        node("6", kind="matter", node_id=6, role="router"),
        node("u", kind="thread_unknown", ext_address="DD"),
    ],
    "connections": [
        # Router 1 reaches A directly, but poorly; through B and 2 it is cheaper.
        link("1", "br_A", 1, lqi=1),
        link("1", "2", 1),
        link("2", "br_B", 1, lqi=None, back=True),
        link("br_B", "2", 1),
        # A sleepy device with its parent link and a stray link; it never relays.
        link("3", "1", 0, rssi=-70),
        link("3", "2", None, rssi=-50),
        # A sleepy device measured only by its parent.
        link("1", "4", 0, back=True, rssi=-90, lqi=1),
        # A device without a parent link: the strongest relaying neighbour wins.
        link("5", "1", None, rssi=-80),
        link("5", "br_B", None, rssi=-65),
        # A foreign border router and an unknown device.
        link("br_X", "1", 1),
        link("u", "1", 0),
    ],
}


def by_id(tree: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in tree}


def test_every_device_gets_one_way_in() -> None:
    tree = by_id(build_tree(TOPOLOGY, HOME_PAN))

    assert "br_X" not in tree
    assert {i: e["parent"] for i, e in tree.items()} == {
        "br_A": ROOT,
        "br_B": ROOT,
        "1": "2",
        "2": "br_B",
        "3": "1",
        "4": "1",
        "5": "br_B",
        "6": None,
        "u": "1",
    }
    assert {i: e["kind"] for i, e in tree.items()} == {
        "br_A": "border_router",
        "br_B": "border_router",
        "1": "router",
        "2": "router",
        "3": "sleepy",
        "4": "sleepy",
        "5": "end_device",
        "6": "router",
        "u": "unknown",
    }
    assert tree["1"]["subject"] == "node:1"
    assert tree["br_A"]["subject"] == "br:a1"
    assert tree["u"]["subject"] == "br:dd"
    # The link is what the device itself measured, else what the parent heard.
    assert tree["3"]["link"] == {"rssi": -70, "lqi": 3, "strength": "strong"}
    assert tree["4"]["link"]["rssi"] == -90
    assert tree["6"]["link"] == {"rssi": None, "lqi": None, "strength": None}
    # Router 1 could also use A, but that link is too poor to count.
    assert tree["1"]["alternatives"] == 0
    assert tree["2"]["alternatives"] == 1
    assert tree["3"]["alternatives"] == 0


def test_without_a_home_network_every_border_router_counts() -> None:
    tree = by_id(build_tree(TOPOLOGY))

    assert tree["br_X"]["parent"] == ROOT


def test_nodes_without_an_id_or_links() -> None:
    tree = build_tree(
        {"nodes": [node("7", kind="matter", role="end_device")], "connections": []}
    )

    assert tree == [
        {
            "id": "7",
            "subject": None,
            "kind": "end_device",
            "parent": None,
            "link": {"rssi": None, "lqi": None, "strength": None},
            "alternatives": 0,
            "vendor": None,
        }
    ]
    assert build_tree({}) == []


def test_parents_are_remembered() -> None:
    tree = build_tree(TOPOLOGY, HOME_PAN)

    parents = remember_parents(tree, {"node:9": "node:1", "node:3": "br:b1"})

    assert parents["node:9"] == "node:1"
    assert parents["node:3"] == "node:1"
    assert parents["node:1"] == "node:2"
    assert "br:a1" not in parents
