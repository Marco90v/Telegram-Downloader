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
        if r in ("n", "no", "not"):
            return False
        print("  Respondé 's' para sí o 'n' para no.")


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
    return ask_bool("  ¿Seguir con los siguientes mensajes? (s/n): ")


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


def progress_callback(current: int, total: int):
    """Callback de progreso para download_media."""
    if total <= 0:
        return
    percent = current / total * 100
    bar_len = 30
    filled = int(bar_len * current // total)
    bar = "█" * filled + "░" * (bar_len - filled)
    cur_s = format_size(current).rjust(10)
    tot_s = format_size(total).ljust(10)
    print(f"\r  │{bar}│ {cur_s} / {tot_s} ({percent:3.0f}%)", end="", flush=True)


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

    # Estadísticas
    total_ok = 0
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
            for m in pendientes:
                try:
                    ruta = await client.download_media(
                        m,
                        file=config["OUTPUT_DIR"],
                        progress_callback=progress_callback,
                    )
                    print()  # salir de la línea de progreso

                    if ruta is None:
                        print(f"  ⚠  Mensaje {m.id}: download devolvió None (archivo no disponible)")
                        batch_err += 1
                        continue

                    try:
                        batch_bytes += Path(ruta).stat().st_size
                    except OSError:
                        pass

                    batch_ok += 1
                    icono = "📷" if m.photo else "🎬"
                    print(f"  {icono} {m.id:>8} → {Path(ruta).name}")

                except errors.FloodWaitError as e:
                    espera = e.seconds
                    print(f"\n  ⚠  Límite de requests. Esperar {espera}s...")
                    await asyncio.sleep(espera)
                    # Reintento único
                    try:
                        ruta = await client.download_media(m, file=config["OUTPUT_DIR"])
                        print()
                        if ruta:
                            batch_ok += 1
                            icono = "📷" if m.photo else "🎬"
                            print(f"  {icono} {m.id:>8} → {Path(ruta).name}")
                        else:
                            batch_err += 1
                    except Exception as e2:
                        print(f"  ✗ Mensaje {m.id}: error tras espera: {e2}")
                        batch_err += 1

                except Exception as e:
                    print(f"\n  ✗ Mensaje {m.id}: {e}")
                    batch_err += 1

            # ---- Resumen del lote ----
            total_ok += batch_ok
            total_err += batch_err
            total_bytes += batch_bytes

            print(f"\n  ── Lote {batch_num} ──────────────────")
            print(f"     Descargados: {batch_ok}")
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
        print("\n\n  ⚑  Interrumpido por el usuario.")
    finally:
        await client.disconnect()

    # ---- Resumen final ----
    print(f"\n  {'═' * 46}")
    print(f"  DESCARGA FINALIZADA")
    print(f"  Archivos descargados: {total_ok}")
    if total_err:
        print(f"  Errores:              {total_err}")
    if total_bytes:
        print(f"  Tamaño total:         {format_size(total_bytes)}")
    print(f"  Guardado en:          {config['OUTPUT_DIR']}")
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
    print(f"  Directorio:    {config['OUTPUT_DIR']}")
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

    asyncio.run(run(config))


if __name__ == "__main__":
    main()
