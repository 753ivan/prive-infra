import time


def test_order_flow(client):
    cid = client.post("/api/clients", json={"display": "t"}).json()["id"]
    assert client.post(f"/api/clients/{cid}/verify", json={"decision": "verified"}).json()["status"] == "verified"
    o = client.post("/api/orders", json={"client_id": cid, "service": "check", "queries": {"nicks": ["nick_x"]}}).json()
    assert o["status"] == "new" and o["price_usd"] == 190
    job = client.post(f"/api/orders/{o['id']}/scan").json()["job_id"]
    time.sleep(1.5)
    j = next(j for j in client.get("/api/jobs").json() if j["id"] == job)
    assert j["status"] == "done" and "google_cse" in j["result"]["not_configured"]
    f = client.post(f"/api/orders/{o['id']}/findings", json={"url": "https://instagram.com/fake.x", "kind": "fake"}).json()
    td = client.post(f"/api/findings/{f['id']}/takedown", json={}).json()
    assert td["template"] == "impersonation" and td["channel"] == "form"
    r = client.post(f"/api/takedowns/{td['id']}/send").json()
    assert r["sent"] is False and r["reason"] == "manual channel"
    client.post(f"/api/takedowns/{td['id']}/mark-sent")
    assert client.get("/api/takedowns?status=sent").json()[0]["id"] == td["id"]
    pdf = client.get(f"/api/orders/{o['id']}/pdf/lawyer")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_csv_import_dedup(client):
    cid = client.post("/api/clients", json={"display": "csv"}).json()["id"]
    o = client.post("/api/orders", json={"client_id": cid, "service": "fakes"}).json()
    csv = "source,query_type,query_value,url,match_type,confidence\nPimEyes,face,r1,https://a.example/1,exact,0.9\nPimEyes,face,r2,https://a.example/1,exact,0.8\n"
    r = client.post(f"/api/orders/{o['id']}/import", files={"file": ("f.csv", csv)}).json()
    assert r["added"] == 1 and r["skipped_duplicates"] == 1


def test_localized_template_for_de_domain(client):
    cid = client.post("/api/clients", json={"display": "de"}).json()["id"]
    o = client.post("/api/orders", json={"client_id": cid, "service": "leaks"}).json()
    f = client.post(f"/api/orders/{o['id']}/findings", json={"url": "https://irgendwas.de/anzeige/5"}).json()
    td = client.post(f"/api/findings/{f['id']}/takedown", json={"template": "dmca"}).json()
    assert "Urheberrecht" in td["subject"]


def test_roles(client):
    assert client.post("/api/users", json={"username": "duty1", "password": "pw", "role": "duty"}).status_code == 200
    from fastapi.testclient import TestClient
    from app.main import app
    d = TestClient(app); d.auth = ("duty1", "pw")
    assert d.get("/api/orders").status_code == 200
    assert d.put("/api/platforms/x.example", json={}).status_code == 403
    assert d.get("/api/users").status_code == 403
    assert TestClient(app).get("/api/orders", auth=("duty1", "wrong")).status_code == 401


def test_nick_variants_and_canonical():
    from app.services.enrich import canonical_url, nick_variants, phone_variants
    v = nick_variants("Alisa_Kyiv")
    assert "Alisa_Kyiv" in v and "AlisaKyiv" in v and any("алиса" in x.lower() for x in v)
    assert "380991234567" in phone_variants("+380 99 123 45 67") and "0991234567" in phone_variants("+380991234567")
    assert canonical_url("http://www.Example.com/a/?utm_source=x&b=1") == canonical_url("https://example.com/a?b=1")
    assert canonical_url("https://m.site.com/p/") == "https://site.com/p"


def test_rules_classify():
    from app.services.rules import classify

    class F:
        url = "https://example-escort.site/anketa/1"; domain = "example-escort.site"; match_type = "exact"; confidence = 0.95
        source = "google_cse"; kind = "unknown"; status = "new"; query_type = "nick"
    r = classify(F(), {"nick_on_page": True, "city_on_page": True, "http_status": 200})
    assert r["status"] == "confirmed" and r["kind"] == "anketa" and r["risk"] == 2

    class L(F):
        url = "https://t.me/slivy_x/1"; domain = "t.me"; match_type = "text"; confidence = None; source = "brave"
    r = classify(L(), {"http_status": 200})
    assert r["kind"] == "leak" and r["risk"] == 3

    class G(F):
        match_type = "text"; confidence = None
    r = classify(G(), {"http_status": 410})
    assert r["status"] == "ignored"


def test_free_report_counts_only_confirmed(client):
    cid = client.post("/api/clients", json={"display": "free"}).json()["id"]
    o = client.post("/api/orders", json={"client_id": cid, "service": "check_free"}).json()
    a = client.post(f"/api/orders/{o['id']}/findings", json={"url": "https://a.example/1", "kind": "anketa"}).json()
    client.post(f"/api/orders/{o['id']}/findings", json={"url": "https://b.example/2", "kind": "fake"})
    client.patch(f"/api/findings/{a['id']}", json={"status": "confirmed"})
    txt = client.post(f"/api/orders/{o['id']}/report?send=false").json()["preview"]
    assert "анкет с твоими фото: 1" in txt and "1 совпадений требуют" in txt


def test_funnel_and_health(client):
    f = client.get("/api/funnel").json()
    assert "conversion_free_to_paid_pct" in f and f["clients_new"] >= 1
    assert client.get("/healthz").json()["db"] is True
