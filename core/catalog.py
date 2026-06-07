"""
Operaciones de catálogo persistente (catalog.json).

Expone:
  - load_catalog / save_catalog / update_catalog
  - list_catalog / remove_catalog_entry
"""

import json
from datetime import datetime
from pathlib import Path

from core.config import CATALOG_PATH

# ===========================================================================
# Catálogo
# ===========================================================================


def load_catalog() -> dict:
    """Carga catalog.json o devuelve catálogo vacío."""
    if CATALOG_PATH.exists():
        try:
            with open(CATALOG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"chats": {}}


def save_catalog(catalog: dict) -> None:
    """Guarda catalog.json."""
    try:
        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)
    except OSError:
        pass


def update_catalog(
    catalog: dict,
    chat_key: str,
    min_id: int,
    max_id: int,
    total_new: int,
) -> None:
    """Actualiza el catálogo con los IDs procesados en esta sesión."""
    if max_id <= 0:
        return
    cat = catalog.setdefault("chats", {}).setdefault(chat_key, {})
    cat["newest_id"] = max(cat.get("newest_id", 0), max_id)
    old = cat.get("oldest_id", float("inf"))
    cat["oldest_id"] = min(old, min_id)
    if cat["oldest_id"] == float("inf"):
        cat["oldest_id"] = min_id
    cat["last_date"] = datetime.now().strftime("%Y-%m-%d")
    cat["total_count"] = cat.get("total_count", 0) + total_new
    save_catalog(catalog)


# ===========================================================================
# Gestión del catálogo
# ===========================================================================


def list_catalog() -> dict:
    """Retorna el catálogo completo: {chats: {chat_key: {...}}}."""
    return load_catalog()


def remove_catalog_entry(
    chat_key: str,
    output_dir: Path | None = None,
    delete_files: bool = False,
) -> bool:
    """Elimina una entrada del catálogo.

    Args:
        chat_key: nombre del chat (folder name).
        output_dir: directorio base de descargas (para borrar carpeta).
        delete_files: si True, borra también la carpeta del chat.

    Returns:
        True si se eliminó la entrada, False si no existía.
    """
    import shutil

    catalog = load_catalog()
    chats = catalog.get("chats", {})
    if chat_key not in chats:
        return False

    del chats[chat_key]
    save_catalog(catalog)

    if delete_files and output_dir is not None:
        chat_dir = output_dir / chat_key
        if chat_dir.exists():
            shutil.rmtree(chat_dir)

    return True
