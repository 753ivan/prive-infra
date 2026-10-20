"""PRIVÉ Ops — рабочее место оператора. API + панель."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import cast, String, func
from sqlalchemy.orm import Session

from . import bot, pipeline
from .auth import Principal, audit, current_user, ensure_admin, hash_password, require
from .config import settings
from .db import Base, engine, get_db, migrate_light, session_scope
from .models import (FINDING_KINDS, FINDING_STATUSES, ORDER_STATUS_RU, SERVICES, TAKEDOWN_STATUSES, AuditLog, Client, Finding,
                     Incident, IntegrationLog, Job, Order, Takedown, User, now)
from .services import evidence as ev_svc
from .services import importer
from .services import leaks as leaks_svc
from .services import search as search_svc
from .services import takedown as td_svc
from .services import telegram as tg_svc

BASE = Path(__file__).resolve().parent
app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
scheduler = BackgroundScheduler(timezone="UTC")




@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    migrate_light()
    ensure_admin()
    if settings.scheduler_enabled:
        scheduler.add_job(pipeline.scheduled_tick, "interval", hours=1, id="tick", replace_existing=True)
        scheduler.start()


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        try:
            with session_scope() as db:
                db.add(AuditLog(user=getattr(request.state, "user", "?"), method=request.method, path=request.url.path, status=response.status_code))
        except Exception:
            pass
    return response


@app.on_event("shutdown")
def shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Панель
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(user: Principal = Depends(current_user)):
    return (BASE / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/meta")
def meta(user: Principal = Depends(current_user)):
    return {"services": SERVICES, "order_statuses": ORDER_STATUS_RU, "finding_kinds": FINDING_KINDS,
            "finding_statuses": FINDING_STATUSES, "takedown_statuses": TAKEDOWN_STATUSES,
            "templates": list(td_svc.TEMPLATES), "user": user.username, "role": user.role}


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    today = now() - timedelta(days=1)
    q = db.query
    return {
        "funnel": funnel(30, db, user),
        # cast в текст: оператор != для JSON есть в SQLite, но не в PostgreSQL
        "clients_to_verify": q(Client).filter(Client.status == "new",
                                              cast(Client.reference_photos, String) != "[]").count(),
        "orders_by_status": dict(q(Order.status, func.count()).group_by(Order.status).all()),
        "sos_open": q(Order).filter(Order.service.in_(["sos", "sos_consult"]), Order.status.notin_(["done", "cancelled"])).count(),
        "findings_to_review": q(Finding).filter(Finding.status == "new").count(),
        "takedowns_due": q(Takedown).filter(Takedown.next_check_at <= now(), Takedown.status.in_(["sent", "removed", "escalated"])).count(),
        "takedowns_draft": q(Takedown).filter(Takedown.status == "draft").count(),
        "orders_overdue": q(Order).filter(Order.due_at < now(), Order.status.notin_(["done", "cancelled"])).count(),
        "jobs_errors_24h": q(Job).filter(Job.status == "error", Job.created_at >= today).count(),
        "removed_total": q(Finding).filter(Finding.status == "removed").count(),
        "integrations": integrations_status(),
    }


def integrations_status() -> dict:
    return {"search": search_svc.status(), "leaks": leaks_svc.status(), "evidence": ev_svc.status(),
            "takedown": td_svc.status(), "telegram": tg_svc.status(), "face": face_status() | facecheck_status()}


def facecheck_status() -> dict:
    from .services import facecheck, search4faces
    return facecheck.status() | search4faces.status()


def face_status() -> dict:
    from .services import face
    return face.status()


# ---------------------------------------------------------------------------
# Клиентки и верификация
# ---------------------------------------------------------------------------

class ClientIn(BaseModel):
    display: str = ""
    telegram_id: str = ""
    referred_by: str = ""


@app.get("/api/clients")
def list_clients(status: str | None = None, db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    q = db.query(Client).order_by(Client.id.desc())
    if status:
        q = q.filter(Client.status == status)
    return [c.to_dict() for c in q.limit(500)]


@app.post("/api/clients")
def create_client(data: ClientIn, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    c = Client(**data.model_dump())
    db.add(c)
    db.commit()
    return c.to_dict()


class VerifyIn(BaseModel):
    decision: str  # verified | rejected | retry
    note: str = ""


@app.post("/api/clients/{cid}/verify")
def verify_client(cid: int, data: VerifyIn, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    c = db.get(Client, cid) or _404()
    c.verification_note = data.note
    if data.decision == "verified":
        c.status = "verified"
        for o in c.orders:
            if o.status == "verifying":
                o.status = "new"
        tg_svc.send_message(c.telegram_id, "Личность подтверждена. Можно запускать проверку — напиши «Проверка» или «SOS».") if c.telegram_id else None
    elif data.decision == "rejected":
        c.status = "rejected"
        tg_svc.send_message(c.telegram_id, "К сожалению, подтвердить личность не получилось.") if c.telegram_id else None
    else:
        c.status = "new"
        gesture = data.note or "коснись носа"
        tg_svc.send_message(c.telegram_id, f"Нужен новый кружок: запиши его с жестом «{gesture}».") if c.telegram_id else None
    db.commit()
    return c.to_dict()


@app.post("/api/clients/{cid}/auto-verify")
def auto_verify(cid: int, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    """Повторить авто-сверку кружка с эталонами (InsightFace)."""
    from .services import face
    c = db.get(Client, cid) or _404()
    res = face.verify_circle(c)
    if res:
        c.verification_score = res.get("score")
        c.verification_note = f"авто: {res['decision']}, score {res.get('score')}"
        db.commit()
    return res or {"decision": "manual", "reason": "модуль лица недоступен или нет кружка/эталонов"}


@app.post("/api/clients/{cid}/photos")
async def upload_photo(cid: int, file: UploadFile, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    import hashlib
    c = db.get(Client, cid) or _404()
    data = await file.read()
    sha = hashlib.sha256(data).hexdigest()
    folder = settings.storage_dir / "refs" / c.code
    folder.mkdir(parents=True, exist_ok=True)
    ext = (file.filename or "jpg").rsplit(".", 1)[-1]
    path = folder / f"{sha[:16]}.{ext}"
    path.write_bytes(data)
    c.reference_photos = [*c.reference_photos, {"file": str(path.relative_to(settings.storage_dir)), "sha256": sha, "uploaded_at": now().isoformat()}]
    c.photos_expire_at = tg_svc.photo_expiry()
    db.commit()
    return c.to_dict()


@app.get("/files/{path:path}")
def get_file(path: str, user: Principal = Depends(current_user)):
    p = (settings.storage_dir / path).resolve()
    if settings.storage_dir.resolve() not in p.parents or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


# ---------------------------------------------------------------------------
# Заказы
# ---------------------------------------------------------------------------

class OrderIn(BaseModel):
    client_id: int
    service: str
    queries: dict = {}
    notes: str = ""
    price_usd: float | None = None


class OrderPatch(BaseModel):
    status: str | None = None
    queries: dict | None = None
    notes: str | None = None
    paid_usd: float | None = None
    price_usd: float | None = None


@app.get("/api/orders")
def list_orders(status: str | None = None, service: str | None = None, db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    q = db.query(Order).order_by(Order.priority, Order.due_at)
    if status:
        q = q.filter(Order.status == status)
    if service:
        q = q.filter(Order.service == service)
    return [o.to_dict() for o in q.limit(500)]


@app.post("/api/orders")
def create_order(data: OrderIn, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    if data.service not in SERVICES:
        raise HTTPException(400, "Неизвестная услуга")
    c = db.get(Client, data.client_id) or _404()
    o = Order(client_id=c.id, service=data.service, queries=data.queries, notes=data.notes, price_usd=data.price_usd or 0)
    o.set_defaults()
    if c.status != "verified":
        o.status = "verifying"
    db.add(o)
    db.commit()
    if SERVICES[data.service]["kind"] == "sos":
        db.add(Incident(order_id=o.id))
        db.commit()
        tg_svc.alert_operators(f"SOS: заказ #{o.id}, клиентка {c.code}. Ответить в течение 15 минут.")
    return o.to_dict(with_findings=True)


@app.get("/api/orders/{oid}")
def get_order(oid: int, db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    o = db.get(Order, oid) or _404()
    d = o.to_dict(with_findings=True)
    inc = db.query(Incident).filter(Incident.order_id == oid).first()
    d["incident"] = inc.to_dict() if inc else None
    d["client"] = o.client.to_dict()
    d["report_preview"] = pipeline.build_report_text(o)
    return d


@app.patch("/api/orders/{oid}")
def patch_order(oid: int, data: OrderPatch, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    o = db.get(Order, oid) or _404()
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(o, k, v)
    db.commit()
    return o.to_dict()


@app.post("/api/orders/{oid}/scan")
def run_scan(oid: int, bg: BackgroundTasks, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    o = db.get(Order, oid) or _404()
    if o.client.status != "verified":
        raise HTTPException(409, "Клиентка не верифицирована — сначала подтвердите кружок")
    job = pipeline.enqueue(db, oid, "scan", {"queries": o.queries})
    db.commit()
    bg.add_task(pipeline._run_job, job.id, lambda: pipeline.scan_order(oid))
    return {"job_id": job.id}


@app.post("/api/orders/{oid}/report")
def report(oid: int, send: bool = True, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    db.get(Order, oid) or _404()
    if send:
        return pipeline.send_report(oid)
    with session_scope() as s:
        return {"preview": pipeline.build_report_text(s.get(Order, oid))}


@app.post("/api/orders/{oid}/match-photos")
def match_photos(oid: int, bg: BackgroundTasks, ids: list[int] | None = None, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    """M4: сверка картинок на страницах находок с эталонами (pHash)."""
    db.get(Order, oid) or _404()
    job = pipeline.enqueue(db, oid, "match_photos", {"ids": ids})
    db.commit()
    bg.add_task(pipeline._run_job, job.id, lambda: pipeline.match_photos(oid, ids))
    return {"job_id": job.id}


@app.post("/api/orders/{oid}/face-search")
def face_search(oid: int, bg: BackgroundTasks, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    """Внешний поиск по лицу (FaceCheck + Search4faces API) по эталонам клиентки."""
    db.get(Order, oid) or _404()
    job = pipeline.enqueue(db, oid, "face_search", {})
    db.commit()
    bg.add_task(pipeline._run_job, job.id, lambda: pipeline.face_search_external(oid))
    return {"job_id": job.id}


@app.post("/api/orders/{oid}/import-report")
async def import_report(oid: int, file: UploadFile, source: str = "pimeyes", db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    """Импорт отчёта PimEyes/Lenso/любого сервиса: PDF, HTML или текст со ссылками."""
    db.get(Order, oid) or _404()
    data = await file.read()
    return pipeline.import_pimeyes(oid, data, file.filename or "report", user.username, source)


@app.post("/api/clients/{cid}/pick-references")
def pick_references(cid: int, n: int = 3, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    """Автовыбор эталонов для PimEyes и внешних сервисов: одно лицо, крупно, резко, разные ракурсы."""
    from .services import face
    c = db.get(Client, cid) or _404()
    if not face.available():
        return {"picked": [p["file"] for p in (c.reference_photos or [])][:n], "details": [], "warning": "модуль лица недоступен — взяты первые по порядку"}
    return face.pick_references(c, n)


@app.post("/api/orders/{oid}/enrich")
def enrich(oid: int, bg: BackgroundTasks, ids: list[int] | None = None, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    """Верификация страниц + правила + автофиксация по новым находкам."""
    db.get(Order, oid) or _404()
    job = pipeline.enqueue(db, oid, "enrich", {"ids": ids})
    db.commit()
    bg.add_task(pipeline._run_job, job.id, lambda: pipeline.enrich_order(oid, ids))
    return {"job_id": job.id}


@app.get("/api/funnel")
def funnel(days: int = 30, db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    since = now() - timedelta(days=days)
    q = db.query
    clients = q(Client).filter(Client.created_at >= since)
    orders = q(Order).filter(Order.created_at >= since)
    free = orders.filter(Order.service == "check_free")
    paid_check = orders.filter(Order.service == "check", Order.paid_usd > 0)
    paid_any = orders.filter(Order.paid_usd > 0)
    done_free = free.filter(Order.free_report_sent_at.isnot(None))
    durations = [(o.free_report_sent_at - o.created_at).total_seconds() / 3600 for o in done_free.all() if o.free_report_sent_at]
    return {
        "days": days,
        "clients_new": clients.count(),
        "clients_verified": clients.filter(Client.status == "verified").count(),
        "free_checks": free.count(),
        "free_reports_sent": done_free.count(),
        "paid_checks": paid_check.count(),
        "paid_orders_any": paid_any.count(),
        "revenue_usd": round(sum(o.paid_usd for o in paid_any.all()), 2),
        "api_cost_usd": round(sum(o.api_cost_usd or 0 for o in orders.all()), 2),
        "takedown_orders": orders.filter(Order.service.in_(["fakes", "leaks", "exit_std", "exit_total"]), Order.paid_usd > 0).count(),
        "conversion_free_to_paid_pct": round(100 * paid_check.count() / done_free.count(), 1) if done_free.count() else None,
        "median_hours_to_free_report": round(sorted(durations)[len(durations) // 2], 1) if durations else None,
        "overdue_now": q(Order).filter(Order.due_at < now(), Order.status.notin_(["done", "cancelled"])).count(),
    }


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    """Для мониторинга (UptimeRobot и т. п.): база отвечает, планировщик жив."""
    try:
        db.execute(func.now().select() if settings.database_url.startswith("postgres") else __import__("sqlalchemy").text("SELECT 1"))
        ok_db = True
    except Exception:
        ok_db = False
    last_err = db.query(Job).filter(Job.status == "error").order_by(Job.id.desc()).first()
    return {"ok": ok_db and (scheduler.running or not settings.scheduler_enabled), "db": ok_db, "scheduler": scheduler.running,
            "last_job_error": last_err.created_at.isoformat() if last_err else None}


@app.get("/api/orders/{oid}/pdf/{kind}")
def order_pdf(oid: int, kind: str, send: bool = False, db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    """kind = client (отчёт клиентке) | lawyer (пакет доказательств)."""
    from .services import reports
    o = db.get(Order, oid) or _404()
    path = reports.client_report(o) if kind == "client" else reports.lawyer_package(o) if kind == "lawyer" else _404()
    if send and kind == "client" and o.client.telegram_id:
        tg_svc.send_document(o.client.telegram_id, path, "PRIVÉ · отчёт")
        o.last_report_at = now()
        db.commit()
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@app.post("/api/tools/strip-metadata")
async def strip_metadata(file: UploadFile, user: Principal = Depends(require("duty"))):
    """«Невидимка», шаг 2: вернуть фото без EXIF/GPS."""
    from .services import images as img_svc
    data = await file.read()
    fmt = "PNG" if (file.filename or "").lower().endswith(".png") else "JPEG"
    clean = img_svc.strip_metadata(data, fmt)
    out = settings.storage_dir / "clean" / f"{secrets.token_hex(6)}.{fmt.lower()}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(clean)
    return FileResponse(out, filename="clean_" + (file.filename or "photo"), media_type=f"image/{fmt.lower()}")


@app.post("/api/orders/{oid}/import")
async def import_csv(oid: int, file: UploadFile, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    o = db.get(Order, oid) or _404()
    rows = importer.parse_csv((await file.read()).decode("utf-8", errors="replace"))
    res = importer.import_rows(db, o, rows, operator=user.username)
    if o.status in ("new", "scanning"):
        o.status = "review"
    db.commit()
    return res | {"rows": len(rows)}


class IncidentIn(BaseModel):
    extortionist: str | None = None
    what_they_have: str | None = None
    demand: str | None = None
    deadline: str | None = None
    already_sent_to: str | None = None
    relatives_at_risk: str | None = None
    physical_danger: bool | None = None
    stopncii_case: str | None = None
    plan_72h: str | None = None
    checklist: dict | None = None


@app.patch("/api/orders/{oid}/incident")
def patch_incident(oid: int, data: IncidentIn, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    inc = db.query(Incident).filter(Incident.order_id == oid).first()
    if not inc:
        inc = Incident(order_id=oid)
        db.add(inc)
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(inc, k, v)
    db.commit()
    return inc.to_dict()


# ---------------------------------------------------------------------------
# Находки
# ---------------------------------------------------------------------------

class FindingPatch(BaseModel):
    kind: str | None = None
    risk: int | None = None
    status: str | None = None


class FindingIn(BaseModel):
    url: str
    source: str = "manual"
    query_type: str = "face"
    query_value: str = ""
    kind: str = "unknown"


@app.get("/api/findings")
def list_findings(status: str | None = None, db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    q = db.query(Finding).order_by(Finding.id.desc())
    if status:
        q = q.filter(Finding.status == status)
    return [f.to_dict() for f in q.limit(500)]


@app.post("/api/orders/{oid}/findings")
def add_finding(oid: int, data: FindingIn, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    db.get(Order, oid) or _404()
    f = Finding(order_id=oid, domain=search_svc.domain_of(data.url), operator=user.username, match_type="manual", **data.model_dump())
    db.add(f)
    db.commit()
    return f.to_dict()


@app.patch("/api/findings/{fid}")
def patch_finding(fid: int, data: FindingPatch, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    f = db.get(Finding, fid) or _404()
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(f, k, v)
    f.operator = user.username
    db.commit()
    return f.to_dict()


@app.post("/api/findings/bulk")
def bulk_findings(ids: list[int], patch: FindingPatch, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    n = 0
    for f in db.query(Finding).filter(Finding.id.in_(ids)).all():
        for k, v in patch.model_dump(exclude_none=True).items():
            setattr(f, k, v)
        f.operator = user.username
        n += 1
    db.commit()
    return {"updated": n}


@app.post("/api/findings/{fid}/capture")
def capture(fid: int, bg: BackgroundTasks, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    f = db.get(Finding, fid) or _404()
    job = pipeline.enqueue(db, f.order_id, "capture", {"finding_id": fid})
    db.commit()
    bg.add_task(pipeline._run_job, job.id, lambda: pipeline.capture_finding(fid))
    return {"job_id": job.id}


class TakedownIn(BaseModel):
    template: str | None = None
    stopncii_case: str = ""
    wave: int = 1


@app.post("/api/findings/{fid}/takedown")
def create_takedown(fid: int, data: TakedownIn, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    f = db.get(Finding, fid) or _404()
    td = pipeline.draft_takedown(db, f, data.template, {"stopncii_case": data.stopncii_case}, data.wave)
    db.commit()
    return td.to_dict()


@app.post("/api/findings/bulk-takedown")
def bulk_takedown(ids: list[int], data: TakedownIn, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    """Пакетная подача: черновики по всем выбранным находкам (карта 4, шаг 4)."""
    out = []
    for f in db.query(Finding).filter(Finding.id.in_(ids)).all():
        if not f.takedowns:
            out.append(pipeline.draft_takedown(db, f, data.template, {"stopncii_case": data.stopncii_case}, data.wave).id)
    db.commit()
    return {"created": out}


# ---------------------------------------------------------------------------
# Снятия
# ---------------------------------------------------------------------------

class TakedownPatch(BaseModel):
    to_address: str | None = None
    subject: str | None = None
    body: str | None = None
    status: str | None = None
    note: str = ""


@app.get("/api/takedowns")
def list_takedowns(status: str | None = None, db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    q = db.query(Takedown).order_by(Takedown.next_check_at.is_(None), Takedown.next_check_at)
    if status:
        q = q.filter(Takedown.status == status)
    out = []
    for t in q.limit(500):
        d = t.to_dict()
        d["url"] = t.finding.url
        d["order_id"] = t.finding.order_id
        d["client_code"] = t.finding.order.client.code
        out.append(d)
    return out


@app.patch("/api/takedowns/{tid}")
def patch_takedown(tid: int, data: TakedownPatch, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    td = db.get(Takedown, tid) or _404()
    for k, v in data.model_dump(exclude_none=True, exclude={"note"}).items():
        if k == "status":
            td.add_log(f"статус → {v}" + (f" ({data.note})" if data.note else ""))
            if v == "removed":
                td.finding.status = "removed"
        setattr(td, k, v)
    db.commit()
    return td.to_dict()


@app.post("/api/takedowns/{tid}/send")
def send_td(tid: int, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    db.get(Takedown, tid) or _404()
    return pipeline.send_takedown(tid)


@app.post("/api/takedowns/{tid}/mark-sent")
def mark_sent(tid: int, note: str = "", db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    db.get(Takedown, tid) or _404()
    pipeline.mark_sent(tid, note)
    return {"ok": True}


@app.post("/api/takedowns/{tid}/recheck")
def recheck(tid: int, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    db.get(Takedown, tid) or _404()
    return pipeline.recheck_takedown(tid)


# ---------------------------------------------------------------------------
# Справочник площадок, интеграции, задачи
# ---------------------------------------------------------------------------

@app.get("/api/platforms")
def platforms(user: Principal = Depends(current_user)):
    return td_svc.load_platforms()


@app.put("/api/platforms/{domain}")
def put_platform(domain: str, data: dict, user: Principal = Depends(require("manager"))):
    p = td_svc.load_platforms()
    p[domain] = data
    td_svc.save_platforms(p)
    return p[domain]


@app.get("/api/sources")
def sources(user: Principal = Depends(current_user)):
    from .services import crawl as crawl_svc
    return crawl_svc.load_sources()


@app.put("/api/sources")
def put_sources(data: list[dict], user: Principal = Depends(require("manager"))):
    from .services import crawl as crawl_svc
    crawl_svc.save_sources(data)
    return {"saved": len(data)}


@app.post("/api/sources/preview")
def preview_queries(nick: str = "demo_nick", city: str = "", user: Principal = Depends(current_user)):
    """Показывает, какие поисковые запросы уйдут по списку (без выполнения)."""
    from .services import crawl as crawl_svc
    out = []
    for s in crawl_svc.load_sources():
        if s.get("enabled") and s.get("method") == "site_search":
            out += [q for _, _, q in crawl_svc.source_queries(s, [("nick", nick)], city)]
    return {"queries": out, "count": len(out)}


@app.get("/api/templates")
def templates(user: Principal = Depends(current_user)):
    return td_svc.TEMPLATES


@app.get("/api/integrations")
def integrations(db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    log = db.query(IntegrationLog).order_by(IntegrationLog.id.desc()).limit(100).all()
    return {"status": integrations_status(), "log": [l.to_dict() for l in log],
            "env_file": str(Path(settings.model_config["env_file"]))}


@app.post("/api/integrations/test/{name}")
def test_integration(name: str, q: str = "prive-test", user: Principal = Depends(require("manager"))):
    try:
        if name in search_svc.PROVIDERS:
            return {"ok": True, "results": search_svc.PROVIDERS[name](q)[:3]}
        if name == "facecheck":
            from .services import facecheck
            import io
            from PIL import Image
            b = io.BytesIO(); Image.new("RGB", (200, 200), (128, 128, 128)).save(b, "JPEG")
            return {"ok": True, "results": facecheck.search(b.getvalue())[:3], "note": "серое изображение — ожидаем 0 результатов, важно отсутствие ошибки авторизации"}
        if name == "search4faces":
            from .services import search4faces
            return {"ok": True, "limits": search4faces._rpc("rateLimit", {})}
        if name == "hibp":
            return {"ok": True, "results": leaks_svc.hibp_breaches("test@example.com")[:3]}
        if name == "telegram":
            return {"ok": tg_svc.alert_operators("Тест связи из панели PRIVÉ Ops")}
        if name == "mail":
            mid = td_svc.send_email(settings.mail_reply_to or settings.mail_from, "PRIVÉ Ops test", "Тест отправки")
            return {"ok": True, "message_id": mid}
        if name == "archive":
            return {"ok": True, "result": ev_svc.capture("https://example.com", 0)}
        raise HTTPException(404, "нет такого теста")
    except search_svc.NotConfigured as e:
        return {"ok": False, "error": f"не настроено: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}


@app.get("/api/jobs")
def jobs(db: Session = Depends(get_db), user: Principal = Depends(current_user)):
    return [j.to_dict() for j in db.query(Job).order_by(Job.id.desc()).limit(100)]


@app.post("/api/scheduler/tick")
def tick(bg: BackgroundTasks, user: Principal = Depends(require("duty"))):
    bg.add_task(pipeline.scheduled_tick)
    return {"queued": True}


# ---------------------------------------------------------------------------
# Пользователи, журнал, бот-действия
# ---------------------------------------------------------------------------

class UserIn(BaseModel):
    username: str
    password: str = ""
    role: str = "duty"
    active: bool = True


@app.get("/api/users")
def list_users(db: Session = Depends(get_db), user: Principal = Depends(require("admin"))):
    return [u.to_dict() for u in db.query(User).order_by(User.id)]


@app.post("/api/users")
def upsert_user(data: UserIn, db: Session = Depends(get_db), user: Principal = Depends(require("admin"))):
    u = db.query(User).filter(User.username == data.username).first()
    if not u:
        if not data.password:
            raise HTTPException(400, "Нужен пароль")
        u = User(username=data.username, password_hash=hash_password(data.password), role=data.role, active=data.active)
        db.add(u)
    else:
        u.role, u.active = data.role, data.active
        if data.password:
            u.password_hash = hash_password(data.password)
    db.commit()
    return u.to_dict()


@app.get("/api/audit")
def audit_log(db: Session = Depends(get_db), user: Principal = Depends(require("manager"))):
    return [a.to_dict() for a in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(300)]


@app.post("/api/findings/{fid}/ask-client")
def ask_client(fid: int, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    db.get(Finding, fid) or _404()
    return {"sent": bot.ask_is_yours(fid)}


@app.post("/api/orders/{oid}/confirm-payment")
def confirm_payment(oid: int, amount: float | None = None, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    o = db.get(Order, oid) or _404()
    o.paid_usd = amount if amount is not None else o.price_usd
    if o.status == "new" and o.client.status == "verified":
        o.status = "scanning" if SERVICES[o.service]["kind"] in ("scan", "subscription") else "review"
    db.commit()
    if o.client.telegram_id:
        tg_svc.send_message(o.client.telegram_id, f"Оплата по заказу #{o.id} получена, начинаем. Отчёт пришлю сюда.")
    return o.to_dict()


@app.post("/api/orders/{oid}/send-pdf")
def send_pdf(oid: int, db: Session = Depends(get_db), user: Principal = Depends(require("duty"))):
    from .services import reports
    o = db.get(Order, oid) or _404()
    path = reports.client_report(o)
    ok = tg_svc.send_document(o.client.telegram_id, path, "PRIVÉ · отчёт") if o.client.telegram_id else False
    if ok:
        o.last_report_at = now()
        db.commit()
    return {"sent": ok, "file": path.name}


# ---------------------------------------------------------------------------
# Telegram webhook (без Basic-auth, защищён секретом)
# ---------------------------------------------------------------------------

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(403)
    upd = await request.json()
    try:
        bot.handle_update(upd)
    except Exception as e:  # noqa: BLE001
        search_svc.log_call("telegram", "webhook", False, repr(e))
    return {"ok": True}


@app.post("/api/telegram/set-webhook")
def set_webhook(user: Principal = Depends(require("manager"))):
    try:
        return tg_svc.set_webhook(settings.public_base_url)
    except tg_svc.NotConfigured:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN не задан"}


def _404():
    raise HTTPException(404, "Не найдено")
