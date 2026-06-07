"""
Pantalla principal con layout de 3 paneles: resumen, detalle, log.
"""

import asyncio
import threading
import time
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ProgressBar, RichLog, Static

from core import (
    DownloadEngine,
    download_one,
    fmt_count,
    format_size,
    media_path,
    media_size,
)
from tui.helpers import _err, _esc, _head, _ok, _warn
from tui.screens.catalog import CatalogScreen
from tui.screens.config import ConfigScreen
from tui.screens.dialogs import ContinueDialog, ResumeDialog


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
        self._continue_event: threading.Event | None = None
        self._continue_response: bool = False
        self._resume_event: threading.Event | None = None
        self._resume_choice: str = "resume"
        self._engine: DownloadEngine | None = None

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
        yield RichLog(id="log", highlight=True, wrap=True, max_lines=10000, markup=True)

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
            if self._current_file and total > 0:
                self._update_stat(
                    "detail-file", f"Archivo:  {_esc(self._current_file)}  ({format_size(total)})"
                )
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
            self._save_checkpoint()
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
        self._save_checkpoint()
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
        self._engine = engine
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

                # Preguntar cómo reanudar
                self._resume_event = threading.Event()
                self._resume_choice = "resume"
                self.app.call_from_thread(self._show_resume_dialog, pc, pn, po, pd)
                self._resume_event.wait()

                if self._resume_choice == "resume":
                    self.app.call_from_thread(self._log, _ok("↻ Reanudando — solo contenido nuevo"))
                    resume_newest = pn
                    resume_oldest = po
                else:
                    self.app.call_from_thread(
                        self._log, _warn("↻ Verificando todo — omitiendo duplicados")
                    )
                    # resume_newest/oldest se quedan en None → arranca desde cero
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
                    self.app.call_from_thread(self._log, _err(f"✗ {batch['error']}"))
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
                    line_prefix = f"  {icono} [{_esc(seq)}] {_esc(fpath.name)}"

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
                            f"  {_err('✗')} {line_prefix}  {result.get('error', 'desconocido')}"
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

                # ── Preguntar si continuar ──
                if not self.app.settings.get("auto_continue", False):
                    self._continue_event = threading.Event()
                    self._continue_response = False
                    self.app.call_from_thread(self._show_continue_dialog, engine.totals["ok"])
                    self._continue_event.wait()
                    if self.stop_requested:
                        break
                    if not self._continue_response:
                        self.app.call_from_thread(self._log, _warn("⚑ Detenido por el usuario."))
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
            self._log(f"  Errores:              {_err(str(t['err']))}")
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
        self._log(_err(f"✗ Error: {error}"))
        self._set_status(f"[red]Error: {error}[/]")
        self._downloading = False
        try:
            self.query_one("#btn-start").disabled = False
            self.query_one("#btn-start").label = "▶  Reintentar"
            self.query_one("#btn-pause").disabled = True
            self.query_one("#btn-config").disabled = False
        except Exception:
            pass

    def _save_checkpoint(self) -> None:
        """Guarda checkpoint del catálogo actual con finalize(), si hay engine."""
        engine = getattr(self, "_engine", None)
        if engine is None:
            return
        try:
            engine.finalize()
            # después de finalize() reseteamos contadores para no duplicar
            engine.total_ok = 0
            engine.total_skip = 0
            self._log(_ok("✓ Checkpoint guardado"))
        except Exception as e:
            self._log(_err(f"✗ Error guardando checkpoint: {e}"))

    def _show_continue_dialog(self, ok_count: int) -> None:
        """Muestra el diálogo de continuar (llamado desde el worker thread vía call_from_thread)."""
        self._save_checkpoint()
        dialog = ContinueDialog(self._batch_num, ok_count)

        def _on_response(confirmed: bool) -> None:
            self._continue_response = confirmed
            if self._continue_event is not None:
                self._continue_event.set()

        self.app.push_screen(dialog, _on_response)

    def _show_resume_dialog(
        self, total_count: int, newest_id: int, oldest_id: int, last_date: str
    ) -> None:
        """Muestra el diálogo de reanudación (llamado desde el worker thread vía call_from_thread)."""
        dialog = ResumeDialog(total_count, newest_id, oldest_id, last_date)

        def _on_response(choice: str | None) -> None:
            self._resume_choice = choice or "fresh"
            if self._resume_event is not None:
                self._resume_event.set()

        self.app.push_screen(dialog, _on_response)
