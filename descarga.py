#!/usr/bin/env python3
"""
Descarga masiva de contenido multimedia (fotos y videos) de Telegram.

Procesa por lotes y pregunta al usuario si desea continuar después de cada uno.
Las credenciales van en .env (ver .env.example).

Uso:
    python descarga.py
"""

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, errors
from telethon.tl.functions.messages import SearchRequest
from telethon.tl.types import InputMessagesFilterPhotos, InputMessagesFilterVideo

# ===========================================================================
# Colores ANSI (compatibles con cualquier terminal moderna)
# ===========================================================================
# Sin dependencias, puro escape codes. Las funciones devuelven strings
# listas para print(). NO se usa color en operators, solo en UI.


class _c:
    """ANSI escape codes — usa _c.GREEN + texto + _c.RST como marcador."""

    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YEL = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    MAG = "\033[35m"


# Convenience: wrapper corto para emojis/iconos de estado
def _ok(t: str) -> str:
    return f"{_c.GREEN}{t}{_c.RST}"


def _warn(t: str) -> str:
    return f"{_c.YEL}{t}{_c.RST}"


def _err(t: str) -> str:
    return f"{_c.RED}{t}{_c.RST}"


def _head(t: str) -> str:
    return f"{_c.CYAN}{_c.BOLD}{t}{_c.RST}"


# ===========================================================================
# .env parser (zero dependencies)
# ===========================================================================


def _load_dotenv(path: str = ".env") -> None:
    """Carga variables de entorno desde un archivo .env (formato KEY=VAL)."""
    try:
        with open(path, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                key, _, val = linea.partition("=")
                key = key.strip()
                val = val.strip()
                # Sacar comillas envolventes si las hay
                if len(val) > 1 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                os.environ.setdefault(key, val)
    except FileNotFoundError:
        pass  # Sin .env no es error — usará defaults y fallará en validación si faltan vars


# ===========================================================================
# Config
# ===========================================================================


def load_config() -> dict:
    """Carga y valida configuración desde variables de entorno."""
    config = {}
    # API_ID y API_HASH: chequeo estricto
    for key, cast in [("TELEGRAM_API_ID", int), ("TELEGRAM_API_HASH", str)]:
        value = os.getenv(key)
        if not value:
            print(f"  ERROR: Falta la variable de entorno {key}. Revisá el archivo .env")
            sys.exit(1)
        try:
            config[key] = cast(value)
        except (ValueError, TypeError):
            print(f"  ERROR: {key} tiene un valor inválido: {value!r}")
            sys.exit(1)

    # TARGET_CHAT: acepta ID numérico (int), username (str) o link (str)
    raw = os.getenv("TELEGRAM_TARGET_CHAT", "").strip()
    if not raw:
        print("  ERROR: Falta la variable de entorno TELEGRAM_TARGET_CHAT. Revisá el archivo .env")
        sys.exit(1)
    # Intentar convertir a int; si falla, usar como string (username/link)
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
# Interacción con el usuario
# ===========================================================================


def ask_bool(prompt: str) -> bool:
    """Pregunta sí/no al usuario. Bucle hasta respuesta válida."""
    while True:
        r = input(prompt).strip().lower()
        if r in ("s", "si", "sí", "y", "yes"):
            return True
        if r in ("n", "no", "not", "q"):
            return False
        print("  Respondé 's' para sí, 'n' para no, 'q' para salir.")


def ask_date_filter():
    """Pregunta si filtrar por rango de fechas. Devuelve (since, until) como datetime UTC o (None, None)."""
    if not ask_bool("\n  ¿Filtrar por rango de fechas? (s/n): "):
        return None, None

    since = until = None

    raw = input("  Fecha inicio (YYYY-MM-DD, Enter para omitir): ").strip()
    if raw:
        try:
            since = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print("    └ Formato inválido, se ignora.")

    raw = input("  Fecha fin    (YYYY-MM-DD, Enter para omitir): ").strip()
    if raw:
        try:
            # Fin del día para incluir mensajes de esa fecha
            until = datetime.strptime(raw, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        except ValueError:
            print("    └ Formato inválido, se ignora.")

    return since, until


def ask_continue(total_downloaded: int) -> bool:
    """Pregunta si seguir con el siguiente lote."""
    print("\n  ─────────────────────────────────────────────")
    print(f"  Descargados hasta ahora: {total_downloaded} archivos")
    return ask_bool("  ¿Seguir? (s/n/q): ")


# ===========================================================================
# Utilidades
# ===========================================================================


def format_size(bytes_: int) -> str:
    """Formatea bytes a unidad legible."""
    if bytes_ >= 1024**3:
        return f"{bytes_ / 1024**3:.2f} GB"
    if bytes_ >= 1024**2:
        return f"{bytes_ / 1024**2:.1f} MB"
    if bytes_ >= 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_} B"


def _clear_line() -> str:
    """Devuelve un prefijo que borra toda la línea actual de la terminal.

    Usa el ancho real de la terminal. Útil después de líneas con barra de progreso
    para que no queden restos al escribir un mensaje más corto.
    """
    cols = shutil.get_terminal_size().columns
    return f"\r{' ' * cols}\r"


def progress_factory(prefix: str, show_size: bool = True):
    """Devuelve un callback de progreso que actualiza una línea con el prefijo dado.

    Muestra barra de progreso, porcentaje y (opcionalmente) bytes descargados/total.
    Uso: progress_factory('📷 [  3/80 ] nombre.jpg')(current, total)
    """

    def cb(current: int, total: int):
        if total <= 0:
            return
        pct = current / total * 100
        blen = 25
        fill = int(blen * current / total)
        bar = "█" * fill + "░" * (blen - fill)
        line = f"\r{prefix} │{bar}│ {pct:3.0f}%"
        if show_size:
            line += f"  {format_size(current)}/{format_size(total)}"
        print(line, end="", flush=True)

    return cb


def _media_ext(msg) -> str:
    """Determina la extensión del archivo multimedia de un mensaje."""
    if msg.photo and getattr(msg.photo, "ext", None):
        return str(msg.photo.ext)
    if msg.video and getattr(msg.video, "ext", None):
        return str(msg.video.ext)
    # Fallback por mime type
    doc = getattr(msg, "document", None)
    if doc and doc.mime_type:
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
            "video/x-matroska": ".mkv",
            "video/avi": ".avi",
        }
        return mime_map.get(doc.mime_type, ".bin")
    return ".jpg"


def _media_path(msg, output_dir: Path) -> Path:
    """Path único y determinístico para el multimedia de un mensaje.

    El nombre incluye fecha + message_id para evitar duplicados y
    ordenar cronológicamente.
    """
    fecha = msg.date.strftime("%Y%m%d") if msg.date else "00000000"
    return output_dir / f"{fecha}_{msg.id}{_media_ext(msg)}"


def _chat_folder_name(entity) -> str:
    """Deriva un nombre de carpeta legible y seguro desde la entidad del chat."""
    name = None
    if hasattr(entity, "title") and entity.title:
        name = entity.title
    elif hasattr(entity, "username") and entity.username:
        name = entity.username
    else:
        name = f"chat_{entity.id}" if hasattr(entity, "id") else "desconocido"

    # Solo caracteres seguros para sistema de archivos
    safe = "".join(c if c.isalnum() or c in " _-." else "_" for c in name)
    return safe.strip().strip(".")[:60] or "telegram_chat"


def is_media_wanted(msg) -> bool:
    """Fotos y videos, nada más."""
    return bool(msg.photo) or bool(msg.video)


def _fmt_count(n: int | None) -> str:
    """Formatea un contador o '?' si es None."""
    if n is None:
        return "?"
    return f"{n:,}"


def _media_size(msg) -> int | None:
    """Estima el tamaño en bytes del contenido multimedia sin descargarlo.

    Para videos/documentos usa document.size (exacto).
    Para fotos usa el tamaño del tamaño completo (aproximado).
    """
    if msg.document:
        return msg.document.size
    if msg.photo and msg.photo.sizes:
        # El último size suele ser la resolución completa
        biggest = msg.photo.sizes[-1]
        if hasattr(biggest, "size"):
            return biggest.size
    return None


# ===========================================================================
# Config persistente (settings.json)
# ===========================================================================

SETTINGS_PATH = Path(__file__).parent / "settings.json"
CATALOG_PATH = Path(__file__).parent / "catalog.json"

DEFAULT_SETTINGS = {
    "auto_skip_all_dupes": False,  # Si lote completo es dupe/omitido, no preguntar (auto-continúa)
    "auto_continue": False,  # Modo silencioso total: nunca preguntar entre lotes
    "large_file_threshold_mb": 50,  # Umbral en MB (modos "skip" o "ask" ignoran archivos > esto)
    "large_file_action": "ask",  # "ask" | "download" | "skip"
}


def _load_settings() -> dict:
    """Carga settings.json o crea uno con valores por defecto."""
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                user = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(user)  # lo que puso el usuario pisa defaults
            return merged
        except (json.JSONDecodeError, OSError) as e:
            print(f"  {_warn('⚠')} settings.json inválido ({e}), se usan valores por defecto.")
    else:
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTINGS, f, indent=2)
            print(f"  {_ok('✓')} Creado settings.json")
            print("    └ Editálo para cambiar el comportamiento sin tocar código.")
            print("    └ Opciones:")
            print("    └   large_file_action → ask | download | skip")
            print("    └   large_file_threshold_mb → número (MB)")
            print("    └   auto_skip_all_dupes → true | false")
            print("    └   auto_continue → true | false (modo silencioso total)")
        except OSError as e:
            print(f"  {_warn('⚠')} No se pudo crear settings.json: {e}")
    return dict(DEFAULT_SETTINGS)


# ===========================================================================
# Catálogo de descargas (catalog.json)
# ===========================================================================
# Registra el rango de message_ids procesados por chat para poder reanudar
# en sesiones futuras sin re-descargar todo.


def _load_catalog() -> dict:
    """Carga catalog.json o devuelve un catálogo vacío."""
    if CATALOG_PATH.exists():
        try:
            with open(CATALOG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"chats": {}}


def _save_catalog(catalog: dict) -> None:
    """Guarda catalog.json."""
    try:
        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)
    except OSError as e:
        print(f"  {_warn('⚠')} No se pudo guardar catalog.json: {e}")


async def _count_media(client, entity):
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
        # SearchRequest devuelve .count en la mayoría de los casos;
        # .total es alternativa para get_messages.
        total_fotos = getattr(fotos, "count", None)
        if total_fotos is None:
            total_fotos = getattr(fotos, "total", 0) or 0
        total_videos = getattr(videos, "count", None)
        if total_videos is None:
            total_videos = getattr(videos, "total", 0) or 0
        return total_fotos, total_videos
    except Exception:
        return None, None


# ===========================================================================
# Lógica de descarga
# ===========================================================================


async def _resolve_entity(client: TelegramClient, chat_id: int | str) -> object | None:
    """Resuelve un chat ID a un objeto Entity de Telegram.

    Si el ID numérico falla, prueba con el prefijo -100 (canales/supergrupos
    necesitan ese prefijo en el ID numérico).
    """
    try:
        return await client.get_entity(chat_id)
    except Exception:
        if isinstance(chat_id, int) and chat_id < 0 and not str(chat_id).startswith("-100"):
            try:
                return await client.get_entity(int(f"-100{abs(chat_id)}"))
            except Exception:
                return None
        return None


def _show_config_banner(settings: dict):
    """Muestra la configuración activa (modo, dupes, umbral de tamaño)."""
    action_label = {"ask": "preguntar", "download": "siempre", "skip": "omitir"}.get(
        settings["large_file_action"], settings["large_file_action"]
    )
    mode = (
        "silencioso"
        if settings.get("auto_continue")
        else ("auto-skip" if settings.get("auto_skip_all_dupes") else "normal")
    )
    print(
        f"  {_head('⚙')} Modo: {mode}  |  "
        f"Auto-skip dupes: {'ON' if settings['auto_skip_all_dupes'] else 'OFF'}  |  "
        f"Archivos >{settings['large_file_threshold_mb']}MB: {action_label}"
    )
    print()


async def _setup_output_dir(client: TelegramClient, entity: object, config: dict) -> tuple:
    """Crea y retorna la carpeta de salida con subcarpetas por chat.

    Si el chat es un sub-grupo vinculado a un canal, usa una estructura
    de dos niveles: Canal / Sub-grupo. Retorna (output_dir, chat_key).
    """
    chat_folder = _chat_folder_name(entity)

    parent_folder = None
    linked_id = getattr(entity, "linked_chat_id", None)
    if linked_id:
        try:
            parent_entity = await client.get_entity(linked_id)
            parent_folder = _chat_folder_name(parent_entity)
        except Exception:
            pass

    if parent_folder and parent_folder != chat_folder:
        output_dir = Path(config["OUTPUT_DIR"]) / parent_folder / chat_folder
    else:
        output_dir = Path(config["OUTPUT_DIR"]) / chat_folder

    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir, chat_folder or str(entity.id)


def _handle_catalog_resume(catalog: dict, chat_key: str) -> tuple:
    """Muestra el menú de reanudar si hay una sesión anterior.

    Retorna (resume_newest, resume_oldest, resume_complete).
    Si el usuario elige "empezar de nuevo" o no hay catálogo, todo None/False.
    """
    prev = catalog.get("chats", {}).get(chat_key)
    if not prev:
        return None, None, False

    pn = prev.get("newest_id", 0)
    po = prev.get("oldest_id", 0)
    pc = prev.get("total_count", 0)
    pd = prev.get("last_date", "?")

    print(f"    Última sesión: {pc} archivos procesados (mensajes {po}→{pn}, {pd})")
    print("    1. Reanudar — solo contenido nuevo")
    print("    2. Reanudar — continuar también hacia atrás")
    print("    3. Empezar de nuevo")
    opt = input("  Opción (1/2/3): ").strip()
    if opt in ("1", "2"):
        print()
        return pn, po, opt == "2"

    return None, None, False


async def _download_one(
    client: TelegramClient,
    msg: object,
    output_dir: Path,
    settings: dict,
    pos: int,
    batch_size: int,
) -> dict:
    """Descarga UN archivo multimedia con manejo completo de errores.

    Flujo: dup → tamaño grande → descargar → FloodWait con retry → error.
    Cada caso imprime su propia línea de estado.

    Retorna {'status': 'ok'|'dup'|'skip'|'err', 'size': int}
    """
    fpath = _media_path(msg, output_dir)
    icono = "📷" if msg.photo else "🎬"
    w = len(str(batch_size))
    inicio = f"  {icono} [{pos:>{w}}/{batch_size}] {fpath.name}"

    # ── Ya existe (dup) ──
    if fpath.exists():
        try:
            dup_size = format_size(fpath.stat().st_size)
            print(f"  {_warn('⏭')} {inicio}  ({dup_size})  (ya existe)")
        except OSError:
            print(f"  {_warn('⏭')} {inicio}  (ya existe)")
        return {"status": "dup", "size": 0}

    # ── Archivo muy grande ──
    fsize = _media_size(msg)
    thr = settings["large_file_threshold_mb"] * 1024 * 1024
    es_grande = fsize is not None and fsize > thr and thr > 0

    if es_grande and settings["large_file_action"] == "skip":
        print(f"  {_warn('⏭')} {inicio}  {_warn(format_size(fsize))}  (omitido por tamaño)")
        return {"status": "skip", "size": 0}

    if es_grande and settings["large_file_action"] == "ask":
        if not ask_bool(f"{inicio}  ({format_size(fsize)})  ¿Descargar? (s/n): "):
            return {"status": "skip", "size": 0}
    else:
        sys.stdout.write(inicio)
        sys.stdout.flush()

    # ── Descarga con retry en FloodWait ──
    try:
        ruta = await client.download_media(
            msg,
            file=str(fpath),
            progress_callback=progress_factory(inicio),
        )
        if ruta is None:
            fpath.unlink(missing_ok=True)
            print(f"{_clear_line()}{inicio}  {_err('✗')} no disponible")
            return {"status": "err", "size": 0}

        file_size = fpath.stat().st_size
        print(f"{_clear_line()}{inicio}  {_ok('✓')}  {format_size(file_size)}")
        return {"status": "ok", "size": file_size}

    except errors.FloodWaitError as e:
        espera = e.seconds
        print(f"{_clear_line()}{inicio}  {_warn('⏳')} FloodWait {espera}s...")
        await asyncio.sleep(espera)
        # Reintento único
        try:
            ruta = await client.download_media(
                msg,
                file=str(fpath),
                progress_callback=progress_factory(inicio),
            )
            if ruta:
                file_size = fpath.stat().st_size
                print(f"{_clear_line()}{inicio}  {_ok('✓')}  {format_size(file_size)}")
                return {"status": "ok", "size": file_size}
            fpath.unlink(missing_ok=True)
            print(f"{_clear_line()}{inicio}  {_err('✗')} no disponible")
            return {"status": "err", "size": 0}
        except Exception as e2:
            print(f"{_clear_line()}{inicio}  {_err('✗')} {e2}")
            return {"status": "err", "size": 0}

    except Exception as e:
        print(f"{_clear_line()}{inicio}  {_err('✗')} {e}")
        return {"status": "err", "size": 0}


def _print_batch_summary(batch_num: int, ok: int, dup: int, err: int, skip: int, byte: int):
    """Resumen de un lote después de procesarlo."""
    print(f"\n  ── Lote {batch_num} ──────────────────")
    print(f"     Descargados: {ok}")
    if skip:
        print(f"     Omitidos:    {skip}")
    if dup:
        print(f"     Ya tenías:   {dup}")
    if err:
        print(f"  {_err('Errores:')}     {err}")
    if byte:
        print(f"     Tamaño:      {format_size(byte)}")


def _print_final_summary(total: dict, output_dir: Path):
    """Resumen global de toda la sesión."""
    print(f"\n  {_head('═' * 46)}")
    print(f"  {_head('DESCARGA FINALIZADA')}")
    print(f"  Archivos descargados: {_ok(str(total['ok']))}")
    if total["skip"]:
        print(f"  {_warn('Omitidos (pesados):')}  {total['skip']}")
    if total["dup"]:
        print(f"  Ya existían:          {total['dup']}")
    if total["err"]:
        print(f"  {_err('Errores:')}              {total['err']}")
    if total["bytes"]:
        print(f"  Tamaño total:         {format_size(total['bytes'])}")
    print(f"  Guardado en:          {output_dir}/")
    print(f"  {_head('═' * 46)}\n")


def _update_catalog(
    catalog: dict,
    chat_key: str,
    min_id: int,
    max_id: int,
    total_new: int,
):
    """Actualiza el catálogo de sesión con los IDs procesados.

    Si es la primera sesión (no existía el chat en el catálogo),
    lo crea desde cero.
    """
    if max_id <= 0:
        return
    cat = catalog.setdefault("chats", {}).setdefault(chat_key, {})
    cat["newest_id"] = max(cat.get("newest_id", 0), max_id)
    cat["oldest_id"] = min(cat.get("oldest_id", float("inf")), min_id)
    if cat["oldest_id"] == float("inf"):
        cat["oldest_id"] = min_id
    cat["last_date"] = datetime.now().strftime("%Y-%m-%d")
    cat["total_count"] = cat.get("total_count", 0) + total_new
    _save_catalog(catalog)


async def run(config: dict, settings: dict):
    """Ciclo principal de descarga por lotes adaptativos."""
    client = TelegramClient(
        config["SESSION_NAME"],
        config["TELEGRAM_API_ID"],
        config["TELEGRAM_API_HASH"],
    )

    await client.start()
    print(f"  {_ok('✓')} Conectado a Telegram.\n")

    _show_config_banner(settings)

    entity = await _resolve_entity(client, config["TELEGRAM_TARGET_CHAT"])
    if entity is None:
        print(f"  ERROR: No se pudo resolver el chat {config['TELEGRAM_TARGET_CHAT']}.")
        print("  Consejos:")
        print("    - Si es un grupo/canal público, usá el username (sin @) en TELEGRAM_TARGET_CHAT")
        print("    - Si es privado, usá el invite link completo (t.me/joinchat/...)")
        print("    - La sesión tiene que ser miembro del chat")
        await client.disconnect()
        return

    output_dir, chat_key = await _setup_output_dir(client, entity, config)

    # ── Contar multimedia disponible ──
    fotos, videos = await _count_media(client, entity)

    chat_folder = _chat_folder_name(entity)

    # ── Banner enriquecido ──
    print(f"\n  {'═' * 46}")
    print(f"  Chat:         {chat_folder}")
    print(f"  Contenido:    {_fmt_count(fotos)} fotos · {_fmt_count(videos)} videos")
    if fotos is not None and videos is not None:
        total_media = fotos + videos
        print(f"  Total:        {total_media:,} archivos multimedia")
    print(f"  Lote de:      {config['BATCH_SIZE']} archivos")
    print(f"  Guardando en: {output_dir}/\n")

    # ── Catálogo de sesiones anteriores (reanudar) ──
    catalog = _load_catalog()
    resume_newest, resume_oldest, resume_complete = _handle_catalog_resume(catalog, chat_key)

    if resume_newest is None and not ask_bool("  ¿Empezamos? (s/n/q): "):
        print(f"  {_warn('⚐')} Omitido por el usuario.\n")
        await client.disconnect()
        return

    # ── Estado ──
    total_ok = total_dup = total_err = total_bytes = total_skip = 0
    batch_num = 0
    offset_id = 0
    since = config.get("_since")
    until = config.get("_until")
    seguir = True
    session_min_id = float("inf")
    session_max_id = 0

    try:
        while seguir:
            batch_num += 1
            batch_ok = batch_dup = batch_err = batch_bytes = batch_skip = 0
            media_en_batch = 0  # multimedia procesados en este lote
            llegue_al_inicio = False

            print(f"\n  {'─' * 46}")
            print(f"  Lote {batch_num} — juntando {config['BATCH_SIZE']} archivos multimedia...")

            # ── Sub-lotes adaptativos ──
            while media_en_batch < config["BATCH_SIZE"]:
                faltan = config["BATCH_SIZE"] - media_en_batch
                pedir = min(100, faltan)

                if media_en_batch > 0:
                    print(
                        f"  → Acumulando: {media_en_batch}/{config['BATCH_SIZE']}, "
                        f"faltan {faltan}, pidiendo {pedir} más…"
                    )

                kwargs = dict(limit=pedir)
                if offset_id:
                    kwargs["offset_id"] = offset_id
                if until:
                    kwargs["offset_date"] = until

                try:
                    mensajes = await client.get_messages(entity, **kwargs)
                except errors.FloodWaitError as e:
                    espera = e.seconds
                    print(
                        f"  {_warn('⚠')} Límite de requests. Esperar {espera}s ({espera / 60:.1f} min)..."
                    )
                    await asyncio.sleep(espera)
                    continue
                except Exception as e:
                    print(f"  {_err('✗')} Error al obtener mensajes: {e}")
                    seguir = False
                    break

                if not mensajes:
                    break

                # ── Filtrar multimedia ──
                pendientes = []
                for m in mensajes:
                    if since and m.date.replace(tzinfo=timezone.utc) < since:
                        llegue_al_inicio = True
                        break
                    if is_media_wanted(m):
                        pendientes.append(m)

                if not pendientes:
                    offset_id = mensajes[-1].id
                    if llegue_al_inicio:
                        break
                    continue

                # ── Resume: filtrar mensajes ya procesados ──
                if resume_newest is not None:
                    _before = len(pendientes)
                    pendientes = [
                        m for m in pendientes if not (resume_oldest <= m.id <= resume_newest)
                    ]
                    if not pendientes and _before > 0:
                        if resume_complete:
                            offset_id = resume_oldest
                            resume_newest = None
                            continue
                        else:
                            seguir = False
                            break
                    if pendientes and pendientes[-1].id < resume_oldest:
                        resume_newest = None

                # ── Descargar cada uno usando _download_one() ──
                for msg in pendientes:
                    session_min_id = min(session_min_id, msg.id)
                    session_max_id = max(session_max_id, msg.id)

                    r = await _download_one(
                        client,
                        msg,
                        output_dir,
                        settings,
                        media_en_batch + 1,
                        config["BATCH_SIZE"],
                    )
                    if r["status"] == "ok":
                        batch_ok += 1
                        batch_bytes += r["size"]
                    elif r["status"] == "dup":
                        batch_dup += 1
                    elif r["status"] == "skip":
                        batch_skip += 1
                    elif r["status"] == "err":
                        batch_err += 1
                    media_en_batch += 1

                # Preparar siguiente sub-lote
                offset_id = mensajes[-1].id
                if llegue_al_inicio:
                    break

            # ── Resumen del lote ──
            total_ok += batch_ok
            total_dup += batch_dup
            total_err += batch_err
            total_skip += batch_skip
            total_bytes += batch_bytes

            _print_batch_summary(batch_num, batch_ok, batch_dup, batch_err, batch_skip, batch_bytes)

            # ── Decidir si seguimos ──
            if not seguir:
                break

            if llegue_al_inicio:
                print(f"\n  {_ok('✓')} Se alcanzó la fecha de inicio (no hay mensajes más viejos).")
                break

            if not mensajes:
                if media_en_batch == 0:
                    print(f"  {_ok('✓')} No hay más mensajes.")
                else:
                    print(f"  {_ok('✓')} Solo quedaban {media_en_batch} archivos multimedia.")
                break

            # ── Auto-skip si no se descargó nada nuevo ──
            if (
                settings.get("auto_skip_all_dupes")
                and batch_ok == 0
                and batch_err == 0
                and (batch_dup > 0 or batch_skip > 0)
            ):
                print(f"     ({_warn('sin novedades')}, paso al siguiente automáticamente)")
                continue

            # ── Auto-continue (modo silencioso) ──
            if settings.get("auto_continue"):
                continue

            seguir = ask_continue(total_ok)

    except KeyboardInterrupt:
        print(f"\n  {_warn('⚑')} Interrumpido.")
    finally:
        await client.disconnect()

    # ── Resumen final (solo si hubo actividad) ──
    total = {
        "ok": total_ok,
        "skip": total_skip,
        "dup": total_dup,
        "err": total_err,
        "bytes": total_bytes,
    }
    if any(v for v in total.values()):
        _print_final_summary(total, output_dir)
        _update_catalog(catalog, chat_key, session_min_id, session_max_id, sum(total.values()))
        print(f"  {_ok('✓')} Catálogo actualizado — próximas ejecuciones podrán reanudar.")
        print(f"  {_head('═' * 46)}\n")


# ===========================================================================
# Entry point
# ===========================================================================


def main():
    _load_dotenv()  # Cargar .env automáticamente, sin dependencias externas
    config = load_config()
    settings = _load_settings()

    print()
    print(f"  {_head('╔' + '═' * 46 + '╗')}")
    print(f"  {_head('║    Descargador Masivo de Telegram         ║')}")
    print(f"  {_head('╚' + '═' * 46 + '╝')}")
    print()

    os.makedirs(config["OUTPUT_DIR"], exist_ok=True)

    since, until = ask_date_filter()
    if since:
        config["_since"] = since
        print(f"    └ Desde: {since.date()}")
    if until:
        config["_until"] = until
        print(f"    └ Hasta: {until.date()}")
    print()

    try:
        asyncio.run(run(config, settings))
    except KeyboardInterrupt:
        print(f"\n  {_warn('⚑')} Interrumpido.")


if __name__ == "__main__":
    main()
