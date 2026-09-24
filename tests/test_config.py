import json
from pathlib import Path

import pytest

from matter_health.config import DATA_DIR, Options, load_options


def test_load_options_from_file_and_environment(tmp_path: Path) -> None:
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps(
            {
                "retention_days": "14",
                "log_level": "debug",
                "matter_server_url": "ws://192.0.2.10:5580",
                "otbr_url": "",
            }
        ),
        encoding="utf-8",
    )

    options = load_options(
        path,
        {
            "SUPERVISOR_URL": "http://192.0.2.20",
            "SUPERVISOR_TOKEN": "secret",
            "MATTER_HEALTH_DATA": str(tmp_path),
            "MATTER_HEALTH_PORT": "8123",
        },
    )

    assert options == Options(
        retention_days=14,
        log_level="debug",
        matter_server_url="ws://192.0.2.10:5580",
        otbr_url=None,
        supervisor_url="http://192.0.2.20",
        supervisor_token="secret",
        data_dir=tmp_path,
        port=8123,
        extra={
            "retention_days": "14",
            "log_level": "debug",
            "matter_server_url": "ws://192.0.2.10:5580",
            "otbr_url": "",
        },
    )
    assert options.core_websocket_url == "ws://192.0.2.20/core/websocket"


def test_missing_file_and_empty_environment_give_defaults(tmp_path: Path) -> None:
    options = load_options(tmp_path / "absent.json", {})

    assert options == Options()
    assert options.data_dir == DATA_DIR
    assert options.core_websocket_url == "ws://supervisor/core/websocket"


def test_process_environment_is_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "from-env")
    monkeypatch.delenv("SUPERVISOR_URL", raising=False)

    options = load_options(tmp_path / "absent.json")

    assert options.supervisor_token == "from-env"
