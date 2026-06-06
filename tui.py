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
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RadioSet,
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
    save_catalog,
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


# ── Pantalla de configuración ──


class SetupScreen(Screen):
    """Pantalla de configuración previa a la descarga."""

    def compose(self) -> ComposeResult:
        app = self.app

        yield Header(show_clock=True)

        with Container(id="setup-box"):
            yield Static("[bold cyan]Configuración de descarga[/]", id="setup-title")

            yield Static(f"[bold]Chat:[/] {app.config['TELEGRAM_TARGET_CHAT']}")
            yield Static(f"[bold]Destino:[/] {app.config['OUTPUT_DIR']}")
            yield Static(f"[bold]Lote:[/] {app.config['BATCH_SIZE']} archivos")
            yield Static(
                f"[bold]Umbral archivos grandes:[/] {app.settings['large_file_threshold_mb']} MB"
            )

            yield Static("")  # spacing

            yield Static("[bold]Filtro de fecha (opcional, YYYYMMDD):[/]")
            yield Input(placeholder="Desde (ej. 20250101)", id="input-since")
            yield Input(placeholder="Hasta (ej. 20251231)", id="input-until")

            yield Static("")  # spacing

            yield Static(id="resume-status")
            # RadioSet para resume, oculto hasta tener info del catálogo
            radio = RadioSet(
                "Reanudar desde donde quedó",
                "Empezar desde el principio",
                id="resume-radio",
            )
            radio.display = False
            yield radio

            yield Static("")  # spacing

            with Horizontal(id="setup-controls"):
                yield Button("▶  Comenzar descarga", id="btn-start", variant="primary")
                yield Button("✕  Salir", id="btn-quit", variant="error")

        yield Footer()

    def on_mount(self) -> None:
        """Arranca conexión en background para verificar catálogo."""
        self.query_one("#resume-status", Static).update(
            _warn("Conectando para verificar sesión anterior...")
        )
        self.query_one("#btn-start", Button).disabled = True
        self.query_one("#input-since", Input).focus()

        self._setup_error = None
        self._setup_prev = None
        self.thread = threading.Thread(target=self._thread_setup, daemon=True)
        self.thread.start()

    def _thread_setup(self) -> None:
        """Thread de setup: conecta y verifica catálogo."""
        try:
            asyncio.run(self._async_setup())
        except Exception as e:
            self.call_from_thread(self._on_setup_error, str(e))

    async def _async_setup(self) -> None:
        """Conecta y verifica si hay sesión anterior."""
        engine = DownloadEngine(self.app.config, self.app.settings)
        await engine.connect()
        info = await engine.prepare()

        # Verificar catálogo
        prev = engine.catalog.get("chats", {}).get(info["chat_key"])

        self.call_from_thread(self._on_setup_ready, prev)

    def _on_setup_ready(self, prev: dict | None) -> None:
        """Callback desde el thread: actualiza UI con info del catálogo."""
        status = self.query_one("#resume-status", Static)
        radio = self.query_one("#resume-radio", RadioSet)

        if prev:
            count = prev.get("total_count", 0)
            last = prev.get("last_date", "?")
            status.update(_warn(f"Sesión anterior: {count} archivos ({last})"))
            radio.display = True
            self.app._resume_mode = "resume"
            self._setup_prev = prev
        else:
            status.update("[dim]Sin sesión anterior[/]")
            self.app._resume_mode = "fresh"

        self.query_one("#btn-start", Button).disabled = False

    def _on_setup_error(self, error: str) -> None:
        """Callback si falla la conexión en setup."""
        self._setup_error = error
        self.query_one("#resume-status", Static).update(_fail(f"Error al conectar: {error}"))
        self.query_one("#btn-start", Button).disabled = True

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Guarda el modo de reanudación."""
        self.app._resume_mode = "resume" if event.index == 0 else "fresh"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self._start()
        elif event.button.id == "btn-quit":
            self.app.exit()

    def _start(self) -> None:
        """Valida y pasa a la pantalla de descarga."""
        app = self.app
        since = self.query_one("#input-since", Input).value.strip() or None
        until = self.query_one("#input-until", Input).value.strip() or None

        # Validar formato
        for val, label in [(since, "Desde"), (until, "Hasta")]:
            if val and not (val.isdigit() and len(val) == 8):
                self.query_one("#setup-title", Static).update(
                    f"[bold red]Error: {label} debe ser YYYYMMDD (8 dígitos)[/]"
                )
                return

        app.config["_since"] = since
        app.config["_until"] = until

        app.push_screen(DownloadScreen())


# ── Pantalla de descarga ──


class DownloadScreen(Screen):
    """Pantalla de descarga con progreso, stats y log."""

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
            yield Button("⏸ Pausar", id="btn-pause", disabled=True)
            yield Button("⏹ Salir", id="btn-quit", variant="error")

        yield Footer()

    def on_mount(self) -> None:
        """Arranca la descarga automáticamente al mostrar la pantalla."""
        self._start_workflow()

    def _start_workflow(self) -> None:
        """Arranca el worker en thread separado."""
        if self._started:
            return
        self._started = True
        self.query_one("#btn-pause", Button).disabled = False

        self.thread = threading.Thread(target=self._thread_run, daemon=True)
        self.thread.start()

    def _thread_run(self) -> None:
        """Corre en thread separado: crea su propio event loop."""
        asyncio.run(self._async_run())

    async def _async_run(self) -> None:
        """Async real: conecta, prepara, descarga (event loop propio)."""
        config = self.app.config
        settings = self.app.settings
        resume_mode = self.app._resume_mode

        engine = DownloadEngine(config, settings)
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
        if prev and resume_mode == "resume":
            pc = prev.get("total_count", 0)
            pd = prev.get("last_date", "?")
            self.call_from_thread(self._log, _warn(f"⏭ Sesión anterior: {pc} archivos ({pd})"))
            resume_newest = prev.get("newest_id")
            resume_oldest = prev.get("oldest_id")
            resume_complete = True
            self.call_from_thread(self._log, "  → Reanudando")
        elif prev:
            self.call_from_thread(self._log, _warn("  → Empezando desde el principio"))
            engine.catalog["chats"][engine.chat_key] = {}
            save_catalog(engine.catalog)

        self.call_from_thread(self._log, "")

        # ── Ciclo de descarga ──
        offset_id = 0
        since = config.get("_since")
        until = config.get("_until")

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
                limit=config["BATCH_SIZE"],
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

            w = len(str(config["BATCH_SIZE"]))

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
                thr = settings["large_file_threshold_mb"] * 1024 * 1024
                if fsize is not None and fsize > thr and thr > 0:
                    action = settings.get("large_file_action", "skip")
                    if action in ("skip", "ask"):
                        engine.add_result({"status": "skip", "size": 0})
                        self.call_from_thread(
                            self._log,
                            f"  {icono} [{pos:>{w}}/{config['BATCH_SIZE']}] "
                            f"[yellow]⏭[/] {file_name} ({format_size(fsize)}, omitido)",
                        )
                        batch_skip += 1
                        batch_count += 1
                        continue

                # Descargar
                self.call_from_thread(
                    self._log,
                    f"  {icono} [{pos:>{w}}/{config['BATCH_SIZE']}] [dim]{file_name}[/]",
                )

                result = await download_one(
                    engine.client,
                    msg,
                    engine.output_dir,
                    settings,
                    progress_callback=lambda c, t, fn=file_name: self.call_from_thread(
                        self._update_bar, c, t
                    ),
                )

                engine.add_result(result)

                if result["status"] == "ok":
                    self.call_from_thread(
                        self._log,
                        f"  {icono} [{pos:>{w}}/{config['BATCH_SIZE']}] "
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
                        f"  {icono} [{pos:>{w}}/{config['BATCH_SIZE']}] "
                        f"[yellow]⏭[/] {file_name} {sz} (ya existe)",
                    )
                    batch_dup += 1
                elif result["status"] == "err":
                    err_msg = result.get("error", "desconocido")
                    self.call_from_thread(
                        self._log,
                        f"  {icono} [{pos:>{w}}/{config['BATCH_SIZE']}] "
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
        try:
            self.query_one("#log", RichLog).write(text)
        except Exception:
            pass

    def _update_bar(self, current: int, total: int) -> None:
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
        self.set_timer(0.5, lambda: self.app.exit())


# ── App ──


class DownloadApp(App):
    """App principal: carga config y gestiona las pantallas."""

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

    /* Setup screen */
    #setup-box {
        padding: 1 2;
        margin: 1 2;
    }

    #setup-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #setup-controls {
        height: 3;
        align: center middle;
    }

    Input {
        margin: 0 0 0 2;
        width: 30;
    }

    RadioSet {
        margin: 0 0 0 2;
        width: 40;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.config: dict = {}
        self.settings: dict = {}
        self._resume_mode: str = "resume"

    def on_mount(self) -> None:
        """Carga configuración y muestra pantalla de setup."""
        load_dotenv()

        try:
            self.config = load_config()
        except ValueError as e:
            print(f"ERROR: {e}")
            self.exit(1)
            return

        self.settings = load_settings()
        os.makedirs(self.config["OUTPUT_DIR"], exist_ok=True)

        self.push_screen(SetupScreen())


def main():
    app = DownloadApp()
    app.run()


if __name__ == "__main__":
    main()
