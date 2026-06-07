"""
Diálogos modales: ResumeDialog y ContinueDialog.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Static

from core import fmt_count


class ResumeDialog(Screen[str]):
    """Modal: preguntar cómo reanudar al iniciar descarga."""

    def __init__(self, total_count: int, newest_id: int, oldest_id: int, last_date: str) -> None:
        super().__init__()
        self.total_count = total_count
        self.newest_id = newest_id
        self.oldest_id = oldest_id
        self.last_date = last_date

    CSS = """
    ResumeDialog {
        align: center middle;
    }
    #resume-box {
        width: 60;
        height: auto;
        border: thick $primary;
        padding: 1 2;
        background: $surface;
    }
    #resume-box Button {
        margin: 0 1;
        min-width: 14;
    }
    .resume-info {
        padding: 0;
        margin: 0 0 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-box"):
            yield Static("[bold]Sesión anterior encontrada[/]")
            yield Static(
                f"  {self.total_count} archivos  (IDs {self.oldest_id}→{self.newest_id}, "
                f"{self.last_date})",
                classes="resume-info",
            )
            yield Static("")
            yield Static("¿Qué querés hacer?")
            yield Static("")
            with Horizontal():
                yield Button("Solo nuevo", id="resume", variant="primary")
                yield Button("Verificar todo", id="fresh", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)


class ContinueDialog(Screen[bool]):
    """Modal: preguntar si continuar descargando."""

    def __init__(self, batch_num: int, ok_count: int) -> None:
        super().__init__()
        self.batch_num = batch_num
        self.ok_count = ok_count

    CSS = """
    ContinueDialog {
        align: center middle;
    }
    #cont-box {
        width: 50;
        height: auto;
        border: thick $primary;
        padding: 1 2;
        background: $surface;
    }
    #cont-box Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="cont-box"):
            yield Static(f"[bold]Lote {self.batch_num} completado[/]")
            yield Static(f"Descargados: {fmt_count(self.ok_count)} archivos")
            yield Static("")
            yield Static("¿Continuar descargando más mensajes?")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Detener", id="stop", variant="error")
                yield Button("Continuar", id="continue", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "continue")
