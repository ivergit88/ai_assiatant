"""Весь SQL проекта в одном месте. MCP-сервер и HTTP API ходят в базу только через этот класс."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from common.embeddings import Embedder
from common.schemas import Event, EventCreate, EventStatus, EventType, SearchHit

_COLUMNS = "id, user_id, type, name, description, start_at, status, created_at, updated_at"

# Поиск: три независимых способа найти запись, результаты склеиваются через
# Reciprocal Rank Fusion: score = Σ 1 / (60 + место в выдаче способа).
# Почему RRF: оценки ts_rank, trigram и косинуса несравнимы между собой,
# а места в выдаче сравнимы. Константа 60 — стандартное значение из статьи про RRF.
#   text     — полнотекстовый поиск по-русски: «договору» найдёт «договор»;
#   fuzzy    — триграммы: «презинтация» найдёт «презентация»;
#   semantic — эмбеддинги GigaChat: «слайды» найдёт «презентацию».
# Слова запроса объединяем через ИЛИ (а не И, как plainto_tsquery), иначе
# «задачу про договор» не найдёт запись «Подписать договор» из-за слова «задача».
_SEARCH_SQL = f"""
WITH base AS (
    SELECT * FROM events
    WHERE user_id = $1
      AND status <> 'cancelled'
      AND ($3::timestamptz IS NULL OR start_at >= $3)
      AND ($4::timestamptz IS NULL OR start_at < $4)
),
q AS (
    SELECT replace(plainto_tsquery('russian', $2)::text, ' & ', ' | ')::tsquery AS tq
),
by_text AS (
    SELECT b.id, row_number() OVER (ORDER BY ts_rank_cd(b.search_tsv, q.tq) DESC) AS rn
    FROM base b, q
    WHERE numnode(q.tq) > 0 AND b.search_tsv @@ q.tq
    ORDER BY rn LIMIT 20
),
by_fuzzy AS (
    SELECT b.id, row_number() OVER (ORDER BY word_similarity($2, b.search_text) DESC) AS rn
    FROM base b
    WHERE word_similarity($2, b.search_text) >= 0.4
    ORDER BY rn LIMIT 20
),
by_meaning AS (
    SELECT b.id, row_number() OVER (ORDER BY b.embedding <=> $5::vector) AS rn
    FROM base b
    WHERE $5::vector IS NOT NULL AND b.embedding IS NOT NULL
    ORDER BY rn LIMIT 10
),
ranked AS (
    SELECT id, 'text' AS source, rn FROM by_text
    UNION ALL SELECT id, 'fuzzy', rn FROM by_fuzzy
    UNION ALL SELECT id, 'semantic', rn FROM by_meaning
),
scored AS (
    SELECT id,
           sum(1.0 / (60 + rn))::float8 AS score,
           array_agg(source ORDER BY source) AS matched_by
    FROM ranked
    GROUP BY id
)
SELECT {", ".join(f"e.{c}" for c in _COLUMNS.split(", "))}, s.score, s.matched_by
FROM scored s
JOIN events e ON e.id = s.id
ORDER BY s.score DESC, e.start_at DESC NULLS LAST
LIMIT $6
"""


class EventRepository:
    def __init__(self, pool: asyncpg.Pool, embedder: Embedder, tz: ZoneInfo) -> None:
        self._pool = pool
        self._embedder = embedder
        self._tz = tz

    async def create(self, data: EventCreate) -> Event:
        embedding = await self._embed_one(_embedding_text(data.type, data.name, data.description))
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO events (user_id, type, name, description, start_at, embedding)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {_COLUMNS}
            """,
            data.user_id,
            data.type.value,
            data.name,
            data.description,
            data.start_at,
            embedding,
        )
        return self._to_event(row)

    async def get(self, user_id: str, event_id: UUID) -> Event | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM events WHERE id = $1 AND user_id = $2",
            event_id,
            user_id,
        )
        return None if row is None else self._to_event(row)

    async def list_by_period(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        types: Sequence[EventType] | None = None,
        include_undated: bool = False,
    ) -> list[Event]:
        """События в полуинтервале [start, end): «сегодня» = [00:00 сегодня, 00:00 завтра).

        include_undated добавляет активные задачи без даты в конец списка.
        Отменённые события не возвращаются.
        """
        rows = await self._pool.fetch(
            f"""
            SELECT {_COLUMNS} FROM events
            WHERE user_id = $1
              AND status <> 'cancelled'
              AND ($4::text[] IS NULL OR type = ANY($4))
              AND (
                    (start_at >= $2 AND start_at < $3)
                 OR ($5 AND start_at IS NULL AND status = 'active')
              )
            ORDER BY start_at NULLS LAST, created_at
            """,
            user_id,
            start,
            end,
            [t.value for t in types] if types else None,
            include_undated,
        )
        return [self._to_event(r) for r in rows]

    async def search(
        self,
        user_id: str,
        query: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 5,
    ) -> list[SearchHit]:
        embedding = await self._embed_one(query)
        rows = await self._pool.fetch(_SEARCH_SQL, user_id, query, start, end, embedding, limit)
        return [
            SearchHit(
                **self._to_event(r).model_dump(exclude={"weekday"}),
                score=round(r["score"], 5),
                matched_by=list(r["matched_by"]),
            )
            for r in rows
        ]

    async def _embed_one(self, text: str) -> list[float] | None:
        vectors = await self._embedder.embed([text])
        return vectors[0] if vectors else None

    def _to_event(self, row: asyncpg.Record | dict[str, Any]) -> Event:
        start_at = row["start_at"]
        return Event(
            id=row["id"],
            user_id=row["user_id"],
            type=EventType(row["type"]),
            name=row["name"],
            description=row["description"],
            # База отдаёт UTC; наружу — время пользователя, чтобы агенту не пересчитывать.
            start_at=start_at.astimezone(self._tz) if start_at else None,
            status=EventStatus(row["status"]),
            created_at=row["created_at"].astimezone(self._tz),
            updated_at=row["updated_at"].astimezone(self._tz),
        )


_TYPE_LABELS = {
    EventType.TASK: "Задача",
    EventType.MEETING: "Встреча",
    EventType.REMINDER: "Напоминание",
}


def _embedding_text(event_type: EventType, name: str, description: str) -> str:
    text = f"{_TYPE_LABELS[event_type]}: {name}"
    return f"{text}. {description}" if description and description != name else text
