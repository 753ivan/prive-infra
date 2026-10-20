"""Сценарий Telegram-бота PRIVÉ (M1). Одна точка входа: handle_update(update).
Работает и через webhook, и через polling (scripts/bot_polling.py).

Состояние диалога хранится в Client.bot_state = {"step": ..., "draft": {...}}.
Язык бота — «девочки», коротко, без драмы (по анализу чатов).
"""
from __future__ import annotations

from datetime import timedelta

from .config import settings
from .db import session_scope
from .models import SERVICES, Client, Finding, Incident, Order, now
from .services import telegram as tg
from .services.telegram import keyboard as kb

MENU = kb([
    [("🔍 Проверка", "menu:check"), ("🆘 SOS", "menu:sos")],
    [("💼 Услуги и цены", "menu:services"), ("👯 Мой код для подруги", "menu:ref")],
    [("📖 Инструкции", "menu:help"), ("👤 Мои заказы", "menu:orders")],
])

SERVICE_MENU_IDS = ["check", "fakes", "leaks", "invisible", "sos", "sub_lite", "sub_pro", "sub_vip", "exit_std"]

HELP_TEXTS = {
    "stopncii": ("StopNCII — отпечатки интимных фото",
                 "1. Открой stopncii.org → «Create your case». Фото никуда не уходит: отпечаток считается на твоём телефоне.\n"
                 "2. Подтверди 18+ и что на фото ты.\n3. Выбери файлы по одному, видео тоже можно.\n"
                 "4. Пришли сюда номер кейса — внесём в твой заказ.\n\nРаботает на Facebook, Instagram, TikTok, Reddit, Bumble, OnlyFans и др. На эскорт-сайтах и в Telegram — нет, там работаем мы."),
    "optout": ("Убрать лицо из PimEyes и FaceCheck",
               "PimEyes: раздел Opt-out request — нужны селфи и фото документа с закрытыми данными (кроме фото и имени). Отправляешь сама, через нас документы не идут.\n"
               "FaceCheck.ID: форма Remove my face, селфи для сверки.\n\nРассмотрение до 14 дней. Через 14 дней мы проверим, что тебя больше не находят."),
    "phone": ("Убрать номер из GetContact / Truecaller",
              "GetContact: getcontact.com → Unlist → код из SMS.\nTruecaller: truecaller.com/unlisting.\nNumBuster: запрос в поддержку приложения.\n\nЧерез 7 дней проверим, определяется ли номер."),
    "memo": ("Как не спалиться — памятка",
             "• Рабочий и личный номер — разные сим-карты, лучше eSIM другого оператора.\n• Рабочий Telegram на рабочем номере; «Кто видит номер» → никто; «Находить по номеру» → контакты.\n"
             "• Фон на рабочих фото не должен совпадать с личными (окна, мебель, татуировки).\n• Один ник на всех площадках = одна ниточка ко всему.\n• Выключи автозагрузку рабочих фото в iCloud/Google Фото.\n• Метаданные с фото убирай перед выкладкой — пришли фото в бот с подписью «чистить».\n"
             "• Не плати шантажисту: после первой оплаты требуют ещё. Пиши SOS."),
    "rules": ("Наши правила",
              "Работаем только с той, кто на фото. Никого не пробиваем и не ищем — ни клиентов, ни шантажистов. Эталонные фото удаляем через 7 дней после заказа, кружок — после сверки. В выписке по карте — «Digital Privacy Services»."),
}


def _services_text() -> str:
    lines = ["<b>Услуги</b>"]
    for sid in SERVICE_MENU_IDS:
        s = SERVICES[sid]
        price = f"${s['price']}" + ("/мес" if s["kind"] == "subscription" else "")
        lines.append(f"• {s['name']} — {price}")
    lines.append("\nБесплатная проверка — по кружку. Полный прайс и условия: pri.ve (сайт).")
    return "\n".join(lines)


def _payment_text(order: Order) -> str:
    return (f"<b>{SERVICES[order.service]['name']}</b> — ${order.price_usd:.0f}\n\n"
            f"USDT (TRC-20): <code>{settings.usdt_trc20_address or 'адрес выдаст оператор'}</code>\n"
            f"Комментарий к переводу не нужен. Карта — напиши оператору, в выписке будет «Digital Privacy Services».\n\n"
            f"После перевода нажми «Я оплатила» — оператор подтвердит и запустит работу.")


def _order_pay_kb(order_id: int) -> dict:
    return kb([[("✅ Я оплатила", f"paid:{order_id}"), ("✖ Отменить", f"cancel:{order_id}")]])


# ---------------------------------------------------------------------------

def handle_update(upd: dict) -> None:
    if "callback_query" in upd:
        _handle_callback(upd["callback_query"])
    elif "message" in upd:
        _handle_message(upd["message"])


def _get_client(db, chat_id: str, text: str = "") -> Client:
    c = db.query(Client).filter(Client.telegram_id == chat_id).first()
    if not c:
        ref = text.split(maxsplit=1)[1].strip().upper() if text.lower().startswith("/start") and " " in text else ""
        if ref.lower() in DEEP_LINKS:  # deep-link с сайта, не реф-код
            ref = ""
        c = Client(telegram_id=chat_id, referred_by=ref)
        db.add(c)
        db.flush()
        tg.send_message(chat_id, "PRIVÉ. Не для чужих глаз.\n\nЗакрытый сервис: находим твои фото и данные в сети и убираем их. Сначала — что тебе нужно?", MENU)
        if ref:
            tg.send_message(chat_id, "Код подруги принят: −20% на первый заказ и проверка без очереди.")
    return c


def _set(c: Client, step: str | None, **draft):
    st = dict(c.bot_state or {})
    st["step"] = step
    st["draft"] = {**st.get("draft", {}), **draft} if step else {}
    c.bot_state = st


def _handle_message(msg: dict) -> None:
    chat_id = str(msg["chat"]["id"])
    text = (msg.get("text") or msg.get("caption") or "").strip()
    with session_scope() as db:
        c = _get_client(db, chat_id, text)
        step = (c.bot_state or {}).get("step")
        low = text.lower()

        # --- файлы ---
        if msg.get("photo"):
            if low == "чистить" or step == "clean_photo":
                _clean_photo(db, c, msg)
                return
            _save_photo(db, c, msg)
            if step == "await_photos" and len(c.reference_photos) >= 3:
                _ask_circle(c)
            return
        if msg.get("video_note"):
            try:
                rel, _ = tg.download_file(msg["video_note"]["file_id"], settings.storage_dir / "refs" / c.code)
                c.circle_file = rel
                c.verification_note = "кружок получен"
            except Exception as e:  # noqa: BLE001
                tg.send_message(chat_id, "Кружок не загрузился, попробуй ещё раз.")
                return
            tg.send_message(chat_id, "Кружок получен. Сверим с фото и напишем — обычно в течение часа.")
            if step == "await_circle":
                _ask_nicks(c)
            _auto_verify(db, c)
            return
        if msg.get("forward_from") or msg.get("forward_sender_name") or msg.get("forward_date"):
            _save_forward(db, c, msg)
            return

        # --- команды ---
        if low.startswith("/start") or low in ("меню", "/menu"):
            _set(c, None)
            arg = low.split(maxsplit=1)[1].strip() if " " in low else ""
            if arg in DEEP_LINKS:  # кнопки с сайта: t.me/<bot>?start=sos|scan|partner|private
                _deep_link(db, c, arg)
                return
            tg.send_message(chat_id, "Что нужно?", MENU)
            return
        if low in ("sos", "сос", "/sos"):
            _start_sos(db, c)
            return
        if low in ("проверка", "проверить", "/check"):
            _start_check(c)
            return

        # --- шаги диалога ---
        if step == "await_nicks":
            _set(c, "await_phone", nicks=[t.strip() for t in text.replace(";", ",").split(",") if t.strip()])
            tg.send_message(chat_id, "Рабочий номер, если есть (или «нет»):")
            return
        if step == "await_phone":
            _set(c, "await_city", phones=[] if low in ("нет", "-", "no") else [text])
            tg.send_message(chat_id, "Город, где работаешь (один, главный):")
            return
        if step == "await_city":
            draft = dict(c.bot_state["draft"], city=text)
            _create_order(db, c, draft.get("service", "check_free"), {"nicks": draft.get("nicks", []), "phones": draft.get("phones", []), "city": text})
            _set(c, None)
            return
        if step == "sos_collect":
            _append_incident(db, c, "сообщение: " + text)
            tg.send_message(chat_id, "Записала. Пересылай дальше всё, что он пишет; оператор уже в курсе.")
            return
        if step == "stopncii_case":
            _append_incident(db, c, "", stopncii=text)
            _set(c, None)
            tg.send_message(chat_id, "Номер кейса сохранён. Спасибо.", MENU)
            return

        # свободный текст без шага
        tg.send_message(chat_id, "Не поняла. Выбери в меню:", MENU)


def _handle_callback(cq: dict) -> None:
    chat_id = str(cq["message"]["chat"]["id"])
    data = cq.get("data", "")
    tg.answer_callback(cq["id"])
    with session_scope() as db:
        c = _get_client(db, chat_id)
        if data == "menu:check":
            _start_check(c)
        elif data == "menu:sos":
            _start_sos(db, c)
        elif data == "menu:services":
            rows = [[(SERVICES[s]["name"] + f" ${SERVICES[s]['price']}", f"svc:{s}")] for s in SERVICE_MENU_IDS]
            rows.append([("◀ Меню", "menu:main")])
            tg.send_message(chat_id, _services_text(), kb(rows))
        elif data == "menu:ref":
            friends = db.query(Client).filter(Client.referred_by == c.referral_code).count()
            tg.send_message(chat_id, f"Твой код: <code>{c.referral_code}</code>\nПодруга пишет боту /start {c.referral_code} и получает −20% на первый заказ. Тебе $100 на баланс через 14 дней после её оплаты.\n\nПодруг по коду: {friends}. Баланс: ${c.balance_usd:.0f}.", MENU)
        elif data == "menu:help":
            rows = [[(t, f"help:{k}")] for k, (t, _) in HELP_TEXTS.items()] + [[("◀ Меню", "menu:main")]]
            tg.send_message(chat_id, "Инструкции:", kb(rows))
        elif data.startswith("help:"):
            title, body = HELP_TEXTS[data[5:]]
            extra = kb([[("Прислать номер кейса", "stopncii:case")], [("◀ Меню", "menu:main")]]) if data == "help:stopncii" else MENU
            tg.send_message(chat_id, f"<b>{title}</b>\n\n{body}", extra)
        elif data == "stopncii:case":
            _set(c, "stopncii_case")
            tg.send_message(chat_id, "Пришли номер кейса StopNCII одним сообщением:")
        elif data == "menu:orders":
            if not c.orders:
                tg.send_message(chat_id, "Заказов пока нет.", MENU)
            else:
                lines = [f"#{o.id} {SERVICES[o.service]['name']} — {o.to_dict()['status_ru']}, оплачено ${o.paid_usd:.0f}/{o.price_usd:.0f}" for o in sorted(c.orders, key=lambda o: o.id, reverse=True)[:8]]
                tg.send_message(chat_id, "\n".join(lines), MENU)
        elif data == "menu:main":
            _set(c, None)
            tg.send_message(chat_id, "Что нужно?", MENU)
        elif data == "consent:yes":
            c.consent_at, c.consent_version = now(), CONSENT_VERSION
            nxt = (c.bot_state or {}).get("draft", {}).get("next_step", "check")
            tg.send_message(chat_id, "Спасибо, записала.")
            if nxt.startswith("svc:"):
                _set(c, "await_photos" if not c.reference_photos else "await_nicks", service=nxt[4:])
                tg.send_message(chat_id, "Пришли 3–10 фото, по которым будем искать." if not c.reference_photos else "Рабочие ники через запятую:")
            else:
                _start_check(c)
        elif data == "consent:no":
            _set(c, None)
            tg.send_message(chat_id, "Хорошо. Без согласия искать не можем — вернись, когда будешь готова.", MENU)
        elif data.startswith("svc:"):
            sid = data[4:]
            if sid == "sos":
                _start_sos(db, c)
            elif _need_consent(c):
                _ask_consent(c, f"svc:{sid}")
            elif not c.reference_photos:
                _set(c, "await_photos", service=sid)
                tg.send_message(chat_id, "Сначала пришли 3–10 фото, по которым будем искать (можно рабочие). Потом кружок.")
            else:
                _set(c, "await_nicks", service=sid)
                tg.send_message(chat_id, "Рабочие ники через запятую:")
        elif data.startswith("paid:"):
            o = db.get(Order, int(data[5:]))
            if o and o.client_id == c.id:
                o.payment_claimed_at = now()
                o.payment_method = o.payment_method or "usdt"
                tg.send_message(chat_id, "Спасибо. Оператор проверит перевод и запустит работу — напишем, когда начнём.")
                tg.alert_operators(f"Оплата заявлена: заказ #{o.id} ({SERVICES[o.service]['name']}, ${o.price_usd:.0f}), клиентка {c.code}. Проверить и отметить в панели.")
        elif data.startswith("cancel:"):
            o = db.get(Order, int(data[7:]))
            if o and o.client_id == c.id and o.status in ("new", "verifying"):
                o.status = "cancelled"
                tg.send_message(chat_id, "Отменила.", MENU)
        elif data.startswith("own:"):
            _, fid, ans = data.split(":")
            f = db.get(Finding, int(fid))
            if f and f.order.client_id == c.id:
                f.status = "confirmed" if ans == "no" else "ignored"
                f.snippet = (f.snippet + f" | клиентка: {'не её размещение' if ans == 'no' else 'её собственное'}")[:2000]
                tg.send_message(chat_id, "Поняла, спасибо." if ans == "no" else "Ок, не трогаем.")


# ---------------------------------------------------------------------------

def _save_photo(db, c: Client, msg: dict):
    try:
        rel, sha = tg.download_file(msg["photo"][-1]["file_id"], settings.storage_dir / "refs" / c.code)
    except Exception as e:  # noqa: BLE001
        tg.send_message(c.telegram_id, "Фото не загрузилось, попробуй ещё раз.")
        return
    c.reference_photos = [*c.reference_photos, {"file": rel, "sha256": sha, "uploaded_at": now().isoformat()}][:10]
    c.photos_expire_at = tg.photo_expiry()
    n = len(c.reference_photos)
    tg.send_message(c.telegram_id, f"Фото принято ({n}/10)." + ("" if n >= 3 else f" Ещё минимум {3 - n}."))


def _clean_photo(db, c: Client, msg: dict):
    from .services import images as img
    try:
        rel, _ = tg.download_file(msg["photo"][-1]["file_id"], settings.storage_dir / "clean" / c.code)
        src = settings.storage_dir / rel
        clean = img.strip_metadata(src.read_bytes())
        out = src.with_name("clean_" + src.name)
        out.write_bytes(clean)
        src.unlink(missing_ok=True)
        tg.send_document(c.telegram_id, out, "Без метаданных. Файл удалён с нашей стороны.")
        out.unlink(missing_ok=True)
    except Exception:
        tg.send_message(c.telegram_id, "Не получилось обработать это фото.")
    _set(c, None)


CONSENT_VERSION = "2026-10-20"
CONSENT_TEXT = ("Перед поиском нужно твоё согласие: мы ищем только твоё лицо и твои ники, передаём твои фото сервисам поиска по лицу "
                "(без имени), удаляем эталоны через 7 дней после заказа. Никого другого не ищем и не пробиваем.")


def _need_consent(c: Client) -> bool:
    return not c.consent_at or c.consent_version != CONSENT_VERSION


def _ask_consent(c: Client, next_step: str):
    _set(c, "await_consent", next_step=next_step)
    tg.send_message(c.telegram_id, CONSENT_TEXT, kb([[("✅ Согласна", "consent:yes"), ("Не сейчас", "consent:no")]]))


def _ask_circle(c: Client):
    if c.circle_file or c.status == "verified":
        _ask_nicks(c)
        return
    _set(c, "await_circle")
    tg.send_message(c.telegram_id, "Теперь кружок — короткое видео-сообщение с лицом, чтобы мы знали, что это ты. Удаляем после сверки.")


def _ask_nicks(c: Client):
    _set(c, "await_nicks")
    tg.send_message(c.telegram_id, "Рабочие ники через запятую (как в анкетах):")


def _start_check(c: Client):
    if _need_consent(c):
        _ask_consent(c, "check")
        return
    _set(c, "await_photos" if len(c.reference_photos) < 3 else ("await_circle" if not (c.circle_file or c.status == "verified") else "await_nicks"), service="check_free")
    step = c.bot_state["step"]
    if step == "await_photos":
        tg.send_message(c.telegram_id, "Пришли 3–10 фото, по которым ищем (рабочие подойдут). Потом кружок — и запускаем.")
    elif step == "await_circle":
        _ask_circle(c)
    else:
        _ask_nicks(c)


def _create_order(db, c: Client, service: str, queries: dict):
    o = Order(client_id=c.id, service=service, queries=queries, status="new" if c.status == "verified" else "verifying")
    o.set_defaults()
    if c.referred_by and not any(x.paid_usd > 0 for x in c.orders) and o.price_usd:
        o.price_usd = round(o.price_usd * 0.8)
        o.notes = f"−20% по коду {c.referred_by}"
    db.add(o)
    db.flush()
    if o.price_usd:
        tg.send_message(c.telegram_id, _payment_text(o), _order_pay_kb(o.id))
    else:
        tg.send_message(c.telegram_id, "Заявка принята. Проверка займёт до 24 часов — пришлю цифры: сколько анкет, фейков, сливов и совпадений по лицу.", MENU)
    tg.alert_operators(f"Новый заказ #{o.id}: {SERVICES[service]['name']}, клиентка {c.code}" + (" (ждёт верификации)" if c.status != "verified" else ""))


DEEP_LINKS = {"sos", "scan", "check", "partner", "private"}


def _deep_link(db, c: Client, arg: str):
    if arg == "sos":
        _start_sos(db, c)
    elif arg in ("scan", "check"):
        _start_check(c)
    elif arg == "partner":
        tg.send_message(c.telegram_id, "<b>Партнёрская программа</b>\n\nДля агентств, менеджеров, админов болталок, фотографов: "
                        "20–30% с первого заказа, 10–15% с подписки.\n\nНапиши одним сообщением, кто ты и откуда к тебе приходят девочки — "
                        "оператор выдаст код и ссылку.", MENU)
        tg.alert_operators(f"Партнёр: клиент {c.code} (tg {c.telegram_id}) пришёл по ссылке «Партнёрам». Написать и выдать код.")
    elif arg == "private":
        tg.send_message(c.telegram_id, "<b>PRIVÉ Private</b> — персональное сопровождение по договору и NDA.\n\n"
                        "Менеджер напишет сюда, чтобы договориться о приватной консультации (40 минут, бесплатно). "
                        "Заранее можно ничего не рассказывать — NDA подпишем до разговора.", MENU)
        tg.alert_operators(f"Private: клиент {c.code} (tg {c.telegram_id}) запросил консультацию. Связаться в течение часа.")


def _start_sos(db, c: Client):
    o = Order(client_id=c.id, service="sos_consult", status="new" if c.status == "verified" else "verifying")
    o.set_defaults()
    db.add(o)
    db.flush()
    db.add(Incident(order_id=o.id))
    _set(c, "sos_collect", order_id=o.id)
    tg.send_message(c.telegram_id, "Мы рядом. Три правила прямо сейчас:\n1. Никому не плати.\n2. Не удаляй переписку.\n3. Не отвечай ему — ответы согласуем.\n\nОператор напишет в течение 15 минут. Пока перешли сюда его сообщения (пересылкой, не скринами) и ник/номер.")
    tg.alert_operators(f"SOS: заказ #{o.id}, клиентка {c.code} (tg {c.telegram_id}). Ответить в течение 15 минут.")


def _append_incident(db, c: Client, text: str, stopncii: str = ""):
    oid = (c.bot_state or {}).get("draft", {}).get("order_id")
    inc = db.query(Incident).filter(Incident.order_id == oid).first() if oid else None
    if not inc:
        last = next((o for o in sorted(c.orders, key=lambda o: o.id, reverse=True) if SERVICES[o.service]["kind"] == "sos"), None)
        inc = db.query(Incident).filter(Incident.order_id == last.id).first() if last else None
    if not inc:
        return
    if text:
        inc.already_sent_to = (inc.already_sent_to + "\n" + text).strip()[:8000]
    if stopncii:
        inc.stopncii_case = stopncii


def _save_forward(db, c: Client, msg: dict):
    who = msg.get("forward_sender_name") or (msg.get("forward_from") or {}).get("username") or (msg.get("forward_from") or {}).get("first_name") or "скрытый отправитель"
    text = msg.get("text") or msg.get("caption") or "[медиа]"
    _append_incident(db, c, f"пересылка от «{who}» ({msg.get('forward_date', '')}): {text}")
    tg.send_message(c.telegram_id, "Сохранила.")


def _auto_verify(db, c: Client):
    """Сверка кружка с эталонами (InsightFace). Спорные случаи — оператору (SOP-3)."""
    from .services import face
    res = face.verify_circle(c)
    if not res:
        tg.alert_operators(f"Новый кружок на проверку: {c.code} (авто-сверка недоступна)")
        return
    c.verification_score = res.get("score")
    c.verification_note = f"авто: {res['decision']}, score {res.get('score')}, {res.get('reason', '')}".strip(", ")
    if res["decision"] == "verified":
        c.status = "verified"
        for o in c.orders:
            if o.status == "verifying":
                o.status = "new"
        tg.send_message(c.telegram_id, "Сверили — это ты. Можно запускать проверку.")
        _start_after_verify(db, c)
    elif res["decision"] == "rejected":
        tg.alert_operators(f"Кружок {c.code}: авто-сверка «не совпало» (score {res.get('score')}). Решить вручную.")
    else:
        tg.alert_operators(f"Кружок {c.code}: спорный (score {res.get('score')}). Посмотреть в панели.")


def _start_after_verify(db, c: Client):
    """Бесплатная проверка после верификации уходит в скан сама (карта 1, шаг 3)."""
    from . import pipeline
    for o in c.orders:
        if o.service == "check_free" and o.status == "new" and o.queries.get("nicks"):
            job = pipeline.enqueue(db, o.id, "scan", {"auto": True})
            db.flush()
            import threading
            threading.Thread(target=pipeline._run_job, args=(job.id, lambda oid=o.id: pipeline.scan_order(oid)), daemon=True).start()


# ---------------------------------------------------------------------------
# вызовы из панели

def ask_is_yours(finding_id: int) -> bool:
    with session_scope() as db:
        f = db.get(Finding, finding_id)
        chat = f.order.client.telegram_id
        url = f.url
    return tg.send_message(chat, f"Нашли анкету/страницу. Это твоё собственное размещение?\n{url}",
                           kb([[("Моё, не трогать", f"own:{finding_id}:yes"), ("Не моё — убирайте", f"own:{finding_id}:no")]]))


def payment_reminders() -> int:
    """Вызывается из планировщика: напоминание о неоплаченном заказе через 3 дня, один раз."""
    n = 0
    with session_scope() as db:
        for o in db.query(Order).filter(Order.status == "new", Order.paid_usd == 0, Order.price_usd > 0,
                                        Order.payment_claimed_at.is_(None), Order.payment_reminded_at.is_(None),
                                        Order.created_at <= now() - timedelta(days=3)).all():
            if o.client.telegram_id and tg.send_message(o.client.telegram_id, f"Заказ #{o.id} ждёт оплаты. Если передумала — просто отмени, без вопросов.", _order_pay_kb(o.id)):
                o.payment_reminded_at = now()
                n += 1
    return n
