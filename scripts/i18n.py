#!/usr/bin/env python3
"""Языковые версии сайта: /uk/ и /en/ собираются из русских страниц + словари переводов.

    python3 scripts/i18n.py extract   # выписать новые русские фрагменты без перевода -> i18n/todo-<lang>.txt
    python3 scripts/i18n.py build     # собрать site/uk/ и site/en/
    python3 scripts/seo.py            # потом как обычно (head, hreflang, OG, пререндер, sitemap)

Словари: i18n/<lang>.json – {русский фрагмент: перевод}. Фрагмент – внутренний HTML «листового»
блока (абзац, пункт списка, заголовок, ячейка…), значение атрибута (alt/title/aria-label/placeholder)
или строковый литерал в JS-бандле гида. После правки русской страницы: extract -> перевести todo ->
добавить в словарь -> build. Непереведённые фрагменты остаются по-русски и печатаются в отчёте.
"""
import json
import re
import shutil
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Comment

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
I18N = ROOT / "i18n"
LANGS = {"uk": "UA", "en": "EN"}
CYR = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")

# что переводим (относительно site/)
PAGES = ["index.html", "sos.html", "private.html", "partners.html", "404.html", "blog/index.html"]
GUIDE = "blog/zima-v-gruzii/index.html"

INLINE = {"a", "strong", "b", "em", "i", "span", "br", "small", "code", "sup", "sub", "mark", "abbr",
          "u", "s", "nobr", "time", "wbr", "q", "cite", "del", "ins", "kbd"}
ATTRS = ["alt", "title", "aria-label", "placeholder"]
PRE_START, PRE_END = "<!--pre:start-->", "<!--pre:end-->"


def page_files():
    out = [p for p in PAGES if (SITE / p).exists()]
    for d in sorted((SITE / "blog").iterdir()):
        rel = f"blog/{d.name}/index.html"
        if d.is_dir() and (SITE / rel).exists() and rel != GUIDE:
            out.append(rel)
    return out


# ------------------------------------------------------------------ HTML-страницы
def has_block(el):
    return any(getattr(c, "name", None) and (c.name not in INLINE or has_block(c)) for c in el.children)


def walk(el, found):
    """found(kind, node): kind = 'el' (заменить содержимое) | 'txt' (текстовый узел) | ('attr', name)."""
    for c in list(el.children):
        if isinstance(c, Comment):
            continue
        if isinstance(c, NavigableString):
            if CYR.search(c):
                found("txt", c)
            continue
        if c.name in ("script", "style"):
            continue
        for a in ATTRS:
            v = c.get(a)
            if isinstance(v, str) and CYR.search(v):
                found(("attr", a), c)
        if c.name == "meta" and c.get("name") == "description" and CYR.search(c.get("content", "")):
            found(("attr", "content"), c)
        if c.name == "title" or (not has_block(c) and CYR.search(c.get_text())):
            if CYR.search(c.get_text()):
                found("el", c)
            # атрибуты внутри листового блока
            for d in c.find_all(True):
                for a in ATTRS:
                    v = d.get(a)
                    if isinstance(v, str) and CYR.search(v):
                        found(("attr", a), d)
            continue
        walk(c, found)


def key_of(kind, node):
    if kind == "el":
        return node.decode_contents().strip()
    if kind == "txt":
        return str(node).strip()
    return node[kind[1]].strip()


# подписи из CSS (content:"Как выглядит: ") – тоже текст для читательницы
CSS_CONTENT = re.compile(r'content:"([^"]*[А-Яа-яЁёІіЇїЄєҐґ][^"]*)"')


def html_segments(src):
    soup = BeautifulSoup(src, "html.parser")
    segs = []
    walk(soup, lambda k, n: segs.append(key_of(k, n)))
    segs += CSS_CONTENT.findall(src)
    return [s for s in segs if s]


def translate_html(src, d, lang, missing):
    soup = BeautifulSoup(src, "html.parser")

    def apply(kind, node):
        k = key_of(kind, node)
        if not k:
            return
        t = d.get(k)
        if t is None:
            missing.append(k)
            return
        if kind == "el":
            node.clear()
            frag = BeautifulSoup(t, "html.parser")
            for c in list(frag.contents):
                node.append(c)
        elif kind == "txt":
            raw = str(node)
            lead = raw[: len(raw) - len(raw.lstrip())]
            trail = raw[len(raw.rstrip()):]
            node.replace_with(NavigableString(lead + t + trail))
        else:
            node[kind[1]] = t

    walk(soup, apply)

    def css(m):
        t = d.get(m.group(1))
        if t is None:
            missing.append(m.group(1))
            return m.group(0)
        return 'content:"' + t.replace('"', '\\"') + '"'

    return CSS_CONTENT.sub(css, str(soup))


# ------------------------------------------------------------------ гид (React-бандл)
JS_LIT = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def guide_split(src):
    """(до бандла, бандл) – пререндер выкидываем, его заново делает seo.py."""
    src = re.sub(re.escape(PRE_START) + r".*?" + re.escape(PRE_END), "", src, flags=re.S)
    i = src.find('<script type="module">')
    return src[:i], src[i:]


def _js_tokens(js):
    """Строковые и шаблонные литералы бандла с кириллицей: (start, end, key, quote)."""
    import esprima  # pip install esprima
    out = []
    for t in esprima.tokenize(js, {"range": True}):
        if t.type == "String" and CYR.search(t.value):
            out.append((t.range[0], t.range[1], t.value[1:-1], t.value[0]))
        elif t.type == "Template" and CYR.search(t.value):
            out.append((t.range[0], t.range[1], t.value, None))  # ключ – вместе с ` } ${
    return out


def _bundle(src):
    i = src.find('<script type="module">') + len('<script type="module">')
    j = src.find("</script>", i)
    return i, j


def guide_segments(src):
    head, js = guide_split(src)
    segs = html_segments(head)
    i, j = _bundle(js)
    segs += [k for _, _, k, _ in _js_tokens(js[i:j])]
    return segs


def js_escape(t):
    return t.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def translate_guide(src, d, lang, missing):
    head, js = guide_split(src)
    head = translate_html(head, d, lang, missing)
    i, j = _bundle(js)
    code = js[i:j]
    for a, b, k, q in reversed(_js_tokens(code)):
        t = d.get(k)
        if t is None:
            missing.append(k)
            continue
        if q:  # обычная строка: экранируем только то, что сломает литерал
            t = re.sub(r"(?<!\\)" + re.escape(q), "\\" + q, t).replace("\n", "\\n")
            t = q + t + q
        code = code[:a] + t + code[b:]
    js = relink_js(js[:i] + code + js[j:], lang)
    # картинки гида лежат у русской версии: относительные пути -> абсолютные
    base = "/" + GUIDE.rsplit("/", 1)[0] + "/"
    out = re.sub(r'(["\'])img/', lambda m: m.group(1) + base + "img/", head + js)
    return out


# ------------------------------------------------------------------ ссылки и переключатель
PAGE_HREF = re.compile(r'href="(/(?:#[^"]*)?|/(?:sos|private|partners)(?:#[^"]*)?|/blog/(?:[a-z0-9-]+/)?(?:#[^"]*)?)"')


def relink(s, lang):
    return PAGE_HREF.sub(lambda m: f'href="/{lang}{m.group(1)}"', s)


def relink_js(js, lang):
    return re.sub(r'href:"(/blog/[a-z0-9-]*/?(?:#[^"]*)?|/(?:sos|private|partners)?)"',
                  lambda m: f'href:"/{lang}{m.group(1)}"', js)


SW_CSS = ("<style>.lang-sw{display:inline-flex;gap:2px;align-items:center;font:600 12px/1 system-ui,sans-serif;"
          "letter-spacing:.06em;margin:0 10px}.lang-sw a{padding:6px 7px;border-radius:999px;color:inherit;"
          "opacity:.55;text-decoration:none}.lang-sw a:hover{opacity:1}.lang-sw a[aria-current]{opacity:1;"
          "background:rgba(127,127,127,.16)}.lang-sw.fix{position:fixed;top:12px;right:12px;z-index:9999;margin:0;"
          "padding:3px;border-radius:999px;background:rgba(18,13,15,.82);color:#f3e9e4;"
          "backdrop-filter:blur(6px)}.lang-sw.fix.bot{top:auto;bottom:16px}@media(max-width:560px){.lang-sw{margin:0 2px 0 auto;font-size:10.5px;gap:0}.lang-sw a{padding:5px 3px}header.top .wrap{gap:8px}.lang-sw+.sos-pill{padding-left:10px;padding-right:10px;font-size:12px}}</style>")


def url_of(rel):
    """site-relative файл -> путь страницы без языка."""
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel[: -len(".html")]


def switcher(rel, cur, fixed):
    path = url_of(rel)
    items = []
    for code, label in [("ru", "RU"), ("uk", "UA"), ("en", "EN")]:
        href = path if code == "ru" else f"/{code}{path}"
        cur_attr = ' aria-current="true"' if code == cur else ""
        items.append(f'<a href="{href}" hreflang="{code}" lang="{code}"{cur_attr}>{label}</a>')
    cls = "lang-sw fix" if fixed else "lang-sw"
    return f'<span class="{cls}" data-i18n-sw>{"".join(items)}</span>'


def add_switcher(s, rel, cur):
    s = re.sub(r'<span class="lang-sw[^"]*" data-i18n-sw(?:="")?>(?:<a [^>]*>[A-Z]{2}</a>)+</span>', "", s)
    s = re.sub(r"<style>\.lang-sw\{.*?</style>", "", s, flags=re.S)
    if rel == "404.html":
        return s
    sw_inline = SW_CSS + switcher(rel, cur, False)
    if 'class="sos-pill"' in s:
        return s.replace('<a class="sos-pill"', sw_inline + '<a class="sos-pill"', 1)
    m = re.search(r'<a class="logo"[^>]*>.*?</a>', s, re.S)
    if m and rel != GUIDE and 'class="sos-pill"' not in s:
        return s[: m.end()] + sw_inline + s[m.end():]
    m = re.search(r"<body[^>]*>", s)
    sw = SW_CSS + switcher(rel, cur, True)
    if rel == GUIDE:
        sw = sw.replace('class="lang-sw fix"', 'class="lang-sw fix bot"')
    return s[: m.end()] + sw + s[m.end():] if m else s


# ------------------------------------------------------------------ команды
def load(lang):
    f = I18N / f"{lang}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def all_segments():
    out = {}
    for rel in page_files():
        out[rel] = html_segments((SITE / rel).read_text(encoding="utf-8"))
    if (SITE / GUIDE).exists():
        out[GUIDE] = guide_segments((SITE / GUIDE).read_text(encoding="utf-8"))
    return out


def cmd_extract():
    I18N.mkdir(exist_ok=True)
    segs = all_segments()
    for lang in LANGS:
        d = load(lang)
        seen, lines, n = set(), [], 0
        for rel, ss in segs.items():
            new = [x for x in ss if x not in d and x not in seen]
            if not new:
                continue
            lines.append(f"#### {rel}")
            for x in new:
                seen.add(x)
                n += 1
                lines.append(f"@@{n}\n{x}")
        (I18N / f"todo-{lang}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{lang}: {n} фрагментов без перевода -> i18n/todo-{lang}.txt")


def cmd_build():
    for lang in LANGS:
        d = load(lang)
        missing = []
        out_root = SITE / lang
        for rel in page_files() + ([GUIDE] if (SITE / GUIDE).exists() else []):
            src = (SITE / rel).read_text(encoding="utf-8")
            if rel == GUIDE:
                s = translate_guide(src, d, lang, missing)
            else:
                s = translate_html(src, d, lang, missing)
            s = relink(s, lang)
            s = re.sub(r'<html lang="ru"', f'<html lang="{lang}"', s, count=1)
            for r2 in (rel,):
                s = add_switcher(s, r2, lang)
            dst = out_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(s, encoding="utf-8")
            # локальные картинки поста – общие, ссылки остаются на русские пути (/blog/<slug>/img/…)
        print(f"{lang}: собрано, без перевода {len(set(missing))} фрагментов")
        for x in list(dict.fromkeys(missing))[:15]:
            print("   ·", x[:90].replace("\n", " "))
    # переключатель и на русских страницах
    for rel in page_files() + [GUIDE]:
        f = SITE / rel
        if f.exists():
            f.write_text(add_switcher(f.read_text(encoding="utf-8"), rel, "ru"), encoding="utf-8")


if __name__ == "__main__":
    {"extract": cmd_extract, "build": cmd_build}[sys.argv[1] if len(sys.argv) > 1 else "build"]()
