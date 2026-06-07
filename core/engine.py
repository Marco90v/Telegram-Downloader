"""
DownloadEngine — orquestación de alto nivel.

Maneja conexión, estado y ciclo de fetch adaptativo.
"""

import asyncio
from datetime import timezone
from pathlib import Path

from telethon import TelegramClient, errors

from core.catalog import load_catalog, update_catalog
from core.media import is_media_wanted
from core.telegram import count_media, resolve_entity, setup_output_dir


class DownloadEngine:
    """Motor de descarga: maneja conexión, estado y ciclo de fetch.

    Uso:
        engine = DownloadEngine(config, settings)
        await engine.connect()
        info = await engine.prepare()
        # en un loop: engine.fetch_batch(...)
        await engine.disconnect()
    """

    def __init__(self, config: dict, settings: dict):
        self.config = config
        self.settings = settings
        self.client: TelegramClient | None = None
        self.entity = None
        self.chat_key: str | None = None
        self.output_dir: Path | None = None
        self.catalog: dict = {}

        # Contadores de sesión
        self.total_ok = 0
        self.total_dup = 0
        self.total_err = 0
        self.total_skip = 0
        self.total_bytes = 0
        self.session_min_id = float("inf")
        self.session_max_id = 0

    # ── Conexión ──

    async def connect(self):
        """Crea el TelegramClient y lo conecta."""
        self.client = TelegramClient(
            self.config["SESSION_NAME"],
            self.config["TELEGRAM_API_ID"],
            self.config["TELEGRAM_API_HASH"],
        )
        await self.client.start()
        return self.client

    async def disconnect(self):
        """Desconecta el client si está conectado."""
        if self.client:
            await self.client.disconnect()

    # ── Preparación ──

    async def prepare(self) -> dict:
        """Resuelve entidad, crea directorios, cuenta media, carga catálogo.

        Retorna dict con metadatos del chat:
            entity, chat_key, output_dir, chat_name,
            fotos, videos, has_catalog, resume_info
        """
        chat_val = self.config.get("TELEGRAM_TARGET_CHAT", "")
        if not chat_val:
            raise ValueError(
                "No hay chat configurado. Definí TELEGRAM_TARGET_CHAT en .env "
                "o desde la configuración de la TUI."
            )
        self.entity = await resolve_entity(self.client, chat_val)
        if self.entity is None:
            raise ValueError(
                f"No se pudo resolver el chat {chat_val}. ¿La sesión es miembro del chat?"
            )

        self.output_dir, self.chat_key = await setup_output_dir(
            self.client, self.entity, self.config
        )

        fotos, videos = await count_media(self.client, self.entity)
        self.catalog = load_catalog()
        prev = self.catalog.get("chats", {}).get(self.chat_key)

        return {
            "entity": self.entity,
            "chat_key": self.chat_key,
            "output_dir": self.output_dir,
            "chat_name": self._chat_folder_name(self.entity),
            "fotos": fotos,
            "videos": videos,
            "has_catalog": prev is not None,
            "resume_info": prev,
        }

    @staticmethod
    def _chat_folder_name(entity) -> str:
        """Nombre de carpeta legible y seguro desde la entidad del chat."""
        from core.media import chat_folder_name

        return chat_folder_name(entity)

    # ── Búsqueda adaptativa de mensajes ──

    async def fetch_batch(
        self,
        *,
        offset_id: int = 0,
        limit: int = 100,
        since=None,
        until=None,
        resume_newest: int | None = None,
        resume_oldest: int | None = None,
        resume_complete: bool = False,
    ) -> dict:
        """Busca un lote de mensajes multimedia con sub-lotes adaptativos.

        Retorna dict:
            media: list[Message] — multimedia listo para descargar
            reached_start: bool — True si tocó el límite 'since'
            next_offset: int — para la próxima iteración
            should_stop: bool — True si no hay más mensajes o se vació por resume
        """
        assert self.client is not None
        assert self.entity is not None

        media_found: list = []
        reached_start = False
        next_offset = offset_id

        while len(media_found) < limit:
            faltan = limit - len(media_found)
            pedir = min(100, faltan)

            kwargs: dict = dict(limit=pedir)
            if next_offset:
                kwargs["offset_id"] = next_offset
            if until:
                kwargs["offset_date"] = until

            try:
                mensajes = await self.client.get_messages(self.entity, **kwargs)
            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                continue
            except Exception:
                return {
                    "media": media_found,
                    "reached_start": False,
                    "next_offset": next_offset,
                    "should_stop": True,
                    "error": "Error al obtener mensajes",
                }

            if not mensajes:
                return {
                    "media": media_found,
                    "reached_start": reached_start,
                    "next_offset": next_offset,
                    "should_stop": len(media_found) == 0,
                    "error": None,
                }

            # Filtrar multimedia y fecha
            pendientes = []
            for m in mensajes:
                if since and m.date.replace(tzinfo=timezone.utc) < since:
                    reached_start = True
                    break
                if is_media_wanted(m):
                    pendientes.append(m)

            if not pendientes:
                next_offset = mensajes[-1].id
                if reached_start:
                    break
                continue

            # Filtrar por resume
            if resume_newest is not None and pendientes:
                before = len(pendientes)
                pendientes = [m for m in pendientes if not (resume_oldest <= m.id <= resume_newest)]
                if not pendientes and before > 0:
                    if resume_complete:
                        next_offset = resume_oldest
                        resume_newest = None
                        continue
                    else:
                        return {
                            "media": media_found,
                            "reached_start": False,
                            "next_offset": next_offset,
                            "should_stop": True,
                            "error": None,
                        }
                if pendientes and pendientes[-1].id < resume_oldest:
                    resume_newest = None  # ya pasamos la ventana de resume

            media_found.extend(pendientes)
            next_offset = mensajes[-1].id
            if reached_start:
                break

        return {
            "media": media_found,
            "reached_start": reached_start,
            "next_offset": next_offset,
            "should_stop": False,
            "error": None,
        }

    # ── Contadores globales (acumular resultados de download_one) ──

    def add_result(self, result: dict):
        """Acumula el resultado de un download_one en los totales de sesión."""
        s = result["status"]
        if s == "ok":
            self.total_ok += 1
            self.total_bytes += result.get("size", 0)
        elif s == "dup":
            self.total_dup += 1
        elif s == "skip":
            self.total_skip += 1
        elif s == "err":
            self.total_err += 1

    # ── Resumen de sesión ──

    @property
    def totals(self) -> dict:
        return {
            "ok": self.total_ok,
            "dup": self.total_dup,
            "err": self.total_err,
            "skip": self.total_skip,
            "bytes": self.total_bytes,
        }

    def finalize(self):
        """Guarda el catálogo con los resultados de esta sesión.

        Llama a actualizar catálogo y lo guarda. Debe llamarse al finalizar.
        """
        if self.session_max_id <= 0:
            return
        update_catalog(
            self.catalog,
            self.chat_key,
            int(self.session_min_id),
            self.session_max_id,
            self.total_ok + self.total_skip,
        )
