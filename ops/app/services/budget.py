"""Дневной лимит расходов на внешние вызовы.

Платные вызовы (поиск, лицо, утечки) пишутся в леджер CostEntry. Пока расход за
сегодня ниже settings.daily_cost_limit_usd — платные источники разрешены; при
превышении конвейер переключается на бесплатные (SearXNG, свой индекс лиц), а не
останавливается. Лимит 0 или меньше — без ограничения. Граница суток — UTC
(совпадает с models.now()).
"""
from __future__ import annotations

from sqlalchemy import func

from ..config import settings
from ..models import CostEntry, now


def _day_start():
    return now().replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today(db) -> float:
    v = (db.query(func.coalesce(func.sum(CostEntry.amount_usd), 0.0))
         .filter(CostEntry.created_at >= _day_start()).scalar())
    return float(v or 0.0)


def remaining_today(db) -> float:
    if settings.daily_cost_limit_usd <= 0:
        return float("inf")
    return max(settings.daily_cost_limit_usd - spent_today(db), 0.0)


def allow_paid(db) -> bool:
    """Есть ли остаток дневного лимита под платные источники."""
    if settings.daily_cost_limit_usd <= 0:
        return True
    return spent_today(db) < settings.daily_cost_limit_usd


def charge(db, order_id, amount: float, source: str = "scan") -> None:
    """Записать расход в леджер (amount<=0 игнорируется)."""
    if not amount or amount <= 0:
        return
    db.add(CostEntry(order_id=order_id, source=source, amount_usd=round(float(amount), 4)))
