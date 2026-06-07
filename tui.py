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
import re
import threading
import time
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
    DownloadEngine,
    download_one,
    fmt_count,
    format_size,
    list_catalog,
    load_config,
    load_dotenv,
    load_settings,
    media_path,
    media_size,
    remove_catalog_entry,
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


# ── Pantalla principal (3 paneles) ──


class MainScreen(Screen):
    """Pantalla principal con layout de 3 paneles: resumen, detalle, log."""

    TRANSITION = "slide 0.3s"

    BINDINGS = [
        Binding("s", "start_download", "Iniciar"),
        Binding("p", "toggle_pause", "Pausa"),
        Binding("c", "open_config", "Config"),
        Binding("d", "toggle_dark", "Tema"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.paused = False
        self.stop_requested = False
        self._started = False
        self._downloading = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._worker_thread: threading.Thread | None = None
        self._start_time: float = 0.0
        self._download_bytes: int = 0
        self._current_file = ""
        self._chat_name = ""
        self._batch_num = 0

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
                yield Static("", id="stat-lote")

            with Vertical(id="right-panel", classes="panel"):
                yield Static("[bold]Detalle[/]", id="detail-title")
                yield Static("Archivo actual:  —", id="detail-file")
                yield ProgressBar(total=100, id="detail-progress", show_eta=False)
                yield Static("Lote:  —", id="detail-batch")
                yield Static("Chat:  —", id="detail-chat")

        # ── Log ──
        yield RichLog(id="log", highlight=True, wrap=True, max_lines=10000)

        # ── Controles ──
        with Horizontal(id="controls"):
            yield Button("▶  Iniciar", id="btn-start", variant="primary")
            yield Button("⏸  Pausar", id="btn-pause", disabled=True)
            yield Button("⚙  Config", id="btn-config", variant="default")
            yield Button("📋  Catálogo", id="btn-catalog", variant="default")
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

    def _update_stat(self, stat_id: str, text: str) -> None:
        try:
            self.query_one(f"#{stat_id}", Static).update(text)
        except Exception:
            pass

    def _update_progress_bar(self, current: int, total: int) -> None:
        """Actualiza la barra de progreso del archivo actual."""
        try:
            pb = self.query_one("#detail-progress", ProgressBar)
            if total > 0:
                pb.update(progress=current, total=total)
            else:
                pb.update(progress=0, total=100)
        except Exception:
            pass

    def _reset_progress_bar(self) -> None:
        try:
            self.query_one("#detail-progress", ProgressBar).update(progress=0, total=100)
        except Exception:
            pass

    # ── Acciones ──

    def action_toggle_pause(self) -> None:
        """Pausa/Reanuda (activo solo durante descarga activa)."""
        if not self._started or not self._downloading:
            return
        self.paused = not self.paused
        if self.paused:
            self._pause_event.clear()
            self._set_status("[yellow]⏸ PAUSADO[/]")
            self._log(_warn("⏸ PAUSADO"))
            self.query_one("#btn-pause", Button).label = "▶  Reanudar"
        else:
            self._pause_event.set()
            self._set_status("[green]▶ Reanudado[/]")
            self._log(_ok("▶ Reanudado"))
            self.query_one("#btn-pause", Button).label = "⏸  Pausar"

    def action_open_config(self) -> None:
        """Abrir pantalla de configuración."""
        if self._downloading:
            self._log(_warn("No se puede cambiar configuración durante la descarga."))
            return
        self.app.push_screen(ConfigScreen())

    def action_open_catalog(self) -> None:
        """Abrir pantalla de gestión del catálogo."""
        self.app.push_screen(CatalogScreen())

    def action_quit(self) -> None:
        """Detiene todo y cierra la app."""
        self.stop_requested = True
        self._pause_event.set()  # desbloquear worker si está pausado
        self._set_status("[yellow]Deteniendo...[/]")
        self._log(_warn("Deteniendo..."))
        self.set_timer(0.5, lambda: self.app.exit())

    def action_start_download(self) -> None:
        """Atajo de teclado: inicia descarga."""
        if not self._downloading and not self._started:
            self._start_download()

    def action_toggle_dark(self) -> None:
        """Atajo de teclado: alterna tema claro/oscuro."""
        self.app.dark = not self.app.dark
        theme = "🌙 Oscuro" if self.app.dark else "☀ Claro"
        self._log(f"  {_head(theme)}")
        self._set_status(theme)

    # ── Botones ──

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self._start_download()
        elif event.button.id == "btn-pause":
            self.action_toggle_pause()
        elif event.button.id == "btn-config":
            self.action_open_config()
        elif event.button.id == "btn-catalog":
            self.action_open_catalog()
        elif event.button.id == "btn-quit":
            self.action_quit()

    # ── Iniciar descarga ──

    def _start_download(self) -> None:
        """Inicia el worker thread de descarga."""
        if self._downloading:
            return

        self._downloading = True
        self._started = True
        self.stop_requested = False
        self._download_bytes = 0
        self._batch_num = 0
        self._pause_event.set()

        try:
            self.query_one("#btn-start").disabled = True
            self.query_one("#btn-start").label = "▶  Descargando"
            self.query_one("#btn-pause").disabled = False
            self.query_one("#btn-pause").label = "⏸  Pausar"
            self.query_one("#btn-config").disabled = True
        except Exception as e:
            self._set_status(f"[red]Error UI: {e}[/]")
            return

        self._log(_ok("Iniciando descarga..."))
        self._set_status("[green]Conectando a Telegram...[/]")

        self._reset_progress_bar()

        self._worker_thread = threading.Thread(target=self._thread_download, daemon=True)
        self._worker_thread.start()

        # Timer para actualizar tiempo transcurrido (cada 1s)
        self.set_interval(1, self._tick_elapsed_time)

    def _thread_download(self) -> None:
        """Worker: corre la descarga en su propio event loop."""
        try:
            asyncio.run(self._async_download())
        except Exception as e:
            self.app.call_from_thread(self._on_download_error, str(e))

    async def _async_download(self) -> None:
        """Async: conecta, prepara, descarga por lotes."""
        engine = DownloadEngine(self.app.config, self.app.settings)
        self._start_time = time.time()

        try:
            await engine.connect()
            self.app.call_from_thread(self._log, _ok("✓ Conectado a Telegram"))

            info = await engine.prepare()
            self._chat_name = info["chat_name"]
            output_dir = info["output_dir"]

            self.app.call_from_thread(self._update_stat, "detail-chat", f"Chat:  {self._chat_name}")

            # ── Resumen del chat ──
            fotos, videos = info["fotos"], info["videos"]
            total_media = (fotos or 0) + (videos or 0)
            self.app.call_from_thread(self._log, f"Chat: {self._chat_name}")
            self.app.call_from_thread(
                self._log, f"Contenido: {fmt_count(fotos)} fotos · {fmt_count(videos)} videos"
            )
            self.app.call_from_thread(
                self._log, f"Total: {fmt_count(total_media)} archivos multimedia"
            )
            self.app.call_from_thread(self._log, f"Guardando en: {output_dir}/")

            # ── Reanudación ──
            resume_newest, resume_oldest, resume_complete = None, None, False
            prev = info.get("resume_info")
            if prev:
                pc = prev.get("total_count", 0)
                pn = prev.get("newest_id", 0)
                po = prev.get("oldest_id", 0)
                pd = prev.get("last_date", "?")
                self.app.call_from_thread(
                    self._log,
                    _ok(f"↻ Sesión anterior: {pc} archivos (IDs {po}→{pn}, {pd})"),
                )
                self.app.call_from_thread(self._log, _ok("↻ Reanudando — solo contenido nuevo"))
                resume_newest = pn
                resume_oldest = po
            else:
                self.app.call_from_thread(
                    self._log, _warn("⚐ Sin sesión anterior — descarga completa")
                )

            self.app.call_from_thread(self._set_status, "[green]Descargando...[/]")

            # ── Variables de iteración ──
            offset_id = 0
            since = self.app.config.get("_since")
            until = self.app.config.get("_until")

            while not self.stop_requested:
                self._pause_event.wait()
                if self.stop_requested:
                    break

                self._batch_num += 1
                self.app.call_from_thread(
                    self._update_stat, "detail-batch", f"Lote:  {self._batch_num}"
                )
                self.app.call_from_thread(self._log, f"\n{'─' * 30}")
                self.app.call_from_thread(
                    self._log,
                    f"Lote {self._batch_num} — "
                    f"juntando {self.app.config['BATCH_SIZE']} archivos...",
                )

                batch = await engine.fetch_batch(
                    offset_id=offset_id,
                    limit=self.app.config["BATCH_SIZE"],
                    since=since,
                    until=until,
                    resume_newest=resume_newest,
                    resume_oldest=resume_oldest,
                    resume_complete=resume_complete,
                )

                if batch["error"]:
                    self.app.call_from_thread(self._log, _fail(f"✗ {batch['error']}"))
                    break

                media_messages = batch["media"]
                offset_id = batch["next_offset"]
                reached_start = batch.get("reached_start", False)
                should_stop = batch.get("should_stop", False)

                if not media_messages:
                    if reached_start:
                        self.app.call_from_thread(
                            self._log, _ok("✓ Se alcanzó la fecha de inicio.")
                        )
                    elif should_stop:
                        self.app.call_from_thread(self._log, _ok("✓ No hay más mensajes."))
                    break

                # ── Descargar cada mensaje ──
                for msg in media_messages:
                    if self.stop_requested:
                        break
                    self._pause_event.wait()
                    if self.stop_requested:
                        break

                    engine.session_min_id = min(engine.session_min_id, msg.id)
                    engine.session_max_id = max(engine.session_max_id, msg.id)

                    fpath = media_path(msg, output_dir)
                    icono = "📷" if msg.photo else "🎬"
                    seq = engine.total_ok + engine.total_dup + engine.total_skip + 1
                    line_prefix = f"  {icono} [{seq}] {fpath.name}"

                    self._current_file = fpath.name
                    self.app.call_from_thread(self._reset_progress_bar)
                    self.app.call_from_thread(
                        self._update_stat, "detail-file", f"Archivo:  {fpath.name}"
                    )

                    # ── Large file check (en TUI, si es "ask" tratamos como skip) ──
                    fsize = media_size(msg)
                    thr = engine.settings["large_file_threshold_mb"] * 1024 * 1024
                    es_grande = fsize is not None and fsize > thr and thr > 0
                    if es_grande and engine.settings["large_file_action"] in ("ask", "skip"):
                        engine.add_result({"status": "skip", "size": 0})
                        self.app.call_from_thread(
                            self._log,
                            f"  {_warn('⏭')} {line_prefix}  "
                            f"{_warn(format_size(fsize))}  (omitido por tamaño)",
                        )
                        self.app.call_from_thread(self._update_stats, engine)
                        continue

                    # ── Descargar ──
                    result = await download_one(
                        engine.client,
                        msg,
                        output_dir,
                        engine.settings,
                        progress_callback=self._progress_cb,
                    )

                    engine.add_result(result)

                    if result.get("size", 0):
                        self._download_bytes += result["size"]

                    # ── Mostrar resultado según status ──
                    if result["status"] == "ok":
                        log_line = f"{line_prefix}  {_ok('✓')}  {format_size(result['size'])}"
                    elif result["status"] == "dup":
                        try:
                            dup_size = format_size(Path(result["path"]).stat().st_size)
                            log_line = f"  {_warn('⏭')} {line_prefix}  ({dup_size})  (ya existe)"
                        except OSError:
                            log_line = f"  {_warn('⏭')} {line_prefix}  (ya existe)"
                    elif result["status"] == "err":
                        log_line = (
                            f"  {_fail('✗')} {line_prefix}  {result.get('error', 'desconocido')}"
                        )
                    else:
                        log_line = f"  {line_prefix}"

                    self.app.call_from_thread(self._log, log_line)
                    self.app.call_from_thread(self._update_stats, engine)

                    # Pequeña pausa para que la UI respire entre archivos
                    await asyncio.sleep(0.01)

                # ── Resumen del lote ──
                t = engine.totals
                self.app.call_from_thread(
                    self._log,
                    f"  ── Lote {self._batch_num}: "
                    f"{t['ok']} ok · {t['dup']} dup · {t['err']} err · {t['skip']} skip",
                )

                if reached_start:
                    self.app.call_from_thread(self._log, _ok("✓ Se alcanzó la fecha de inicio."))
                    break

                if should_stop:
                    self.app.call_from_thread(self._log, _ok("✓ Descarga completa."))
                    break

        except Exception as e:
            self.app.call_from_thread(self._on_download_error, str(e))
            return
        finally:
            try:
                await engine.disconnect()
            except Exception:
                pass

        self.app.call_from_thread(self._on_download_complete, engine)

    # ── Callback de progreso (por archivo) ──

    def _progress_cb(self, current: int, total: int) -> None:
        """Llamado desde download_one para progreso del archivo actual."""
        try:
            self.app.call_from_thread(self._update_progress_bar, current, total)
        except Exception:
            pass

    # ── Actualizaciones de UI ──

    def _update_stats(self, engine: DownloadEngine) -> None:
        """Actualiza el panel izquierdo con stats en tiempo real."""
        t = engine.totals
        self._update_stat("stat-files", f"Archivos:  {t['ok']}")
        self._update_stat("stat-size", f"Tamaño:  {format_size(self._download_bytes)}")
        self._update_stat("stat-errors", f"Errores:  {t['err']}")
        self._update_stat("stat-lote", f"Procesados:  {t['ok'] + t['dup'] + t['skip'] + t['err']}")

        elapsed = time.time() - self._start_time
        if elapsed > 0 and self._download_bytes > 0:
            speed = self._download_bytes / elapsed
            self._update_stat("stat-speed", f"Velocidad:  {format_size(int(speed))}/s")

    def _tick_elapsed_time(self) -> None:
        """Timer 1s: actualiza el tiempo transcurrido."""
        if not self._downloading:
            return
        elapsed = time.time() - self._start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)
        self._update_stat("stat-time", f"Tiempo:  {hours:02d}:{minutes:02d}:{seconds:02d}")

    def _on_download_complete(self, engine: DownloadEngine) -> None:
        """Descarga finalizada — muestra resumen y habilita controles."""
        self._log(_head("═" * 30))
        self._log(_head("DESCARGA FINALIZADA"))
        t = engine.totals
        self._log(f"  Archivos descargados: {_ok(str(t['ok']))}")
        if t["skip"]:
            self._log(f"  Omitidos (pesados):  {_warn(str(t['skip']))}")
        if t["dup"]:
            self._log(f"  Ya existían:          {t['dup']}")
        if t["err"]:
            self._log(f"  Errores:              {_fail(str(t['err']))}")
        if self._download_bytes:
            self._log(f"  Tamaño total:         {format_size(self._download_bytes)}")
        self._log(_head("═" * 30))

        engine.finalize()
        self._log(_ok("✓ Catálogo actualizado — próximas ejecuciones podrán reanudar."))

        self._downloading = False
        self._set_status("[green]✓ Descarga finalizada[/]")
        self.query_one("#btn-start").disabled = False
        self.query_one("#btn-start").label = "▶  Iniciar"
        self.query_one("#btn-pause").disabled = True
        self.query_one("#btn-config").disabled = False

    def _on_download_error(self, error: str) -> None:
        """Error durante la descarga."""
        self._log(_fail(f"✗ Error: {error}"))
        self._set_status(f"[red]Error: {error}[/]")
        self._downloading = False
        try:
            self.query_one("#btn-start").disabled = False
            self.query_one("#btn-start").label = "▶  Reintentar"
            self.query_one("#btn-pause").disabled = True
            self.query_one("#btn-config").disabled = False
        except Exception:
            pass


# ── Pantalla de configuración ──


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


# ── Pantalla de gestión del catálogo ──


class CatalogScreen(Screen):
    """Lista los chats del catálogo y permite borrarlos."""

    TRANSITION = "slide 0.3s"

    BINDINGS = [
        Binding("escape", "back", "Volver"),
        Binding("q", "back", "Salir"),
    ]

    CSS = """
    CatalogScreen {
        align: center top;
    }

    #catalog-box {
        width: 100%;
        max-width: 80;
        height: 1fr;
        overflow-y: auto;
        border: round $primary;
        padding: 0 1;
        margin: 0;
    }

    #catalog-title {
        text-style: bold;
        padding: 0;
        margin: 0 0 1 0;
    }

    .catalog-entry {
        height: auto;
        padding: 0;
        margin: 0 0 1 0;
        border: solid $primary 30%;
    }

    .catalog-name {
        text-style: bold;
        padding: 0;
        margin: 0;
    }

    .catalog-info {
        padding: 0;
        margin: 0;
    }

    .catalog-confirm {
        height: auto;
        padding: 0;
        margin: 0;
    }

    #catalog-controls {
        height: 3;
        align: center middle;
        padding: 0;
        margin: 0;
    }

    CatalogScreen Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._chat_map: dict[str, str] = {}  # id_safe -> nombre_original
        self._confirming: set[str] = set()  # safes en modo confirmación

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="catalog-box"):
            yield Static("[bold cyan]Catálogo de descargas[/]", id="catalog-title")
        with Horizontal(id="catalog-controls"):
            yield Button("Volver", id="btn-back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self._build_catalog()

    def _build_catalog(self, msg: str | None = None) -> None:
        """(Re)construye la lista de entradas."""
        box = self.query_one("#catalog-box", Vertical)
        # Limpiar todo excepto el título
        for child in list(box.children):
            if child.id != "catalog-title":
                child.remove()

        catalog = list_catalog()
        chats = catalog.get("chats", {})

        if msg:
            box.mount(Static(msg, id="catalog-status"))

        if not chats:
            box.mount(Static("[yellow]No hay chats en el catálogo.[/]"))
            return

        self._chat_map.clear()
        for name in sorted(chats.keys()):
            info = chats[name]
            safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
            self._chat_map[safe] = name

            if safe in self._confirming:
                actions = Horizontal(
                    Static("¿Borrar", classes="catalog-name"),
                    Switch(id=f"files-{safe}", value=False),
                    Static("también carpeta?", classes="catalog-info"),
                    Button("✓ Sí", id=f"confirm-{safe}", variant="error"),
                    Button("✗ No", id=f"cancel-{safe}", variant="default"),
                    classes="catalog-confirm",
                )
            else:
                actions = Horizontal(
                    Button("🗑  Borrar", id=f"del-{safe}"),
                    classes="catalog-confirm",
                )

            entry = Vertical(
                Static(name, classes="catalog-name"),
                Static(
                    f"  Procesados: {info.get('total_count', '?')}  "
                    f"({info.get('oldest_id', '?')}→{info.get('newest_id', '?')})  "
                    f"Última descarga: {info.get('last_date', '?')}",
                    classes="catalog-info",
                ),
                actions,
                classes="catalog-entry",
            )
            box.mount(entry)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid == "btn-back":
            self.app.pop_screen()
        elif bid.startswith("del-"):
            safe = bid[4:]
            self._confirming.add(safe)
            self._build_catalog()
        elif bid.startswith("confirm-"):
            safe = bid[8:]
            self._confirming.discard(safe)
            self._do_delete(safe)
        elif bid.startswith("cancel-"):
            safe = bid[8:]
            self._confirming.discard(safe)
            self._build_catalog()

    def _do_delete(self, safe: str) -> None:
        """Ejecuta la eliminación."""
        name = self._chat_map.get(safe, safe)
        try:
            switch = self.query_one(f"#files-{safe}", Switch)
            delete_files = switch.value
        except Exception:
            delete_files = False

        output_dir = Path(self.app.config["OUTPUT_DIR"])
        ok = remove_catalog_entry(name, output_dir, delete_files)
        if ok:
            msg = f"[green]✓ '{name}' eliminado del catálogo.[/]"
            if delete_files:
                msg = f"[green]✓ '{name}' eliminado (carpeta borrada).[/]"
        else:
            msg = f"[red]✗ No se encontró '{name}'.[/]"
        self._build_catalog(msg)


# ── App ──


class TUIApp(App):
    """App principal: carga config y gestiona las pantallas."""

    TITLE = "Descargador Masivo de Telegram"
    SUB_TITLE = "TUI"

    BINDINGS = [
        Binding("ctrl+t", "toggle_dark", "Tema"),
    ]

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
        height: 1fr;
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

    def action_toggle_dark(self) -> None:
        """Alterna tema claro/oscuro (disponible en toda la app)."""
        self.dark = not self.dark

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

        # Merge settings persistentes que pueden sobreescribir .env
        if self.settings.get("TELEGRAM_TARGET_CHAT"):
            self.config["TELEGRAM_TARGET_CHAT"] = self.settings["TELEGRAM_TARGET_CHAT"]
        if self.settings.get("BATCH_SIZE"):
            self.config["BATCH_SIZE"] = self.settings["BATCH_SIZE"]

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
