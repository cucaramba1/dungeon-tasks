from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from dungeon.app import App
from dungeon.db import DB, iso
from dungeon.web import create_app

TZ = timezone(timedelta(hours=7))


def make():
    app = App(DB(":memory:"))
    start = datetime.now(TZ) + timedelta(days=2)
    eid = app.db.x("INSERT INTO events_feed(key, chat_id, channel, msg_id, link, starts_at, has_time, title, text, "
                   "kind, status, ritual, extra, created_at) VALUES('k',-3,'c',1,'https://t.me/x/1',?,1,'Встреча',"
                   "'t','announce','new','Цели и привычки','{}',?)", (iso(start), iso(start)))
    return TestClient(create_app(app)), app, eid


def test_state_lists_rituals_and_accept_creates_task():
    client, app, eid = make()
    items = client.get("/api/state").json()["rituals"]
    assert [i["id"] for i in items] == [eid] and items[0]["status"] == "new"
    r = client.post(f"/api/rituals/{eid}/accept")
    assert r.status_code == 200 and "Встреча" in r.json()["say"]
    assert client.get("/api/state").json()["rituals"][0]["status"] == "accepted"
    assert any(t["title"] == "Встреча" for t in client.get("/api/state").json()["open"])


def test_accept_unknown_event_is_404():
    client, _, _ = make()
    r = client.post("/api/rituals/999/accept")
    assert r.status_code == 404 and r.json()["detail"] == "нет такого анонса"
