#!/usr/bin/env python3
"""
Interfaz TUI (Textual) para descarga masiva de contenido multimedia de Telegram.

Usa core.py como motor compartido.

Uso:
    python tui.py

Requiere: pip install textual>=8.0
"""

import os

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
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
    load_config,
    load_dotenv,
    load_settings,
)

# ── Helpers ANSI para el log ──


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


# ── Pantalla principal (3 paneles) ──


class MainScreen(Screen):
    """Pantalla principal con layout de 3 paneles: resumen, detalle, log."""

    BINDINGS = [
        Binding("p", "toggle_pause", "Pausa"),
        Binding("c", "open_config", "Config"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.paused = False
        self.stop_requested = False
        self._started = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("Listo para comenzar", id="status-bar")

        # ── Paneles superiores ──
        with Horizontal(id="panels"):
            with Vertical(id="left-panel", classes="panel"):
                yield Static("[bold]Resumen[/]", id="summary-title")
                yield Static("Archivos:  0", id="stat-files")
                yield Static("Tamaño:  0 B", id="stat-size")
                yield Static("Velocidad:  —", id="stat-speed")
                yield Static("Tiempo:  00:00:00", id="stat-time")
                yield Static("Errores:  0", id="stat-errors")

            with Vertical(id="right-panel", classes="panel"):
                yield Static("[bold]Detalle[/]", id="detail-title")
                yield Static("Archivo actual:  —", id="detail-file")
                yield ProgressBar(total=100, id="detail-progress")
                yield Static("Lote:  —", id="detail-batch")
                yield Static("Chat:  —", id="detail-chat")

        # ── Log ──
        yield RichLog(id="log", highlight=True, wrap=True, max_lines=10000)

        # ── Controles ──
        with Horizontal(id="controls"):
            yield Button("▶  Iniciar", id="btn-start", variant="primary")
            yield Button("⏸  Pausar", id="btn-pause", disabled=True)
            yield Button("⚙  Config", id="btn-config", variant="default")
            yield Button("✕  Salir", id="btn-quit", variant="error")

        yield Footer()

    def on_mount(self) -> None:
        """Muestra banner inicial en el log."""
        self._log(_head("╔════════════════════════════════════╗"))
        self._log(_head("║   Descargador Masivo de Telegram   ║"))
        self._log(_head("╚════════════════════════════════════╝"))
        self._log(_warn("Listo para comenzar — configurá los ajustes e iniciá la descarga."))

    # ── Helpers thread-safe ──

    def _log(self, text: str) -> None:
        try:
            self.query_one("#log", RichLog).write(text)
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status-bar", Label).update(text)
        except Exception:
            pass

    # ── Acciones ──

    def action_toggle_pause(self) -> None:
        """Pausa/Reanuda (activo solo durante descarga activa)."""
        if not self._started:
            return
        self.paused = not self.paused
        if self.paused:
            self._set_status("[yellow]⏸ PAUSADO[/]")
            self._log(_warn("⏸ PAUSADO"))
            self.query_one("#btn-pause", Button).label = "▶  Reanudar"
        else:
            self._set_status("[green]▶ Reanudado[/]")
            self._log(_ok("▶ Reanudado"))
            self.query_one("#btn-pause", Button).label = "⏸  Pausar"

    def action_open_config(self) -> None:
        """Abrir pantalla de configuración (Fase 3)."""
        self._log(_warn("Configuración — próximamente (Fase 3)."))

    def action_quit(self) -> None:
        """Detiene todo y cierra la app."""
        self.stop_requested = True
        self._set_status("[yellow]Deteniendo...[/]")
        self._log(_warn("Deteniendo..."))
        self.set_timer(0.5, lambda: self.app.exit())

    # ── Botones ──

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self._log(_warn("Iniciar descarga — próximamente (Fase 4)."))
        elif event.button.id == "btn-pause":
            self.action_toggle_pause()
        elif event.button.id == "btn-config":
            self.action_open_config()
        elif event.button.id == "btn-quit":
            self.action_quit()


# ── App ──


class TUIApp(App):
    """App principal: carga config y gestiona las pantallas."""

    TITLE = "Descargador Masivo de Telegram"
    SUB_TITLE = "TUI"

    CSS = """
    Screen {
        layout: vertical;
    }

    /* ── Barra de estado ── */

    #status-bar {
        height: 1;
        padding: 0 2;
        background: $primary 20%;
        color: $text;
        text-style: bold;
    }

    /* ── Paneles superiores ── */

    #panels {
        height: 1fr;
        margin: 1 1 0 1;
    }

    .panel {
        border: round $primary;
        padding: 1 2;
    }

    #left-panel {
        width: 2fr;
        margin-right: 1;
    }

    #right-panel {
        width: 3fr;
    }

    #summary-title, #detail-title {
        text-style: bold;
        margin-bottom: 1;
        border-bottom: solid $primary 30%;
        padding-bottom: 0;
    }

    /* ── Barra de progreso en panel derecho ── */

    #detail-progress {
        margin: 1 0;
    }

    /* ── Log ── */

    #log {
        height: 8;
        border: round $primary;
        margin: 1 1 0 1;
    }

    /* ── Controles ── */

    #controls {
        height: 3;
        align: center middle;
        margin: 0 1;
    }

    Button {
        margin: 0 1;
        min-width: 14;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.config: dict = {}
        self.settings: dict = {}

    def on_mount(self) -> None:
        """Carga configuración y muestra pantalla principal."""
        load_dotenv()

        try:
            self.config = load_config()
        except ValueError as e:
            print(f"ERROR: {e}")
            self.exit(1)
            return

        self.settings = load_settings()
        os.makedirs(self.config["OUTPUT_DIR"], exist_ok=True)

        self.push_screen(MainScreen())


def main():
    app = TUIApp()
    app.run()


if __name__ == "__main__":
    main()
