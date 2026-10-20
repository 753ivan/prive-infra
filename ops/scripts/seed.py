"""Демо-данные для знакомства с панелью: python -m scripts.seed"""
from datetime import timedelta

from app.db import Base, engine, session_scope
from app.models import Client, Finding, Incident, Order, Takedown, now
from app.pipeline import draft_takedown

Base.metadata.create_all(engine)

with session_scope() as db:
    if db.query(Client).count():
        print("база не пуста — пропускаю")
        raise SystemExit
    c1 = Client(display="демо А", telegram_id="100001", status="verified")
    c2 = Client(display="демо Б", telegram_id="100002", status="new", verification_note="кружок: refs/demo/circle.mp4")
    c3 = Client(display="демо В", telegram_id="100003", status="verified")
    db.add_all([c1, c2, c3]); db.flush()

    o1 = Order(client_id=c1.id, service="check", status="review", paid_usd=190, queries={"nicks": ["Alisa_Kyiv", "alisa_vip"], "phones": ["+380991234567"], "emails": ["demo@example.com"]})
    o1.set_defaults()
    o2 = Order(client_id=c3.id, service="sos_consult", status="new")
    o2.set_defaults()
    o3 = Order(client_id=c1.id, service="sub_pro", status="monitoring", paid_usd=299, queries={"nicks": ["Alisa_Kyiv"]})
    o3.set_defaults(); o3.next_scan_at = now() + timedelta(days=5)
    o4 = Order(client_id=c2.id, service="check_free", status="verifying", queries={"nicks": ["nika_lux"]})
    o4.set_defaults()
    db.add_all([o1, o2, o3, o4]); db.flush()
    db.add(Incident(order_id=o2.id, demand="15 000 ₽ до вечера", what_they_have="скрины переписки, 2 фото"))

    demo = [
        ("google_cse", "nick", "Alisa_Kyiv", "https://example-dosug.site/anketa/48213", "anketa", "confirmed", 2),
        ("brave", "nick", "alisa_vip", "https://example-escort-forum.net/thread/9921", "mention", "new", 0),
        ("pimeyes", "face", "ref_01", "https://t.me/example_slivy/1550", "leak", "confirmed", 3),
        ("pimeyes", "face", "ref_02", "https://instagram.com/fake.alisa.k", "fake", "confirmed", 3),
        ("serpapi_yandex", "phone", "+380991234567", "https://example-numbers.ru/phone/380991234567", "mention", "ignored", 1),
    ]
    fs = []
    for src, qt, qv, url, kind, st, risk in demo:
        f = Finding(order_id=o1.id, source=src, query_type=qt, query_value=qv, url=url, domain=url.split("/")[2], kind=kind, status=st, risk=risk, match_type="exact" if qt == "face" else "text")
        db.add(f); fs.append(f)
    db.flush()
    t1 = draft_takedown(db, fs[0]); t1.to_address = "abuse@example-dosug.site"; t1.status = "sent"; t1.sent_at = now() - timedelta(days=8); t1.next_check_at = now() - timedelta(days=1); t1.add_log("отправлено (демо)")
    t2 = draft_takedown(db, fs[2], "ncii", {"stopncii_case": "SN-123456"})
    t3 = draft_takedown(db, fs[3]); t3.status = "removed"; t3.sent_at = now() - timedelta(days=3); fs[3].status = "removed"
    print("демо-данные созданы")
