import asyncpg
from pgvector.asyncpg import register_vector


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Пул соединений с поддержкой типа vector (списки float ↔ pgvector)."""
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=register_vector)
