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
