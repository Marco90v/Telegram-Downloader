#!/usr/bin/env python3
"""
Interfaz TUI (Textual) para descarga masiva de contenido multimedia de Telegram.

Usa core.py como motor compartido.

Uso:
    python tui.py

Requiere: pip install textual>=8.0
"""

import asyncio
import os
import threading
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ProgressBar,
    RichLog,
    Static,
)

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

# ── helpers ANSI para el log (igual que en CLI) ──


class _c:
    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    MAG = "\033[35m"


def _ok(t: str) -> str:
    return f"{_c.GREEN}{t}{_c.RST}"


def _warn(t: str) -> str:
    return f"{_c.YELLOW}{t}{_c.RST}"


def _fail(t: str) -> str:
    return f"{_c.RED}{t}{_c.RST}"


def _head(t: str) -> str:
    return f"{_c.CYAN}{_c.BOLD}{t}{_c.RST}"


# ── App ──


class DownloadApp(App):
    TITLE = "Descargador Masivo de Telegram"
    SUB_TITLE = "TUI"

    CSS = """
    Screen { layout: vertical; }

    #status-bar {
        height: 1;
        padding: 0 1;
        background: $primary 20%;
    }

    #top-row {
        height: 1fr;
    }

    #left-panel {
        width: 2fr;
        border: solid $primary;
    }

    #right-panel {
        width: 1fr;
        border: solid $primary;
        margin-left: 1;
        padding: 1;
    }

    #progress-box {
        height: 5;
        border: solid $primary;
        padding: 0 1;
        margin: 0 1 1 1;
    }

    #log-box {
        height: 10;
        border: solid $primary;
        margin: 0 1 1 1;
    }

    #progress-bar { margin: 1 0; }

    #controls {
        height: 3;
        align: center middle;
        margin: 0 1;
    }

    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("p", "toggle_pause", "Pausa"),
        Binding("r", "toggle_dark", "Tema"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.paused = False
        self.stop_requested = False
        self._started = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("[bold]Inicializando...[/]", id="status-bar")

        with Horizontal(id="top-row"):
            with Container(id="left-panel"):
                yield Static("[bold]Progreso[/]", id="current-file")
                yield ProgressBar(total=100, id="progress-bar")
            with Container(id="right-panel"):
                yield Static("[bold]Estadísticas[/]\n\n  Esperando...", id="stats")

        yield RichLog(id="log", highlight=True, wrap=True, max_lines=10000)

        with Horizontal(id="controls"):
            yield Button("▶ Iniciar descarga", id="btn-start", variant="primary")
            yield Button("⏸ Pausar", id="btn-pause", disabled=True)
            yield Button("⏹ Salir", id="btn-quit", variant="error")

        yield Footer()

    # ── Eventos ──

    def on_mount(self) -> None:
        """Carga config y muestra botón de inicio."""
        self._set_status("[yellow]Cargando configuración...[/]")

        load_dotenv()

        try:
            self.config = load_config()
        except ValueError as e:
            self._log(_fail(f"ERROR: {e}"))
            self._set_status(f"[bold red]ERROR: {e}[/]")
            self.set_timer(3, lambda: self.exit(1))
            return

        self.settings = load_settings()
        os.makedirs(self.config["OUTPUT_DIR"], exist_ok=True)

        self._set_status("[green]Configuración cargada[/]")
        self._log(_ok("Configuración cargada"))
        self._log(f"  Chat: {self.config['TELEGRAM_TARGET_CHAT']}")
        self._log(f"  Destino: {self.config['OUTPUT_DIR']}")
        self._log(f"  Lote: {self.config['BATCH_SIZE']} archivos")
        self._log(f"  Umbral archivos grandes: {self.settings['large_file_threshold_mb']} MB\n")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Maneja clicks en botones."""
        if event.button.id == "btn-start":
            self._start_workflow()
        elif event.button.id == "btn-pause":
            self.action_toggle_pause()
        elif event.button.id == "btn-quit":
            self.action_quit()

    def _start_workflow(self) -> None:
        """Desactiva botón de inicio y arranca el worker en thread."""
        if self._started:
            return
        self._started = True
        self.query_one("#btn-start", Button).disabled = True
        self.query_one("#btn-pause", Button).disabled = False

        # Arrancar en thread separado (evita conflicto Textual/Telethon)
        self.thread = threading.Thread(target=self._thread_run, daemon=True)
        self.thread.start()

    def _thread_run(self) -> None:
        """Corre en thread separado: crea su propio event loop."""
        asyncio.run(self._async_run())

    async def _async_run(self) -> None:
        """Async real: conecta, prepara, descarga (event loop propio)."""
        engine = DownloadEngine(self.config, self.settings)
        self.call_from_thread(self._log, _ok("Motor inicializado"))

        # ── Conectar ──
        self.call_from_thread(self._set_status, "[yellow]Conectando a Telegram...[/]")
        try:
            await engine.connect()
        except Exception as e:
            self.call_from_thread(self._log, _fail(f"Error al conectar: {e}"))
            self.call_from_thread(self._set_status, f"[bold red]Error: {e}[/]")
            return
        self.call_from_thread(self._log, _ok("Conectado a Telegram"))

        # ── Resolver chat ──
        self.call_from_thread(self._set_status, "[yellow]Resolviendo chat...[/]")
        try:
            info = await engine.prepare()
        except Exception as e:
            self.call_from_thread(self._log, _fail(f"Error al preparar: {e}"))
            self.call_from_thread(self._set_status, f"[bold red]Error: {e}[/]")
            return
        chat_name = info["chat_name"]
        fotos, videos = info["fotos"], info["videos"]
        self.call_from_thread(self._log, _ok(f"Chat: {chat_name}"))
        if fotos is not None and videos is not None:
            self.call_from_thread(
                self._log,
                f"  {fmt_count(fotos)} fotos · {fmt_count(videos)} videos  ({fotos + videos:,} total)",
            )

        # ── Reanudación ──
        resume_newest, resume_oldest, resume_complete = None, None, False
        prev = engine.catalog.get("chats", {}).get(engine.chat_key)
        if prev:
            pc = prev.get("total_count", 0)
            pd = prev.get("last_date", "?")
            self.call_from_thread(self._log, _warn(f"⏭ Sesión anterior: {pc} archivos ({pd})"))
            resume_newest = prev.get("newest_id")
            resume_oldest = prev.get("oldest_id")
            resume_complete = True
            self.call_from_thread(self._log, "  → Reanudando (completo)")

        self.call_from_thread(self._log, "")

        # ── Ciclo de descarga ──
        offset_id = 0
        since = self.config.get("_since")
        until = self.config.get("_until")

        self.call_from_thread(self._set_status, "[green]Descargando...[/]")
        self.call_from_thread(
            lambda: self.query_one("#progress-bar", ProgressBar).update(total=100, progress=0)
        )

        while not self._is_stopped():
            # ── Pausa ──
            while self._is_paused() and not self._is_stopped():
                await asyncio.sleep(0.2)
            if self._is_stopped():
                break

            batch_ok = batch_dup = batch_err = batch_skip = batch_bytes = 0
            batch_count = 0
            self._inc_batch()

            self.call_from_thread(self._log, _head(f"── Lote {self._batch_num} ──"))

            # Fetch lote
            batch_result = await engine.fetch_batch(
                offset_id=offset_id,
                limit=self.config["BATCH_SIZE"],
                since=since,
                until=until,
                resume_newest=resume_newest,
                resume_oldest=resume_oldest,
                resume_complete=resume_complete,
            )

            if batch_result["error"]:
                self.call_from_thread(self._log, _fail(f"✗ {batch_result['error']}"))
                break

            media_messages = batch_result["media"]
            offset_id = batch_result["next_offset"]

            if not media_messages:
                self.call_from_thread(self._log, _ok("No hay más mensajes."))
                break

            w = len(str(self.config["BATCH_SIZE"]))

            for msg in media_messages:
                if self._is_stopped():
                    break
                while self._is_paused() and not self._is_stopped():
                    await asyncio.sleep(0.2)
                if self._is_stopped():
                    break

                engine.session_min_id = min(engine.session_min_id, msg.id)
                engine.session_max_id = max(engine.session_max_id, msg.id)
                pos = batch_count + 1

                icono = "📷" if msg.photo else "🎬"
                fpath = media_path(msg, engine.output_dir)
                file_name = fpath.name

                self.call_from_thread(
                    lambda fn=file_name, ic=icono: self.query_one("#current-file", Static).update(
                        f"[bold]{ic}  {fn}[/]"
                    )
                )

                # Archivo grande
                fsize = media_size(msg)
                thr = self.settings["large_file_threshold_mb"] * 1024 * 1024
                if fsize is not None and fsize > thr and thr > 0:
                    action = self.settings.get("large_file_action", "skip")
                    if action in ("skip", "ask"):
                        engine.add_result({"status": "skip", "size": 0})
                        self.call_from_thread(
                            self._log,
                            f"  {icono} [{pos:>{w}}/{self.config['BATCH_SIZE']}] "
                            f"[yellow]⏭[/] {file_name} ({format_size(fsize)}, omitido)",
                        )
                        batch_skip += 1
                        batch_count += 1
                        continue

                # Descargar
                self.call_from_thread(
                    self._log,
                    f"  {icono} [{pos:>{w}}/{self.config['BATCH_SIZE']}] [dim]{file_name}[/]",
                )

                result = await download_one(
                    engine.client,
                    msg,
                    engine.output_dir,
                    self.settings,
                    progress_callback=lambda c, t, fn=file_name: self.call_from_thread(
                        self._update_bar, c, t
                    ),
                )

                engine.add_result(result)

                if result["status"] == "ok":
                    self.call_from_thread(
                        self._log,
                        f"  {icono} [{pos:>{w}}/{self.config['BATCH_SIZE']}] "
                        f"[green]✓[/] {file_name} ({format_size(result['size'])})",
                    )
                    batch_ok += 1
                    batch_bytes += result["size"]
                elif result["status"] == "dup":
                    sz = ""
                    try:
                        sz = f"({format_size(Path(result['path']).stat().st_size)})"
                    except OSError:
                        pass
                    self.call_from_thread(
                        self._log,
                        f"  {icono} [{pos:>{w}}/{self.config['BATCH_SIZE']}] "
                        f"[yellow]⏭[/] {file_name} {sz} (ya existe)",
                    )
                    batch_dup += 1
                elif result["status"] == "err":
                    err_msg = result.get("error", "desconocido")
                    self.call_from_thread(
                        self._log,
                        f"  {icono} [{pos:>{w}}/{self.config['BATCH_SIZE']}] "
                        f"[red]✗[/] {file_name} — {err_msg}",
                    )
                    batch_err += 1

                batch_count += 1
                self.call_from_thread(self._update_stats_ui, engine.totals)

            # Fin del lote
            self.call_from_thread(
                self._log,
                f"[bold]Lote {self._batch_num}:[/] "
                f"[green]{batch_ok}[/] descargados, "
                f"[yellow]{batch_skip} omitidos, {batch_dup} dup[/], "
                f"[red]{batch_err} errores[/] "
                f"({format_size(batch_bytes)})",
            )

            self.call_from_thread(lambda: self.query_one("#current-file", Static).update(""))
            self.call_from_thread(
                lambda: self.query_one("#progress-bar", ProgressBar).update(total=100, progress=0)
            )

            if batch_result.get("reached_start"):
                self.call_from_thread(self._log, _ok("Se alcanzó la fecha de inicio."))
                break

        # ── Limpieza final ──
        await engine.disconnect()
        self.call_from_thread(self._set_status, "[bold green]✓ Completado[/]")
        self.call_from_thread(self._log, _ok("\nDESCARGA FINALIZADA"))
        self.call_from_thread(lambda: self.query_one("#btn-pause", Button).disabled)
        self.call_from_thread(lambda: self.query_one("#btn-start", Button).disabled)

    # ── Helpers thread-safe ──

    def _is_paused(self) -> bool:
        return self.paused

    def _is_stopped(self) -> bool:
        return self.stop_requested

    def _inc_batch(self) -> None:
        self._batch_num = getattr(self, "_batch_num", 0) + 1

    # ── UI updates ──

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status-bar", Label).update(text)
        except Exception:
            pass

    def _log(self, text: str) -> None:
        """Escribe directamente al RichLog."""
        try:
            self.query_one("#log", RichLog).write(text)
        except Exception:
            pass

    def _update_bar(self, current: int, total: int) -> None:
        """Actualiza barra de progreso desde callback de download_one."""
        if total <= 0:
            return
        try:
            pct = int(current / total * 100)
            self.query_one("#progress-bar", ProgressBar).update(total=100, progress=pct)
        except Exception:
            pass

    def _update_stats_ui(self, totals: dict) -> None:
        try:
            self.query_one("#stats", Static).update(
                f"[bold]Estadísticas[/]\n\n"
                f"✓ Descargados: [green]{totals['ok']}[/]\n"
                f"⏭ Ya existían: [yellow]{totals['dup']}[/]\n"
                f"⏭ Omitidos: [yellow]{totals['skip']}[/]\n"
                f"✗ Errores: [red]{totals['err']}[/]\n"
                f"───\n"
                f"Total: [bold]{totals['ok'] + totals['dup'] + totals['err'] + totals['skip']}[/]\n"
                f"Tamaño: [bold]{format_size(totals['bytes'])}[/]"
            )
        except Exception:
            pass

    # ── Acciones ──

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self._set_status("[yellow]⏸ PAUSADO[/]")
            self._log(_warn("⏸ PAUSADO"))
            self.query_one("#btn-pause", Button).label = "▶ Reanudar"
        else:
            self._set_status("[green]▶ Reanudado[/]")
            self._log(_ok("▶ Reanudado"))
            self.query_one("#btn-pause", Button).label = "⏸ Pausar"

    def action_quit(self) -> None:
        self.stop_requested = True
        self._set_status("[yellow]Deteniendo...[/]")
        self._log(_warn("Deteniendo..."))
        self.set_timer(0.5, self.exit)


def main():
    app = DownloadApp()
    app.run()


if __name__ == "__main__":
    main()
