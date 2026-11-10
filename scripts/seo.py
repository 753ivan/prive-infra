#!/usr/bin/env python3
"""SEO-сборка статического сайта getprive.me.

Запускать после любого изменения страниц в site/ (идемпотентно, можно гонять сколько угодно):

    python3 scripts/seo.py            # всё: <head>, OG-картинки, пререндер, sitemap, robots
    python3 scripts/seo.py --no-render  # без Chrome (не трогает OG-картинки и пререндер)

Что делает:
  * чинит <head>: lang="ru", charset в начале, title/description/canonical/OG/Twitter, favicon,
    JSON-LD (Organization, WebSite, FAQPage, BlogPosting, BreadcrumbList);
  * заменяет ссылки вида /sos.html на чистые /sos (Caddy редиректит .html → без .html);
  * рисует OG-карточки 1200×630 в site/og/ (нужен Chrome + playwright);
  * пререндерит React-страницы (<div id=root>) в статический HTML, чтобы поисковики видели текст;
  * пересобирает site/sitemap.xml (lastmod из git) и site/robots.txt.

Новая статья блога: положить site/blog/<slug>/index.html с <title> и <meta name="description">,
добавить slug в POSTS ниже (дата публикации), запустить скрипт, закоммитить.

Языковые версии (/uk/, /en/): после правки русских страниц – `python3 scripts/i18n.py extract`,
перевести i18n/todo-*.txt (см. i18n/merge.py), `python3 scripts/i18n.py build`, затем этот скрипт.
"""
import html
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
BASE = "https://getprive.me"
BRAND = "PRIVÉ"
SLOGAN = "Не для чужих глаз"
BOT = "https://t.me/prive_scan_bot"
GA4_ID = "G-55DZMGMHMN"  # Google Analytics 4 – подключён напрямую; НЕ добавлять второй GA4-тег в GTM (будет двойной счёт)
GTM_ID = "GTM-K67NSMPB"  # Google Tag Manager (GA4 и прочие теги настраиваются внутри GTM)
RENDER = "--no-render" not in sys.argv

# ---- Языковые версии: site/uk/… и site/en/… собирает scripts/i18n.py из русских страниц ----
LANGS = {"ru": "", "uk": "/uk", "en": "/en"}
LOCALE = {"ru": "ru_RU", "uk": "uk_UA", "en": "en_US"}
L10N = {  # подписи, которые скрипт пишет сам (крошки, OG-карточки, 404)
    "ru": dict(blog="Блог", blog_brand="Блог PRIVÉ", slogan=SLOGAN,
               blog_card="Честные гайды о приватности, безопасности и жизни за границей",
               home_card="Сливы, фейки, поиск по лицу – удалим. Шантаж – остановим.",
               nf_title="Страница не найдена – PRIVÉ", nf_desc="Такой страницы нет. Вернитесь на главную PRIVÉ или откройте блог."),
    "uk": dict(blog="Блог", blog_brand="Блог PRIVÉ", slogan="Не для чужих очей",
               blog_card="Чесні гайди про приватність, безпеку й життя за кордоном",
               home_card="Зливи, фейки, пошук за обличчям – видалимо. Шантаж – зупинимо.",
               nf_title="Сторінку не знайдено – PRIVÉ", nf_desc="Такої сторінки немає. Поверніться на головну PRIVÉ або відкрийте блог."),
    "en": dict(blog="Blog", blog_brand="PRIVÉ Blog", slogan="Not for strangers’ eyes",
               blog_card="Honest guides on privacy, safety and life abroad",
               home_card="Leaks, fakes, face search – we remove them. Blackmail – we stop it.",
               nf_title="Page not found – PRIVÉ", nf_desc="This page doesn’t exist. Go back to the PRIVÉ home page or open the blog."),
}

# ---- Основные страницы: path -> (файл, title, description, тип JSON-LD, крошка) ----
PAGES = {
    "/": dict(
        file="index.html",
        title="PRIVÉ – удалить слив, фейк-анкету и фото из поиска по лицу",
        desc="Не для чужих глаз. Находим тебя по лицу, удаляем сливы, фейковые анкеты и фото "
             "из PimEyes, помогаем при шантаже. Бесплатная проверка в Telegram-боте.",
        og_title="PRIVÉ – Не для чужих глаз",
        crumb=None,
    ),
    "/sos": dict(
        file="sos.html",
        title="Шантаж интимными фото: срочная помощь 24/7 – PRIVÉ SOS",
        desc="Шантажируют фото или перепиской? Не плати и ничего не удаляй. Ответим за 15 минут "
             "днём и ночью и возьмём первые 72 часа на себя: доказательства, удаление, заявления.",
        og_title="PRIVÉ SOS – помощь при шантаже 24/7",
        crumb="SOS",
    ),
    "/private": dict(
        file="private.html",
        title="PRIVÉ Private – персональная защита репутации по NDA",
        desc="Не для чужих глаз. Персональный менеджер, договор и NDA до первого разговора: аудит "
             "телефона, чистка прошлого в сети, сопровождение переезда и новых этапов жизни.",
        og_title="PRIVÉ Private – персональное сопровождение",
        crumb="Private",
    ),
    "/partners": dict(
        file="partners.html",
        title="Партнёрская программа для агентств и админов чатов – PRIVÉ",
        desc="Комиссия до 30% с первого заказа и до 15% с подписок: рекомендуйте PRIVÉ моделям или "
             "оплачивайте их защиту пакетом. Меньше фейков и жалоб на агентство.",
        og_title="PRIVÉ Partners – защищённые модели, спокойное агентство",
        crumb="Партнёрам",
    ),
    "/blog/": dict(
        file="blog/index.html",
        title=None,  # берём из файла
        desc=None,
        crumb="Блог",
    ),
}

# ---- Статьи блога: slug -> дата публикации (ISO). Заголовок и описание берутся из файла. ----
POSTS = {
    "zima-v-gruzii": "2026-11-01",
    "issledovanie-chata": "2026-10-05",
    "slili-foto-24-chasa": "2026-09-25",
    "udalenie-po-zakonu": "2026-09-11",
    "armeniya-ili-gruziya": "2026-11-08",
    "shantazh-i-sliv": "2026-09-01",
    "proverka-menedzhera": "2026-10-27",
    "byuti-v-gruzii": "2026-10-22",
    "doksing": "2026-09-03",
    "brachnoe-agentstvo": "2026-11-10",
}

# Если заголовок статьи длиннее ~65 символов, Google его обрежет – короткий <title> для выдачи
# (H1 и OG-заголовок остаются авторскими).
TITLE_OVERRIDES = {
    "brachnoe-agentstvo": "Модель в брачном агентстве: где окажутся ваши фото · PRIVÉ",
    "zima-v-gruzii": "Зима со светом: Тбилиси на 3–4 месяца без визы · PRIVÉ",
    "issledovanie-chata": "Как вербуют и шантажируют девушек в 2026: разбор 1,1 млн сообщений · PRIVÉ",
}

SEO_START, SEO_END = "<!-- seo:start -->", "<!-- seo:end -->"
PRE_START, PRE_END = "<!--pre:start-->", "<!--pre:end-->"

# Ссылки на .html → чистые адреса
LINK_FIX = [
    (r'href="/?sos\.html', 'href="/sos'),
    (r'href="/?private\.html', 'href="/private'),
    (r'href="/?partners\.html', 'href="/partners'),
    (r'href="index\.html"', 'href="/"'),
]

# Теги, которые скрипт выкидывает из исходника и пишет заново в своём блоке
STRIP = [
    r"<title>.*?</title>",
    r'<meta\s+name="?description"?[^>]*>',
    r'<meta\s+property="?og:[^>]*>',
    r'<meta\s+name="?twitter:[^>]*>',
    r'<link\s+rel="?canonical"?[^>]*>',
    r'<meta\s+charset=[^>]*>',
    r'<meta\s+name="?claude:viewport-fit-sides"?[^>]*>',
]


def git_lastmod(path: Path) -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cs", "--", str(path)],
                             cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return out or None
    except Exception:
        return None


def attr(s, pattern):
    m = re.search(pattern, s, re.S)
    return html.unescape(m.group(1)).strip() if m else None


def head_zone(s):
    """Граница, до которой ищем мета-теги (до первого видимого блока / скрипта)."""
    ends = [m.start() for m in re.finditer(r"<header\b|<main\b|<div\b|<script\b|<a\b|<article\b|<section\b", s)]
    return min(ends) if ends else len(s)


def faq_from(s):
    items = []
    for q, a in re.findall(r"<details[^>]*>\s*<summary[^>]*>(.*?)</summary>(.*?)</details>", s, re.S):
        clean = lambda t: " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())
        if clean(q) and clean(a):
            items.append({"@type": "Question", "name": clean(q),
                          "acceptedAnswer": {"@type": "Answer", "text": clean(a)}})
    return items


ORG = {
    "@type": "Organization", "@id": BASE + "/#org", "name": BRAND, "url": BASE + "/",
    "logo": BASE + "/logo-512.png", "slogan": SLOGAN,
    "description": "Закрытый сервис, который возвращает тебе контроль над тем, что о тебе видно в интернете.",
    "contactPoint": {"@type": "ContactPoint", "contactType": "customer support", "url": BOT,
                     "availableLanguage": ["ru", "uk"]},
}


def crumbs(items):
    return {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": BASE + u}
        for i, (n, u) in enumerate(items)]}


def build_block(*, url, title, desc, og_title, og_desc, og_image, og_type, ld, noindex=False, lang="ru", alternates=None):
    e = lambda t: html.escape(t, quote=True)
    lines = [
        SEO_START,
        "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':new Date().getTime(),event:'gtm.js'});"
        "var f=d.getElementsByTagName(s)[0],j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;"
        "j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);"
        f"}})(window,document,'script','dataLayer','{GTM_ID}');</script>",
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA4_ID}"></script>',
        "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
        f"gtag('js',new Date());gtag('config','{GA4_ID}');"
        # Ключевое событие: переход в Telegram-бот (параметр start = источник: scan/sos/partner/private)
        "document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[href*=\"t.me/prive_scan_bot\"]');"
        "if(!a)return;var m=a.href.match(/start=([\\w-]+)/);gtag('event','bot_open',{bot_start:m?m[1]:'none',"
        "link_text:(a.textContent||'').trim().slice(0,60)});},true);</script>",
        f"<title>{e(title)}</title>",
        f'<meta name="description" content="{e(desc)}">',
    ]
    if noindex:
        lines.append('<meta name="robots" content="noindex">')
    else:
        lines += [
            f'<link rel="canonical" href="{e(url)}">',
            '<meta name="robots" content="index, follow, max-image-preview:large">',
        ]
        for code, href in (alternates or {}).items():
            lines.append(f'<link rel="alternate" hreflang="{code}" href="{e(href)}">')
    lines += [
        f'<meta property="og:site_name" content="{BRAND}">',
        f'<meta property="og:locale" content="{LOCALE[lang]}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:title" content="{e(og_title)}">',
        f'<meta property="og:description" content="{e(og_desc)}">',
        f'<meta property="og:url" content="{e(url)}">',
        f'<meta property="og:image" content="{e(og_image)}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="theme-color" content="#120d0f">',
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">',
        '<link rel="icon" href="/favicon-48.png" sizes="48x48" type="image/png">',
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
    ]
    if ld:
        data = json.dumps({"@context": "https://schema.org", "@graph": ld}, ensure_ascii=False,
                          separators=(",", ":")).replace("</", "<\\/")
        lines.append(f'<script type="application/ld+json">{data}</script>')
    lines.append(SEO_END)
    return "\n".join(lines)


def inject(path: Path, block: str):
    s = path.read_text(encoding="utf-8")
    # 1) убрать прошлый блок скрипта
    s = re.sub(re.escape(SEO_START) + r".*?" + re.escape(SEO_END) + r"\n?", "", s, flags=re.S)
    # 2) обёртка экспорта артефакта закрывала <head> до <title> – мета-теги попадали в <body>
    s = re.sub(r"</style></head><body>\s*", "</style>\n", s, count=1)
    # 3) выкинуть старые мета-теги из «головы»
    z = head_zone(s)
    head, rest = s[:z], s[z:]
    for p in STRIP:
        head = re.sub(p + r"\n?", "", head, flags=re.S | re.I)
    s = head + rest
    # 4) <html lang="ru">
    s = re.sub(r"<html(?![^>]*\blang=)", '<html lang="ru"', s, count=1, flags=re.I)
    # 5) вставить charset + блок сразу после <html ...> или <head>
    m = re.search(r"<head>|<html[^>]*>", s, re.I)
    m2 = re.search(r"<head>", s[: m.end() + 20], re.I)
    at = (m2.end() if m2 else m.end())
    s = s[:at] + '<meta charset="utf-8">\n' + block + "\n" + s[at:]
    # 6) ссылки на .html
    for a, b in LINK_FIX:
        s = re.sub(a, b, s)
    path.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------- рендер (Chrome)
OG_CSS = """
*{margin:0;box-sizing:border-box}
body{width:1200px;height:630px;background:#120d0f;color:#f3e9e4;font-family:Lora,'DejaVu Serif',serif;
 position:relative;overflow:hidden}
body:before{content:"";position:absolute;inset:0;background:
 radial-gradient(900px 500px at 105% -10%,#5a1726 0%,transparent 60%),
 radial-gradient(700px 400px at -10% 120%,#2b1016 0%,transparent 60%)}
.in{position:absolute;inset:64px 72px;display:flex;flex-direction:column;justify-content:space-between}
.eb{font:600 22px/1 'DejaVu Sans',sans-serif;letter-spacing:.22em;text-transform:uppercase;color:#e5c9a6}
h1{font-weight:400;font-style:italic;line-height:1.08;text-wrap:balance}
.ft{display:flex;justify-content:space-between;align-items:flex-end;font:500 24px/1 'DejaVu Sans',sans-serif;color:#b9a7a6}
.ft b{font:400 44px/1 Lora,serif;color:#f3e9e4;letter-spacing:.04em}
.bar{position:absolute;left:0;top:0;bottom:0;width:10px;background:#b3263f}
"""


def og_html(eyebrow, title, size, slogan=SLOGAN):
    e = html.escape
    return (f"<html><head><meta charset=utf-8><style>{OG_CSS}</style></head><body><div class=bar></div>"
            f"<div class=in><div class=eb>{e(eyebrow)}</div><h1 style='font-size:{size}px'>{e(title)}</h1>"
            f"<div class=ft><b>{BRAND}</b><span>{e(slogan)} · getprive.me</span></div></div></body></html>")


ICON_HTML = """<html><head><meta charset=utf-8><style>*{margin:0}body{width:%(s)dpx;height:%(s)dpx;
background:#120d0f;display:grid;place-items:center;border-radius:%(r)dpx;overflow:hidden}
b{font:italic 400 %(f)dpx/1 Lora,serif;color:#e5c9a6;transform:translateY(-%(y)dpx)}
i{position:absolute;bottom:%(b)dpx;width:%(w)dpx;height:%(h)dpx;background:#b3263f;border-radius:9px}</style></head>
<body><b>P</b><i></i></body></html>"""

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#120d0f"/><text x="32" y="45" text-anchor="middle" font-family="Bodoni 72,Didot,Georgia,serif" font-style="italic" font-size="42" fill="#e5c9a6">P</text><rect x="20" y="52" width="24" height="4" rx="2" fill="#b3263f"/></svg>
"""


def render_assets(cards):
    """cards: list of (outfile, eyebrow, title). Плюс иконки и пререндер."""
    import asyncio
    from playwright.async_api import async_playwright
    import http.server, threading, functools, socketserver

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(SITE))
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    async def run():
        async with async_playwright() as p:
            exe = next((x for x in ["/usr/bin/google-chrome", "/usr/bin/chromium"] if Path(x).exists()), None)
            b = await p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            pg = await b.new_page(viewport={"width": 1200, "height": 630})
            (SITE / "og").mkdir(exist_ok=True)
            for card in cards:
                out, eb, title = card[:3]
                slogan = card[3] if len(card) > 3 else SLOGAN
                out.parent.mkdir(parents=True, exist_ok=True)
                title = title[:1].upper() + title[1:]
                size = 76 if len(title) < 40 else 62 if len(title) < 70 else 50
                await pg.set_content(og_html(eb, title, size, slogan))
                await pg.screenshot(path=str(out), type="jpeg", quality=88)
            for name, s in [("favicon-48.png", 48), ("apple-touch-icon.png", 180), ("logo-512.png", 512)]:
                await pg.set_viewport_size({"width": s, "height": s})
                k = s / 64
                await pg.set_content(ICON_HTML % dict(s=s, r=0 if name != "favicon-48.png" else 10, f=int(46 * k),
                                                      y=int(3 * k), b=int(9 * k), w=int(24 * k), h=max(2, int(4 * k))))
                await pg.screenshot(path=str(SITE / name), type="png")
            # пререндер React-страниц
            await pg.set_viewport_size({"width": 1280, "height": 900})
            await pg.route("**/fonts.g*/**", lambda r: r.abort())
            await pg.route("**/*googletagmanager*/**", lambda r: r.abort())
            for f in SITE.rglob("*.html"):
                s = f.read_text(encoding="utf-8")
                if not re.search(r"<div id=\"?root\"?>", s):
                    continue
                s = re.sub(re.escape(PRE_START) + r".*?" + re.escape(PRE_END), "", s, flags=re.S)
                f.write_text(s, encoding="utf-8")
                rel = f.relative_to(SITE).as_posix().replace("index.html", "")
                await pg.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle")
                await pg.wait_for_timeout(1200)
                inner = await pg.evaluate("document.getElementById('root').innerHTML")
                s = re.sub(r"(<div id=\"?root\"?>)", lambda m: m.group(1) + PRE_START + inner + PRE_END, s, count=1)
                f.write_text(s, encoding="utf-8")
                print(f"  пререндер: {rel} ({len(inner)//1024} КБ)")
            await b.close()

    asyncio.run(run())
    srv.shutdown()


# ---------------------------------------------------------------- main
def alt_urls(path):
    """hreflang для страницы (path без языкового префикса) – только для языков, где файл есть."""
    out = {}
    for code, pre in LANGS.items():
        rel = path.strip("/")
        f = SITE / pre.strip("/") / (rel + "/index.html" if path.endswith("/") and rel else
                                     "index.html" if not rel else rel + ".html")
        if f.exists():
            out[code] = BASE + pre + path
    if out:
        out["x-default"] = BASE + path
    return out


def main():
    entries = []   # (url, file) для sitemap
    cards = []

    for lang, pre in LANGS.items():
        root = SITE / pre.strip("/") if pre else SITE
        T = L10N[lang]
        og_dir = "og" + pre  # og/, og/uk/, og/en/
        if pre and not root.exists():
            continue

        # статьи
        posts_meta = {}
        for slug, published in POSTS.items():
            f = root / "blog" / slug / "index.html"
            if not f.exists():
                print("нет файла", f); continue
            s = f.read_text(encoding="utf-8")
            z = s[:head_zone(s)] if SEO_START not in s else s
            title = attr(z, r"<title>(.*?)</title>")
            desc = (attr(z, r'<meta\s+name="?description"?\s+content="([^"]*)"') or attr(z, r'<meta\s+content="([^"]*)"\s+name="?description"?'))
            short = re.sub(r"\s*·\s*(Блог PRIVÉ|PRIVÉ Blog|PRIVÉ)\s*$", "", title)
            if lang == "ru":
                short = attr(z, r'<meta\s+property="?og:title"?\s+content="([^"]*)"') or short
                if slug in TITLE_OVERRIDES:
                    title = TITLE_OVERRIDES[slug]
            posts_meta[slug] = (title, desc, short, published, f)

        for slug, (title, desc, short, published, f) in posts_meta.items():
            path = f"/blog/{slug}/"
            url = f"{BASE}{pre}{path}"
            img = f"{BASE}/{og_dir}/{slug}.jpg"
            cards.append((SITE / og_dir / f"{slug}.jpg", T["blog_brand"], short, T["slogan"]))
            modified = git_lastmod(f) or published
            ld = [ORG,
                  {"@type": "BlogPosting", "headline": short[:110], "description": desc, "inLanguage": lang,
                   "url": url, "mainEntityOfPage": url, "image": img,
                   "datePublished": published, "dateModified": max(modified, published),
                   "author": {"@id": BASE + "/#org"}, "publisher": {"@id": BASE + "/#org"},
                   "isPartOf": {"@type": "Blog", "@id": BASE + pre + "/blog/#blog"}},
                  crumbs([("PRIVÉ", pre + "/"), (T["blog"], pre + "/blog/"), (short, pre + path)])]
            inject(f, build_block(url=url, title=title, desc=desc, og_title=short, og_desc=desc,
                                  og_image=img, og_type="article", ld=ld, lang=lang, alternates=alt_urls(path)))
            entries.append((url, f, "0.7"))

        # основные страницы
        for path, cfg in PAGES.items():
            f = root / cfg["file"]
            if not f.exists():
                continue
            s = f.read_text(encoding="utf-8")
            z = s[:head_zone(s)] if SEO_START not in s else s
            ru = lang == "ru"
            title = (cfg.get("title") if ru else None) or attr(z, r"<title>(.*?)</title>")
            desc = (cfg.get("desc") if ru else None) or (attr(z, r'<meta\s+name="?description"?\s+content="([^"]*)"') or attr(z, r'<meta\s+content="([^"]*)"\s+name="?description"?'))
            og_title = (cfg.get("og_title") if ru else None) or title
            url = BASE + pre + path
            slug = "home" if path == "/" else path.strip("/").replace("/", "-")
            img = f"{BASE}/{og_dir}/{slug}.jpg"
            eyebrow = {"/": T["slogan"], "/blog/": T["blog_brand"]}.get(path, "PRIVÉ")
            card_title = {"/": T["home_card"], "/blog/": T["blog_card"]}.get(path, og_title.split(" – ", 1)[-1])
            cards.append((SITE / og_dir / f"{slug}.jpg", eyebrow, card_title, T["slogan"]))
            if path == "/":
                faq = faq_from(s)
                ld = [ORG, {"@type": "WebSite", "@id": BASE + pre + "/#site", "name": BRAND, "url": BASE + pre + "/",
                            "inLanguage": lang, "publisher": {"@id": BASE + "/#org"}}]
                if faq:
                    ld.append({"@type": "FAQPage", "mainEntity": faq})
            elif path == "/blog/":
                ld = [ORG, {"@type": "Blog", "@id": BASE + pre + "/blog/#blog", "name": T["blog_brand"], "url": url,
                            "inLanguage": lang, "publisher": {"@id": BASE + "/#org"},
                            "blogPost": [{"@type": "BlogPosting", "headline": m[2],
                                          "url": f"{BASE}{pre}/blog/{k}/"} for k, m in posts_meta.items()]},
                      crumbs([("PRIVÉ", pre + "/"), (T["blog"], pre + "/blog/")])]
            else:
                crumb = {"uk": {"Партнёрам": "Партнерам"}, "en": {"Партнёрам": "Partners"}}.get(lang, {}).get(cfg["crumb"], cfg["crumb"])
                ld = [ORG, {"@type": "Service", "name": og_title, "description": desc, "url": url,
                            "provider": {"@id": BASE + "/#org"}, "areaServed": ["UA", "GE", "Europe"]},
                      crumbs([("PRIVÉ", pre + "/"), (crumb, pre + path)])]
            inject(f, build_block(url=url, title=title, desc=desc, og_title=og_title, og_desc=desc,
                                  og_image=img, og_type="website", ld=ld, lang=lang, alternates=alt_urls(path)))
            entries.append((url, f, "1.0" if path == "/" else "0.8"))

        # 404
        f404 = root / "404.html"
        if f404.exists():
            inject(f404, build_block(url=BASE + pre + "/404", title=T["nf_title"], desc=T["nf_desc"],
                                     og_title="PRIVÉ", og_desc=T["slogan"], og_image=f"{BASE}/{og_dir}/home.jpg",
                                     og_type="website", ld=None, noindex=True, lang=lang))

    (SITE / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")

    if RENDER:
        try:
            render_assets(cards)
        except Exception as ex:  # без Chrome всё остальное всё равно собрано
            print("рендер пропущен:", ex)

    # sitemap + robots
    order = sorted(entries, key=lambda e: (-float(e[2]), e[0]))
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, f, prio in order:
        lm = git_lastmod(f)
        xml.append(f"  <url><loc>{url}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "") +
                   f"<priority>{prio}</priority></url>")
    xml.append("</urlset>")
    (SITE / "sitemap.xml").write_text("\n".join(xml) + "\n", encoding="utf-8")
    (SITE / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\n"
        f"Sitemap: {BASE}/sitemap.xml\n", encoding="utf-8")
    print(f"готово: {len(entries)} страниц в sitemap")


if __name__ == "__main__":
    main()
