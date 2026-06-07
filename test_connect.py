#!/usr/bin/env python3
"""Test mínimo: conecta a Telegram SIN usar la TUI.

Ejecutalo y fijate dónde se traba:
- ¿Se conecta?
- ¿Resuelve el chat?
- ¿Cuenta media?

Uso: python test_connect.py
"""

import asyncio
import sys
import time

from core import (
    DownloadEngine,
    load_config,
    load_dotenv,
    load_settings,
)

LOG = True


def log(msg):
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] {msg}", flush=True)


async def main():
    log("Cargando configuración...")
    load_dotenv()
    config = load_config()
    settings = load_settings()
    log(f"Chat: {config['TELEGRAM_TARGET_CHAT']}")
    log(f"Output: {config['OUTPUT_DIR']}")

    engine = DownloadEngine(config, settings)

    log("Conectando a Telegram...")
    t0 = time.time()
    try:
        await asyncio.wait_for(engine.connect(), timeout=15)
        dt = time.time() - t0
        log(f"✓ Conectado en {dt:.1f}s")
    except asyncio.TimeoutError:
        log("✗ TIMEOUT: connect() no respondió en 15s")
        sys.exit(1)
    except Exception as e:
        log(f"✗ Error en connect(): {e}")
        sys.exit(1)

    log("Resolviendo chat...")
    t0 = time.time()
    try:
        info = await asyncio.wait_for(engine.prepare(), timeout=30)
        dt = time.time() - t0
        log(f"✓ Chat resuelto en {dt:.1f}s")
        log(f"  Chat: {info['chat_name']}")
        log(f"  Fotos: {info['fotos']}, Videos: {info['videos']}")
    except asyncio.TimeoutError:
        log("✗ TIMEOUT: prepare() no respondió en 30s")
        sys.exit(1)
    except Exception as e:
        log(f"✗ Error en prepare(): {e}")
        sys.exit(1)

    log("Desconectando...")
    await engine.disconnect()
    log("✓ Listo")


if __name__ == "__main__":
    asyncio.run(main())
