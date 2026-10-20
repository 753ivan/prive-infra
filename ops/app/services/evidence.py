"""M5 — фиксатор доказательств.

Для ссылки: HTML + SHA-256, скриншот (если включён Playwright), копия в web.archive.org
(Save Page Now), копия в archive.today, метка времени OpenTimestamps (если установлен клиент `ots`).
Всё, что не настроено, пропускается с пометкой в note; минимум — HTML и хеш — делается всегда.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ..config import settings
from .base import client, log_call


def _stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def capture(url: str, finding_id: int) -> dict:
    folder = settings.storage_dir / "evidence" / str(finding_id)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    out = {"http_status": None, "html_file": "", "screenshot_file": "", "sha256": "", "archive_url": "",
           "archive_today_url": "", "ots_file": "", "note": ""}
    notes = []

    # 1. HTML + хеш
    try:
        with client() as c:
            r = c.get(url)
            out["http_status"] = r.status_code
            html_path = folder / f"page_{stamp}.html"
            html_path.write_bytes(r.content)
            out["html_file"] = str(html_path.relative_to(settings.storage_dir))
            out["sha256"] = hashlib.sha256(r.content).hexdigest()
    except Exception as e:  # noqa: BLE001
        notes.append(f"HTML не получен: {e!r}")

    # 2. Скриншот через Playwright (опционально)
    if settings.enable_playwright:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore

            shot = folder / f"shot_{stamp}.png"
            with sync_playwright() as p:
                b = p.chromium.launch()
                page = b.new_page(viewport={"width": 1280, "height": 900})
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.screenshot(path=str(shot), full_page=True)
                b.close()
            out["screenshot_file"] = str(shot.relative_to(settings.storage_dir))
        except Exception as e:  # noqa: BLE001
            notes.append(f"скриншот не сделан: {e!r}")
    else:
        notes.append("скриншот: Playwright выключен (ENABLE_PLAYWRIGHT=false) — сделать вручную по SOP-1")

    # 3. web.archive.org Save Page Now
    try:
        headers = {"Accept": "application/json"}
        if settings.archive_org_access_key:
            headers["Authorization"] = f"LOW {settings.archive_org_access_key}:{settings.archive_org_secret_key}"
        with client() as c:
            r = c.post("https://web.archive.org/save", data={"url": url, "capture_all": "on"}, headers=headers)
            if r.status_code in (200, 201, 302):
                loc = r.headers.get("Content-Location") or r.headers.get("Location") or ""
                job = {}
                try:
                    job = r.json()
                except Exception:
                    pass
                if loc:
                    out["archive_url"] = "https://web.archive.org" + loc if loc.startswith("/web/") else loc
                elif job.get("job_id"):
                    out["archive_url"] = f"https://web.archive.org/web/*/{url}"
                    notes.append(f"SPN job {job['job_id']} принят, ссылка на снимок появится позже")
                else:
                    out["archive_url"] = f"https://web.archive.org/web/*/{url}"
                log_call("archive_org", "save", True, out["archive_url"])
            else:
                notes.append(f"web.archive.org ответил {r.status_code}")
                log_call("archive_org", "save", False, f"{r.status_code} {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"web.archive.org: {e!r}")
        log_call("archive_org", "save", False, repr(e))

    # 4. archive.today
    try:
        with client() as c:
            r = c.post("https://archive.ph/submit/", data={"url": url, "anyway": "1"})
            refresh = r.headers.get("Refresh", "")
            loc = r.headers.get("Location", "") or (refresh.split("url=", 1)[1] if "url=" in refresh else "")
            if loc:
                out["archive_today_url"] = loc
                log_call("archive_today", "submit", True, loc)
            else:
                notes.append("archive.today: ссылка не получена (возможно капча) — сделать вручную")
    except Exception as e:  # noqa: BLE001
        notes.append(f"archive.today: {e!r}")

    # 5. OpenTimestamps (если установлен клиент ots)
    if out["sha256"] and shutil.which("ots"):
        try:
            html_abs = settings.storage_dir / out["html_file"]
            subprocess.run(["ots", "stamp", str(html_abs)], check=True, timeout=60, capture_output=True)
            out["ots_file"] = out["html_file"] + ".ots"
        except Exception as e:  # noqa: BLE001
            notes.append(f"ots: {e!r}")
    elif out["sha256"]:
        notes.append("OpenTimestamps: клиент `ots` не установлен (pip install opentimestamps-client)")

    out["note"] = "; ".join(notes)
    return out


def is_still_up(url: str, marker: str = "") -> tuple[bool, int | None]:
    """Повторная проверка: страница жива? (для контроля снятий)."""
    try:
        with client() as c:
            r = c.get(url)
            if r.status_code in (404, 410, 451):
                return False, r.status_code
            if marker and marker.lower() not in r.text.lower():
                return False, r.status_code
            return True, r.status_code
    except Exception:
        return None, None  # неизвестно: сеть/прокси, не считать снятым


def status() -> dict:
    return {
        "playwright": settings.enable_playwright,
        "archive_org_keys": bool(settings.archive_org_access_key),
        "ots_client": bool(shutil.which("ots")),
    }
