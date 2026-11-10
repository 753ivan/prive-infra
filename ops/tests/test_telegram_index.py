import uuid

import numpy as np

from app.pipeline import session_scope
from app.services import face as face_svc
from app.services import face_index
from app.services import telegram_index as tgidx


def _unit(idx):
    v = np.zeros(128, dtype=np.float32)
    v[idx] = 1.0
    return v


def test_not_available_without_keys(client, monkeypatch):
    # без TELEGRAM_API_* модуль инертен, даже если telethon установлен
    monkeypatch.setattr(tgidx.settings, "telegram_api_id", "")
    monkeypatch.setattr(tgidx.settings, "telegram_session", "")
    assert tgidx.available() is False
    assert tgidx._creds() is None


def test_channels_parsing(client, monkeypatch):
    monkeypatch.setattr(tgidx.settings, "telegram_index_channels", " @a, b ,, @c ")
    assert tgidx.channels() == ["a", "b", "c"]


def test_msg_url():
    assert tgidx._msg_url("@cat", 42) == "https://t.me/cat/42"


def test_index_channel_feeds_face_index(client, monkeypatch):
    tag = uuid.uuid4().hex[:8]  # изоляция: тестовая БД переживает прогоны
    emb = _unit(3)
    img = f"P{tag}".encode()
    monkeypatch.setattr(face_svc, "embeddings", lambda data: [emb] if data == img else [])

    url = f"https://t.me/{tag}/7"

    def fake_fetch(channel, limit):
        assert channel == tag and limit == 50
        return [(url, img), (url, img)]  # второй — дубль по image_url

    r = tgidx.index_channel(f"@{tag}", limit=50, _fetch=fake_fetch)
    assert r == {"channel": tag, "images": 1, "faces": 1}  # дубль отсеян

    with session_scope() as db:
        hits = face_index.search(db, [emb], threshold=0.9)
    assert any(h["image_url"] == url and h["source"] == "telegram" for h in hits)


def test_index_channel_fetch_error_is_contained(client):
    def boom(channel, limit):
        raise RuntimeError("network down")

    r = tgidx.index_channel("@x", _fetch=boom)
    assert r["images"] == 0 and r["faces"] == 0 and "error" in r
