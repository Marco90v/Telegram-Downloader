"""
Formateo ANSI para salida de terminal (CLI).

Usado por descarga.py vía:
    from format.ansi import ok as _ok, warn as _warn, err as _err, ...
"""

import shutil


class _c:
    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YEL = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    MAG = "\033[35m"


def ok(t: str) -> str:
    return f"{_c.GREEN}{t}{_c.RST}"


def warn(t: str) -> str:
    return f"{_c.YEL}{t}{_c.RST}"


def err(t: str) -> str:
    return f"{_c.RED}{t}{_c.RST}"


def head(t: str) -> str:
    return f"{_c.CYAN}{_c.BOLD}{t}{_c.RST}"


def clear_line() -> str:
    """Prefijo que borra toda la línea actual de la terminal."""
    cols = shutil.get_terminal_size().columns
    return f"\r{' ' * cols}\r"
