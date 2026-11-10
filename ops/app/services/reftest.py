"""Эталонный тест: по размеченному набору меряет полноту и ложные совпадения по лицу
и подбирает порог SAME.

Набор (spec):
  {"refs": ["/путь/эталон1.jpg", ...],          # фото тестовой персоны (с согласием) или синтетика
   "positives": ["https://...", ...],           # страницы, где персона ЕСТЬ (должны найтись)
   "negatives": ["https://...", ...]}            # страницы с чужими лицами (не должны совпадать)

Полнота = доля positives с similarity ≥ порога; FPR = доля negatives с similarity ≥ порога.
Цель: полнота ≥ 0.8, FPR ≤ 0.25 (из плана этапа 1). similarity берётся через тот же
SFace, что и в бою (face.match_page_faces), поэтому найденный порог применим и к индексу лиц.
"""
from __future__ import annotations

from pathlib import Path

RECALL_TARGET = 0.8
FPR_TARGET = 0.25


def _ref_embeddings(ref_paths):
    from . import face
    embs = []
    for p in ref_paths:
        embs.extend(face.embeddings(Path(p).read_bytes()))
    return embs


def similarities(spec: dict) -> dict:
    """{url: лучшее сходство персоны со страницей} по positives+negatives (через face.match_page_faces)."""
    from . import face
    refs = _ref_embeddings(spec["refs"])
    urls = list(spec.get("positives", [])) + list(spec.get("negatives", []))
    return {u: face.match_page_faces(u, refs)["similarity"] for u in urls}


def evaluate(spec: dict, thresholds: list[float] | None = None) -> dict:
    """Считает полноту/FPR по порогам и рекомендует порог. spec может нести готовый _sims
    (например, из прошлого прогона), тогда сеть/модели не трогаются."""
    sims = spec.get("_sims") or similarities(spec)
    pos = [sims.get(u, -1.0) for u in spec.get("positives", [])]
    neg = [sims.get(u, -1.0) for u in spec.get("negatives", [])]
    ths = thresholds or [round(x / 100, 2) for x in range(20, 61, 2)]  # 0.20 … 0.60
    rows = []
    for th in ths:
        recall = (sum(1 for s in pos if s >= th) / len(pos)) if pos else 0.0
        fpr = (sum(1 for s in neg if s >= th) / len(neg)) if neg else 0.0
        rows.append({"threshold": th, "recall": round(recall, 3), "fpr": round(fpr, 3)})
    # рекомендация: среди порогов с FPR ≤ цели — максимальная полнота, при равенстве ниже FPR и выше порог
    ok = [r for r in rows if r["fpr"] <= FPR_TARGET]
    best = max(ok, key=lambda r: (r["recall"], -r["fpr"], r["threshold"])) if ok else None
    return {"rows": rows, "recommended": best, "positives": len(pos), "negatives": len(neg),
            "targets": {"recall": RECALL_TARGET, "fpr": FPR_TARGET}}
