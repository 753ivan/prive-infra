from app.config import settings
from app.pipeline import session_scope
from app.services import budget


def test_charge_and_daily_limit(client):
    old = settings.daily_cost_limit_usd
    settings.daily_cost_limit_usd = 5.0
    try:
        with session_scope() as db:
            before = budget.spent_today(db)
            budget.charge(db, None, 3.0, "test")
        with session_scope() as db:
            assert round(budget.spent_today(db) - before, 2) == 3.0
            assert budget.allow_paid(db) is True  # before+3 ещё под лимитом (если тест первый)
            budget.charge(db, None, 10.0, "test")  # гарантированно за лимит
        with session_scope() as db:
            assert budget.allow_paid(db) is False
    finally:
        settings.daily_cost_limit_usd = old


def test_zero_limit_is_unlimited(client):
    old = settings.daily_cost_limit_usd
    settings.daily_cost_limit_usd = 0
    try:
        with session_scope() as db:
            budget.charge(db, None, 999.0, "test")
            assert budget.allow_paid(db) is True
    finally:
        settings.daily_cost_limit_usd = old


def test_charge_ignores_nonpositive(client):
    with session_scope() as db:
        before = budget.spent_today(db)
        budget.charge(db, None, 0, "test")
        budget.charge(db, None, -5, "test")
    with session_scope() as db:
        assert budget.spent_today(db) == before
