"""Реестр (модуль M2): клиентки, заказы, находки, доказательства, снятия, задачи."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# ---------------------------------------------------------------------------
# Справочники
# ---------------------------------------------------------------------------

SERVICES = {
    "check_free": {"name": "Бесплатная проверка", "price": 0, "days": 1, "kind": "scan"},
    "check": {"name": "Проверка", "price": 190, "days": 2, "kind": "scan"},
    "sos_consult": {"name": "SOS-консультация", "price": 149, "days": 1, "kind": "sos"},
    "sos": {"name": "SOS (72 часа)", "price": 490, "days": 3, "kind": "sos"},
    "fakes": {"name": "Фейки и чужие анкеты", "price": 490, "days": 14, "kind": "takedown"},
    "leaks": {"name": "Сливы", "price": 790, "days": 60, "kind": "takedown"},
    "invisible": {"name": "Невидимка", "price": 990, "days": 7, "kind": "invisible"},
    "exit_std": {"name": "Выход «Стандарт»", "price": 3900, "days": 180, "kind": "exit"},
    "exit_total": {"name": "Выход «Тотал»", "price": 7900, "days": 365, "kind": "exit"},
    "sub_start": {"name": "Охрана Старт", "price": 0, "days": 30, "kind": "subscription", "scan_every_days": 30, "nicks": 1},
    "sub_lite": {"name": "Охрана Lite", "price": 149, "days": 30, "kind": "subscription", "scan_every_days": 14, "nicks": 1},
    "sub_pro": {"name": "Охрана Pro", "price": 299, "days": 30, "kind": "subscription", "scan_every_days": 7, "nicks": 3},
    "sub_vip": {"name": "Охрана VIP", "price": 590, "days": 30, "kind": "subscription", "scan_every_days": 1, "nicks": 99},
    "private_phone": {"name": "Private: аудит телефона", "price": 690, "days": 3, "kind": "private"},
    "private_trip": {"name": "Private: перед поездкой", "price": 1190, "days": 5, "kind": "private"},
    "private_sb": {"name": "Private: проверка как у СБ", "price": 1490, "days": 7, "kind": "private"},
    "private_public": {"name": "Private: публичная персона", "price": 2900, "days": 30, "kind": "private"},
    "private_year": {"name": "Private на год", "price": 1190, "days": 365, "kind": "private"},
}

ORDER_STATUSES = ["new", "verifying", "scanning", "review", "takedown", "monitoring", "done", "cancelled"]
ORDER_STATUS_RU = {
    "new": "Новый", "verifying": "Верификация", "scanning": "Сканирование", "review": "Разбор оператором",
    "takedown": "Снятия", "monitoring": "Контроль", "done": "Закрыт", "cancelled": "Отменён",
}
FINDING_KINDS = {"anketa": "Анкета", "fake": "Фейк", "leak": "Слив", "mention": "Упоминание", "unknown": "Не определено"}
FINDING_STATUSES = {"new": "Новая", "confirmed": "Подтверждена", "ignored": "Не её", "takedown": "На снятии", "removed": "Снята", "returned": "Вернулась"}
TAKEDOWN_STATUSES = {"draft": "Черновик", "sent": "Отправлено", "answered": "Есть ответ", "removed": "Снято", "escalated": "Эскалация", "failed": "Ошибка"}


def now() -> datetime:
    return datetime.utcnow()


def gen_code(prefix: str = "PRIVE") -> str:
    return f"{prefix}-{secrets.token_hex(2).upper()}"


# ---------------------------------------------------------------------------
# Таблицы
# ---------------------------------------------------------------------------


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, default=gen_code)
    telegram_id: Mapped[str] = mapped_column(String(32), default="")
    display: Mapped[str] = mapped_column(String(64), default="")  # внутренний ярлык, не имя
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | verified | rejected
    verification_note: Mapped[str] = mapped_column(Text, default="")
    verification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    referral_code: Mapped[str] = mapped_column(String(16), default=lambda: gen_code("REF"))
    referred_by: Mapped[str] = mapped_column(String(16), default="")
    balance_usd: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    reference_photos: Mapped[list] = mapped_column(JSON, default=list)  # [{file, sha256, uploaded_at}]
    photos_expire_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    circle_file: Mapped[str] = mapped_column(String(255), default="")  # кружок для верификации
    consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # согласие на поиск по своему лицу
    consent_version: Mapped[str] = mapped_column(String(16), default="")
    bot_state: Mapped[dict] = mapped_column(JSON, default=dict)  # {step, draft:{...}}
    lang: Mapped[str] = mapped_column(String(4), default="ru")

    orders: Mapped[list["Order"]] = relationship(back_populates="client", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "telegram_id": self.telegram_id, "display": self.display,
            "status": self.status, "verification_note": self.verification_note,
            "verification_score": self.verification_score, "referral_code": self.referral_code,
            "referred_by": self.referred_by, "balance_usd": self.balance_usd,
            "created_at": self.created_at.isoformat(), "reference_photos": self.reference_photos,
            "photos_expire_at": self.photos_expire_at.isoformat() if self.photos_expire_at else None,
            "orders_count": len(self.orders), "circle_file": self.circle_file,
            "consent_at": self.consent_at.isoformat() if self.consent_at else None,
        }


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    service: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="new")
    price_usd: Mapped[float] = mapped_column(Float, default=0)
    paid_usd: Mapped[float] = mapped_column(Float, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 срочно (SOS) … 3 обычный
    queries: Mapped[dict] = mapped_column(JSON, default=dict)  # {nicks:[], phones:[], emails:[], phrases:[]}
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # подписки и контроль
    monitoring_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_method: Mapped[str] = mapped_column(String(16), default="")  # usdt | card | balance
    payment_claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # клиентка нажала «я оплатила»
    payment_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    api_cost_usd: Mapped[float] = mapped_column(Float, default=0)  # сумма внешних вызовов по заказу
    sla_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    free_report_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # для воронки

    client: Mapped[Client] = relationship(back_populates="orders")
    findings: Mapped[list["Finding"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    def set_defaults(self):
        svc = SERVICES.get(self.service, {})
        if not self.price_usd:
            self.price_usd = svc.get("price", 0)
        if not self.due_at:
            self.due_at = now() + timedelta(days=svc.get("days", 7))
        if svc.get("kind") == "sos":
            self.priority = 1
        if svc.get("kind") in ("subscription", "exit") or self.service == "leaks":
            self.monitoring_until = now() + timedelta(days=svc.get("days", 30))

    def to_dict(self, with_findings=False):
        svc = SERVICES.get(self.service, {})
        d = {
            "id": self.id, "client_id": self.client_id, "client_code": self.client.code if self.client else None,
            "client_status": self.client.status if self.client else None,
            "service": self.service, "service_name": svc.get("name", self.service), "service_kind": svc.get("kind"),
            "status": self.status, "status_ru": ORDER_STATUS_RU.get(self.status, self.status),
            "price_usd": self.price_usd, "paid_usd": self.paid_usd, "priority": self.priority,
            "payment_method": self.payment_method, "api_cost_usd": round(self.api_cost_usd or 0, 2),
            "payment_claimed_at": self.payment_claimed_at.isoformat() if self.payment_claimed_at else None,
            "queries": self.queries, "notes": self.notes,
            "created_at": self.created_at.isoformat(),
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "next_scan_at": self.next_scan_at.isoformat() if self.next_scan_at else None,
            "monitoring_until": self.monitoring_until.isoformat() if self.monitoring_until else None,
            "findings_total": len(self.findings),
            "findings_by_status": {},
        }
        for f in self.findings:
            d["findings_by_status"][f.status] = d["findings_by_status"].get(f.status, 0) + 1
        if with_findings:
            d["findings"] = [f.to_dict() for f in self.findings]
            d["jobs"] = [j.to_dict() for j in sorted(self.jobs, key=lambda j: j.id, reverse=True)[:30]]
        return d


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    source: Mapped[str] = mapped_column(String(32))  # google_cse | brave | serpapi_yandex | pimeyes | lens | manual | hibp | dehashed
    query_type: Mapped[str] = mapped_column(String(16), default="nick")  # nick | phone | email | face | image | phrase
    query_value: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    match_type: Mapped[str] = mapped_column(String(16), default="")  # exact | similar | text
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="unknown")
    risk: Mapped[int] = mapped_column(Integer, default=0)  # 0 не оценено, 1 низкий … 3 высокий
    status: Mapped[str] = mapped_column(String(16), default="new")
    screenshot_file: Mapped[str] = mapped_column(String(255), default="")
    found_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    operator: Mapped[str] = mapped_column(String(64), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verify: Mapped[dict] = mapped_column(JSON, default=dict)  # результат enrich.verify_page + reasons правил
    auto_note: Mapped[str] = mapped_column(Text, default="")  # почему система поставила тип/риск

    order: Mapped[Order] = relationship(back_populates="findings")
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="finding", cascade="all, delete-orphan")
    takedowns: Mapped[list["Takedown"]] = relationship(back_populates="finding", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "order_id": self.order_id, "source": self.source, "query_type": self.query_type,
            "query_value": self.query_value, "url": self.url, "domain": self.domain, "title": self.title,
            "snippet": self.snippet[:300], "match_type": self.match_type, "confidence": self.confidence,
            "kind": self.kind, "kind_ru": FINDING_KINDS.get(self.kind, self.kind), "risk": self.risk,
            "status": self.status, "status_ru": FINDING_STATUSES.get(self.status, self.status),
            "screenshot_file": self.screenshot_file, "found_at": self.found_at.isoformat(),
            "operator": self.operator,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "verify": {k: v for k, v in (self.verify or {}).items() if k != "text_sample"}, "auto_note": self.auto_note,
            "evidence": [e.to_dict() for e in self.evidence],
            "takedowns": [t.to_dict() for t in self.takedowns],
        }


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    html_file: Mapped[str] = mapped_column(String(255), default="")
    screenshot_file: Mapped[str] = mapped_column(String(255), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    archive_url: Mapped[str] = mapped_column(Text, default="")  # web.archive.org
    archive_today_url: Mapped[str] = mapped_column(Text, default="")
    ots_file: Mapped[str] = mapped_column(String(255), default="")  # OpenTimestamps
    note: Mapped[str] = mapped_column(Text, default="")

    finding: Mapped[Finding] = relationship(back_populates="evidence")

    def to_dict(self):
        return {
            "id": self.id, "captured_at": self.captured_at.isoformat(), "http_status": self.http_status,
            "html_file": self.html_file, "screenshot_file": self.screenshot_file, "sha256": self.sha256,
            "archive_url": self.archive_url, "archive_today_url": self.archive_today_url,
            "ots_file": self.ots_file, "note": self.note,
        }


class Takedown(Base):
    __tablename__ = "takedowns"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    platform: Mapped[str] = mapped_column(String(255), default="")
    channel: Mapped[str] = mapped_column(String(16), default="email")  # email | form | manual
    template: Mapped[str] = mapped_column(String(32), default="dmca")
    to_address: Mapped[str] = mapped_column(String(255), default="")
    form_url: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    wave: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_message_id: Mapped[str] = mapped_column(String(255), default="")
    log: Mapped[list] = mapped_column(JSON, default=list)

    finding: Mapped[Finding] = relationship(back_populates="takedowns")

    def add_log(self, text: str):
        self.log = [*(self.log or []), {"at": now().isoformat(), "text": text}]

    def to_dict(self):
        return {
            "id": self.id, "finding_id": self.finding_id, "platform": self.platform, "channel": self.channel,
            "template": self.template, "to_address": self.to_address, "form_url": self.form_url,
            "subject": self.subject, "body": self.body, "status": self.status,
            "status_ru": TAKEDOWN_STATUSES.get(self.status, self.status), "wave": self.wave,
            "created_at": self.created_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "next_check_at": self.next_check_at.isoformat() if self.next_check_at else None,
            "provider_message_id": self.provider_message_id, "log": self.log,
        }


class Job(Base):
    """Фоновая задача конвейера: скан, фиксация, отправка, повторная проверка."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | error
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    order: Mapped[Order | None] = relationship(back_populates="jobs")

    def to_dict(self):
        return {
            "id": self.id, "order_id": self.order_id, "type": self.type, "status": self.status,
            "params": self.params, "result": self.result, "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class IntegrationLog(Base):
    __tablename__ = "integration_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def to_dict(self):
        return {"id": self.id, "service": self.service, "action": self.action, "ok": self.ok,
                "detail": self.detail[:500], "created_at": self.created_at.isoformat()}


class Incident(Base):
    """Карточка SOS-инцидента (SOP-4)."""
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    extortionist: Mapped[str] = mapped_column(Text, default="")  # ник/номер, без пробива
    what_they_have: Mapped[str] = mapped_column(Text, default="")
    demand: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[str] = mapped_column(Text, default="")
    already_sent_to: Mapped[str] = mapped_column(Text, default="")
    relatives_at_risk: Mapped[str] = mapped_column(Text, default="")
    physical_danger: Mapped[bool] = mapped_column(Boolean, default=False)
    stopncii_case: Mapped[str] = mapped_column(String(64), default="")
    plan_72h: Mapped[str] = mapped_column(Text, default="")
    checklist: Mapped[dict] = mapped_column(JSON, default=dict)  # SOP-6
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def to_dict(self):
        return {c: getattr(self, c) for c in (
            "id", "order_id", "extortionist", "what_they_have", "demand", "deadline", "already_sent_to",
            "relatives_at_risk", "physical_danger", "stopncii_case", "plan_72h", "checklist")} | {
            "created_at": self.created_at.isoformat()}


class User(Base):
    """Операторы панели. Роли: admin (всё), manager (заказы, снятия, справочники), duty (заявки, SOS, находки), viewer."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="duty")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def to_dict(self):
        return {"id": self.id, "username": self.username, "role": self.role, "active": self.active, "created_at": self.created_at.isoformat()}


class AuditLog(Base):
    """Журнал действий операторов — для NDA-обещаний и разбора спорных случаев."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer, default=200)
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def to_dict(self):
        return {"id": self.id, "user": self.user, "method": self.method, "path": self.path, "status": self.status,
                "summary": self.summary[:300], "created_at": self.created_at.isoformat()}


ROLE_LEVEL = {"viewer": 0, "duty": 1, "manager": 2, "admin": 3}
