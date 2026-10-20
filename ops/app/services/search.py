"""M3 — сканер по тексту: Google Custom Search, Brave Search, SerpAPI (Yandex).

Каждый провайдер возвращает список словарей {url, title, snippet, source}.
Если ключ не задан — возвращается пустой список и NotConfigured пишется в лог.
"""
from __future__ import annotations

from urllib.parse import urlparse

from ..config import settings
from .base import NotConfigured, client, log_call


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def google_cse(query: str, num: int = 10) -> list[dict]:
    if not (settings.google_cse_key and settings.google_cse_cx):
        raise NotConfigured("google_cse")
    with client() as c:
        r = c.get("https://www.googleapis.com/customsearch/v1", params={
            "key": settings.google_cse_key, "cx": settings.google_cse_cx, "q": query, "num": min(num, 10)})
        r.raise_for_status()
        items = r.json().get("items", [])
    log_call("google_cse", f"search {query!r}", True, f"{len(items)} results")
    return [{"url": i["link"], "title": i.get("title", ""), "snippet": i.get("snippet", ""), "source": "google_cse"} for i in items]


def brave(query: str, num: int = 20) -> list[dict]:
    if not settings.brave_search_key:
        raise NotConfigured("brave")
    with client() as c:
        r = c.get("https://api.search.brave.com/res/v1/web/search",
                  params={"q": query, "count": min(num, 20), "safesearch": "off"},
                  headers={"X-Subscription-Token": settings.brave_search_key, "Accept": "application/json"})
        r.raise_for_status()
        items = r.json().get("web", {}).get("results", [])
    log_call("brave", f"search {query!r}", True, f"{len(items)} results")
    return [{"url": i["url"], "title": i.get("title", ""), "snippet": i.get("description", ""), "source": "brave"} for i in items]


def serpapi_yandex(query: str, num: int = 20) -> list[dict]:
    """Yandex через SerpAPI — для сайтов СНГ находит больше, чем Google."""
    if not settings.serpapi_key:
        raise NotConfigured("serpapi")
    with client() as c:
        r = c.get("https://serpapi.com/search.json", params={
            "engine": "yandex", "text": query, "api_key": settings.serpapi_key, "lang": "ru"})
        r.raise_for_status()
        items = r.json().get("organic_results", [])[:num]
    log_call("serpapi_yandex", f"search {query!r}", True, f"{len(items)} results")
    return [{"url": i["link"], "title": i.get("title", ""), "snippet": i.get("snippet", ""), "source": "serpapi_yandex"} for i in items]


def serpapi_google_lens(image_url: str) -> list[dict]:
    """Поиск по изображению (Google Lens через SerpAPI). image_url должен быть публично доступен."""
    if not settings.serpapi_key:
        raise NotConfigured("serpapi")
    with client() as c:
        r = c.get("https://serpapi.com/search.json", params={
            "engine": "google_lens", "url": image_url, "api_key": settings.serpapi_key})
        r.raise_for_status()
        items = r.json().get("visual_matches", [])
    log_call("serpapi_lens", "image search", True, f"{len(items)} matches")
    return [{"url": i["link"], "title": i.get("title", ""), "snippet": i.get("source", ""), "source": "serpapi_lens",
             "match_type": "similar"} for i in items]


PROVIDERS = {"google_cse": google_cse, "brave": brave, "serpapi_yandex": serpapi_yandex}


def status() -> dict:
    return {
        "google_cse": bool(settings.google_cse_key and settings.google_cse_cx),
        "brave": bool(settings.brave_search_key),
        "serpapi": bool(settings.serpapi_key),
    }


def build_queries(queries: dict) -> list[tuple[str, str, str]]:
    """Из полей заказа собирает (query_type, value, search_string).
    Ники — с вариантами написания (разделители, транслит), телефоны — во всех типичных форматах."""
    from .enrich import nick_variants, phone_variants
    out = []
    for nick in queries.get("nicks", []):
        nick = nick.strip()
        if not nick:
            continue
        variants = nick_variants(nick, 5)
        out.append(("nick", nick, " OR ".join(f'"{v}"' for v in variants)))
        out.append(("nick", nick, f'"{nick}" анкета OR эскорт OR escort OR индивидуалка OR массаж'))
    for phone in queries.get("phones", []):
        variants = phone_variants(phone)
        if len(variants) < 2:
            continue
        out.append(("phone", phone, " OR ".join(f'"{v}"' for v in variants[:6])))
    for phrase in queries.get("phrases", []):
        if phrase.strip():
            out.append(("phrase", phrase, f'"{phrase.strip()}"'))
    for email in queries.get("emails", []):
        if email.strip():
            out.append(("email", email, f'"{email.strip()}"'))
    return out


def _with_backoff(name: str, fn, search_string: str, tries: int = 3) -> list[dict]:
    import time
    import httpx
    delay = 1.5
    for attempt in range(tries):
        try:
            return fn(search_string)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            log_call(name, f"search {search_string!r}", False, f"HTTP {e.response.status_code}")
            return []
        except Exception as e:  # noqa: BLE001
            log_call(name, f"search {search_string!r}", False, repr(e))
            return []
    return []


def run_all(search_string: str) -> tuple[list[dict], list[str]]:
    """Прогоняет запрос по всем настроенным провайдерам параллельно, с backoff на 429/5xx."""
    from concurrent.futures import ThreadPoolExecutor
    configured = {n: f for n, f in PROVIDERS.items() if status().get(n.split("_")[0] if n.startswith("serpapi") else n)}
    missing = [n for n in PROVIDERS if n not in configured]
    if not configured:
        return [], missing
    results = []
    with ThreadPoolExecutor(max_workers=len(configured)) as ex:
        for res in ex.map(lambda kv: _with_backoff(kv[0], kv[1], search_string), configured.items()):
            results.extend(res)
    return results, missing
