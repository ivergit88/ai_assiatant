"""Эмбеддинги для поиска по смыслу.

Если GigaChat недоступен, мы не роняем запрос: событие сохраняется без эмбеддинга,
а поиск работает по тексту. Для пользователя это лучше, чем ошибка.
"""

import logging
from typing import Protocol

from gigachat import GigaChat

from common.config import Settings

logger = logging.getLogger(__name__)

# Должно совпадать с vector(1024) в db/migrations/001_init.sql.
EMBEDDING_DIM = 1024


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]] | None: ...

    async def aclose(self) -> None: ...


class NullEmbedder:
    """Заглушка, когда ключа GigaChat нет: поиск по смыслу просто выключен."""

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None

    async def aclose(self) -> None:
        return None


class GigaChatEmbedder:
    def __init__(self, settings: Settings) -> None:
        self._model = settings.gigachat_embeddings_model
        self._client = GigaChat(
            credentials=settings.gigachat_credentials,
            scope=settings.gigachat_scope,
            verify_ssl_certs=settings.gigachat_verify_ssl,
            timeout=10,
        )

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        try:
            response = await self._client.aembeddings(texts=texts, model=self._model)
        except Exception:
            logger.warning("GigaChat embeddings недоступны, продолжаем без них", exc_info=True)
            return None

        vectors = [item.embedding for item in response.data]
        if any(len(v) != EMBEDDING_DIM for v in vectors):
            logger.error("Модель %s вернула не %d измерений", self._model, EMBEDDING_DIM)
            return None
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


def build_embedder(settings: Settings) -> Embedder:
    if not settings.gigachat_credentials:
        logger.info("GIGACHAT_CREDENTIALS не задан: поиск по смыслу выключен")
        return NullEmbedder()
    return GigaChatEmbedder(settings)
