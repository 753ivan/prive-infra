import uuid

import numpy as np

from app.pipeline import session_scope
from app.services import face as face_svc
from app.services import face_index


def _unit(idx):
    v = np.zeros(128, dtype=np.float32)
    v[idx] = 1.0
    return v


def test_index_search_and_dedup(client, monkeypatch):
    tag = uuid.uuid4().hex[:8]  # изоляция: тестовая БД переживает прогоны
    a, b = _unit(0), _unit(1)  # ортогональны
    ua, ub = f"http://i/{tag}-a.jpg", f"http://i/{tag}-b.jpg"
    fake = {f"A{tag}".encode(): [a], f"B{tag}".encode(): [b]}
    monkeypatch.setattr(face_svc, "embeddings", lambda data: fake.get(data, []))

    with session_scope() as db:
        assert face_index.index_image(db, tag, f"http://p/{tag}-a", ua, f"A{tag}".encode()) == 1
        assert face_index.index_image(db, tag, f"http://p/{tag}-b", ub, f"B{tag}".encode()) == 1
        assert face_index.index_image(db, tag, f"http://p/{tag}-a", ua, f"A{tag}".encode()) == 0  # дедуп по image_url

    with session_scope() as db:
        urls = [m["image_url"] for m in face_index.search(db, [a], threshold=0.9)]
        assert ua in urls           # совпало с a
        assert ub not in urls        # ортогональное b отсеклось


def test_search_empty_query(client):
    with session_scope() as db:
        assert face_index.search(db, []) == []


def test_stats_shape(client):
    with session_scope() as db:
        s = face_index.stats(db)
    assert "faces" in s and "by_source" in s and isinstance(s["faces"], int)
