"""Правила автоклассификации находок: тип, риск, статус. Оператор только правит.

Источники сигналов: домен (закрытый список источников и справочник площадок), совпадение фото
(pHash exact / лицо), признаки на странице (ник, телефон, город, реальное имя), источник находки.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .search import domain_of

DATA = Path(__file__).resolve().parent.parent / "data"

TYPE_BY_SOURCE_TYPE = {"каталог анкет": "anketa", "клоны-каталоги": "fake", "доска объявлений": "anketa", "отзывы": "mention",
                       "сливы / копии анкет": "leak", "форум": "mention", "соцсеть": "fake", "платформа": "fake", "хостинг изображений": "leak"}
LEAK_WORDS = ("sliv", "слив", "leak", "porn", "xvideos", "pornhub", "xhamster", "telegra.ph", "nude", "onlyfans-leak", "mega.nz", "anon")
SOCIAL = ("instagram.com", "facebook.com", "vk.com", "ok.ru", "tiktok.com", "x.com", "twitter.com", "threads.net", "t.me")


@lru_cache(maxsize=1)
def _source_types() -> dict[str, str]:
    out = {}
    try:
        for s in json.loads((DATA / "sources.json").read_text(encoding="utf-8"))["sources"]:
            for dom in [s["domain"], *s.get("region_sites", [])]:
                if "*" not in dom:
                    out[dom.lower()] = s.get("type", "")
    except Exception:
        pass
    return out


def _source_type_for(domain: str) -> str:
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        t = _source_types().get(".".join(parts[i:]))
        if t:
            return t
    return ""


def classify(finding, verify: dict | None = None) -> dict:
    """→ {kind, risk, status, confidence, reasons[]}. Не понижает статус, выставленный оператором."""
    dom = (finding.domain or domain_of(finding.url)).lower()
    v = verify or {}
    reasons: list[str] = []
    photo_match = finding.match_type in ("exact", "similar") and (finding.confidence or 0) >= 0.45
    face_src = finding.source in ("facecheck", "search4faces", "pimeyes", "lenso", "camgirlfinder")
    src_type = _source_type_for(dom)
    low = finding.url.lower()

    # --- тип ---
    kind = finding.kind if finding.kind != "unknown" else "unknown"
    if any(w in low or w in dom for w in LEAK_WORDS):
        kind, _ = "leak", reasons.append("домен/URL похож на слив")
    elif src_type in TYPE_BY_SOURCE_TYPE:
        kind = TYPE_BY_SOURCE_TYPE[src_type]
        reasons.append(f"площадка из закрытого списка: {src_type}")
    elif any(dom == s or dom.endswith("." + s) for s in SOCIAL):
        kind = "fake" if (photo_match or face_src) else "mention"
        reasons.append("соцсеть" + (" + совпадение лица" if (photo_match or face_src) else ""))
    elif finding.source == "camgirlfinder":
        kind, _ = "fake", reasons.append("кам-сайт")
    elif v.get("nick_on_page") and v.get("city_on_page"):
        kind, _ = "anketa", reasons.append("ник и город на странице")
    elif kind == "unknown" and (photo_match or face_src):
        kind, _ = "anketa", reasons.append("её фото на неизвестной площадке")

    # --- уверенность, что это она ---
    conf = finding.confidence or 0.0
    if v.get("nick_on_page"):
        conf = max(conf, 0.6)
    if v.get("phone_on_page"):
        conf = max(conf, 0.85)
    if photo_match:
        conf = max(conf, finding.confidence or 0.7)
    if v.get("nick_on_page") and v.get("phone_on_page"):
        conf = max(conf, 0.95)

    # --- риск ---
    risk = 1
    if kind == "leak":
        risk = 3
    elif v.get("name_on_page") or (kind == "fake" and v.get("phone_on_page")):
        risk = 3
        reasons.append("связь рабочего с личным (имя/номер)")
    elif kind == "fake":
        risk = 2
    elif kind == "anketa":
        risk = 2
    elif kind == "mention" and (v.get("phone_on_page") or v.get("name_on_page")):
        risk = 2

    # --- статус ---
    status = finding.status
    if status == "new":
        if photo_match and (finding.confidence or 0) >= 0.85:
            status = "confirmed"
            reasons.append("точная копия эталона / лицо ≥0,85")
        elif v.get("nick_on_page") and v.get("phone_on_page"):
            status = "confirmed"
            reasons.append("ник и телефон на странице")
        elif v.get("http_status") in (404, 410):
            status = "ignored"
            reasons.append("страница уже недоступна")
        elif finding.query_type in ("nick", "phrase") and v.get("http_status") and not v.get("nick_on_page") and not photo_match:
            reasons.append("ник на странице не найден — вероятно, не её")
    return {"kind": kind, "risk": risk, "status": status, "confidence": round(conf, 2) if conf else None, "reasons": reasons}


# ---------------------------------------------------------------------------
# Стоимость внешних вызовов (USD) — для счётчика на заказ

COSTS = {"google_cse": 0.005, "brave": 0.005, "serpapi_yandex": 0.015, "serpapi_lens": 0.015, "facecheck": 0.30,
         "search4faces": 0.005, "hibp": 0.0, "dehashed": 0.002, "phash": 0.0, "verify": 0.0}
