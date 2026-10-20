"""Поиск abuse-контакта для домена без записи в справочнике (карта 3, шаг 6; SOP-9, шаг 2).

Порядок: RDAP домена (abuse-контакт регистратора) → DNS → RDAP IP (abuse-контакт хостера)
→ признаки Cloudflare (тогда их форма). Всё через открытые RDAP-серверы, ключей не нужно.
"""
from __future__ import annotations

import re
import socket

from .base import client, log_call

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _rdap_emails(obj: dict, want_roles=("abuse",)) -> list[str]:
    """Вытаскивает email из vCard сущностей с нужными ролями (рекурсивно)."""
    out = []
    for ent in obj.get("entities", []) or []:
        roles = ent.get("roles", []) or []
        if any(r in roles for r in want_roles):
            for item in (ent.get("vcardArray", [None, []])[1] or []):
                if item and item[0] == "email" and isinstance(item[3], str):
                    out.append(item[3].lower())
        out += _rdap_emails(ent, want_roles)
    return out


def rdap_domain(domain: str) -> dict:
    with client() as c:
        r = c.get(f"https://rdap.org/domain/{domain}", headers={"Accept": "application/rdap+json"})
        if r.status_code != 200:
            return {}
        return r.json()


def rdap_ip(ip: str) -> dict:
    with client() as c:
        r = c.get(f"https://rdap.org/ip/{ip}", headers={"Accept": "application/rdap+json"})
        if r.status_code != 200:
            return {}
        return r.json()


def lookup(domain: str) -> dict:
    """Возвращает {registrar_abuse, hoster_abuse, ip, nameservers, cloudflare, suggested_to, notes}."""
    domain = domain.lower().lstrip("www.")
    res = {"domain": domain, "registrar_abuse": [], "hoster_abuse": [], "ip": "", "nameservers": [], "cloudflare": False,
           "suggested_to": "", "suggested_channel": "email", "notes": []}
    try:
        d = rdap_domain(domain)
        if not d and domain.count(".") > 1:  # поддомен → родитель
            d = rdap_domain(".".join(domain.split(".")[-2:]))
        res["registrar_abuse"] = sorted(set(_rdap_emails(d)))
        res["nameservers"] = [n.get("ldhName", "").lower() for n in d.get("nameservers", []) or []]
        if not res["registrar_abuse"]:
            # некоторые регистраторы кладут abuse-почту в remarks
            text = str(d.get("remarks", "")) + str(d.get("notices", ""))
            res["registrar_abuse"] = sorted({m.lower() for m in EMAIL_RE.findall(text) if "abuse" in m.lower()})
    except Exception as e:  # noqa: BLE001
        res["notes"].append(f"RDAP домена: {e!r}")
    try:
        ip = socket.gethostbyname(domain)
        res["ip"] = ip
        ipd = rdap_ip(ip)
        res["hoster_abuse"] = sorted(set(_rdap_emails(ipd)))
        name = (ipd.get("name", "") + " " + str(ipd.get("remarks", ""))).lower()
        if "cloudflare" in name or any("cloudflare" in ns for ns in res["nameservers"]):
            res["cloudflare"] = True
    except Exception as e:  # noqa: BLE001
        res["notes"].append(f"RDAP IP: {e!r}")
    if res["cloudflare"]:
        res["suggested_to"], res["suggested_channel"] = "https://abuse.cloudflare.com/", "form"
        res["notes"].append("За Cloudflare: жалоба через их форму (DMCA), они пересылают хостеру. Параллельно — регистратору.")
    elif res["hoster_abuse"]:
        res["suggested_to"] = res["hoster_abuse"][0]
    elif res["registrar_abuse"]:
        res["suggested_to"] = res["registrar_abuse"][0]
        res["notes"].append("Хостер не определён — пишем регистратору.")
    log_call("rdap", domain, bool(res["suggested_to"]), res["suggested_to"] or "контакт не найден")
    return res
