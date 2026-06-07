"""
Pantalla de gestión del catálogo: lista los chats y permite borrarlos.
"""

import re
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static, Switch

from core import list_catalog, remove_catalog_entry


class ConfirmDelete(Screen[bool]):
    """Modal de confirmación para borrar una entrada del catálogo."""

    def __init__(self, chat_name: str, also_files: bool) -> None:
        super().__init__()
        self.chat_name = chat_name
        self.also_files = also_files

    CSS = """
    ConfirmDelete {
        align: center middle;
    }
    #confirm-box {
        width: 50;
        height: auto;
        border: thick $error;
        padding: 1 2;
        background: $surface;
    }
    #confirm-box Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(f"[bold]¿Borrar '{self.chat_name}' del catálogo?[/]")
            if self.also_files:
                yield Static("[red]La carpeta de descargas también se eliminará.[/]")
            yield Static("[dim]Esta acción no se puede deshacer.[/]")
            with Horizontal(classes="confirm-buttons"):
                yield Button("Cancelar", id="cancel", variant="primary")
                yield Button("Borrar", id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


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
        padding: 0 1;
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
    .catalog-actions {
        height: 3;
        align: center middle;
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
        self._chat_map: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="catalog-box"):
            yield Static("[bold cyan]Catálogo de descargas[/]", id="catalog-title")
            catalog = list_catalog()
            chats = catalog.get("chats", {})
            if not chats:
                yield Static("[yellow]No hay chats en el catálogo.[/]")
            else:
                self._chat_map.clear()
                for name in sorted(chats.keys()):
                    info = chats[name]
                    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
                    self._chat_map[safe] = name
                    with Vertical(classes="catalog-entry"):
                        yield Static(name, classes="catalog-name")
                        yield Static(
                            f"  Procesados: {info.get('total_count', '?')}  "
                            f"({info.get('oldest_id', '?')}→{info.get('newest_id', '?')})  "
                            f"Última descarga: {info.get('last_date', '?')}",
                            classes="catalog-info",
                        )
                        with Horizontal(classes="catalog-actions"):
                            yield Label("Borrar carpeta:")
                            yield Switch(id=f"del-files-{safe}", value=False)
                            yield Button("🗑  Borrar", id=f"del-{safe}", variant="error")
        with Horizontal(id="catalog-controls"):
            yield Button("Volver", id="btn-back", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid == "btn-back":
            self.app.pop_screen()
        elif bid.startswith("del-"):
            safe = bid[4:]
            name = self._chat_map.get(safe, safe)

            try:
                switch = self.query_one(f"#del-files-{safe}", Switch)
                delete_files = switch.value
            except Exception:
                delete_files = False

            confirm = ConfirmDelete(name, delete_files)
            self.app.push_screen(
                confirm, lambda ok, n=name, df=delete_files: self._on_delete_confirm(ok, n, df)
            )

    def _on_delete_confirm(self, ok: bool, name: str, delete_files: bool) -> None:
        if not ok:
            return

        output_dir = Path(self.app.config["OUTPUT_DIR"])
        remove_catalog_entry(name, output_dir, delete_files)

        # Reemplazar este CatalogScreen con uno fresco
        self.app.pop_screen()
        self.app.push_screen(CatalogScreen())
