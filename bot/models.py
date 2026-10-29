"""Strict shared Agent API schema (copy into common/ only via its owner)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentMessageRequest(BaseModel):
    user_id: str
    text: str = Field(min_length=1)


class AgentMessageResponse(BaseModel):
    reply_text: str
    status: Literal["done", "need_clarification", "error"]
    events: list[dict[str, Any]] = Field(default_factory=list)
