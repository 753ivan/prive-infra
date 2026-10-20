"""Обогащение и верификация находок (шаг между сканом и разбором оператором).

1. Варианты написания ника и телефона — для запросов.
2. Каноникализация URL — для дедупа.
3. Верификация страницы: скачать, проверить, что ник/телефон/город реально на ней,
   вытащить title, og:image, картинки для face/pHash-сверки, текст для OCR-подобных признаков.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .base import client, log_call

# ---------------------------------------------------------------------------
# 1. Варианты ника и телефона

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}
_REV = {"zh": "ж", "kh": "х", "ts": "ц", "ch": "ч", "sh": "ш", "sch": "щ", "yu": "ю", "ya": "я", "ye": "е", "yi": "ї",
        "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з", "i": "и", "y": "й", "k": "к", "l": "л", "m": "м",
        "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т", "u": "у", "f": "ф", "h": "х", "c": "к", "x": "кс", "w": "в", "q": "к", "j": "дж"}


def translit(s: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in s.lower())


def detranslit(s: str) -> str:
    out, i, s = "", 0, s.lower()
    while i < len(s):
        for ln in (3, 2, 1):
            chunk = s[i:i + ln]
            if chunk in _REV:
                out += _REV[chunk]
                i += ln
                break
        else:
            out += s[i]
            i += 1
    return out


def nick_variants(nick: str, max_variants: int = 8) -> list[str]:
    """Варианты написания: разделители, регистр, транслит в обе стороны, без цифр на конце."""
    n = nick.strip()
    if not n:
        return []
    base = {n}
    core = re.sub(r"[_\-\.\s]+", " ", n).strip()
    base.update({core, core.replace(" ", ""), core.replace(" ", "_"), core.replace(" ", "-")})
    stripped = re.sub(r"\d+$", "", core).strip()
    if stripped and stripped != core and len(stripped) >= 4:
        base.add(stripped)
    if re.search(r"[а-яёіїє]", n, re.I):
        base.add(translit(core))
        base.add(translit(core).replace(" ", ""))
    elif re.fullmatch(r"[a-z0-9 _\-\.]+", core, re.I):
        base.add(detranslit(core))
    out = []
    for v in base:
        v = v.strip()
        if len(v) >= 3 and v.lower() not in {x.lower() for x in out}:
            out.append(v)
    out.sort(key=lambda v: (v != n, len(v)))
    return out[:max_variants]


def phone_variants(phone: str) -> list[str]:
    d = "".join(ch for ch in phone if ch.isdigit())
    if len(d) < 9:
        return [phone.strip()] if phone.strip() else []
    out = {phone.strip(), d, "+" + d, d[-10:], d[-9:]}
    if len(d) >= 12:  # +380 XX XXX XX XX / +7 XXX XXX XX XX
        cc, rest = d[:-10], d[-10:]
        out.add(f"+{cc} {rest[:3]} {rest[3:6]} {rest[6:8]} {rest[8:]}")
        out.add(f"+{cc} ({rest[:3]}) {rest[3:6]}-{rest[6:8]}-{rest[8:]}")
        out.add(f"{rest[:3]}-{rest[3:6]}-{rest[6:8]}-{rest[8:]}")
        out.add(f"0{rest}" if cc in ("380", "7") else rest)
    return [v for v in out if v]


# ---------------------------------------------------------------------------
# 2. Каноникализация URL

TRACKING = ("utm_", "fbclid", "gclid", "yclid", "ref", "ref_src", "igshid", "_ga", "mc_cid", "mc_eid", "si", "feature")


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
        scheme = "https"
        host = p.netloc.lower()
        for pre in ("www.", "m.", "mobile.", "amp."):
            if host.startswith(pre):
                host = host[len(pre):]
        host = host.split(":")[0] if host.endswith((":80", ":443")) else host
        path = re.sub(r"/+$", "", p.path) or "/"
        path = re.sub(r"/amp$", "", path)
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if not k.lower().startswith(TRACKING)]
        q.sort()
        return urlunparse((scheme, host, path, "", urlencode(q), ""))
    except Exception:
        return url.strip()


# ---------------------------------------------------------------------------
# 3. Верификация страницы

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
OG_IMG_RE = re.compile(r"""<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']""", re.I)
TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.I | re.S)
PHONE_IN_TEXT = re.compile(r"\+?\d[\d\s\-\(\)]{8,}\d")


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-\.]+", "", s.lower())


def verify_page(url: str, nicks: list[str], phones: list[str], city: str = "", real_name: str = "") -> dict:
    """Возвращает признаки: nick_on_page, phone_on_page, city_on_page, name_on_page, title, og_image, http_status, text_len."""
    res = {"http_status": None, "title": "", "og_image": "", "nick_on_page": False, "phone_on_page": False,
           "city_on_page": False, "name_on_page": False, "signals": [], "text_sample": ""}
    try:
        with client() as c:
            r = c.get(url)
        res["http_status"] = r.status_code
        if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", "text/html"):
            return res
        html = r.text
    except Exception as e:  # noqa: BLE001
        log_call("verify", url, False, repr(e))
        return res
    m = TITLE_RE.search(html)
    res["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:200] if m else ""
    m = OG_IMG_RE.search(html)
    res["og_image"] = m.group(1) if m else ""
    text = TAG_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text)
    ntext = _norm(text)
    for n in nicks:
        for v in nick_variants(n, 6):
            if len(_norm(v)) >= 4 and _norm(v) in ntext:
                res["nick_on_page"] = True
                res["signals"].append(f"ник «{v}»")
                break
        if res["nick_on_page"]:
            break
    digits_on_page = {"".join(ch for ch in p if ch.isdigit())[-9:] for p in PHONE_IN_TEXT.findall(text)}
    for p in phones:
        d = "".join(ch for ch in p if ch.isdigit())
        if len(d) >= 9 and d[-9:] in digits_on_page:
            res["phone_on_page"] = True
            res["signals"].append("телефон")
            break
    if city and _norm(city) in ntext:
        res["city_on_page"] = True
        res["signals"].append(f"город «{city}»")
    if real_name and len(real_name) >= 3 and _norm(real_name) in ntext:
        res["name_on_page"] = True
        res["signals"].append("реальное имя")
    res["text_sample"] = text[:400]
    log_call("verify", url, True, ", ".join(res["signals"]) or "признаков нет")
    return res
