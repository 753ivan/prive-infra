"""Собственный индекс лиц: бесплатный поиск клиентки по закрытому списку и публичным
источникам, без платных API и с лучшим охватом СНГ (каталоги анкет, Telegram).

Эмбеддинги SFace (128-мерные, L2-нормированные) хранятся в обычной таблице IndexedFace
как список float — портируемо (Postgres и SQLite в тестах), без pgvector. Поиск —
брутфорс-косинус на numpy: при тысячах лиц это миллисекунды. Если индекс дорастёт до
сотен тысяч — заменить _all()+search() на pgvector (ANN), интерфейс сохранив.
"""
from __future__ import annotations

import numpy as np
from sqlalchemy import func

from ..models import IndexedFace
from .base import client, log_call


def index_image(db, source: str, page_url: str, image_url: str, data: bytes) -> int:
    """Детектит лица на картинке и кладёт их эмбеддинги в индекс. Возвращает число новых лиц.
    Дедуп по image_url — повторно та же картинка не индексируется."""
    from . import face as face_svc
    if not image_url or db.query(IndexedFace.id).filter(IndexedFace.image_url == image_url).first():
        return 0
    n = 0
    for i, e in enumerate(face_svc.embeddings(data)):
        db.add(IndexedFace(source=source, page_url=page_url, image_url=image_url, face_idx=i,
                           embedding=[round(float(x), 6) for x in e]))
        n += 1
    if n:
        db.flush()  # чтобы дедуп по image_url видел строку в этой же сессии (autoflush off)
    return n


def index_url(db, source: str, page_url: str, max_images: int = 40) -> dict:
    """Качает картинки со страницы и индексирует лица на них."""
    from .images import page_images
    indexed, faces = 0, 0
    try:
        urls = page_images(page_url, max_images)
    except Exception as e:  # noqa: BLE001
        log_call("face_index", f"page_images {page_url}", False, repr(e))
        return {"images": 0, "faces": 0, "error": repr(e)}
    with client() as c:
        for iu in urls:
            if db.query(IndexedFace.id).filter(IndexedFace.image_url == iu).first():
                continue
            try:
                r = c.get(iu)
                if r.status_code != 200 or len(r.content) > 15_000_000:
                    continue
                k = index_image(db, source, page_url, iu, r.content)
                faces += k
                indexed += 1 if k else 0
            except Exception:  # noqa: BLE001
                continue
    log_call("face_index", f"index {page_url}", True, f"{indexed} img, {faces} faces")
    return {"images": indexed, "faces": faces}


def ingest_urls(source: str, urls: list[str]) -> dict:
    """Фоновая индексация списка страниц (каждая — в своей транзакции)."""
    from ..db import session_scope
    total = {"pages": 0, "images": 0, "faces": 0}
    for u in urls:
        with session_scope() as db:
            r = index_url(db, source, u)
        total["pages"] += 1
        total["images"] += r.get("images", 0)
        total["faces"] += r.get("faces", 0)
    return total


def _all(db):
    rows = db.query(IndexedFace).all()
    if not rows:
        return [], None
    return rows, np.asarray([r.embedding for r in rows], dtype=np.float32)


def search(db, query_embs, threshold: float | None = None, limit: int = 20) -> list[dict]:
    """Ищет лицо клиентки в индексе. query_embs — нормированные эмбеддинги эталонов.
    Возвращает совпадения {source, page_url, image_url, similarity} выше порога, по убыванию."""
    from . import face as face_svc
    if not query_embs:
        return []
    th = face_svc.SAME if threshold is None else threshold
    rows, mat = _all(db)
    if mat is None:
        return []
    q = np.asarray(query_embs, dtype=np.float32)
    best = (mat @ q.T).max(axis=1)  # лучшее сходство по всем эталонам для каждого индексного лица
    out = [{"source": r.source, "page_url": r.page_url, "image_url": r.image_url, "similarity": round(float(s), 3)}
           for r, s in zip(rows, best) if s >= th]
    out.sort(key=lambda x: -x["similarity"])
    # на одну страницу — одно (лучшее) совпадение
    seen, uniq = set(), []
    for m in out:
        if m["page_url"] in seen:
            continue
        seen.add(m["page_url"])
        uniq.append(m)
    return uniq[:limit]


def stats(db) -> dict:
    total = db.query(func.count(IndexedFace.id)).scalar() or 0
    by_src = db.query(IndexedFace.source, func.count(IndexedFace.id)).group_by(IndexedFace.source).all()
    return {"faces": int(total), "by_source": {k: int(v) for k, v in by_src}}
