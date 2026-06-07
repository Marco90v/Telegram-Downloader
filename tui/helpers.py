"""
Helpers compartidos para la TUI: markup Textual y persistencia de settings.
"""

import json

from core.config import SETTINGS_PATH
from format.textual import err as _err
from format.textual import esc as _esc
from format.textual import head as _head
from format.textual import ok as _ok
from format.textual import warn as _warn

__all__ = ["_ok", "_warn", "_err", "_head", "_esc", "_save_settings"]


def _save_settings(settings: dict) -> None:
    """Guarda settings.json."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass
