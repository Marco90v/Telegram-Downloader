"""
Helpers para determinar tipo, tamaño y path de archivos multimedia.

Expone:
  - format_size, fmt_count: formateo legible
  - media_ext, media_path, media_size, is_media_wanted, chat_folder_name
"""

from pathlib import Path


def format_size(bytes_: int) -> str:
    """Formatea bytes a unidad legible (KB / MB / GB)."""
    if bytes_ >= 1024**3:
        return f"{bytes_ / 1024**3:.2f} GB"
    if bytes_ >= 1024**2:
        return f"{bytes_ / 1024**2:.1f} MB"
    if bytes_ >= 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_} B"


def _mime_ext(mime: str) -> str:
    """Mapa mime → extensión."""
    m = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "video/x-matroska": ".mkv",
        "video/avi": ".avi",
    }
    return m.get(mime, ".bin")


def media_ext(msg) -> str:
    """Determina la extensión del archivo multimedia de un mensaje."""
    if msg.photo and getattr(msg.photo, "ext", None):
        return str(msg.photo.ext)
    if msg.video and getattr(msg.video, "ext", None):
        return str(msg.video.ext)
    doc = getattr(msg, "document", None)
    if doc and doc.mime_type:
        return _mime_ext(doc.mime_type)
    return ".jpg"


def media_path(msg, output_dir: Path) -> Path:
    """Path único y determinístico: YYYYMMDD_MessageID.ext."""
    fecha = msg.date.strftime("%Y%m%d") if msg.date else "00000000"
    return output_dir / f"{fecha}_{msg.id}{media_ext(msg)}"


def chat_folder_name(entity) -> str:
    """Nombre de carpeta legible y seguro desde la entidad del chat."""
    name = None
    if hasattr(entity, "title") and entity.title:
        name = entity.title
    elif hasattr(entity, "username") and entity.username:
        name = entity.username
    else:
        name = f"chat_{entity.id}" if hasattr(entity, "id") else "desconocido"
    safe = "".join(c if c.isalnum() or c in " _-." else "_" for c in name)
    return safe.strip().strip(".")[:60] or "telegram_chat"


def is_media_wanted(msg) -> bool:
    """Fotos y videos, nada más."""
    return bool(msg.photo) or bool(msg.video)


def media_size(msg) -> int | None:
    """Estima el tamaño en bytes del contenido multimedia sin descargarlo."""
    if msg.document:
        return msg.document.size
    if msg.photo and msg.photo.sizes:
        biggest = msg.photo.sizes[-1]
        if hasattr(biggest, "size"):
            return biggest.size
    return None


def fmt_count(n: int | None) -> str:
    """Formatea un contador o '?' si es None."""
    if n is None:
        return "?"
    return f"{n:,}"
