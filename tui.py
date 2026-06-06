#!/usr/bin/env python3
"""
Interfaz TUI (Textual) para descarga masiva de contenido multimedia de Telegram.

Usa core.py como motor compartido.

Uso:
    python tui.py

Requiere: pip install textual>=8.0
"""

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
    Switch,
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


SETTINGS_PATH = Path(__file__).parent / "settings.json"


def _save_settings(settings: dict) -> None:
    """Guarda settings.json."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


# ── Pantalla de login ──


class LoginScreen(Screen):
    """Login a Telegram si no hay sesión guardada."""

    BINDINGS = [
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._phone: str | None = None
        self._code_event = threading.Event()
        self._code_value: str | None = None
        self._password_event = threading.Event()
        self._password_value: str | None = None
        self._login_thread: threading.Thread | None = None
        self._awaiting_code = False
        self._awaiting_password = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Vertical(id="login-box"):
            yield Static("[bold cyan]Iniciar sesión en Telegram[/]", id="login-title")
            yield Static("", id="login-status")

            yield Static("Número de teléfono (formato internacional):")
            yield Input(placeholder="+5491112345678", id="input-phone")

            with Vertical(id="code-section"):
                yield Static("Código de verificación enviado a Telegram:")
                yield Input(placeholder="12345", id="input-code")

            with Vertical(id="pass-section"):
                yield Static("Contraseña de verificación en dos pasos:")
                yield Input(placeholder="", id="input-password", password=True)

            with Horizontal(id="login-controls"):
                yield Button("Conectar", id="btn-connect", variant="primary")
                yield Button("Cancelar", id="btn-cancel", variant="error")

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#code-section").display = False
        self.query_one("#pass-section").display = False
        self.query_one("#input-phone", Input).focus()
        self.query_one("#login-status", Static).update(
            "Ingresá el número de teléfono asociado a tu cuenta de Telegram."
        )

    # ── Handlers ──

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-connect":
            self._on_connect()
        elif event.button.id == "btn-cancel":
            self.app.exit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter avanza al siguiente paso."""
        if event.input.id == "input-phone":
            self._on_connect()
        elif event.input.id == "input-code":
            self._on_connect()
        elif event.input.id == "input-password":
            self._on_connect()

    # ── Lógica de conexión ──

    def _on_connect(self) -> None:
        """Maneja el botón Conectar/Verificar según el estado."""
        # Modo: ingresando código
        if self._awaiting_code:
            code = self.query_one("#input-code", Input).value.strip()
            if not code:
                self._set_error("Ingresá el código de verificación.")
                return
            self._code_value = code
            self._code_event.set()
            self._set_loading("Verificando código...")
            return

        # Modo: ingresando contraseña 2FA
        if self._awaiting_password:
            pw = self.query_one("#input-password", Input).value
            self._password_value = pw
            self._password_event.set()
            self._set_loading("Verificando contraseña...")
            return

        # Modo inicial: ingresando número
        phone = self.query_one("#input-phone", Input).value.strip()
        if not phone:
            self._set_error("Ingresá un número de teléfono.")
            return
        if not phone.startswith("+"):
            self._set_error("El número debe incluir código de país (ej. +549...).")
            return

        self._phone = phone
        self._start_login()

    def _start_login(self) -> None:
        """Inicia el worker thread de login."""
        self._set_loading("Conectando a Telegram...")
        self.query_one("#btn-connect").disabled = True
        self.query_one("#input-phone").disabled = True

        self._login_thread = threading.Thread(target=self._thread_login, daemon=True)
        self._login_thread.start()

    def _thread_login(self) -> None:
        """Worker: corre asyncio en su propio event loop."""
        try:
            asyncio.run(self._async_login())
        except Exception as e:
            self.call_from_thread(self._on_login_error, str(e))

    async def _async_login(self) -> None:
        """Async flow: conecta, envía código, verifica, maneja 2FA."""
        client = TelegramClient(
            self.app.config["SESSION_NAME"],
            self.app.config["TELEGRAM_API_ID"],
            self.app.config["TELEGRAM_API_HASH"],
        )

        await client.connect()

        # ¿Ya autorizado?
        if await client.is_user_authorized():
            self.call_from_thread(self._on_login_success)
            return

        # Enviar código
        await client.send_code_request(self._phone)
        self.call_from_thread(self._show_code_input)

        # Esperar código (bloquea thread hasta que TUI lo ingrese)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._code_event.wait)
        self._code_event.clear()
        code = self._code_value

        # Intentar sign in con código
        try:
            await client.sign_in(phone=self._phone, code=code)
        except SessionPasswordNeededError:
            # 2FA: pedir contraseña
            self.call_from_thread(self._show_password_input)
            await loop.run_in_executor(None, self._password_event.wait)
            self._password_event.clear()
            pw = self._password_value
            await client.sign_in(password=pw)
        except errors.PhoneCodeInvalidError:
            self.call_from_thread(self._on_code_error, "Código inválido. Intentá de nuevo.")
            return
        except errors.PhoneCodeExpiredError:
            self.call_from_thread(self._on_code_error, "Código expirado. Solicitá uno nuevo.")
            return
        except errors.PhoneNumberInvalidError:
            self.call_from_thread(self._on_login_error, "Número de teléfono inválido.")
            return

        # Verificar que se haya autenticado
        if not await client.is_user_authorized():
            self.call_from_thread(self._on_login_error, "No se pudo iniciar sesión.")
            return

        await client.disconnect()
        self.call_from_thread(self._on_login_success)

    # ── Cambios de UI ──

    def _show_code_input(self) -> None:
        """Muestra el input de código y oculta el de teléfono."""
        self._awaiting_code = True
        self.query_one("#code-section").display = True
        self.query_one("#input-code", Input).focus()
        self.query_one("#input-code", Input).value = ""
        self.query_one("#btn-connect").disabled = False
        self.query_one("#btn-connect").label = "Verificar"
        self._set_status("Te enviamos un código a Telegram. Ingresalo abajo.")

    def _show_password_input(self) -> None:
        """Muestra el input de contraseña 2FA."""
        self._awaiting_code = False
        self._awaiting_password = True
        self.query_one("#code-section").display = False
        self.query_one("#pass-section").display = True
        self.query_one("#input-password", Input).focus()
        self.query_one("#input-password", Input).value = ""
        self.query_one("#btn-connect").disabled = False
        self.query_one("#btn-connect").label = "Verificar"
        self._set_status("Este chat tiene verificación en dos pasos. Ingresá la contraseña.")

    def _on_login_success(self) -> None:
        """Login exitoso → MainScreen."""
        self._set_status("[bold green]✓ Sesión iniciada correctamente[/]")
        self.query_one("#btn-connect").disabled = True
        self.query_one("#btn-cancel", Button).disabled = True
        self.set_timer(1, lambda: self.app.switch_screen(MainScreen()))

    def _on_login_error(self, error: str) -> None:
        self._set_error(error)
        self._reset_login()

    def _on_code_error(self, error: str) -> None:
        """Error con el código (inválido o expirado)."""
        self._awaiting_code = False
        self._code_event.clear()
        self._set_error(error)

        # Mostrar input de teléfono de nuevo para reintentar
        self.query_one("#code-section").display = False
        self.query_one("#input-phone").disabled = False
        self.query_one("#input-phone", Input).value = self._phone or ""
        self.query_one("#input-phone", Input).focus()
        self.query_one("#btn-connect").label = "Reintentar"
        self.query_one("#btn-connect").disabled = False

    # ── Helpers ──

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#login-status", Static).update(text)
        except Exception:
            pass

    def _set_error(self, text: str) -> None:
        self._set_status(f"[bold red]{text}[/]")

    def _set_loading(self, text: str) -> None:
        self._set_status(f"[yellow]{text}[/]")

    def _reset_login(self) -> None:
        """Rehabilita controles tras un error."""
        self._awaiting_code = False
        self._awaiting_password = False
        self._code_event.clear()
        self._password_event.clear()
        self.query_one("#btn-connect").disabled = False
        self.query_one("#input-phone").disabled = False

    def action_quit(self) -> None:
        self.app.exit()


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
        """Abrir pantalla de configuración."""
        self.app.push_screen(ConfigScreen())

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


# ── Pantalla de configuración ──


class ConfigScreen(Screen):
    """Configuración de la descarga: chat, fechas, settings."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
        Binding("q", "cancel", "Salir"),
    ]

    CSS = """
    ConfigScreen {
        align: center middle;
    }

    #config-box {
        width: 60;
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin: 1 2;
    }

    #config-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #config-status {
        margin-bottom: 1;
    }

    #date-row {
        height: auto;
    }

    #date-row > Vertical {
        width: 1fr;
        margin-right: 1;
    }

    #settings-section {
        margin-top: 1;
        border-bottom: solid $primary 30%;
        padding-bottom: 0;
    }

    #switch-row {
        height: auto;
        align: left middle;
        margin: 1 0;
    }

    #switch-row > Static {
        margin-right: 1;
        width: auto;
    }

    #switch-row > Switch {
        margin-right: 2;
    }

    #config-controls {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    ConfigScreen Input {
        margin: 0 0 1 0;
    }

    ConfigScreen Select {
        margin: 0 0 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Vertical(id="config-box"):
            yield Static("[bold cyan]Configuración[/]", id="config-title")
            yield Static("", id="config-status")

            yield Static("Chat (ID numérico o @username):")
            yield Input(id="cfg-chat", placeholder="-1001234567890 o @chat")

            with Horizontal(id="date-row"):
                with Vertical():
                    yield Static("Fecha desde (opcional):")
                    yield Input(id="cfg-since", placeholder="2025-01-01 o vacío")
                with Vertical():
                    yield Static("Fecha hasta (opcional):")
                    yield Input(id="cfg-until", placeholder="2025-12-31 o vacío")

            yield Static("Archivos por lote:")
            yield Input(id="cfg-batch", placeholder="100")

            yield Static("[bold]Comportamiento[/]", id="settings-section")

            yield Static("Archivos grandes:")
            yield Select.from_values(
                ["ask", "skip", "download"],
                id="cfg-large-action",
                prompt="Seleccionar acción",
            )

            yield Static("Umbral archivo grande (MB):")
            yield Input(id="cfg-large-threshold", placeholder="50")

            with Horizontal(id="switch-row"):
                yield Static("Auto-omitir duplicados:")
                yield Switch(id="cfg-skip-dupes")

                yield Static("Auto-continuar:")
                yield Switch(id="cfg-auto-continue")

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
                    self.app.config["TELEGRAM_TARGET_CHAT"] = int(raw_chat)
                except ValueError:
                    self.app.config["TELEGRAM_TARGET_CHAT"] = raw_chat

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
                self.app.config["BATCH_SIZE"] = int(raw_batch)

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

            status.update("[bold green]✓ Guardado[/]")
            self.set_timer(0.8, self.app.pop_screen)

        except ValueError as e:
            status.update(f"[bold red]Error: formato inválido — {e}[/]")
        except Exception as e:
            status.update(f"[bold red]Error inesperado: {e}[/]")


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

    /* ── Login ── */

    #login-box {
        width: 50;
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin: 2 4;
    }

    #login-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #login-status {
        margin-bottom: 1;
    }

    #code-section, #pass-section {
        display: none;
    }

    #login-controls {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    Input {
        margin: 0 0 1 0;
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

    def _has_session(self) -> bool:
        """Verifica si existe un archivo de sesión de Telegram."""
        session_path = Path(f"{self.config['SESSION_NAME']}.session")
        return session_path.exists()

    def on_mount(self) -> None:
        """Carga configuración y decide qué pantalla mostrar."""
        load_dotenv()

        try:
            self.config = load_config()
        except ValueError as e:
            print(f"ERROR: {e}")
            self.exit(1)
            return

        self.settings = load_settings()
        os.makedirs(self.config["OUTPUT_DIR"], exist_ok=True)

        if self._has_session():
            self.push_screen(MainScreen())
        else:
            self.push_screen(LoginScreen())


def main():
    app = TUIApp()
    app.run()


if __name__ == "__main__":
    main()
