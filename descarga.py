#!/usr/bin/env python3
"""
CLI para descarga masiva de contenido multimedia de Telegram.

Usa core.py como motor compartido. Esta interfaz solo maneja:
  - Output ANSI (colores, barras de progreso)
  - Prompts al usuario (input)
  - Orquestación del flujo CLI

Uso:
    python descarga.py
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path

from core import (
    DownloadEngine,
    download_one,
    fmt_count,
    format_size,
    load_config,
    load_dotenv,
    load_settings,
    media_path,
    media_size,
)

# ===========================================================================
# Colores ANSI
# ===========================================================================


class _c:
    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YEL = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    MAG = "\033[35m"


def _ok(t: str) -> str:
    return f"{_c.GREEN}{t}{_c.RST}"


def _warn(t: str) -> str:
    return f"{_c.YEL}{t}{_c.RST}"


def _err(t: str) -> str:
    return f"{_c.RED}{t}{_c.RST}"


def _head(t: str) -> str:
    return f"{_c.CYAN}{_c.BOLD}{t}{_c.RST}"


def _clear_line() -> str:
    """Prefijo que borra toda la línea actual de la terminal."""
    cols = shutil.get_terminal_size().columns
    return f"\r{' ' * cols}\r"


# ===========================================================================
# Prompts CLI
# ===========================================================================


def ask_bool(prompt: str) -> bool:
    """Pregunta sí/no. Bucle hasta respuesta válida."""
    while True:
        r = input(prompt).strip().lower()
        if r in ("s", "si", "sí", "y", "yes"):
            return True
        if r in ("n", "no", "not", "q"):
            return False
        print("  Respondé 's' para sí, 'n' para no, 'q' para salir.")


def ask_date_filter():
    """Pregunta si filtrar por rango de fechas. Devuelve (since, until) o (None, None)."""
    if not ask_bool("\n  ¿Filtrar por rango de fechas? (s/n): "):
        return None, None

    since = until = None

    raw = input("  Fecha inicio (YYYY-MM-DD, Enter para omitir): ").strip()
    if raw:
        try:
            from datetime import datetime, timezone

            since = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print("    └ Formato inválido, se ignora.")

    raw = input("  Fecha fin    (YYYY-MM-DD, Enter para omitir): ").strip()
    if raw:
        try:
            from datetime import datetime, timezone

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
# Progreso CLI
# ===========================================================================


def progress_factory(prefix: str, show_size: bool = True):
    """Devuelve callback de progreso que actualiza una línea con barra ANSI."""

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


# ===========================================================================
# Output de configuración
# ===========================================================================


def _show_config_banner(settings: dict):
    """Muestra la configuración activa."""
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


# ===========================================================================
# Reanudación (interacción con usuario)
# ===========================================================================


def _handle_catalog_resume(catalog: dict, chat_key: str) -> tuple:
    """Menú de reanudar si hay sesión anterior. Retorna (newest, oldest, complete)."""
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


# ===========================================================================
# Resúmenes
# ===========================================================================


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


def _print_final_summary(engine: DownloadEngine):
    """Resumen global de toda la sesión."""
    t = engine.totals
    print(f"\n  {_head('═' * 46)}")
    print(f"  {_head('DESCARGA FINALIZADA')}")
    print(f"  Archivos descargados: {_ok(str(t['ok']))}")
    if t["skip"]:
        print(f"  {_warn('Omitidos (pesados):')}  {t['skip']}")
    if t["dup"]:
        print(f"  Ya existían:          {t['dup']}")
    if t["err"]:
        print(f"  {_err('Errores:')}              {t['err']}")
    if t["bytes"]:
        print(f"  Tamaño total:         {format_size(t['bytes'])}")
    print(f"  Guardado en:          {engine.output_dir}/")
    print(f"  {_head('═' * 46)}\n")


# ===========================================================================
# Settings con output CLI
# ===========================================================================

SETTINGS_PATH = Path(__file__).parent / "settings.json"

DEFAULT_SETTINGS = {
    "auto_skip_all_dupes": False,
    "auto_continue": False,
    "large_file_threshold_mb": 50,
    "large_file_action": "ask",
}


def _load_settings_with_output() -> dict:
    """Igual que core.load_settings() pero con mensajes CLI informativos."""
    settings = load_settings()

    if not SETTINGS_PATH.exists():
        # load_settings() ya creó el archivo; solo mostramos el mensaje
        print(f"  {_ok('✓')} Creado settings.json")
        print("    └ Editálo para cambiar el comportamiento sin tocar código.")
        print("    └ Opciones:")
        print("    └   large_file_action → ask | download | skip")
        print("    └   large_file_threshold_mb → número (MB)")
        print("    └   auto_skip_all_dupes → true | false")
        print("    └   auto_continue → true | false (modo silencioso total)")

    return settings


# ===========================================================================
# Ciclo principal de descarga (CLI)
# ===========================================================================


async def run(config: dict, settings: dict):
    """Ciclo principal: conecta, descarga por lotes, muestra progreso CLI."""
    engine = DownloadEngine(config, settings)

    try:
        await engine.connect()
        print(f"  {_ok('✓')} Conectado a Telegram.\n")

        _show_config_banner(settings)

        info = await engine.prepare()
        output_dir = info["output_dir"]
        chat_name = info["chat_name"]

        # ── Banner del chat ──
        fotos, videos = info["fotos"], info["videos"]
        print(f"\n  {'═' * 46}")
        print(f"  Chat:         {chat_name}")
        print(f"  Contenido:    {fmt_count(fotos)} fotos · {fmt_count(videos)} videos")
        if fotos is not None and videos is not None:
            print(f"  Total:        {fotos + videos:,} archivos multimedia")
        print(f"  Lote de:      {config['BATCH_SIZE']} archivos")
        print(f"  Guardando en: {output_dir}/\n")

        # ── Reanudación ──
        resume_newest, resume_oldest, resume_complete = _handle_catalog_resume(
            engine.catalog, engine.chat_key
        )

        if resume_newest is None and not ask_bool("  ¿Empezamos? (s/n/q): "):
            print(f"  {_warn('⚐')} Omitido por el usuario.\n")
            return

        # ── Variables de iteración ──
        batch_num = 0
        offset_id = 0
        since = config.get("_since")
        until = config.get("_until")
        seguir = True

        while seguir:
            batch_num += 1
            batch_ok = batch_dup = batch_err = batch_bytes = batch_skip = 0
            media_en_batch = 0

            print(f"\n  {'─' * 46}")
            print(f"  Lote {batch_num} — juntando {config['BATCH_SIZE']} archivos multimedia...")

            # ── Fetch batch via engine ──
            batch_result = await engine.fetch_batch(
                offset_id=offset_id,
                limit=config["BATCH_SIZE"],
                since=since,
                until=until,
                resume_newest=resume_newest,
                resume_oldest=resume_oldest,
                resume_complete=resume_complete,
            )

            if batch_result["error"]:
                print(f"  {_err('✗')} {batch_result['error']}")
                break

            media_messages = batch_result["media"]
            offset_id = batch_result["next_offset"]
            reached_start = batch_result.get("reached_start", False)
            should_stop = batch_result.get("should_stop", False)

            # ── Sin multimedia en este bloque ──
            if not media_messages:
                if reached_start:
                    print(f"\n  {_ok('✓')} Se alcanzó la fecha de inicio.")
                    break
                if should_stop:
                    print(f"\n  {_ok('✓')} No hay más mensajes.")
                    break
                # Sin multimedia pero puede haber más atrás
                continue

            # ── Descargar cada mensaje ──
            w = len(str(config["BATCH_SIZE"]))

            for msg in media_messages:
                engine.session_min_id = min(engine.session_min_id, msg.id)
                engine.session_max_id = max(engine.session_max_id, msg.id)
                pos = media_en_batch + 1

                icono = "📷" if msg.photo else "🎬"
                fpath = media_path(msg, output_dir)
                inicio = f"  {icono} [{pos:>{w}}/{config['BATCH_SIZE']}] {fpath.name}"

                # ── Large file ask (solo CLI) ──
                fsize = media_size(msg)
                thr = settings["large_file_threshold_mb"] * 1024 * 1024
                es_grande = fsize is not None and fsize > thr and thr > 0
                if es_grande and settings["large_file_action"] == "ask":
                    if not ask_bool(f"{inicio}  ({format_size(fsize)})  ¿Descargar? (s/n): "):
                        engine.add_result({"status": "skip", "size": 0})
                        batch_skip += 1
                        media_en_batch += 1
                        continue

                # ── Descargar ──
                result = await download_one(
                    engine.client,
                    msg,
                    output_dir,
                    settings,
                    progress_callback=progress_factory(inicio),
                )

                engine.add_result(result)

                # Mostrar resultado según status
                if result["status"] == "ok":
                    batch_ok += 1
                    batch_bytes += result["size"]
                    print(f"{_clear_line()}{inicio}  {_ok('✓')}  {format_size(result['size'])}")
                elif result["status"] == "dup":
                    try:
                        dup_size = format_size(Path(result["path"]).stat().st_size)
                        print(f"  {_warn('⏭')} {inicio}  ({dup_size})  (ya existe)")
                    except OSError:
                        print(f"  {_warn('⏭')} {inicio}  (ya existe)")
                    batch_dup += 1
                elif result["status"] == "skip":
                    print(
                        f"  {_warn('⏭')} {inicio}  {_warn(format_size(fsize))}  (omitido por tamaño)"
                    )
                    batch_skip += 1
                elif result["status"] == "err":
                    error_msg = result.get("error", "desconocido")
                    print(f"{_clear_line()}{inicio}  {_err('✗')} {error_msg}")
                    batch_err += 1

                media_en_batch += 1

            # ── Resumen del lote ──
            _print_batch_summary(batch_num, batch_ok, batch_dup, batch_err, batch_skip, batch_bytes)

            # ── Decidir si seguimos ──
            if reached_start:
                print(f"\n  {_ok('✓')} Se alcanzó la fecha de inicio (no hay mensajes más viejos).")
                break

            # ── Auto-skip si nada nuevo ──
            if (
                settings.get("auto_skip_all_dupes")
                and batch_ok == 0
                and batch_err == 0
                and (batch_dup > 0 or batch_skip > 0)
            ):
                print(f"     ({_warn('sin novedades')}, paso al siguiente automáticamente)")
                continue

            # ── Auto-continue ──
            if settings.get("auto_continue"):
                continue

            seguir = ask_continue(engine.total_ok)

    except KeyboardInterrupt:
        print(f"\n  {_warn('⚑')} Interrumpido.")
    finally:
        await engine.disconnect()

    # ── Resumen final ──
    if any(v for v in engine.totals.values()):
        _print_final_summary(engine)
        engine.finalize()
        print(f"  {_ok('✓')} Catálogo actualizado — próximas ejecuciones podrán reanudar.")
        print(f"  {_head('═' * 46)}\n")


# ===========================================================================
# Entry point
# ===========================================================================


def main():
    load_dotenv()
    try:
        config = load_config()
    except ValueError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    settings = _load_settings_with_output()

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
