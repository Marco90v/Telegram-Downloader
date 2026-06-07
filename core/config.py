"""
Carga de configuración desde .env + settings.json.

Expone:
  - load_dotenv: parser de .env
  - load_config: valida y retorna config desde variables de entorno
  - load_settings: carga/crea settings.json persistente
"""

import json
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = SCRIPT_DIR / "settings.json"
CATALOG_PATH = SCRIPT_DIR / "catalog.json"

DEFAULT_SETTINGS = {
    "auto_skip_all_dupes": False,
    "auto_continue": False,
    "large_file_threshold_mb": 50,
    "large_file_action": "ask",
    "TELEGRAM_TARGET_CHAT": "",
    "BATCH_SIZE": 100,
}


# ===========================================================================
# .env parser
# ===========================================================================


def load_dotenv(path: str = ".env") -> None:
    """Carga variables de entorno desde un archivo .env (KEY=VAL)."""
    try:
        with open(path, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                key, _, val = linea.partition("=")
                key = key.strip()
                val = val.strip()
                if len(val) > 1 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                os.environ.setdefault(key, val)
    except FileNotFoundError:
        pass


# ===========================================================================
# Config
# ===========================================================================


def _as_int_or_raise(key: str, value: str | None) -> int:
    if not value:
        raise ValueError("Falta TELEGRAM_API_ID en .env")
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValueError(f"TELEGRAM_API_ID inválido: {value!r}")


def _as_str_or_raise(key: str, value: str | None) -> str:
    if not value:
        raise ValueError(f"Falta {key} en .env")
    return value


def load_config() -> dict:
    """Carga y valida configuración desde variables de entorno.

    Lanza ValueError si falta algo esencial. NO imprime ni interactúa.
    """
    config: dict = {}

    config["TELEGRAM_API_ID"] = _as_int_or_raise("TELEGRAM_API_ID", os.getenv("TELEGRAM_API_ID"))
    config["TELEGRAM_API_HASH"] = _as_str_or_raise(
        "TELEGRAM_API_HASH", os.getenv("TELEGRAM_API_HASH")
    )

    raw = os.getenv("TELEGRAM_TARGET_CHAT", "").strip()
    if raw:
        try:
            config["TELEGRAM_TARGET_CHAT"] = int(raw)
        except ValueError:
            config["TELEGRAM_TARGET_CHAT"] = raw

    config["SESSION_NAME"] = os.getenv("TELEGRAM_SESSION_NAME", "sesion_telegram")
    config["OUTPUT_DIR"] = os.path.expanduser(
        os.getenv("OUTPUT_DIR", "~/Descargas/Telegram_Masivo")
    )
    try:
        config["BATCH_SIZE"] = int(os.getenv("BATCH_SIZE", "100"))
    except ValueError:
        config["BATCH_SIZE"] = 100

    return config


# ===========================================================================
# Settings persistentes
# ===========================================================================


def load_settings() -> dict:
    """Carga settings.json o crea uno con defaults.

    NO imprime mensajes — eso lo hace quien llama si quiere.
    """
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                user = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(user)
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    else:
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTINGS, f, indent=2)
        except OSError:
            pass
    return dict(DEFAULT_SETTINGS)
