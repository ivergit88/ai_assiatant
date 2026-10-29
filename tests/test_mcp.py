"""MCP-тулы так, как их увидит агент: через MCP-клиент, а не прямым вызовом функций."""

from datetime import datetime, timedelta

import pytest
from fastmcp import Client

from mcp_server.server import mcp

USER = "user-1"


@pytest.fixture
async def client(pool):
    async with Client(mcp) as c:
        yield c


def iso(dt: datetime) -> str:
    return dt.isoformat()


async def test_tools_are_listed(client):
    names = {t.name for t in await client.list_tools()}
    assert names == {
        "create_event",
        "get_events",
        "search_events",
        "update_event",
        "complete_event",
        "delete_event",
    }


async def test_create_and_get_today(client, tz):
    start = datetime.now(tz).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    created = await client.call_tool(
        "create_event",
        {
            "user_id": USER,
            "event_type": "meeting",
            "name": "Встреча с командой",
            "description": "Добавь встречу с командой через два часа",
            "start_at": iso(start),
        },
    )
    assert created.structured_content["name"] == "Встреча с командой"
    assert created.structured_content["weekday"]

    day_start = start.replace(hour=0)
    result = await client.call_tool(
        "get_events",
        {
            "user_id": USER,
            "start_time": iso(day_start),
            "end_time": iso(day_start + timedelta(days=2)),
        },
    )
    assert [e["name"] for e in result.structured_content["result"]] == ["Встреча с командой"]


async def test_meeting_without_time_is_rejected_with_reason(client):
    result = await client.call_tool(
        "create_event",
        {"user_id": USER, "event_type": "meeting", "name": "Встреча с Иваном"},
        raise_on_error=False,
    )
    assert result.is_error
    assert "нужно указать дату и время" in result.content[0].text


async def test_time_in_past_is_rejected(client, tz):
    result = await client.call_tool(
        "create_event",
        {
            "user_id": USER,
            "event_type": "reminder",
            "name": "Позвонить",
            "start_at": iso(datetime.now(tz) - timedelta(days=1)),
        },
        raise_on_error=False,
    )
    assert result.is_error and "уже прошло" in result.content[0].text


async def test_task_without_date_and_search(client):
    await client.call_tool(
        "create_event",
        {"user_id": USER, "event_type": "task", "name": "Подписать договор"},
    )
    result = await client.call_tool("search_events", {"user_id": USER, "query": "договор"})
    hits = result.structured_content["result"]
    assert hits[0]["name"] == "Подписать договор"
    assert hits[0]["start_at"] is None


async def test_update_event_reschedules_without_losing_name(client, tz):
    start = datetime.now(tz).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    created = await client.call_tool(
        "create_event",
        {"user_id": USER, "event_type": "task", "name": "Созвон", "start_at": iso(start)},
    )
    new_start = start + timedelta(hours=3)
    updated = await client.call_tool(
        "update_event",
        {"user_id": USER, "event_id": created.structured_content["id"], "start_at": iso(new_start)},
    )
    assert updated.structured_content["name"] == "Созвон"
    assert updated.structured_content["start_at"] == iso(new_start)


async def test_update_event_without_changes_is_rejected(client, tz):
    start = datetime.now(tz) + timedelta(hours=2)
    created = await client.call_tool(
        "create_event",
        {"user_id": USER, "event_type": "task", "name": "Задача", "start_at": iso(start)},
    )
    result = await client.call_tool(
        "update_event",
        {"user_id": USER, "event_id": created.structured_content["id"]},
        raise_on_error=False,
    )
    assert result.is_error
    assert "менять" in result.content[0].text


async def test_complete_event_marks_done(client, tz):
    start = datetime.now(tz) + timedelta(hours=1)
    created = await client.call_tool(
        "create_event",
        {"user_id": USER, "event_type": "task", "name": "Отчёт", "start_at": iso(start)},
    )
    completed = await client.call_tool(
        "complete_event", {"user_id": USER, "event_id": created.structured_content["id"]}
    )
    assert completed.structured_content["status"] == "done"


async def test_delete_event_hides_it_from_search(client):
    created = await client.call_tool(
        "create_event",
        {"user_id": USER, "event_type": "task", "name": "Черновик договора"},
    )
    deleted = await client.call_tool(
        "delete_event", {"user_id": USER, "event_id": created.structured_content["id"]}
    )
    assert deleted.structured_content["status"] == "cancelled"

    result = await client.call_tool("search_events", {"user_id": USER, "query": "договор"})
    assert result.structured_content["result"] == []


async def test_update_unknown_event_is_rejected(client, tz):
    result = await client.call_tool(
        "update_event",
        {
            "user_id": USER,
            "event_id": "00000000-0000-0000-0000-000000000000",
            "name": "Новое имя",
        },
        raise_on_error=False,
    )
    assert result.is_error and "не найдено" in result.content[0].text


async def test_bad_period_is_rejected(client, tz):
    now = datetime.now(tz)
    result = await client.call_tool(
        "get_events",
        {"user_id": USER, "start_time": iso(now), "end_time": iso(now)},
        raise_on_error=False,
    )
    assert result.is_error and "раньше" in result.content[0].text
