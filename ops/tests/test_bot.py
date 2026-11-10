def _hook(client, m):
    return client.post("/telegram/webhook", json=m).json()


def test_bot_check_flow(client):
    chat = {"chat": {"id": 9001}}
    _hook(client, {"message": {**chat, "text": "/start REF-TEST"}})
    _hook(client, {"callback_query": {"id": "1", "data": "menu:check", "message": chat}})
    _hook(client, {"callback_query": {"id": "1", "data": "consent:yes", "message": chat}})
    for i in range(3):
        _hook(client, {"message": {**chat, "photo": [{"file_id": f"p{i}"}]}})
    _hook(client, {"message": {**chat, "video_note": {"file_id": "vn"}}})
    for t in ("nick_a, nick_b", "+380501112233", "Киев"):
        _hook(client, {"message": {**chat, "text": t}})
    cl = next(c for c in client.get("/api/clients").json() if c["telegram_id"] == "9001")
    assert len(cl["reference_photos"]) == 3 and cl["referred_by"] == "REF-TEST" and cl["consent_at"]
    o = next(o for o in client.get("/api/orders").json() if o["client_id"] == cl["id"])
    assert o["service"] == "check_free" and o["status"] == "verifying" and o["queries"]["city"] == "Киев"


def test_bot_sos_and_payment(client):
    chat = {"chat": {"id": 9002}}
    _hook(client, {"message": {**chat, "text": "/start"}})
    _hook(client, {"message": {**chat, "text": "SOS"}})
    _hook(client, {"message": {**chat, "text": "давай деньги", "forward_sender_name": "X", "forward_date": 1}})
    cl = next(c for c in client.get("/api/clients").json() if c["telegram_id"] == "9002")
    sos = next(o for o in client.get("/api/orders").json() if o["client_id"] == cl["id"] and o["service"] == "sos_consult")
    inc = client.get(f"/api/orders/{sos['id']}").json()["incident"]
    assert "давай деньги" in inc["already_sent_to"]
    _hook(client, {"callback_query": {"id": "2", "data": f"paid:{sos['id']}", "message": chat}})
    assert client.get(f"/api/orders/{sos['id']}").json()["payment_claimed_at"]


def test_circle_deleted_after_verify(client):
    from app.config import settings
    chat = {"chat": {"id": 9003}}
    _hook(client, {"message": {**chat, "text": "/start"}})
    _hook(client, {"callback_query": {"id": "1", "data": "menu:check", "message": chat}})
    _hook(client, {"callback_query": {"id": "1", "data": "consent:yes", "message": chat}})
    for i in range(3):
        _hook(client, {"message": {**chat, "photo": [{"file_id": f"q{i}"}]}})
    _hook(client, {"message": {**chat, "video_note": {"file_id": "vn9003"}}})
    cl = next(c for c in client.get("/api/clients").json() if c["telegram_id"] == "9003")
    circle = cl["circle_file"]
    assert circle and (settings.storage_dir / circle).exists()  # кружок на диске до сверки
    client.post(f"/api/clients/{cl['id']}/verify", json={"decision": "verified", "note": ""})
    cl = next(c for c in client.get("/api/clients").json() if c["id"] == cl["id"])
    assert not cl["circle_file"]  # поле очищено после сверки
    assert not (settings.storage_dir / circle).exists()  # файл удалён с диска


def test_selfie_code_tariff_29(client):
    from app.models import Client as C
    from app.pipeline import session_scope
    chat = {"chat": {"id": 9100}}
    _hook(client, {"message": {**chat, "text": "/start"}})
    _hook(client, {"callback_query": {"id": "1", "data": "menu:check", "message": chat}})
    _hook(client, {"callback_query": {"id": "1", "data": "consent:yes", "message": chat}})
    for i in range(3):
        _hook(client, {"message": {**chat, "photo": [{"file_id": f"p{i}"}]}})
    _hook(client, {"callback_query": {"id": "1", "data": "verify:selfie", "message": chat}})  # путь без кружка
    _hook(client, {"message": {**chat, "photo": [{"file_id": "selfie9100"}]}})  # селфи с кодом
    _hook(client, {"message": {**chat, "text": "nick1"}})
    _hook(client, {"message": {**chat, "text": "нет"}})
    _hook(client, {"message": {**chat, "text": "Киев"}})
    with session_scope() as db:
        c = db.query(C).filter(C.telegram_id == "9100").first()
        assert any(o.service == "check_free" and o.price_usd == 29 and o.status == "verifying" for o in c.orders)  # $29, ждёт верификации
        assert c.circle_file  # селфи сохранено для оператора (авто-сверка в тестах недоступна → ручная)
