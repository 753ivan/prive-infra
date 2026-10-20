"""M3 — проход по «закрытому списку источников» (sources.json).

Для каждого включённого источника строится запрос с ограничением по домену
(site:домен "ник") и прогоняется через настроенные поисковики. Семейства клонов
(домен с «*») ищутся по названию без site:. Так покрываются сайты, которые
общий поиск по нику хоронит на 5-й странице. Отдельные скрейперы под конкретные
сайты добавляются сюда же как method=url_search с шаблоном search_url.
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import client, log_call
from .search import domain_of, run_all

SOURCES_FILE = Path(__file__).resolve().parent.parent / "data" / "sources.json"


def load_sources() -> list[dict]:
    return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))["sources"]


def save_sources(sources: list[dict]):
    data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    data["sources"] = sources
    SOURCES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def source_queries(src: dict, values: list[tuple[str, str]], city: str = "") -> list[tuple[str, str, str]]:
    """(query_type, value, search_string) для одного источника."""
    out = []
    domains = [src["domain"], *src.get("region_sites", [])]
    for qtype, value in values:
        for dom in domains:
            if "*" in dom:
                name = dom.replace("*", "").strip(".-")
                out.append((qtype, value, f'{name} {city} "{value}"'.replace("  ", " ")))
            else:
                out.append((qtype, value, f'site:{dom} "{value}"'))
    return out


def crawl(values: list[tuple[str, str]], city: str = "", only_enabled: bool = True) -> tuple[list[dict], list[str]]:
    """Прогоняет ники/телефоны по всем источникам. Возвращает (находки, ненастроенные провайдеры)."""
    results, missing, seen = [], set(), set()
    for src in load_sources():
        if only_enabled and not src.get("enabled"):
            continue
        if src.get("method") == "url_search" and src.get("search_url"):
            for qtype, value in values:
                try:
                    with client() as c:
                        r = c.get(src["search_url"].format(q=value))
                        hit = value.lower() in r.text.lower()
                    log_call("crawl", f"{src['domain']} {value}", True, "hit" if hit else "no")
                    if hit:
                        url = src["search_url"].format(q=value)
                        if url not in seen:
                            seen.add(url)
                            results.append({"url": url, "title": f"{src['name']}: внутренний поиск нашёл «{value}»", "snippet": "проверить вручную", "source": "crawl", "query_type": qtype, "query_value": value, "match_type": "text", "kind": "anketa"})
                except Exception as e:  # noqa: BLE001
                    log_call("crawl", f"{src['domain']} {value}", False, repr(e))
            continue
        if src.get("method") != "site_search":
            continue
        for qtype, value, qs in source_queries(src, values, city):
            hits, miss = run_all(qs)
            missing.update(miss)
            for h in hits:
                if h["url"] in seen:
                    continue
                # для site:-запросов отбрасываем результаты с чужих доменов
                dom = src["domain"].lstrip("*.").rstrip(".*")
                if "*" not in src["domain"] and dom not in domain_of(h["url"]):
                    continue
                seen.add(h["url"])
                results.append({**h, "source": "crawl:" + src["domain"], "query_type": qtype, "query_value": value, "match_type": "text",
                                "kind": {"каталог анкет": "anketa", "клоны-каталоги": "fake", "доска объявлений": "anketa", "отзывы": "mention",
                                         "сливы / копии анкет": "leak", "форум": "mention"}.get(src.get("type"), "unknown")})
    return results, sorted(missing)
