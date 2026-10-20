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
