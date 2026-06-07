"""
Pantalla de login: autenticación contra Telegram.
"""

import asyncio
import threading

from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from tui.screens.main import MainScreen


class LoginScreen(Screen):
    """Login a Telegram si no hay sesión guardada."""

    TRANSITION = "slide 0.3s"

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
            self.app.call_from_thread(self._on_login_error, str(e))

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
            self.app.call_from_thread(self._on_login_success)
            return

        # Enviar código
        await client.send_code_request(self._phone)
        self.app.call_from_thread(self._show_code_input)

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
            self.app.call_from_thread(self._show_password_input)
            await loop.run_in_executor(None, self._password_event.wait)
            self._password_event.clear()
            pw = self._password_value
            await client.sign_in(password=pw)
        except errors.PhoneCodeInvalidError:
            self.app.call_from_thread(self._on_code_error, "Código inválido. Intentá de nuevo.")
            return
        except errors.PhoneCodeExpiredError:
            self.app.call_from_thread(self._on_code_error, "Código expirado. Solicitá uno nuevo.")
            return
        except errors.PhoneNumberInvalidError:
            self.app.call_from_thread(self._on_login_error, "Número de teléfono inválido.")
            return

        # Verificar que se haya autenticado
        if not await client.is_user_authorized():
            self.app.call_from_thread(self._on_login_error, "No se pudo iniciar sesión.")
            return

        await client.disconnect()
        self.app.call_from_thread(self._on_login_success)

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
