"""Цикл агента: LLM предлагает вызвать тул → выполняем → отдаём результат → … → ответ.

Один запрос пользователя = до agent_max_steps раундов. Обычно хватает двух:
вызвать тул и сформулировать ответ.
"""

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI

from agent.memory import ConversationMemory, HistoryMessage
from agent.prompt import build_system_prompt
from agent.tools import McpToolbox, ToolResult
from common.schemas import AgentResponse, AgentStatus, Event

logger = logging.getLogger(__name__)

FALLBACK_ERROR = "Не получилось обработать запрос. Попробуйте ещё раз чуть позже."
TOO_MANY_STEPS = "Запутался в этом запросе. Попробуйте сформулировать проще."


class Agent:
    def __init__(
        self,
        llm: AsyncOpenAI,
        model: str,
        toolbox: McpToolbox,
        memory: ConversationMemory,
        tz: ZoneInfo,
        max_steps: int = 6,
    ) -> None:
        self._llm = llm
        self._model = model
        self._toolbox = toolbox
        self._memory = memory
        self._tz = tz
        self._max_steps = max_steps

    async def handle(self, user_id: str, text: str, now: datetime | None = None) -> AgentResponse:
        now = now or datetime.now(self._tz)
        history = await self._memory.load(user_id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(now)},
            *history,
            {"role": "user", "content": text},
        ]

        try:
            response = await self._run(user_id, messages)
        except Exception:  # граница сервиса: бот должен получить ответ, а не 500
            logger.exception("LLM или MCP недоступны")
            return AgentResponse(reply_text=FALLBACK_ERROR, status=AgentStatus.ERROR)

        # В историю пишем только завершённые обмены: после ошибки повтор должен
        # начинаться с чистого листа, а не с половины старого запроса.
        if response.status != AgentStatus.ERROR:
            await self._memory.append(
                user_id,
                HistoryMessage(role="user", content=text),
                HistoryMessage(role="assistant", content=_with_event_refs(response)),
            )
        return response

    async def reset(self, user_id: str) -> None:
        await self._memory.reset(user_id)

    async def _run(self, user_id: str, messages: list[dict[str, Any]]) -> AgentResponse:
        tools = await self._toolbox.openai_tools()
        events: dict[str, Event] = {}

        for _ in range(self._max_steps):
            completion = await self._llm.chat.completions.create(
                model=self._model, messages=messages, tools=tools, temperature=0.1
            )
            message = completion.choices[0].message

            if not message.tool_calls:
                reply = (message.content or "").strip() or FALLBACK_ERROR
                return AgentResponse(
                    reply_text=reply, status=AgentStatus.DONE, events=list(events.values())
                )

            # content: "" вместо отсутствующего поля — не все OpenAI-совместимые API принимают null.
            messages.append(
                {**message.model_dump(exclude_none=True), "content": message.content or ""}
            )
            for call in message.tool_calls:
                name = call.function.name
                args = _parse_args(call.function.arguments)
                logger.info("tool call %s %s", name, args)

                if name == "ask_user":
                    question = (
                        str((args or {}).get("question") or "").strip() or "Уточните, пожалуйста."
                    )
                    return AgentResponse(
                        reply_text=question,
                        status=AgentStatus.NEED_CLARIFICATION,
                        events=list(events.values()),
                    )

                result = (
                    await self._toolbox.call(user_id, name, args)
                    if args is not None
                    else ToolResult(ok=False, payload="Аргументы не являются корректным JSON")
                )
                for raw in result.events:
                    event = Event.model_validate(raw)
                    events[str(event.id)] = event
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result.for_llm()}
                )

        logger.warning("agent exceeded %d steps", self._max_steps)
        return AgentResponse(reply_text=TOO_MANY_STEPS, status=AgentStatus.ERROR)


def _parse_args(raw: str | None) -> dict[str, Any] | None:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _with_event_refs(response: AgentResponse) -> str:
    """Ответ + скрытая сводка событий: чтобы следующий запрос («перенеси её на завтра»)
    знал, о какой записи речь. Пользователь эту сводку не видит — только модель в истории.
    """
    if not response.events:
        return response.reply_text
    refs = "; ".join(
        f"id={e.id}, {e.type}, «{e.name}», {e.start_at.isoformat() if e.start_at else 'без даты'}"
        for e in response.events[:10]
    )
    return f"{response.reply_text}\n[записи в этом ответе: {refs}]"
