"""Открывает все GET-маршруты панели без параметров пути: ловит ошибки SQL/шаблонов,
которые проявляются только на конкретной СУБД (прогонять и с TEST_DATABASE_URL на PostgreSQL)."""
from app.main import app


def test_all_get_routes(client):
    # немного данных, чтобы запросы с фильтрами реально выполнялись
    client.post("/api/scheduler/tick")
    failed = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if "GET" not in getattr(r, "methods", set()) or "{" in path:
            continue
        if path.startswith(("/docs", "/redoc", "/openapi")):
            continue
        resp = client.get(path)
        if resp.status_code >= 500:
            failed.append((path, resp.status_code, resp.text[:200]))
    assert not failed, failed


def test_public_contact(client):
    from unittest import mock
    from app.services import telegram as tg
    with mock.patch.object(tg, "alert_operators", lambda text: True):
        r = client.post("/public/contact", json={"contact": "@test_user", "message": "Помогите, пожалуйста", "topic": "sos"})
        assert r.status_code == 200 and r.json()["ok"]
        assert client.post("/public/contact", json={"contact": "x", "message": "hi"}).status_code == 422
        assert client.post("/public/contact", json={"contact": "@bot", "message": "spam spam", "website": "x"}).json()["ok"]
