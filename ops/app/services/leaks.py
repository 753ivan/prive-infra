"""Проверка утечек: Have I Been Pwned (почта), DeHashed (телефон, ник, почта)."""
from __future__ import annotations

from ..config import settings
from .base import NotConfigured, client, log_call


def hibp_breaches(email: str) -> list[dict]:
    if not settings.hibp_api_key:
        raise NotConfigured("hibp")
    with client() as c:
        r = c.get(f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
                  params={"truncateResponse": "false"},
                  headers={"hibp-api-key": settings.hibp_api_key, "user-agent": "PRIVE-ops"})
        if r.status_code == 404:
            log_call("hibp", f"breaches {email}", True, "0 breaches")
            return []
        r.raise_for_status()
        data = r.json()
    log_call("hibp", f"breaches {email}", True, f"{len(data)} breaches")
    return [{"url": f"https://haveibeenpwned.com/PwnedWebsites#{b['Name']}", "title": f"Утечка: {b['Title']} ({b.get('BreachDate','')})",
             "snippet": ", ".join(b.get("DataClasses", [])[:8]), "source": "hibp"} for b in data]


def dehashed(query_type: str, value: str) -> list[dict]:
    """DeHashed API v2: поиск по email / phone / username."""
    if not (settings.dehashed_email and settings.dehashed_api_key):
        raise NotConfigured("dehashed")
    field = {"email": "email", "phone": "phone", "nick": "username"}.get(query_type, "username")
    with client() as c:
        r = c.post("https://api.dehashed.com/v2/search",
                   json={"query": f"{field}:{value}", "size": 50},
                   headers={"DeHashed-Api-Key": settings.dehashed_api_key, "Content-Type": "application/json"})
        r.raise_for_status()
        entries = r.json().get("entries", [])
    log_call("dehashed", f"{field}:{value}", True, f"{len(entries)} entries")
    out = []
    for e in entries:
        db = e.get("database_name") or e.get("obtained_from") or "unknown"
        out.append({"url": f"dehashed://{db}", "title": f"База утечки: {db}",
                    "snippet": "поля: " + ", ".join(k for k in ("email", "username", "phone", "password", "hashed_password", "address") if e.get(k)),
                    "source": "dehashed"})
    return out


def status() -> dict:
    return {"hibp": bool(settings.hibp_api_key), "dehashed": bool(settings.dehashed_email and settings.dehashed_api_key)}
