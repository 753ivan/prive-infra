"""Пользователи, роли и журнал действий.

Логин: HTTP Basic. Если таблица users пуста — работает ADMIN_USER/ADMIN_PASSWORD из .env с ролью admin
(и при первом запуске этот пользователь создаётся в таблице). Пароли — PBKDF2-SHA256.
Роли: viewer < duty < manager < admin.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings
from .db import session_scope
from .models import ROLE_LEVEL, AuditLog, User

security = HTTPBasic()


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def ensure_admin():
    with session_scope() as db:
        if not db.query(User).count():
            db.add(User(username=settings.admin_user, password_hash=hash_password(settings.admin_password), role="admin"))


class Principal:
    def __init__(self, username: str, role: str):
        self.username, self.role = username, role

    def __str__(self):
        return self.username

    def at_least(self, role: str) -> bool:
        return ROLE_LEVEL.get(self.role, 0) >= ROLE_LEVEL[role]


def current_user(request: Request, creds: HTTPBasicCredentials = Depends(security)) -> Principal:
    p = None
    with session_scope() as db:
        u = db.query(User).filter(User.username == creds.username, User.active.is_(True)).first()
        if u and verify_password(creds.password, u.password_hash):
            p = Principal(u.username, u.role)
    # резерв: env-админ, если в базе его нет
    if not p and secrets.compare_digest(creds.username, settings.admin_user) and secrets.compare_digest(creds.password, settings.admin_password):
        p = Principal(creds.username, "admin")
    if not p:
        raise HTTPException(401, "Неверный логин или пароль", headers={"WWW-Authenticate": "Basic"})
    request.state.user = p.username
    return p


def require(role: str):
    def dep(p: Principal = Depends(current_user)) -> Principal:
        if not p.at_least(role):
            raise HTTPException(403, f"Нужна роль {role} или выше")
        return p
    return dep


def audit(p: Principal, request: Request, summary: str = "", status: int = 200):
    try:
        with session_scope() as db:
            db.add(AuditLog(user=p.username, method=request.method, path=str(request.url.path), status=status, summary=summary[:2000]))
    except Exception:
        pass
