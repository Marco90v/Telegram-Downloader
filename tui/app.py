"""
App principal: carga config y gestiona las pantallas.
"""

import os
from pathlib import Path

from textual.app import App
from textual.binding import Binding

from core import load_config, load_dotenv, load_settings
from tui.screens.login import LoginScreen
from tui.screens.main import MainScreen


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
