"""
Interfaz TUI (Textual) para descarga masiva de contenido multimedia de Telegram.

Uso:
    python -m tui

Requiere: pip install textual>=8.0
"""

from tui.app import TUIApp


def main():
    app = TUIApp()
    app.run()


if __name__ == "__main__":
    main()
