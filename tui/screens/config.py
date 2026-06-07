"""
Pantalla de configuración: chat, fechas, settings.
"""

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Select, Static, Switch

from tui.helpers import _save_settings


class ConfigScreen(Screen):
    """Configuración de la descarga: chat, fechas, settings."""

    TRANSITION = "slide 0.3s"

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
        Binding("q", "cancel", "Salir"),
    ]

    CSS = """
    ConfigScreen {
        align: center top;
    }

    #config-box {
        width: 100%;
        max-width: 80;
        height: 1fr;
        overflow-y: auto;
        border: round $primary;
        padding: 0 1;
        margin: 0;
    }

    #config-title {
        text-style: bold;
        padding: 0;
        margin: 0;
    }

    #config-status {
        min-height: 1;
        padding: 0;
        margin: 0;
    }

    #date-labels, #date-inputs,
    #batch-labels, #batch-inputs {
        height: auto;
        padding: 0;
        margin: 0;
    }

    #date-labels > Static, #date-inputs > Input,
    #batch-labels > Static, #batch-inputs > Input {
        width: 1fr;
        padding: 0;
        margin: 0;
    }

    #settings-section {
        padding: 0;
        margin: 0;
    }

    #config-controls {
        height: 3;
        align: center middle;
        padding: 0;
        margin: 0;
    }

    ConfigScreen Static,
    ConfigScreen Input,
    ConfigScreen Select,
    ConfigScreen Button,
    ConfigScreen Switch {
        margin: 0 0 1 0;
        padding: 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # ── Formulario secuencial puro (sin grid, sin flex) ──
        with Vertical(id="config-box"):
            yield Static("[bold cyan]Configuración[/]", id="config-title")
            yield Static("", id="config-status")

            yield Static("Chat (ID numérico o @username):")
            yield Input(id="cfg-chat", placeholder="-1001234567890 o @chat")

            with Horizontal(id="date-labels"):
                yield Static("Fecha desde (opcional):")
                yield Static("Fecha hasta (opcional):")
            with Horizontal(id="date-inputs"):
                yield Input(id="cfg-since", placeholder="2025-01-01 o vacío")
                yield Input(id="cfg-until", placeholder="2025-12-31 o vacío")

            with Horizontal(id="batch-labels"):
                yield Static("Archivos por lote:")
                yield Static("Umbral archivo grande (MB):")
            with Horizontal(id="batch-inputs"):
                yield Input(id="cfg-batch", placeholder="100")
                yield Input(id="cfg-large-threshold", placeholder="50")

            yield Static("[bold]Comportamiento[/]", id="settings-section")

            yield Static("Archivos grandes:")
            yield Select.from_values(
                ["ask", "skip", "download"],
                id="cfg-large-action",
                prompt="Seleccionar acción",
            )

            yield Static("Auto-omitir duplicados:")
            yield Switch(id="cfg-skip-dupes")
            yield Static("Auto-continuar:")
            yield Switch(id="cfg-auto-continue")

        # ── Botones siempre visibles fuera del scroll ──
        with Horizontal(id="config-controls"):
            yield Button("Guardar", id="btn-save", variant="primary")
            yield Button("Cancelar", id="btn-cancel", variant="error")

        yield Footer()

    def on_mount(self) -> None:
        """Carga valores actuales de config y settings."""
        config = self.app.config
        settings = self.app.settings

        self.query_one("#cfg-chat", Input).value = str(config.get("TELEGRAM_TARGET_CHAT", ""))

        since = config.get("_since")
        if since:
            self.query_one("#cfg-since", Input).value = since.strftime("%Y-%m-%d")
        until = config.get("_until")
        if until:
            self.query_one("#cfg-until", Input).value = until.strftime("%Y-%m-%d")

        self.query_one("#cfg-batch", Input).value = str(config.get("BATCH_SIZE", 100))

        try:
            self.query_one("#cfg-large-action", Select).value = settings.get(
                "large_file_action", "ask"
            )
        except Exception:
            pass

        self.query_one("#cfg-large-threshold", Input).value = str(
            settings.get("large_file_threshold_mb", 50)
        )
        self.query_one("#cfg-skip-dupes", Switch).value = settings.get("auto_skip_all_dupes", False)
        self.query_one("#cfg-auto-continue", Switch).value = settings.get("auto_continue", False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self._save()
        elif event.button.id == "btn-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def _save(self) -> None:
        """Valida y guarda la configuración."""
        status = self.query_one("#config-status", Static)

        try:
            # ── Chat target ──
            raw_chat = self.query_one("#cfg-chat", Input).value.strip()
            if raw_chat:
                try:
                    chat_val = int(raw_chat)
                except ValueError:
                    chat_val = raw_chat
                self.app.config["TELEGRAM_TARGET_CHAT"] = chat_val
                self.app.settings["TELEGRAM_TARGET_CHAT"] = chat_val
            else:
                self.app.config.pop("TELEGRAM_TARGET_CHAT", None)
                self.app.settings["TELEGRAM_TARGET_CHAT"] = ""

            # ── Fechas ──
            raw_since = self.query_one("#cfg-since", Input).value.strip()
            if raw_since:
                self.app.config["_since"] = datetime.strptime(raw_since, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            else:
                self.app.config.pop("_since", None)

            raw_until = self.query_one("#cfg-until", Input).value.strip()
            if raw_until:
                self.app.config["_until"] = datetime.strptime(raw_until, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=timezone.utc
                )
            else:
                self.app.config.pop("_until", None)

            # ── Batch ──
            raw_batch = self.query_one("#cfg-batch", Input).value.strip()
            if raw_batch:
                val = int(raw_batch)
                self.app.config["BATCH_SIZE"] = val
                self.app.settings["BATCH_SIZE"] = val

            # ── Settings ──
            large_action = self.query_one("#cfg-large-action", Select).value
            if large_action and large_action != Select.BLANK:
                self.app.settings["large_file_action"] = large_action

            raw_threshold = self.query_one("#cfg-large-threshold", Input).value.strip()
            if raw_threshold:
                self.app.settings["large_file_threshold_mb"] = int(raw_threshold)

            self.app.settings["auto_skip_all_dupes"] = self.query_one(
                "#cfg-skip-dupes", Switch
            ).value
            self.app.settings["auto_continue"] = self.query_one("#cfg-auto-continue", Switch).value

            # ── Persistir ──
            _save_settings(self.app.settings)

            self.app.pop_screen()

        except ValueError as e:
            status.update(f"[bold red]Error: formato inválido — {e}[/]")
        except Exception as e:
            status.update(f"[bold red]Error inesperado: {e}[/]")
