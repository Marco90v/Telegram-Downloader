#!/usr/bin/env python3
"""
Motor compartido de descarga — usado por CLI (descarga.py) y TUI (tui.py).

Expone:
  - Funciones puras: load_config, load_settings, helpers de media, catálogo
  - Funciones Telegram: resolve_entity, count_media, setup_output_dir, download_one
  - Clase DownloadEngine: estado, conexión, preparación, fetch adaptativo
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, errors
from telethon.tl.functions.messages import SearchRequest
from telethon.tl.types import InputMessagesFilterPhotos, InputMessagesFilterVideo

# ===========================================================================
# Constantes
# ===========================================================================

SCRIPT_DIR = Path(__file__).parent
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
    catalog = load_catalog()
    chats = catalog.get("chats", {})
    if chat_key not in chats:
        return False

    del chats[chat_key]
    save_catalog(catalog)

    if delete_files and output_dir is not None:
        chat_dir = output_dir / chat_key
        if chat_dir.exists():
            import shutil

            shutil.rmtree(chat_dir)

    return True


# ===========================================================================
# Helpers de media
# ===========================================================================


def format_size(bytes_: int) -> str:
    """Formatea bytes a unidad legible (KB / MB / GB)."""
    if bytes_ >= 1024**3:
        return f"{bytes_ / 1024**3:.2f} GB"
    if bytes_ >= 1024**2:
        return f"{bytes_ / 1024**2:.1f} MB"
    if bytes_ >= 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_} B"


def _mime_ext(mime: str) -> str:
    """Mapa mime → extensión."""
    m = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "video/x-matroska": ".mkv",
        "video/avi": ".avi",
    }
    return m.get(mime, ".bin")


def media_ext(msg) -> str:
    """Determina la extensión del archivo multimedia de un mensaje."""
    if msg.photo and getattr(msg.photo, "ext", None):
        return str(msg.photo.ext)
    if msg.video and getattr(msg.video, "ext", None):
        return str(msg.video.ext)
    doc = getattr(msg, "document", None)
    if doc and doc.mime_type:
        return _mime_ext(doc.mime_type)
    return ".jpg"


def media_path(msg, output_dir: Path) -> Path:
    """Path único y determinístico: YYYYMMDD_MessageID.ext."""
    fecha = msg.date.strftime("%Y%m%d") if msg.date else "00000000"
    return output_dir / f"{fecha}_{msg.id}{media_ext(msg)}"


def chat_folder_name(entity) -> str:
    """Nombre de carpeta legible y seguro desde la entidad del chat."""
    name = None
    if hasattr(entity, "title") and entity.title:
        name = entity.title
    elif hasattr(entity, "username") and entity.username:
        name = entity.username
    else:
        name = f"chat_{entity.id}" if hasattr(entity, "id") else "desconocido"
    safe = "".join(c if c.isalnum() or c in " _-." else "_" for c in name)
    return safe.strip().strip(".")[:60] or "telegram_chat"


def is_media_wanted(msg) -> bool:
    """Fotos y videos, nada más."""
    return bool(msg.photo) or bool(msg.video)


def media_size(msg) -> int | None:
    """Estima el tamaño en bytes del contenido multimedia sin descargarlo."""
    if msg.document:
        return msg.document.size
    if msg.photo and msg.photo.sizes:
        biggest = msg.photo.sizes[-1]
        if hasattr(biggest, "size"):
            return biggest.size
    return None


def fmt_count(n: int | None) -> str:
    """Formatea un contador o '?' si es None."""
    if n is None:
        return "?"
    return f"{n:,}"


# ===========================================================================
# Funciones Telegram (bajo nivel)
# ===========================================================================


async def resolve_entity(client: TelegramClient, chat_id: int | str):
    """Resuelve chat ID → Entity. Prueba prefijo -100 si el numérico falla."""
    try:
        return await client.get_entity(chat_id)
    except Exception:
        if isinstance(chat_id, int) and chat_id < 0 and not str(chat_id).startswith("-100"):
            try:
                return await client.get_entity(int(f"-100{abs(chat_id)}"))
            except Exception:
                return None
        return None


async def count_media(client, entity):
    """Cuenta fotos y videos via SearchRequest server-side (sin descargar nada)."""
    try:
        fotos = await client(
            SearchRequest(
                peer=entity,
                q="",
                filter=InputMessagesFilterPhotos(),
                limit=1,
            )
        )
        videos = await client(
            SearchRequest(
                peer=entity,
                q="",
                filter=InputMessagesFilterVideo(),
                limit=1,
            )
        )
        total_fotos = getattr(fotos, "count", None)
        if total_fotos is None:
            total_fotos = getattr(fotos, "total", 0) or 0
        total_videos = getattr(videos, "count", None)
        if total_videos is None:
            total_videos = getattr(videos, "total", 0) or 0
        return total_fotos, total_videos
    except Exception:
        return None, None


async def setup_output_dir(client, entity, config: dict) -> tuple[Path, str]:
    """Crea carpeta de salida con subcarpetas por chat.

    Si el chat tiene un canal vinculado, usa estructura de dos niveles.
    Retorna (output_dir, chat_key).
    """
    folder = chat_folder_name(entity)
    parent_folder = None
    linked_id = getattr(entity, "linked_chat_id", None)
    if linked_id:
        try:
            parent_entity = await client.get_entity(linked_id)
            parent_folder = chat_folder_name(parent_entity)
        except Exception:
            pass
    if parent_folder and parent_folder != folder:
        output_dir = Path(config["OUTPUT_DIR"]) / parent_folder / folder
    else:
        output_dir = Path(config["OUTPUT_DIR"]) / folder
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir, folder or str(entity.id)


# ===========================================================================
# Descarga individual
# ===========================================================================

FileResult = dict  # {'status': str, 'size': int, 'path': str|None, 'error': str|None}


async def download_one(
    client: TelegramClient,
    msg,
    output_dir: Path,
    settings: dict,
    *,
    progress_callback=None,
) -> FileResult:
    """Descarga UN archivo multimedia con manejo de errores.

    No maneja interacción con usuario (large_file_action='ask' se decide afuera).
    El caller decide si preguntar antes de llamar.

    Parámetros:
        progress_callback: async o sync fn(current, total) llamada durante descarga.

    Retorna dict con:
        status: 'ok' | 'dup' | 'skip' | 'err'
        size: bytes descargados (0 si no se descargó)
        path: str | None
        error: str | None (solo si status='err')
    """
    fpath = media_path(msg, output_dir)

    # ── Ya existe ──
    if fpath.exists():
        return {"status": "dup", "size": 0, "path": str(fpath), "error": None}

    # ── Archivo muy grande (skip silencioso) ──
    fsize = media_size(msg)
    thr = settings["large_file_threshold_mb"] * 1024 * 1024
    es_grande = fsize is not None and fsize > thr and thr > 0
    if es_grande and settings["large_file_action"] == "skip":
        return {"status": "skip", "size": 0, "path": str(fpath), "error": None}

    # ── Descarga con retry en FloodWait ──
    try:
        ruta = await client.download_media(
            msg,
            file=str(fpath),
            progress_callback=progress_callback,
        )
        if ruta is None:
            fpath.unlink(missing_ok=True)
            return {"status": "err", "size": 0, "path": str(fpath), "error": "no disponible"}
        file_size = fpath.stat().st_size
        return {"status": "ok", "size": file_size, "path": str(fpath), "error": None}

    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        # Reintento único
        try:
            ruta = await client.download_media(
                msg,
                file=str(fpath),
                progress_callback=progress_callback,
            )
            if ruta:
                file_size = fpath.stat().st_size
                return {"status": "ok", "size": file_size, "path": str(fpath), "error": None}
            fpath.unlink(missing_ok=True)
            return {
                "status": "err",
                "size": 0,
                "path": str(fpath),
                "error": "no disponible (retry)",
            }
        except Exception as e2:
            return {"status": "err", "size": 0, "path": str(fpath), "error": str(e2)}

    except Exception as e:
        return {"status": "err", "size": 0, "path": str(fpath), "error": str(e)}


# ===========================================================================
# DownloadEngine — orquestación de alto nivel
# ===========================================================================


class DownloadEngine:
    """Motor de descarga: maneja conexión, estado y ciclo de fetch.

    Uso:
        engine = DownloadEngine(config, settings)
        await engine.connect()
        info = await engine.prepare()
        # en un loop: engine.fetch_batch(...)
        await engine.disconnect()
    """

    def __init__(self, config: dict, settings: dict):
        self.config = config
        self.settings = settings
        self.client: TelegramClient | None = None
        self.entity = None
        self.chat_key: str | None = None
        self.output_dir: Path | None = None
        self.catalog: dict = {}

        # Contadores de sesión
        self.total_ok = 0
        self.total_dup = 0
        self.total_err = 0
        self.total_skip = 0
        self.total_bytes = 0
        self.session_min_id = float("inf")
        self.session_max_id = 0

    # ── Conexión ──

    async def connect(self):
        """Crea el TelegramClient y lo conecta."""
        self.client = TelegramClient(
            self.config["SESSION_NAME"],
            self.config["TELEGRAM_API_ID"],
            self.config["TELEGRAM_API_HASH"],
        )
        await self.client.start()
        return self.client

    async def disconnect(self):
        """Desconecta el client si está conectado."""
        if self.client:
            await self.client.disconnect()

    # ── Preparación ──

    async def prepare(self) -> dict:
        """Resuelve entidad, crea directorios, cuenta media, carga catálogo.

        Retorna dict con metadatos del chat:
            entity, chat_key, output_dir, chat_name,
            fotos, videos, has_catalog, resume_info
        """
        chat_val = self.config.get("TELEGRAM_TARGET_CHAT", "")
        if not chat_val:
            raise ValueError(
                "No hay chat configurado. Definí TELEGRAM_TARGET_CHAT en .env "
                "o desde la configuración de la TUI."
            )
        self.entity = await resolve_entity(self.client, chat_val)
        if self.entity is None:
            raise ValueError(
                f"No se pudo resolver el chat {chat_val}. ¿La sesión es miembro del chat?"
            )

        self.output_dir, self.chat_key = await setup_output_dir(
            self.client, self.entity, self.config
        )

        fotos, videos = await count_media(self.client, self.entity)
        self.catalog = load_catalog()
        prev = self.catalog.get("chats", {}).get(self.chat_key)

        return {
            "entity": self.entity,
            "chat_key": self.chat_key,
            "output_dir": self.output_dir,
            "chat_name": chat_folder_name(self.entity),
            "fotos": fotos,
            "videos": videos,
            "has_catalog": prev is not None,
            "resume_info": prev,
        }

    # ── Búsqueda adaptativa de mensajes ──

    async def fetch_batch(
        self,
        *,
        offset_id: int = 0,
        limit: int = 100,
        since=None,
        until=None,
        resume_newest: int | None = None,
        resume_oldest: int | None = None,
        resume_complete: bool = False,
    ) -> dict:
        """Busca un lote de mensajes multimedia con sub-lotes adaptativos.

        Retorna dict:
            media: list[Message] — multimedia listo para descargar
            reached_start: bool — True si tocó el límite 'since'
            next_offset: int — para la próxima iteración
            should_stop: bool — True si no hay más mensajes o se vació por resume
        """
        assert self.client is not None
        assert self.entity is not None

        media_found: list = []
        reached_start = False
        next_offset = offset_id

        while len(media_found) < limit:
            faltan = limit - len(media_found)
            pedir = min(100, faltan)

            kwargs: dict = dict(limit=pedir)
            if next_offset:
                kwargs["offset_id"] = next_offset
            if until:
                kwargs["offset_date"] = until

            try:
                mensajes = await self.client.get_messages(self.entity, **kwargs)
            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                continue
            except Exception:
                return {
                    "media": media_found,
                    "reached_start": False,
                    "next_offset": next_offset,
                    "should_stop": True,
                    "error": "Error al obtener mensajes",
                }

            if not mensajes:
                return {
                    "media": media_found,
                    "reached_start": reached_start,
                    "next_offset": next_offset,
                    "should_stop": len(media_found) == 0,
                    "error": None,
                }

            # Filtrar multimedia y fecha
            pendientes = []
            for m in mensajes:
                if since and m.date.replace(tzinfo=timezone.utc) < since:
                    reached_start = True
                    break
                if is_media_wanted(m):
                    pendientes.append(m)

            if not pendientes:
                next_offset = mensajes[-1].id
                if reached_start:
                    break
                continue

            # Filtrar por resume
            if resume_newest is not None and pendientes:
                before = len(pendientes)
                pendientes = [m for m in pendientes if not (resume_oldest <= m.id <= resume_newest)]
                if not pendientes and before > 0:
                    if resume_complete:
                        next_offset = resume_oldest
                        resume_newest = None
                        continue
                    else:
                        return {
                            "media": media_found,
                            "reached_start": False,
                            "next_offset": next_offset,
                            "should_stop": True,
                            "error": None,
                        }
                if pendientes and pendientes[-1].id < resume_oldest:
                    resume_newest = None  # ya pasamos la ventana de resume

            media_found.extend(pendientes)
            next_offset = mensajes[-1].id
            if reached_start:
                break

        return {
            "media": media_found,
            "reached_start": reached_start,
            "next_offset": next_offset,
            "should_stop": False,
            "error": None,
        }

    # ── Contadores globales (acumular resultados de download_one) ──

    def add_result(self, result: dict):
        """Acumula el resultado de un download_one en los totales de sesión."""
        s = result["status"]
        if s == "ok":
            self.total_ok += 1
            self.total_bytes += result.get("size", 0)
        elif s == "dup":
            self.total_dup += 1
        elif s == "skip":
            self.total_skip += 1
        elif s == "err":
            self.total_err += 1

    # ── Resumen de sesión ──

    @property
    def totals(self) -> dict:
        return {
            "ok": self.total_ok,
            "dup": self.total_dup,
            "err": self.total_err,
            "skip": self.total_skip,
            "bytes": self.total_bytes,
        }

    def finalize(self):
        """Guarda el catálogo con los resultados de esta sesión.

        Llama a actualizar catálogo y lo guarda. Debe llamarse al finalizar.
        """
        if self.session_max_id <= 0:
            return
        update_catalog(
            self.catalog,
            self.chat_key,
            int(self.session_min_id),
            self.session_max_id,
            self.total_ok + self.total_skip,
        )
