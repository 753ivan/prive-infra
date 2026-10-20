"""Ассистент веб-форм (M6, очередь 8): python -m scripts.form_assist <takedown_id> [--base http://localhost:8000]

Открывает форму площадки в видимом браузере, заполняет поля, которые удаётся распознать
по подписям (URL, email, имя, описание), и показывает сбоку панель с полным текстом жалобы
и ссылками на доказательства. Кнопку «Отправить» и капчу оператор нажимает сам —
так и должно быть: подача жалобы остаётся осознанным действием человека.

Профиль браузера сохраняется в app/data/browser_profile — логины в формах площадок
(Google-аккаунт компании для форм Google и т. п.) не надо вводить каждый раз.
Нужно: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

FIELD_RULES = [  # (регулярка по label/placeholder/name/aria, что подставить)
    (r"url|link|ссылк|адрес стран|web address|location of", "url"),
    (r"e-?mail|почт|correo", "email"),
    (r"full name|your name|имя|name of|nome|name", "name"),
    (r"company|organi[sz]ation|организац", "org"),
    (r"describ|detail|explain|message|comment|опис|текст|additional|reason|statement", "body"),
    (r"country|страна", "country"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("takedown_id", type=int)
    ap.add_argument("--base", default=os.environ.get("PRIVE_BASE", "http://localhost:8000"))
    ap.add_argument("--user", default=os.environ.get("PRIVE_USER", "operator"))
    ap.add_argument("--password", default=os.environ.get("PRIVE_PASSWORD", ""))
    a = ap.parse_args()

    auth = (a.user, a.password or input("Пароль панели: "))
    with httpx.Client(auth=auth, timeout=30) as c:
        tds = c.get(f"{a.base}/api/takedowns").json()
    td = next((t for t in tds if t["id"] == a.takedown_id), None)
    if not td:
        sys.exit("жалоба не найдена")
    if not td.get("form_url"):
        sys.exit("у этой жалобы нет form_url — канал email, отправляйте из панели")

    from playwright.sync_api import sync_playwright

    values = {"url": td["url"], "email": os.environ.get("MAIL_FROM", "abuse@prive.example"), "name": "PRIVÉ Digital Privacy Services",
              "org": "PRIVÉ Digital Privacy Services", "body": td["body"], "country": ""}
    panel_html = f"""
    <div id="prive-panel" style="position:fixed;top:0;right:0;width:380px;height:100vh;z-index:2147483647;background:#17171c;color:#e8e6e1;
         font:13px system-ui;padding:14px;overflow:auto;box-shadow:-4px 0 20px rgba(0,0,0,.5)">
      <b style="color:#c9a56a">PRIVÉ · жалоба #{td['id']}</b> <span style="float:right;cursor:pointer" onclick="this.parentNode.remove()">✕</span>
      <p><b>Площадка:</b> {td['platform']}<br><b>Шаблон:</b> {td['template']}</p>
      <p><b>URL:</b> <code style="word-break:break-all">{td['url']}</code> <button onclick="navigator.clipboard.writeText({td['url']!r})">копировать</button></p>
      <p><b>Тема:</b> {td['subject']} <button onclick="navigator.clipboard.writeText({td['subject']!r})">копировать</button></p>
      <p><b>Текст:</b> <button onclick="navigator.clipboard.writeText(atob('{base64.b64encode(td['body'].encode()).decode()}'))">копировать текст</button></p>
      <pre style="white-space:pre-wrap;font-size:11px;background:#1f1f26;padding:8px;border-radius:6px;max-height:40vh;overflow:auto">{td['body'].replace('<','&lt;')}</pre>
      <p style="color:#8d8b85">Поля, которые удалось распознать, уже заполнены. Проверьте, пройдите капчу и нажмите «Отправить» на форме. Затем в панели — «Отмечено отправленным».</p>
    </div>"""

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(ROOT / "app" / "data" / "browser_profile"), headless=False, viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(td["form_url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        filled = []
        for el in page.query_selector_all("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]), textarea"):
            try:
                label = " ".join(filter(None, [el.get_attribute("placeholder"), el.get_attribute("name"), el.get_attribute("aria-label"), el.get_attribute("id")]))
                lid = el.get_attribute("id")
                if lid:
                    lab = page.query_selector(f'label[for="{lid}"]')
                    if lab:
                        label += " " + lab.inner_text()
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                for rx, key in FIELD_RULES:
                    if re.search(rx, label, re.I) and values.get(key):
                        if key == "body" and tag != "textarea":
                            continue
                        el.fill(values[key])
                        filled.append(key)
                        break
            except Exception:
                continue
        page.evaluate("(h) => { const d=document.createElement('div'); d.innerHTML=h; document.body.appendChild(d); }", panel_html)
        print("заполнено:", ", ".join(filled) or "ничего (поля не распознаны — используйте кнопки копирования)")
        print("Окно открыто. Закройте браузер, когда отправите форму.")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()


if __name__ == "__main__":
    main()
