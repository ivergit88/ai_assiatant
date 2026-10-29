from datetime import datetime, timedelta

import httpx
import pytest

from api.ics import build_ics
from api.main import app
from common.schemas import EventCreate, EventType

USER = "user-1"


@pytest.fixture
async def client(pool):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def seed(tz, type_=EventType.MEETING, name="Встреча с дизайнером", hours=26):
    start = datetime.now(tz).replace(minute=0, second=0, microsecond=0) + timedelta(hours=hours)
    data = EventCreate(user_id=USER, type=type_, name=name, start_at=start)
    return await app.state.repo.create(data)


async def test_list_events(client, tz):
    event = await seed(tz)
    now = datetime.now(tz)
    response = await client.get(
        "/v1/events",
        params={
            "user_id": USER,
            "from": now.isoformat(),
            "to": (now + timedelta(days=7)).isoformat(),
            "type": "meeting",
        },
    )
    assert response.status_code == 200
    assert [e["id"] for e in response.json()] == [str(event.id)]


async def test_list_rejects_reversed_period(client, tz):
    now = datetime.now(tz).isoformat()
    response = await client.get("/v1/events", params={"user_id": USER, "from": now, "to": now})
    assert response.status_code == 422


async def test_search(client, tz):
    await seed(tz, EventType.TASK, "Отправить отчёт")
    response = await client.get("/v1/events/search", params={"user_id": USER, "q": "отчет"})
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Отправить отчёт"


async def test_ics_download(client, tz):
    event = await seed(tz)
    response = await client.get(f"/v1/events/{event.id}/ics", params={"user_id": USER})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "SUMMARY:Встреча с дизайнером" in response.text


async def test_ics_other_user_gets_404(client, tz):
    event = await seed(tz)
    response = await client.get(f"/v1/events/{event.id}/ics", params={"user_id": "user-2"})
    assert response.status_code == 404


async def test_ics_undated_task_gets_409(client):
    event = await app.state.repo.create(
        EventCreate(user_id=USER, type=EventType.TASK, name="Купить билеты")
    )
    response = await client.get(f"/v1/events/{event.id}/ics", params={"user_id": USER})
    assert response.status_code == 409


def test_ics_format():
    from uuid import uuid4

    from common.schemas import Event

    start = datetime.fromisoformat("2026-10-30T10:00:00+03:00")
    event = Event(
        id=uuid4(),
        user_id=USER,
        type=EventType.MEETING,
        name="Встреча; с Иваном, срочно " + "очень длинное название " * 3,
        description="",
        start_at=start,
        status="active",
        created_at=start,
        updated_at=start,
    )
    text = build_ics(event, now=start)
    assert "DTSTART:20261030T070000Z\r\n" in text
    assert "DTEND:20261030T080000Z\r\n" in text
    assert "TRIGGER:-PT15M" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    unfolded = text.replace("\r\n ", "")
    assert "SUMMARY:Встреча\\; с Иваном\\, срочно" in unfolded
