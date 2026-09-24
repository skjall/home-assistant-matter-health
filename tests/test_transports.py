from typing import Any

import pytest
from conftest import Clock, chain_keys, emit_at, make_engine

from matter_health import kinds
from matter_health.engine import Context
from matter_health.model import Severity
from matter_health.store import Store
from matter_health.transports import FEATURE_MAP, quality, transport_of
from matter_health.transports.ethernet.transport import EthernetTransport
from matter_health.transports.thread.transport import link_quality
from matter_health.transports.wifi.signal import WifiSignalRule
from matter_health.transports.wifi.transport import WifiTransport, describe

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
