"""Контракты между сервисами. Меняем только по договорённости в общем чате.

Кто что использует:
- MCP-сервер и HTTP API: EventCreate, Event, SearchHit.
- Бот (B) и агент (C): AgentRequest, AgentResponse.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from common.config import get_settings
from common.timeutils import WEEKDAYS_RU, ensure_aware


class EventType(StrEnum):
    TASK = "task"
    MEETING = "meeting"
    REMINDER = "reminder"


class EventStatus(StrEnum):
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"


# Встреча и напоминание без времени не имеют смысла: агент должен уточнить время.
TIMED_TYPES = frozenset({EventType.MEETING, EventType.REMINDER})


class EventCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    type: EventType
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    start_at: datetime | None = None

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("start_at")
    @classmethod
    def attach_timezone(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware(value, get_settings().tz)

    @model_validator(mode="after")
    def timed_types_need_start(self) -> "EventCreate":
        if self.type in TIMED_TYPES and self.start_at is None:
            raise ValueError(f"Для типа '{self.type}' нужно указать дату и время (start_at)")
        return self


class Event(BaseModel):
    id: UUID
    user_id: str
    type: EventType
    name: str
    description: str
    start_at: datetime | None
    status: EventStatus
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def weekday(self) -> str | None:
        """День недели словами: LLM нередко ошибается, вычисляя его сама."""
        return None if self.start_at is None else WEEKDAYS_RU[self.start_at.weekday()]


class SearchHit(Event):
    score: float = Field(description="Чем больше, тем релевантнее (Reciprocal Rank Fusion)")
    matched_by: list[str] = Field(description="Какие способы поиска нашли запись")


class AgentStatus(StrEnum):
    DONE = "done"
    NEED_CLARIFICATION = "need_clarification"
    ERROR = "error"


class AgentRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


class AgentResponse(BaseModel):
    reply_text: str
    status: AgentStatus
    events: list[Event] = []


class AgentResetRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
