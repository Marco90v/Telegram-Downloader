#!/usr/bin/env python3
"""
Descarga masiva de contenido multimedia (fotos y videos) de Telegram.

Procesa por lotes y pregunta al usuario si desea continuar después de cada uno.
Las credenciales van en .env (ver .env.example).

Uso:
    python descarga.py
"""

import asyncio
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, errors
from telethon.tl.functions.messages import SearchRequest
from telethon.tl.types import InputMessagesFilterPhotos, InputMessagesFilterVideo


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
    print(f"\n  ─────────────────────────────────────────────")
    print(f"  Descargados hasta ahora: {total_downloaded} archivos")
    return ask_bool("  ¿Seguir? (s/n/q): ")


# ===========================================================================
# Utilidades
# ===========================================================================

def format_size(bytes_: int) -> str:
    """Formatea bytes a unidad legible."""
    if bytes_ >= 1024 ** 3:
        return f"{bytes_ / 1024 ** 3:.2f} GB"
    if bytes_ >= 1024 ** 2:
        return f"{bytes_ / 1024 ** 2:.1f} MB"
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
    if msg.photo and getattr(msg.photo, 'ext', None):
        return str(msg.photo.ext)
    if msg.video and getattr(msg.video, 'ext', None):
        return str(msg.video.ext)
    # Fallback por mime type
    doc = getattr(msg, 'document', None)
    if doc and doc.mime_type:
        mime_map = {
            'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
            'video/mp4': '.mp4', 'video/x-matroska': '.mkv', 'video/avi': '.avi',
        }
        return mime_map.get(doc.mime_type, '.bin')
    return '.jpg'


def _media_path(msg, output_dir: Path) -> Path:
    """Path único y determinístico para el multimedia de un mensaje.

    El nombre incluye fecha + message_id para evitar duplicados y
    ordenar cronológicamente.
    """
    fecha = msg.date.strftime('%Y%m%d') if msg.date else '00000000'
    return output_dir / f"{fecha}_{msg.id}{_media_ext(msg)}"


def _chat_folder_name(entity) -> str:
    """Deriva un nombre de carpeta legible y seguro desde la entidad del chat."""
    name = None
    if hasattr(entity, 'title') and entity.title:
        name = entity.title
    elif hasattr(entity, 'username') and entity.username:
        name = entity.username
    else:
        name = f"chat_{entity.id}" if hasattr(entity, 'id') else "desconocido"

    # Solo caracteres seguros para sistema de archivos
    safe = "".join(c if c.isalnum() or c in ' _-.' else '_' for c in name)
    return safe.strip().strip('.')[:60] or "telegram_chat"


def is_media_wanted(msg) -> bool:
    """Fotos y videos, nada más."""
    return bool(msg.photo) or bool(msg.video)


def _fmt_count(n: int | None) -> str:
    """Formatea un contador o '?' si es None."""
    if n is None:
        return "?"
    return f"{n:,}"


async def _count_media(client, entity):
    """Cuenta fotos y videos via SearchRequest server-side (sin descargar nada)."""
    try:
        fotos = await client(SearchRequest(
            peer=entity, q='', filter=InputMessagesFilterPhotos(), limit=1,
        ))
        videos = await client(SearchRequest(
            peer=entity, q='', filter=InputMessagesFilterVideo(), limit=1,
        ))
        # SearchRequest devuelve .count en la mayoría de los casos;
        # .total es alternativa para get_messages.
        total_fotos = getattr(fotos, 'count', None)
        if total_fotos is None:
            total_fotos = getattr(fotos, 'total', 0) or 0
        total_videos = getattr(videos, 'count', None)
        if total_videos is None:
            total_videos = getattr(videos, 'total', 0) or 0
        return total_fotos, total_videos
    except Exception:
        return None, None


# ===========================================================================
# Lógica de descarga
# ===========================================================================

async def run(config: dict):
    """Ciclo principal de descarga por lotes adaptativos."""
    client = TelegramClient(
        config["SESSION_NAME"],
        config["TELEGRAM_API_ID"],
        config["TELEGRAM_API_HASH"],
    )

    await client.start()
    print("  ✓ Conectado a Telegram.\n")

    chat_id = config["TELEGRAM_TARGET_CHAT"]

    # Intentar resolver el chat. Si falla, probar con prefijo -100
    # (canales/supergrupos necesitan -100 delante del ID numérico).
    try:
        entity = await client.get_entity(chat_id)
    except Exception:
        if isinstance(chat_id, int) and chat_id < 0 and not str(chat_id).startswith("-100"):
            try:
                entity = await client.get_entity(int(f"-100{abs(chat_id)}"))
            except Exception:
                entity = None
        else:
            entity = None

    if entity is None:
        print(f"  ERROR: No se pudo resolver el chat {chat_id}.")
        print("  Consejos:")
        print("    - Si es un grupo/canal público, usá el username (sin @) en TELEGRAM_TARGET_CHAT")
        print("    - Si es privado, usá el invite link completo (t.me/joinchat/...)")
        print("    - La sesión tiene que ser miembro del chat")
        await client.disconnect()
        return

    # ── Subcarpeta por chat ──
    chat_folder = _chat_folder_name(entity)

    # Detectar si es un sub-grupo vinculado a un canal (discussion group)
    parent_folder = None
    linked_id = getattr(entity, 'linked_chat_id', None)
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

    # ── Contar multimedia disponible ──
    fotos, videos = await _count_media(client, entity)

    # ── Banner enriquecido ──
    print(f"\n  {'═' * 46}")
    if parent_folder and parent_folder != chat_folder:
        print(f"  Chat:         {parent_folder} / {chat_folder}")
    else:
        print(f"  Chat:         {chat_folder}")
    print(f"  Contenido:    {_fmt_count(fotos)} fotos · {_fmt_count(videos)} videos")
    if fotos is not None and videos is not None:
        total_media = fotos + videos
        print(f"  Total:        {total_media:,} archivos multimedia")
    print(f"  Lote de:      {config['BATCH_SIZE']} archivos")
    print(f"  Guardando en: {output_dir}/\n")

    if not ask_bool("  ¿Empezamos? (s/n/q): "):
        print("  ⚐  Omitido por el usuario.\n")
        await client.disconnect()
        return

    # ── Estado ──
    total_ok = total_dup = total_err = total_bytes = 0
    batch_num = 0
    offset_id = 0
    since = config.get("_since")
    until = config.get("_until")
    seguir = True

    try:
        while seguir:
            batch_num += 1
            batch_ok = batch_dup = batch_err = batch_bytes = 0
            media_en_batch = 0          # multimedia procesados en este lote
            llegue_al_inicio = False

            print(f"\n  {'─' * 46}")
            print(f"  Lote {batch_num} — juntando {config['BATCH_SIZE']} archivos multimedia...")

            # ── Sub-lotes adaptativos ──
            while media_en_batch < config['BATCH_SIZE']:
                faltan = config['BATCH_SIZE'] - media_en_batch
                pedir = min(100, faltan)

                if media_en_batch > 0:
                    print(f"  → Acumulando: {media_en_batch}/{config['BATCH_SIZE']}, "
                          f"faltan {faltan}, pidiendo {pedir} más…")

                kwargs = dict(limit=pedir)
                if offset_id:
                    kwargs["offset_id"] = offset_id
                if until:
                    kwargs["offset_date"] = until

                try:
                    mensajes = await client.get_messages(entity, **kwargs)
                except errors.FloodWaitError as e:
                    espera = e.seconds
                    print(f"  ⚠  Límite de requests. Esperar {espera}s ({espera / 60:.1f} min)...")
                    await asyncio.sleep(espera)
                    continue
                except Exception as e:
                    print(f"  ✗ Error al obtener mensajes: {e}")
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

                # ── Descargar cada uno (contador global en el lote) ──
                w = len(str(config['BATCH_SIZE']))

                for msg in pendientes:
                    fpath = _media_path(msg, output_dir)
                    pos = media_en_batch + 1   # posición global en el lote

                    # ── Duplicado ──
                    if fpath.exists():
                        batch_dup += 1
                        media_en_batch += 1
                        try:
                            dup_size = format_size(fpath.stat().st_size)
                            print(f"  ⏭ [{pos:>{w}}/{config['BATCH_SIZE']}] {fpath.name}  ({dup_size})  (ya existe)")
                        except OSError:
                            print(f"  ⏭ [{pos:>{w}}/{config['BATCH_SIZE']}] {fpath.name}  (ya existe)")
                        continue

                    icono = "📷" if msg.photo else "🎬"
                    inicio = f"  {icono} [{pos:>{w}}/{config['BATCH_SIZE']}] {fpath.name}"
                    sys.stdout.write(inicio)
                    sys.stdout.flush()

                    try:
                        ruta = await client.download_media(
                            msg, file=str(fpath),
                            progress_callback=progress_factory(inicio),
                        )

                        if ruta is None:
                            fpath.unlink(missing_ok=True)
                            print(f"{_clear_line()}{inicio}  ✗ no disponible")
                            batch_err += 1
                            media_en_batch += 1
                            continue

                        try:
                            file_size = fpath.stat().st_size
                            batch_bytes += file_size
                            size_str = format_size(file_size)
                        except OSError:
                            size_str = ""

                        batch_ok += 1
                        media_en_batch += 1
                        if size_str:
                            print(f"{_clear_line()}{inicio}  ✓  {size_str}")
                        else:
                            print(f"{_clear_line()}{inicio}  ✓")

                    except errors.FloodWaitError as e:
                        espera = e.seconds
                        print(f"{_clear_line()}{inicio}  ⏳ FloodWait {espera}s...")
                        await asyncio.sleep(espera)
                        # Reintento único
                        try:
                            ruta = await client.download_media(
                                msg, file=str(fpath),
                                progress_callback=progress_factory(inicio),
                            )
                            if ruta:
                                try:
                                    file_size = fpath.stat().st_size
                                    batch_bytes += file_size
                                    size_str = format_size(file_size)
                                except OSError:
                                    size_str = ""
                                batch_ok += 1
                                if size_str:
                                    print(f"{_clear_line()}{inicio}  ✓  {size_str}")
                                else:
                                    print(f"{_clear_line()}{inicio}  ✓")
                            else:
                                fpath.unlink(missing_ok=True)
                                print(f"{_clear_line()}{inicio}  ✗ no disponible")
                                batch_err += 1
                            media_en_batch += 1
                        except Exception as e2:
                            print(f"{_clear_line()}{inicio}  ✗ {e2}")
                            batch_err += 1
                            media_en_batch += 1

                    except Exception as e:
                        print(f"{_clear_line()}{inicio}  ✗ {e}")
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
            total_bytes += batch_bytes

            print(f"\n  ── Lote {batch_num} ──────────────────")
            print(f"     Descargados: {batch_ok}")
            if batch_dup:
                print(f"     Ya tenías:   {batch_dup}")
            if batch_err:
                print(f"     Errores:     {batch_err}")
            if batch_bytes:
                print(f"     Tamaño:      {format_size(batch_bytes)}")

            # ── Decidir si seguimos ──
            if not seguir:
                break

            if llegue_al_inicio:
                print("\n  ✓ Se alcanzó la fecha de inicio (no hay mensajes más viejos).")
                break

            if not mensajes:
                if media_en_batch == 0:
                    print("  ✓ No hay más mensajes.")
                else:
                    print(f"  ✓ Solo quedaban {media_en_batch} archivos multimedia.")
                break

            seguir = ask_continue(total_ok)

    except KeyboardInterrupt:
        print("\n  ⚑  Interrumpido.")
    finally:
        await client.disconnect()

    # ── Resumen final (solo si hubo actividad) ──
    if total_ok or total_dup or total_err:
        print(f"\n  {'═' * 46}")
        print(f"  DESCARGA FINALIZADA")
        print(f"  Archivos descargados: {total_ok}")
        if total_dup:
            print(f"  Ya existían:          {total_dup}")
        if total_err:
            print(f"  Errores:              {total_err}")
        if total_bytes:
            print(f"  Tamaño total:         {format_size(total_bytes)}")
        print(f"  Guardado en:          {output_dir}/")
        print(f"  {'═' * 46}\n")


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    _load_dotenv()  # Cargar .env automáticamente, sin dependencias externas
    config = load_config()

    print()
    print("  ╔═══════════════════════════════════════════╗")
    print("  ║    Descargador Masivo de Telegram         ║")
    print("  ╚═══════════════════════════════════════════╝")
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
        asyncio.run(run(config))
    except KeyboardInterrupt:
        print("\n  ⚑  Interrumpido.")


if __name__ == "__main__":
    main()
