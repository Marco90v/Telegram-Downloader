"""
Motor compartido de descarga — usado por CLI (descarga.py) y TUI (tui/).

Re-exporta todo desde los submódulos para mantener compatibilidad con:
    from core import DownloadEngine, download_one, load_config, ...
"""

from core.catalog import (
    list_catalog,
    load_catalog,
    remove_catalog_entry,
    save_catalog,
    update_catalog,
)
from core.config import load_config, load_dotenv, load_settings
from core.download import download_one
from core.engine import DownloadEngine
from core.media import fmt_count, format_size, is_media_wanted, media_ext, media_path, media_size
from core.telegram import count_media, resolve_entity, setup_output_dir

__all__ = [
    "DownloadEngine",
    "count_media",
    "download_one",
    "fmt_count",
    "format_size",
    "is_media_wanted",
    "list_catalog",
    "load_catalog",
    "load_config",
    "load_dotenv",
    "load_settings",
    "media_ext",
    "media_path",
    "media_size",
    "remove_catalog_entry",
    "resolve_entity",
    "save_catalog",
    "setup_output_dir",
    "update_catalog",
]
