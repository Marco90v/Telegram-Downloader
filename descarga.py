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
import sys
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, errors


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


def progress_factory(prefix: str):
    """Devuelve un callback de progreso que actualiza una línea con el prefijo dado.

    Uso: progress_factory('📷 [  3/80 ] nombre.jpg')(current, total)
    """
    def cb(current: int, total: int):
        if total <= 0:
            return
        pct = current / total * 100
        blen = 25
        fill = int(blen * current / total)
        bar = "█" * fill + "░" * (blen - fill)
        print(f"\r{prefix} │{bar}│ {pct:3.0f}%", end="", flush=True)
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


# ===========================================================================
# Lógica de descarga
# ===========================================================================

async def run(config: dict):
    """Ciclo principal de descarga por lotes."""
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
    output_dir = Path(config["OUTPUT_DIR"]) / chat_folder
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Chat:         {chat_folder}")
    print(f"  Guardando en: {output_dir}/\n")

    # Estadísticas
    total_ok = 0
    total_dup = 0
    total_err = 0
    total_bytes = 0
    batch_num = 0
    offset_id = 0
    since = config.get("_since")
    until = config.get("_until")
    seguir = True

    try:
        while seguir:
            batch_num += 1
            batch_ok = 0
            batch_dup = 0
            batch_err = 0
            batch_bytes = 0

            print(f"  {'─' * 46}")
            print(f"  Lote {batch_num} — pidiendo {config['BATCH_SIZE']} mensajes...")

            # ---- Obtener lote ----
            kwargs = dict(limit=config["BATCH_SIZE"])
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
                batch_num -= 1  # no contar como lote
                continue
            except Exception as e:
                print(f"  ✗ Error al obtener mensajes: {e}")
                break

            if not mensajes:
                print("  ✓ No hay más mensajes.")
                break

            # ---- Filtrar ----
            pendientes = []
            llegue_al_inicio = False
            for m in mensajes:
                if since and m.date.replace(tzinfo=timezone.utc) < since:
                    llegue_al_inicio = True
                    break
                if is_media_wanted(m):
                    pendientes.append(m)

            if not pendientes:
                print("  → Sin fotos ni videos en este lote.")
            else:
                print(f"  → {len(pendientes)} archivo(s) para descargar.\n")

            # ---- Descargar ----
            n = len(pendientes)
            w = len(str(n))  # padding para el contador

            for i, m in enumerate(pendientes, start=1):
                fpath = _media_path(m, output_dir)

                # ── Saltear duplicados ──
                if fpath.exists():
                    batch_dup += 1
                    print(f"  ⏭ [{i:>{w}}/{n}] {fpath.name}  (ya existe)")
                    continue

                icono = "📷" if m.photo else "🎬"
                # Mostrar inicio sin barra (la pone el callback)
                inicio = f"  {icono} [{i:>{w}}/{n}] {fpath.name}"
                sys.stdout.write(inicio)
                sys.stdout.flush()

                try:
                    ruta = await client.download_media(
                        m,
                        file=str(fpath),
                        progress_callback=progress_factory(inicio),
                    )

                    if ruta is None:
                        # Limpiar posible archivo parcial
                        fpath.unlink(missing_ok=True)
                        print(f"\r{inicio}  ✗ no disponible{' ' * 30}")
                        batch_err += 1
                        continue

                    try:
                        batch_bytes += fpath.stat().st_size
                    except OSError:
                        pass

                    batch_ok += 1
                    print(f"\r{inicio}  ✓{' ' * 30}")

                except errors.FloodWaitError as e:
                    espera = e.seconds
                    print(f"\r{inicio}  ⏳ FloodWait {espera}s...")
                    await asyncio.sleep(espera)
                    # Reintento único
                    try:
                        ruta = await client.download_media(
                            m, file=str(fpath),
                            progress_callback=progress_factory(inicio),
                        )
                        if ruta:
                            batch_ok += 1
                            print(f"\r{inicio}  ✓{' ' * 30}")
                        else:
                            fpath.unlink(missing_ok=True)
                            print(f"\r{inicio}  ✗ no disponible{' ' * 30}")
                            batch_err += 1
                    except Exception as e2:
                        print(f"\r{inicio}  ✗ {e2}{' ' * 30}")
                        batch_err += 1

                except Exception as e:
                    print(f"\r{inicio}  ✗ {e}{' ' * 30}")
                    batch_err += 1

            # ---- Resumen del lote ----
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

            # ---- Condiciones de salida ----
            if llegue_al_inicio:
                print("\n  ✓ Se alcanzó la fecha de inicio (no hay mensajes más viejos).")
                break

            # Preparar siguiente lote
            offset_id = mensajes[-1].id

            if pendientes:
                seguir = ask_continue(total_ok)

    except KeyboardInterrupt:
        print("\n  ⚑  Interrumpido.")
    finally:
        await client.disconnect()

    # ---- Resumen final ----
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
    print(f"  Chat destino:  {config['TELEGRAM_TARGET_CHAT']}")
    print(f"  Carpeta base:  {config['OUTPUT_DIR']}/")
    print(f"  Lote de:       {config['BATCH_SIZE']} mensajes")
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
