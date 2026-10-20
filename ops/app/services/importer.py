"""Импорт ручных находок (PimEyes, FaceCheck, Google Lens, Yandex Images) из CSV по шаблону SOP-1.

Колонки: source,query_type,query_value,url,match_type,confidence,screenshot_file,found_at,operator
Лишние колонки игнорируются, порядок не важен. Дубли по URL внутри заказа не создаются.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Finding, Order
from .search import domain_of

REQUIRED = {"source", "url"}


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows = []
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not REQUIRED.issubset(row) or not row.get("url"):
            continue
        rows.append(row)
    return rows


def import_rows(db: Session, order: Order, rows: list[dict], operator: str = "") -> dict:
    existing = {f.url for f in order.findings}
    added, skipped = 0, 0
    for row in rows:
        if row["url"] in existing:
            skipped += 1
            continue
        conf = None
        try:
            conf = float(row.get("confidence") or "") if row.get("confidence") else None
        except ValueError:
            conf = None
        found_at = datetime.utcnow()
        if row.get("found_at"):
            for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    found_at = datetime.strptime(row["found_at"], fmt)
                    break
                except ValueError:
                    continue
        src = row["source"].lower().replace(" ", "_")
        f = Finding(
            order_id=order.id, source=src, query_type=row.get("query_type") or "face",
            query_value=row.get("query_value", ""), url=row["url"], domain=domain_of(row["url"]),
            match_type=row.get("match_type") or "similar", confidence=conf,
            screenshot_file=row.get("screenshot_file", ""), found_at=found_at,
            operator=row.get("operator") or operator, kind="unknown",
        )
        db.add(f)
        existing.add(row["url"])
        added += 1
    db.flush()
    return {"added": added, "skipped_duplicates": skipped}
