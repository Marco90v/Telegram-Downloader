"""
Formateo con markup Textual para la TUI.

Usado por tui/helpers.py vía:
    from format.textual import ok as _ok, warn as _warn, err as _err, ...
"""


def ok(t: str) -> str:
    """Verde — éxito."""
    return f"[green]{t}[/]"


def warn(t: str) -> str:
    """Amarillo — advertencia."""
    return f"[yellow]{t}[/]"


def err(t: str) -> str:
    """Rojo — error."""
    return f"[red]{t}[/]"


def head(t: str) -> str:
    """Cian + bold — encabezado."""
    return f"[bold cyan]{t}[/]"


def esc(t: str | int) -> str:
    """Escapa '[' como '\\\\[' para evitar que RichLog lo interprete como markup."""
    return str(t).replace("[", "\\[")
