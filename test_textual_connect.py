#!/usr/bin/env python3
"""Test mínimo: conecta a Telegram DENTRO de una app Textual minimal.

Si esto funciona, el bug está en tui.py.
Si no funciona, hay conflicto entre Textual y Telethon en el event loop.
"""

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Static

from core import DownloadEngine, load_config, load_dotenv, load_settings


class TestApp(App):
    def compose(self) -> ComposeResult:
        yield Static("Test de conexión dentro de Textual...", id="status")

    def on_mount(self) -> None:
        # Arranca igual que tui.py: create_task
        asyncio.create_task(self._run_test())

    async def _run_test(self) -> None:
        log = self.query_one("#status", Static)
        try:
            log.update("Cargando config...")
            load_dotenv()
            config = load_config()
            settings = load_settings()

            log.update("Creando engine...")
            engine = DownloadEngine(config, settings)
            await asyncio.sleep(0)

            log.update("[yellow]Conectando...[/]")
            await asyncio.wait_for(engine.connect(), timeout=15)
            log.update("[green]✓ Conectado![/]")

            await asyncio.sleep(0)

            log.update("[yellow]Preparando...[/]")
            info = await asyncio.wait_for(engine.prepare(), timeout=30)
            log.update(f"[green]✓ Chat: {info['chat_name']}[/]")

            await engine.disconnect()
            log.update("[bold green]✓ TODO OK[/]")
        except asyncio.TimeoutError:
            log.update("[bold red]✗ TIMEOUT[/]")
        except Exception as e:
            log.update(f"[bold red]✗ {e}[/]")
            import traceback

            traceback.print_exc()
        finally:
            self.set_timer(2, self.exit)


if __name__ == "__main__":
    app = TestApp()
    app.run()
