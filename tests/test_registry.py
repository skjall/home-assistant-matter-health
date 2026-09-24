import pytest

from matter_health.registry import Registry


def test_register_get_and_iterate_in_name_order() -> None:
    registry: Registry[str] = Registry("thing")
    registry.register("zeta")("last")
    registry.register("alpha")("first")

    assert registry.get("alpha") == "first"
    assert registry.names() == ["alpha", "zeta"]
    assert list(registry) == ["first", "last"]
    assert len(registry) == 2


def test_registering_a_name_twice_is_refused() -> None:
    registry: Registry[str] = Registry("thing")
    registry.register("one")("a")

    with pytest.raises(ValueError, match="thing 'one' is registered twice"):
        registry.register("one")("b")


def test_unknown_name_names_the_kind() -> None:
    registry: Registry[str] = Registry("parser")

    with pytest.raises(KeyError, match="no parser named 'missing'"):
        registry.get("missing")


def test_plugins_register_themselves_on_import() -> None:
    from matter_health import rules, sources, transports
    from matter_health.engine import RULES, SOURCES
    from matter_health.parsers import PARSERS
    from matter_health.transports import TRANSPORTS

    assert rules.__all__ and sources.__all__ and transports.__all__
    assert TRANSPORTS.names() == ["ethernet", "thread", "wifi"]

    assert RULES.names() == [
        "border_router",
        "flaky",
        "host",
        "mesh",
        "offline",
        "pairing",
        "radio",
        "relay",
        "server",
        "signal",
        "wave",
        "wifi_signal",
    ]
    assert SOURCES.names() == [
        "home_assistant",
        "matter_server",
        "matter_server_log",
        "otbr",
        "otbr_log",
        "system",
        "unifi",
    ]
    assert PARSERS.names() == ["matter_js", "openthread"]
