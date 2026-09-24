"""The add-on's options and where to find the things it observes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OPTIONS_FILE = Path("/data/options.json")
DATA_DIR = Path("/data")

#: The official add-ons this one observes. Community builds use other slugs;
#: the options let a user point at those instead.
MATTER_SERVER_SLUG = "core_matter_server"
OTBR_SLUG = "core_openthread_border_router"

MATTER_SERVER_PORT = 5580
OTBR_REST_PORT = 8081


@dataclass(frozen=True, slots=True)
class Options:
    """Everything the add-on can be told, with defaults for running outside it."""

    retention_days: int = 30
    log_level: str = "info"
    matter_server_url: str | None = None
    otbr_url: str | None = None
    supervisor_url: str = "http://supervisor"
    supervisor_token: str | None = None
    data_dir: Path = DATA_DIR
    port: int = 8099

    @property
    def core_websocket_url(self) -> str:
        """Home Assistant's websocket, reached through the Supervisor proxy."""
        base = self.supervisor_url.replace("http", "ws", 1)
        return f"{base}/core/websocket"


def load_options(
    path: Path = OPTIONS_FILE, env: dict[str, str] | None = None
) -> Options:
    """Read the options the Supervisor wrote, plus the token from the environment."""
    environ = os.environ if env is None else env
    raw: dict[str, Any] = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    return Options(
        retention_days=int(raw.get("retention_days", 30)),
        log_level=str(raw.get("log_level", "info")),
        matter_server_url=raw.get("matter_server_url") or None,
        otbr_url=raw.get("otbr_url") or None,
        supervisor_url=environ.get("SUPERVISOR_URL", "http://supervisor"),
        supervisor_token=environ.get("SUPERVISOR_TOKEN"),
        data_dir=Path(environ.get("MATTER_HEALTH_DATA", str(DATA_DIR))),
        port=int(environ.get("MATTER_HEALTH_PORT", "8099")),
    )
