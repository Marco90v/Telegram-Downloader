"""
Descarga individual de un archivo multimedia.

Expone:
  - download_one: descarga UN archivo con manejo de errores y retry en FloodWait.
"""

import asyncio

from telethon import TelegramClient, errors

from core.media import media_path, media_size

FileResult = dict  # {'status': str, 'size': int, 'path': str|None, 'error': str|None}


async def download_one(
    client: TelegramClient,
    msg,
    output_dir,
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
