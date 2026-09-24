"""Everything the add-on observes. Each module registers one or more sources."""

from . import addon_logs, home_assistant, matter_server, otbr, system

__all__ = ["addon_logs", "home_assistant", "matter_server", "otbr", "system"]
