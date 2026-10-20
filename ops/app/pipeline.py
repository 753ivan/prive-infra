"""Конвейер заказов. Каждая операция — функция, которую можно вызвать из панели,
из фоновой задачи (Job) или из планировщика. Все пишут в реестр и в лог интеграций.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .config import settings
from .db import session_scope
from .models import SERVICES, Evidence, Finding, Job, Order, Takedown, now
from .services import evidence as ev_svc
from .services import leaks as leaks_svc
from .services import search as search_svc
from .services import takedown as td_svc
from .services import telegram as tg_svc

# Домены, которые в контексте эскорта почти всегда означают «анкета»/«слив» — первичная классификация.
# Список расширяется по находкам (панель → Справочник → классификация доменов).
KIND_HINTS = {
    "anketa": ["intimcity", "dosug", "sexkiev", "prostitutki", "escort", "indi", "anketa", "девушки", "sosamba"],
    "leak": ["slivs", "сливы", "leak", "porn", "xvideos", "pornhub", "telegra.ph", "t.me", "onlyfans"],
    "mention": ["forum", "2ch", "pikabu", "vk.com", "reddit"],
}


def guess_kind(url: str, title: str = "") -> str:
    hay = (url + " " + title).lower()
    for kind, words in KIND_HINTS.items():
        if any(w in hay for w in words):
            return kind
    return "unknown"


# ---------------------------------------------------------------------------
# Задачи
# ---------------------------------------------------------------------------


def _run_job(job_id: int, fn):
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.status, job.started_at = "running", now()
    try:
        result = fn()
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status, job.finished_at, job.result = "done", now(), result or {}
    except Exception as e:  # noqa: BLE001
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status, job.finished_at, job.error = "error", now(), repr(e)


def enqueue(db, order_id: int | None, type_: str, params: dict | None = None) -> Job:
    job = Job(order_id=order_id, type=type_, params=params or {})
    db.add(job)
    db.flush()
    return job


# --- скан по тексту + утечки -------------------------------------------------

def scan_order(order_id: int) -> dict:
    from .services.enrich import canonical_url
    from .services.rules import COSTS
    with session_scope() as db:
        order = db.get(Order, order_id)
        queries = dict(order.queries or {})
        existing = {canonical_url(f.url) for f in order.findings}
        order.status = "scanning"
    added, missing_all, cost = 0, set(), 0.0
    new_items: list[dict] = []

    def _take(r: dict, qtype: str, value: str, **extra) -> bool:
        key = canonical_url(r["url"])
        if key in existing:
            return False
        existing.add(key)
        new_items.append({**r, "query_type": qtype, "query_value": value, "match_type": r.get("match_type", "text"), **extra})
        return True

    for qtype, value, qstring in search_svc.build_queries(queries):
        results, missing = search_svc.run_all(qstring)
        missing_all.update(missing)
        cost += sum(COSTS.get(r["source"], 0) for r in results[:1]) * len({r["source"] for r in results})
        for r in results:
            _take(r, qtype, value)

    # проход по закрытому списку источников (site:-запросы по никам и телефонам)
    from .services import crawl as crawl_svc
    values = [("nick", n) for n in queries.get("nicks", []) if n.strip()] + [("phone", p) for p in queries.get("phones", []) if p.strip()]
    if values:
        crawl_hits, miss = crawl_svc.crawl(values, city=queries.get("city", ""))
        missing_all.update(miss)
        cost += 0.005 * len(crawl_hits)
        for r in crawl_hits:
            _take(r, r.get("query_type", "nick"), r.get("query_value", ""))

    for email in queries.get("emails", []):
        try:
            for r in leaks_svc.hibp_breaches(email):
                _take({**r, "url": r["url"] + "#" + email}, "email", email, kind="leak", match_type="exact")
        except search_svc.NotConfigured:
            missing_all.add("hibp")
        except Exception as e:  # noqa: BLE001
            search_svc.log_call("hibp", email, False, repr(e))
    for qtype, key in (("phone", "phones"), ("email", "emails"), ("nick", "nicks")):
        for value in queries.get(key, []):
            try:
                for r in leaks_svc.dehashed(qtype, value):
                    _take({**r, "url": r["url"] + "#" + value}, qtype, value, kind="leak", match_type="exact")
            except search_svc.NotConfigured:
                missing_all.add("dehashed")
            except Exception as e:  # noqa: BLE001
                search_svc.log_call("dehashed", value, False, repr(e))

    with session_scope() as db:
        order = db.get(Order, order_id)
        for it in new_items:
            db.add(Finding(
                order_id=order.id, source=it["source"], query_type=it["query_type"], query_value=it["query_value"],
                url=it["url"], domain=search_svc.domain_of(it["url"]), title=it.get("title", ""),
                snippet=it.get("snippet", ""), match_type=it.get("match_type", "text"),
                kind=it.get("kind") or guess_kind(it["url"], it.get("title", "")),
            ))
            added += 1
        order.status = "review"
        order.api_cost_usd = (order.api_cost_usd or 0) + cost
        svc = SERVICES.get(order.service, {})
        if svc.get("kind") == "subscription":
            order.status = "monitoring"
            order.next_scan_at = now() + timedelta(days=svc.get("scan_every_days", 14))
    enriched = enrich_order(order_id)
    return {"added": added, "not_configured": sorted(missing_all), "queries": len(search_svc.build_queries(queries)), "enrich": enriched}


def enrich_order(order_id: int, finding_ids: list[int] | None = None) -> dict:
    """Верификация страниц + сверка фото + правила классификации + автофиксация подтверждённых."""
    from .services import enrich as en
    from .services import images as img_svc
    from .services import rules
    with session_scope() as db:
        order = db.get(Order, order_id)
        q = order.queries or {}
        nicks, phones, city, real_name = q.get("nicks", []), q.get("phones", []), q.get("city", ""), q.get("real_name", "")
        targets = [f.id for f in order.findings if (finding_ids is None or f.id in finding_ids) and f.status == "new" and "://" in f.url and not f.url.startswith("dehashed://")]
        refs = img_svc.reference_hashes(order.client)
    verified, confirmed, ignored, to_capture = 0, 0, 0, []
    for fid in targets:
        with session_scope() as db:
            f = db.get(Finding, fid)
            url = f.url
        v = en.verify_page(url, nicks, phones, city, real_name)
        verified += 1
        # сверка картинок страницы с эталонами — только если страница жива и фото ещё не сверялось
        if v.get("http_status") and v["http_status"] < 400 and refs and not (f.match_type in ("exact", "similar")):
            m = img_svc.match_page(url, refs)
            if m["match_type"]:
                v["photo_match"] = {"type": m["match_type"], "confidence": m["confidence"], "image": m["image_url"]}
        with session_scope() as db:
            f = db.get(Finding, fid)
            if v.get("photo_match"):
                f.match_type, f.confidence = v["photo_match"]["type"], v["photo_match"]["confidence"]
            if v.get("title") and not f.title:
                f.title = v["title"]
            f.verify = {k: val for k, val in v.items() if k != "text_sample"}
            r = rules.classify(f, v)
            f.kind, f.risk = r["kind"], r["risk"]
            if r["confidence"] is not None:
                f.confidence = r["confidence"]
            f.auto_note = "; ".join(r["reasons"])
            if r["status"] != f.status:
                f.status = r["status"]
                if r["status"] == "confirmed":
                    confirmed += 1
                    to_capture.append(fid)
                elif r["status"] == "ignored":
                    ignored += 1
            f.last_checked_at = now()
    for fid in to_capture:  # автофиксация доказательств по подтверждённым
        try:
            capture_finding(fid)
        except Exception as e:  # noqa: BLE001
            search_svc.log_call("capture", str(fid), False, repr(e))
    return {"verified": verified, "auto_confirmed": confirmed, "auto_ignored": ignored, "captured": len(to_capture)}


# --- точные копии фото (M4, pHash) ----------------------------------------------

def match_photos(order_id: int, finding_ids: list[int] | None = None) -> dict:
    """Проходит по страницам находок и ищет на них копии эталонов клиентки."""
    from .services import images as img_svc

    with session_scope() as db:
        order = db.get(Order, order_id)
        refs = img_svc.reference_hashes(order.client)
        targets = [(f.id, f.url) for f in order.findings
                   if (finding_ids is None or f.id in finding_ids) and f.status != "ignored" and not f.url.startswith("dehashed://")]
    if not refs:
        return {"error": "у клиентки нет эталонных фото", "checked": 0}
    from .services import face as face_svc
    face_refs = []
    if face_svc.available():
        with session_scope() as db:
            face_refs = face_svc.reference_embeddings(db.get(Order, order_id).client)
    matched = 0
    for fid, url in targets:
        res = img_svc.match_page(url, refs)
        if not res["match_type"] and face_refs:
            fr = face_svc.match_page_faces(url, face_refs)
            if fr["similarity"] >= face_svc.SAME:
                res.update({"match_type": "similar", "confidence": round(fr["similarity"], 2), "image_url": fr["image_url"], "ref_file": "face"})
        with session_scope() as db:
            f = db.get(Finding, fid)
            f.last_checked_at = now()
            if res["match_type"]:
                matched += 1
                f.match_type, f.confidence = res["match_type"], res["confidence"]
                f.snippet = (f.snippet + f" | копия эталона {res['ref_file'].rsplit('/', 1)[-1]}: {res['image_url']}")[:2000]
                if f.kind == "unknown":
                    f.kind = guess_kind(f.url, f.title) if guess_kind(f.url, f.title) != "unknown" else "anketa"
                if f.status == "new":
                    f.status = "confirmed"
    return {"checked": len(targets), "matched": matched, "refs": len(refs)}


# --- внешний поиск по лицу (FaceCheck API) --------------------------------------

def face_search_external(order_id: int, max_refs: int = 3) -> dict:
    """Отправляет до 3 эталонов в FaceCheck, новые URL кладёт в находки."""
    from .services import facecheck, search4faces
    providers = {"facecheck": facecheck.search, "search4faces": search4faces.search}
    from .services import face as face_svc
    with session_scope() as db:
        order = db.get(Order, order_id)
        all_refs = [p["file"] for p in (order.client.reference_photos or [])]
        refs = all_refs[:max_refs]
        if face_svc.available() and len(all_refs) > 1:
            pick = face_svc.pick_references(order.client, max_refs)
            if pick["picked"]:
                refs = pick["picked"]
        existing = {f.url for f in order.findings}
    if not refs:
        return {"error": "нет эталонов"}
    added, errors, not_configured = 0, [], set()
    for ref in refs:
        data = (settings.storage_dir / ref).read_bytes()
        for name, fn in providers.items():
            if name in not_configured:
                continue
            try:
                hits = fn(data)
            except search_svc.NotConfigured:
                not_configured.add(name)
                continue
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e!r}")
                continue
            with session_scope() as db:
                for h in hits:
                    if h["url"] in existing:
                        continue
                    existing.add(h["url"])
                    db.add(Finding(order_id=order_id, source=name, query_type="face", query_value=ref.rsplit("/", 1)[-1],
                                   url=h["url"], domain=search_svc.domain_of(h["url"]), title=h["title"], snippet=h.get("snippet", ""),
                                   match_type=h["match_type"], confidence=h["confidence"],
                                   kind="fake" if name == "search4faces" else guess_kind(h["url"]), status="new"))
                    added += 1
    with session_scope() as db:
        o = db.get(Order, order_id)
        o.api_cost_usd = (o.api_cost_usd or 0) + len(refs) * (0.30 if "facecheck" not in not_configured else 0) + len(refs) * (0.015 if "search4faces" not in not_configured else 0)
    enriched = enrich_order(order_id) if added else {}
    return {"added": added, "refs": len(refs), "picked": [r.rsplit("/", 1)[-1] for r in refs], "errors": errors, "not_configured": sorted(not_configured), "enrich": enriched}


def import_pimeyes(order_id: int, data: bytes, filename: str, operator: str = "", source: str = "pimeyes") -> dict:
    from .services import pimeyes_import
    parsed = pimeyes_import.parse(data, filename)
    added = 0
    with session_scope() as db:
        order = db.get(Order, order_id)
        existing = {f.url for f in order.findings}
        for url in parsed["urls"]:
            if url in existing:
                continue
            existing.add(url)
            conf = parsed["scores"].get(url)
            db.add(Finding(order_id=order_id, source=source, query_type="face", query_value=filename[:60], url=url,
                           domain=search_svc.domain_of(url), match_type="similar" if (conf or 0) < 0.9 else "exact",
                           confidence=conf, kind=guess_kind(url), status="new", operator=operator))
            added += 1
        if order.status in ("new", "scanning"):
            order.status = "review"
    return {"urls_found": len(parsed["urls"]), "added": added, "skipped_duplicates": len(parsed["urls"]) - added}


# --- доказательства ------------------------------------------------------------

def capture_finding(finding_id: int) -> dict:
    with session_scope() as db:
        f = db.get(Finding, finding_id)
        url = f.url
    data = ev_svc.capture(url, finding_id)
    with session_scope() as db:
        f = db.get(Finding, finding_id)
        db.add(Evidence(finding_id=f.id, **data))
        if data.get("screenshot_file") and not f.screenshot_file:
            f.screenshot_file = data["screenshot_file"]
    return {k: v for k, v in data.items() if k in ("sha256", "archive_url", "archive_today_url", "http_status", "note")}


# --- снятия ------------------------------------------------------------------

def draft_takedown(db, finding: Finding, template: str | None = None, extra: dict | None = None, wave: int = 1) -> Takedown:
    key, plat = td_svc.resolve_platform(finding.url)
    tpl = template or plat.get("template", "dmca")
    lang = td_svc.lang_for(finding.domain or search_svc.domain_of(finding.url), plat)
    subject, body = td_svc.render(tpl, finding, extra, lang)
    td = Takedown(
        finding_id=finding.id, platform=plat.get("name", key), channel=plat.get("channel", "email"),
        template=tpl, to_address=plat.get("to", ""), form_url=plat.get("form", ""),
        subject=subject, body=body, wave=wave,
    )
    td.add_log(f"черновик по шаблону {tpl} ({lang}); площадка {key}; " + (plat.get("notes") or ""))
    if key == "*" or (td.channel == "email" and not td.to_address):
        # неизвестная площадка: ищем abuse-контакт через RDAP
        from .services import abuse_lookup
        info = abuse_lookup.lookup(finding.domain or search_svc.domain_of(finding.url))
        if info["suggested_channel"] == "form":
            td.channel, td.form_url, td.platform = "form", info["suggested_to"], f"{finding.domain} (за Cloudflare)"
        elif info["suggested_to"]:
            td.to_address, td.platform = info["suggested_to"], f"{finding.domain} → хостер/регистратор"
        td.add_log("RDAP: " + "; ".join(filter(None, [
            f"регистратор {', '.join(info['registrar_abuse'])}" if info["registrar_abuse"] else "",
            f"хостер {', '.join(info['hoster_abuse'])} (ip {info['ip']})" if info["hoster_abuse"] else "",
            *info["notes"]])) or "RDAP: контакт не найден — вписать вручную")
    db.add(td)
    finding.status = "takedown"
    db.flush()
    return td


def send_takedown(takedown_id: int) -> dict:
    with session_scope() as db:
        td = db.get(Takedown, takedown_id)
        if td.channel == "email" and td.to_address:
            try:
                mid = td_svc.send_email(td.to_address, td.subject, td.body)
                td.provider_message_id, td.status, td.sent_at = mid, "sent", now()
                td.next_check_at = td_svc.next_check()
                td.add_log(f"отправлено на {td.to_address}, id {mid}")
                return {"sent": True, "message_id": mid}
            except td_svc.NotConfigured:
                td.status = "draft"
                td.add_log("почтовый провайдер не настроен — отправить вручную из корпоративного ящика и нажать «Отмечено отправленным»")
                return {"sent": False, "reason": "mail not configured"}
        # форма или ручной канал: оператор отправляет сам
        td.add_log("канал требует ручной отправки (веб-форма). После подачи — «Отмечено отправленным»")
        return {"sent": False, "reason": "manual channel", "form_url": td.form_url}


def mark_sent(takedown_id: int, note: str = "") -> None:
    with session_scope() as db:
        td = db.get(Takedown, takedown_id)
        td.status, td.sent_at, td.next_check_at = "sent", now(), td_svc.next_check()
        td.add_log("отмечено отправленным оператором" + (f": {note}" if note else ""))


# --- повторная проверка -------------------------------------------------------

def recheck_takedown(takedown_id: int) -> dict:
    with session_scope() as db:
        td = db.get(Takedown, takedown_id)
        f = td.finding
        url, marker = f.url, (f.order.queries.get("nicks") or [""])[0]
    up, code = ev_svc.is_still_up(url, marker)
    with session_scope() as db:
        td = db.get(Takedown, takedown_id)
        f = td.finding
        f.last_checked_at = now()
        if up is None:
            td.add_log("проверка не удалась (сеть/прокси) — повторить позже")
            td.next_check_at = now() + timedelta(hours=6)
            return {"up": None, "http_status": None}
        if not up:
            td.status, f.status = "removed", "removed"
            td.add_log(f"проверка: страница недоступна (HTTP {code}) → снято")
            td.next_check_at = now() + timedelta(days=14)  # контроль возврата
        else:
            td.add_log(f"проверка: страница на месте (HTTP {code})")
            if td.status == "removed":
                td.status, f.status = "escalated", "returned"
                td.add_log("вернулась после снятия → эскалация, вторая волна")
            elif td.status == "sent" and td.sent_at and now() - td.sent_at > timedelta(days=settings.takedown_recheck_days):
                td.status = "escalated"
                td.add_log("нет реакции за срок → эскалация (хостер / поисковик / платёжка, SOP-9)")
            td.next_check_at = td_svc.next_check()
    return {"up": up, "http_status": code}


# --- отчёт клиентке -----------------------------------------------------------

def build_report_text(order: Order) -> str:
    svc = SERVICES.get(order.service, {})
    by_kind: dict[str, int] = {}
    removed = sum(1 for f in order.findings if f.status == "removed")
    for f in order.findings:
        if f.status != "ignored":
            by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    lines = [f"<b>PRIVÉ · {svc.get('name', order.service)}</b>", ""]
    if order.service == "check_free":
        names = {"anketa": "анкет с твоими фото", "fake": "фейков", "leak": "сливов", "mention": "упоминаний", "unknown": "совпадений"}
        conf = {}
        pending = 0
        for f in order.findings:
            if f.status in ("confirmed", "takedown", "removed", "returned"):
                conf[f.kind] = conf.get(f.kind, 0) + 1
            elif f.status == "new":
                pending += 1
        lines.append("Подтверждено, что это ты:")
        for k, n in conf.items():
            lines.append(f"• {names.get(k, k)}: {n}")
        if not conf:
            lines.append("• подтверждённых находок нет")
        if pending:
            lines.append(f"\nЕщё {pending} совпадений требуют ручной проверки — разберём в «Проверке».")
        lines += ["", "Ссылки, оценка риска и план снятия — в «Проверке» за $190; эти деньги засчитываются в следующий заказ."]
    else:
        lines.append(f"Находок всего: {len(order.findings)}, снято: {removed}")
        for f in order.findings:
            if f.status == "ignored":
                continue
            lines.append(f"• [{f.kind}] {f.url} — {f.status}")
    lines += ["", "Отчёт сформирован автоматически. Вопросы — ответом в этот чат."]
    return "\n".join(lines)


def send_report(order_id: int) -> dict:
    with session_scope() as db:
        order = db.get(Order, order_id)
        text = build_report_text(order)
        chat = order.client.telegram_id
        order.last_report_at = now()
        if order.service == "check_free" and not order.free_report_sent_at:
            order.free_report_sent_at = now()
    ok = tg_svc.send_message(chat, text) if chat else False
    return {"sent": ok, "preview": text}


def sla_reminders(hours_before: int = 4) -> int:
    """Напоминание дежурным за N часов до дедлайна по незакрытым заказам (один раз)."""
    n = 0
    with session_scope() as db:
        soon = now() + timedelta(hours=hours_before)
        for o in db.query(Order).filter(Order.due_at <= soon, Order.status.notin_(["done", "cancelled"]), Order.sla_reminded_at.is_(None)).all():
            left = o.due_at - now()
            mins = int(left.total_seconds() // 60)
            text = (f"Срок по заказу #{o.id} ({SERVICES.get(o.service, {}).get('name', o.service)}, {o.client.code}) "
                    + (f"истекает через {mins // 60} ч {mins % 60} мин" if mins > 0 else f"просрочен на {-mins // 60} ч") + ". Написать клиентке до дедлайна.")
            if tg_svc.alert_operators(text):
                o.sla_reminded_at = now()
                n += 1
    return n


# --- планировщик ----------------------------------------------------------------

def scheduled_tick() -> dict:
    """Вызывается каждый час: повторные проверки снятий, сканы подписок, удаление эталонов, напоминания."""
    done = {"rechecks": 0, "scans": 0, "photos_purged": 0}
    with session_scope() as db:
        due_td = db.query(Takedown).filter(Takedown.next_check_at <= now(), Takedown.status.in_(["sent", "removed", "escalated"])).all()
        td_ids = [t.id for t in due_td]
        due_orders = db.query(Order).filter(Order.next_scan_at <= now(), Order.status == "monitoring").all()
        order_ids = [o.id for o in due_orders]
        from .models import Client
        for c in db.query(Client).filter(Client.photos_expire_at <= now()).all():
            if c.reference_photos and not any(o.status in ("new", "scanning", "review", "takedown", "monitoring") for o in c.orders):
                for p in c.reference_photos:
                    try:
                        (settings.storage_dir / p["file"]).unlink(missing_ok=True)
                    except Exception:
                        pass
                c.reference_photos, c.photos_expire_at = [], None
                done["photos_purged"] += 1
    from . import bot as bot_mod
    done["payment_reminders"] = bot_mod.payment_reminders()
    done["sla_reminders"] = sla_reminders()
    for tid in td_ids:
        recheck_takedown(tid)
        done["rechecks"] += 1
    for oid in order_ids:
        scan_order(oid)
        done["scans"] += 1
        with session_scope() as db:
            new_ids = [f.id for f in db.get(Order, oid).findings if f.status == "new"]
        if new_ids:
            match_photos(oid, new_ids)
            # копии эталонов в подписке снимаются без участия оператора (карта 7, п. 2)
            with session_scope() as db:
                for f in db.get(Order, oid).findings:
                    if f.id in new_ids and f.match_type == "exact" and not f.takedowns:
                        draft_takedown(db, f)
                        done["auto_takedowns"] = done.get("auto_takedowns", 0) + 1
        with session_scope() as db:
            o = db.get(Order, oid)
            if o.monitoring_until and o.monitoring_until < now():
                o.status = "done"
        send_report(oid)
    return done
