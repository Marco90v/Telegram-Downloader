"""
Funciones Telegram de bajo nivel (sin estado).

Expone:
  - resolve_entity: chat ID → Entity
  - count_media: cuenta fotos y videos via SearchRequest
  - setup_output_dir: crea carpeta de salida y retorna (path, chat_key)
"""

from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.messages import SearchRequest
from telethon.tl.types import InputMessagesFilterPhotos, InputMessagesFilterVideo

from core.media import chat_folder_name


async def resolve_entity(client: TelegramClient, chat_id: int | str):
    """Resuelve chat ID → Entity. Prueba prefijo -100 si el numérico falla."""
    try:
        return await client.get_entity(chat_id)
    except Exception:
        if isinstance(chat_id, int) and chat_id < 0 and not str(chat_id).startswith("-100"):
            try:
                return await client.get_entity(int(f"-100{abs(chat_id)}"))
            except Exception:
                return None
        return None


async def count_media(client, entity):
    """Cuenta fotos y videos via SearchRequest server-side (sin descargar nada)."""
    try:
        fotos = await client(
            SearchRequest(
                peer=entity,
                q="",
                filter=InputMessagesFilterPhotos(),
                limit=1,
            )
        )
        videos = await client(
            SearchRequest(
                peer=entity,
                q="",
                filter=InputMessagesFilterVideo(),
                limit=1,
            )
        )
        total_fotos = getattr(fotos, "count", None)
        if total_fotos is None:
            total_fotos = getattr(fotos, "total", 0) or 0
        total_videos = getattr(videos, "count", None)
        if total_videos is None:
            total_videos = getattr(videos, "total", 0) or 0
        return total_fotos, total_videos
    except Exception:
        return None, None


async def setup_output_dir(client, entity, config: dict) -> tuple[Path, str]:
    """Crea carpeta de salida con subcarpetas por chat.

    Si el chat tiene un canal vinculado, usa estructura de dos niveles.
    Retorna (output_dir, chat_key).
    """
    folder = chat_folder_name(entity)
    parent_folder = None
    linked_id = getattr(entity, "linked_chat_id", None)
    if linked_id:
        try:
            parent_entity = await client.get_entity(linked_id)
            parent_folder = chat_folder_name(parent_entity)
        except Exception:
            pass
    if parent_folder and parent_folder != folder:
        output_dir = Path(config["OUTPUT_DIR"]) / parent_folder / folder
    else:
        output_dir = Path(config["OUTPUT_DIR"]) / folder
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir, folder or str(entity.id)
