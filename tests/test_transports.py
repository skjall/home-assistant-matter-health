from datetime import timedelta
from typing import Any

import pytest
from conftest import T0, Clock, chain_keys, emit_at, make_engine

from matter_health import kinds
from matter_health.engine import Context
from matter_health.model import Severity
from matter_health.store import Store
from matter_health.transports import FEATURE_MAP, quality, transport_of
from matter_health.transports.ethernet.transport import EthernetTransport
from matter_health.transports.thread.transport import (
    ThreadTransport,
    announced_role,
    link_quality,
    partitions,
    rates,
)
from matter_health.transports.wifi.signal import WifiSignalRule
from matter_health.transports.wifi.transport import (
    WifiTransport,
    access_points,
    describe,
)

#: 02:00:00:00:00:01, as a device reports its access point.
BSSID = "AgAAAAAB"
#: A network named "Home", connected.
NETWORKS = [{"0": "SG9tZQ==", "1": True}]


def wifi(rssi: int | None = -50, bssid: str | None = BSSID) -> dict[str, Any]:
    return {
        FEATURE_MAP: 1,
        "0/54/0": bssid,
        "0/54/1": 4,
        "0/54/2": 3,
        "0/54/3": 6,
        "0/54/4": rssi,
        "0/49/1": NETWORKS,
    }


@pytest.mark.parametrize(
    ("features", "transport"),
    [(1, "wifi"), (2, "thread"), (4, "ethernet"), (0, None), ("x", None)],
)
def test_a_device_says_which_transport_it_uses(features: Any, transport: Any) -> None:
    assert transport_of({FEATURE_MAP: features}) == transport


def test_without_the_feature_map_nothing_is_known() -> None:
    assert transport_of({}) is None


@pytest.mark.parametrize(
    ("rssi", "rated"),
    [(None, None), (-50, "strong"), (-65, "strong"), (-70, "medium"), (-75, "weak")],
)
def test_quality(rssi: int | None, rated: str | None) -> None:
    assert quality(rssi, -65, -75) == rated


@pytest.mark.parametrize(
    ("link", "rated"),
    [
        ({"strength": "medium", "lqi": 3}, "medium"),
        ({"lqi": 3}, "strong"),
        ({"lqi": 0}, "weak"),
        ({}, None),
    ],
)
def test_thread_link_quality(link: dict[str, Any], rated: str | None) -> None:
    assert link_quality(link) == rated


def test_what_a_wifi_device_reports() -> None:
    assert describe(wifi()) == {
        "bssid": "02:00:00:00:00:01",
        "mac": None,
        "addresses": [],
        "ssid": "Home",
        "channel": 6,
        "rssi": -50,
        "security": "WPA2",
        "standard": "n",
    }


def test_a_wifi_device_says_who_it_is_on_the_home_network() -> None:
    interfaces = [
        {"0": "down", "1": False, "4": "AgAAAAAC"},
        {"0": "odd", "1": True, "4": "AQI="},
        "nonsense",
        # 02:00:00:00:00:03, 192.0.2.7, and an address that is no IPv4.
        {"0": "wlan0", "1": True, "4": "AgAAAAAD", "5": ["wAACBw==", "AQI="]},
    ]

    link = describe({**wifi(), "0/51/0": interfaces})

    assert link["mac"] == "02:00:00:00:00:03"
    assert link["addresses"] == ["192.0.2.7"]


@pytest.mark.parametrize(
    "attributes",
    [
        {"0/54/0": "not base64!"},
        {"0/54/0": "AQI="},  # too short for a BSSID
        {"0/54/0": None, "0/49/1": [{"0": "!!", "1": True}]},
        {"0/49/1": "nonsense"},
    ],
)
def test_odd_wifi_attributes_say_nothing(attributes: dict[str, Any]) -> None:
    link = describe(attributes)
    assert link["bssid"] is None
    assert link["ssid"] is None
    assert link["security"] is None


async def test_wifi_links_are_reported_when_a_rating_changes(
    ctx: Context, store: Store, clock: Clock
) -> None:
    make_engine(ctx, rules=[])
    transport = WifiTransport(ctx)

    await transport.devices({"node:1": wifi(-50)})
    await transport.devices({"node:1": wifi(-52)})  # still strong: quiet
    await transport.devices({"node:1": wifi(-80)})
    # Gone quiet: it keeps its access point, the signal is not known.
    await transport.devices({"node:1": wifi(None, bssid=None)})

    reported = [e.data["devices"] for e in await store.events()]
    assert [[d["quality"] for d in devices] for devices in reported] == [
        ["strong"],
        ["weak"],
        [None],
    ]
    assert reported[0][0]["neighbour"] == "ap:02:00:00:00:00:01"
    stored = await store.get_state("wifi.devices")
    assert stored["node:1"]["bssid"] == "02:00:00:00:00:01"
    summary = await transport.summary(1)
    assert summary == {"gateways": 1, "connected": None}


async def test_a_weak_wifi_link_opens_and_closes_a_finding(
    ctx: Context, store: Store, clock: Clock
) -> None:
    ctx.names.set("node:1", "Garage Plug")
    make_engine(ctx, rules=[WifiSignalRule])
    devices = [{"subject": "node:1", "neighbour": "ap:x", "rssi": -80}]

    await emit_at(
        ctx, clock, 0, kinds.WIFI_LINKS, devices=[{**devices[0], "quality": "weak"}]
    )
    await emit_at(
        ctx, clock, 5, kinds.WIFI_LINKS, devices=[{**devices[0], "quality": "medium"}]
    )

    finding = (await store.findings())[0]
    assert finding.key == "signal:node:1"
    assert finding.rule == "wifi_signal"
    assert finding.severity is Severity.WARNING
    assert chain_keys(finding) == [
        "link.weak_wifi",
        "link.weak_signal_impact",
        "fix.weak_wifi",
    ]
    assert finding.ended_at is not None


async def test_wired_devices_hang_on_the_home_network(
    ctx: Context, store: Store
) -> None:
    await store.set_state("matter.transports", {"node:1": "ethernet", "node:2": "wifi"})
    transport = EthernetTransport(ctx)

    picture = await transport.picture(set())

    assert [(e["subject"], e["parent"]) for e in picture] == [("node:1", "home")]
    assert await transport.summary(1) == {"gateways": None, "connected": None}


@pytest.mark.parametrize(
    ("bitmap", "role"),
    [
        ("00000FB1", "leader"),
        ("00000CB1", "router"),
        ("00000AB1", "child"),
        ("000001B1", "disabled"),
        ("zz", None),
        (None, None),
    ],
)
def test_a_border_router_announces_its_role(bitmap: Any, role: str | None) -> None:
    assert announced_role({"stateBitmapHex": bitmap}) == role


def test_border_routers_are_grouped_by_partition() -> None:
    routers = [
        {"subject": "br:1", "name": "TV", "partition": "bb", "role": "leader"},
        {"subject": "br:2", "name": "Hall", "partition": "cc"},
        {"subject": "br:3", "name": "Kitchen", "partition": "cc"},
        {
            "subject": "br:4",
            "name": "HA",
            "partition": "bb",
            "vendor": "Home Assistant",
        },
        {"subject": "br:5", "name": "Hub", "partition": "dd", "own": False},
        {"subject": "br:6", "name": "Old", "partition": None},
    ]

    parts = partitions(routers)

    # Home Assistant's part first, though it is not the biggest.
    assert [p["partition"] for p in parts] == ["bb", "cc"]
    assert parts[0]["leader"] == "TV"
    assert parts[1]["leader"] is None
    assert [r["name"] for r in parts[1]["border_routers"]] == ["Hall", "Kitchen"]


def test_counter_rates_per_hour() -> None:
    before = {"tx": 1000, "retry": 100, "cca": 10, "busy": 1, "at": T0.isoformat()}
    later = (T0 + timedelta(minutes=30)).isoformat()

    assert rates(
        before, {"tx": 2000, "retry": 300, "cca": 70, "busy": 3, "at": later}
    ) == {
        "cca_per_hour": 120,
        "busy_per_hour": 4,
        "retries_per_frame": 0.2,
    }
    # Nothing sent: no share to tell.
    same = {"tx": 1000, "retry": 100, "cca": 10, "busy": 1, "at": later}
    assert rates(before, same) == {
        "cca_per_hour": 0,
        "busy_per_hour": 0,
        "retries_per_frame": None,
    }
    # A restarted device counts from zero again.
    assert (
        rates(before, {"tx": 5, "retry": 0, "cca": 0, "busy": 0, "at": later}) is None
    )
    assert rates(before, before) is None


def announcement(ext: str, name: str, seen: int, **extra: Any) -> dict[str, Any]:
    return {
        "extAddressHex": ext,
        "hostname": f"{name}.local",
        "vendorName": "Acme",
        "lastSeen": seen,
        **extra,
    }


async def test_border_routers_tell_role_partition_and_silence(
    ctx: Context, store: Store
) -> None:
    make_engine(ctx, rules=[])
    transport = ThreadTransport(ctx)
    minute = 60_000

    await transport.border_routers_seen(
        [
            announcement(
                "0a", "TV", 100 * minute, stateBitmapHex="00000FB1", partitionIdHex="AA"
            ),
            announcement(
                "0b",
                "Hall",
                99 * minute,
                stateBitmapHex="00000AB1",
                partitionIdHex="BB",
            ),
            # Unplugged long ago; the list still carries it.
            announcement("0c", "Shower", 60 * minute, partitionIdHex="AA"),
        ]
    )
    await transport.border_routers_seen(
        [
            announcement(
                "0a", "TV", 101 * minute, stateBitmapHex="00000FB1", partitionIdHex="AA"
            ),
            announcement(
                "0b",
                "Hall",
                100 * minute,
                stateBitmapHex="00000AB1",
                partitionIdHex="BB",
            ),
        ]
    )

    stored = {r["name"]: r for r in await store.get_state("border_routers")}
    assert set(stored) == {"TV", "Hall"}
    assert (stored["TV"]["role"], stored["TV"]["partition"]) == ("leader", "aa")
    reported = [e for e in await store.events() if e.kind == kinds.THREAD_PARTITIONS]
    # Told once, not again while nothing changed.
    assert len(reported) == 1
    assert [p["leader"] for p in reported[0].data["parts"]] == ["TV", None]


async def test_radio_counters_are_read_from_devices_that_stay_awake(
    ctx: Context, store: Store, clock: Clock
) -> None:
    make_engine(ctx, rules=[])
    transport = ThreadTransport(ctx)
    awake = {"0/53/1": 5, "0/53/65532": 15}
    await transport.devices(
        {
            "node:1": {**awake, "0/53/0": 20},
            "node:2": {**awake, "0/53/1": 6, "0/53/0": 20},
            "node:3": {"0/53/1": 2, "0/53/65532": 15, "0/53/0": 15},
            "node:4": {"0/53/1": 5, "0/53/65532": 0},  # keeps no counters
            "node:5": awake,
            "node:6": awake,
            "node:7": awake,
        }
    )
    await store.set_state("matter.nodes", {"unavailable": ["node:7"]})
    counts = {1: 1000, 2: 2000}
    asked: list[int] = []

    async def ask(command: str, **args: Any) -> Any:
        node = args["node_id"]
        asked.append(node)
        assert command == "read_attribute"
        if node == 5:
            raise RuntimeError("0: gone")
        if node == 6:
            return {"0/53/22": "odd"}
        counts[node] += 600
        return {
            "0/53/22": counts[node],
            "0/53/33": 10,
            "0/53/36": counts[node] // 100,
            "0/53/38": 0,
        }

    await transport.measure(ask)
    assert await store.get_state("thread.interference") == {
        "at": T0.isoformat(),
        "devices": {},
    }
    clock.set(T0 + timedelta(minutes=10))
    await transport.measure(ask)

    assert asked == [1, 2, 5, 6] * 2
    assert await store.get_state("thread.roles") == {
        "node:1": 5,
        "node:2": 6,
        "node:3": 2,
        "node:4": 5,
        "node:5": 5,
        "node:6": 5,
        "node:7": 5,
    }
    # The channel most devices report; a stray one does not decide.
    assert await store.get_state("thread.channel") == 20
    radio = await store.get_state("thread.interference")
    assert radio["devices"]["node:1"] == {
        "cca_per_hour": 36,
        "busy_per_hour": 0,
        "retries_per_frame": 0.0,
    }
    reported = [e for e in await store.events() if e.kind == kinds.THREAD_INTERFERENCE]
    assert [d["subject"] for d in reported[0].data["devices"]] == ["node:1", "node:2"]


async def test_the_thread_picture_tells_roles_parts_and_interference(
    ctx: Context, store: Store
) -> None:
    await store.set_state(
        "thread.tree",
        {
            "nodes": [
                {
                    "id": "br_1",
                    "subject": "br:1",
                    "kind": "border_router",
                    "parent": "home",
                    "link": {},
                },
                {
                    "id": "br_2",
                    "subject": "br:2",
                    "kind": "border_router",
                    "parent": "home",
                    "link": {},
                },
                {
                    "id": "n1",
                    "subject": "node:1",
                    "kind": "router",
                    "parent": "br_1",
                    "link": {},
                },
            ]
        },
    )
    await store.set_state(
        "border_routers",
        [
            {"subject": "br:1", "role": "leader", "partition": "aa"},
            {"subject": "br:2", "role": "child", "partition": "bb"},
        ],
    )
    await store.set_state(
        "thread.partitions", [{"partition": "aa"}, {"partition": "bb"}]
    )
    await store.set_state("thread.roles", {"node:1": 6})
    await store.set_state(
        "thread.interference", {"devices": {"node:1": {"cca_per_hour": 75}}}
    )

    picture = {e["id"]: e for e in await ThreadTransport(ctx).picture(set())}

    assert (picture["br_1"]["role"], picture["br_1"]["apart"]) == ("leader", False)
    assert (picture["br_2"]["role"], picture["br_2"]["apart"]) == ("child", True)
    assert picture["n1"]["role"] == "leader"
    assert picture["n1"]["channel_busy_per_hour"] == 75


def test_access_points_are_gathered_from_their_devices() -> None:
    ap = "02:00:00:00:00:01"
    links = {
        "node:1": {"bssid": ap, "channel": 6, "mac": "02:00:00:00:10:01"},
        "node:2": {"bssid": ap, "channel": None, "mac": None},
        "node:3": {"bssid": None},
    }

    assert access_points(links) == {
        ap: {"channel": 6, "clients": [{"mac": "02:00:00:00:10:01", "addresses": []}]}
    }
