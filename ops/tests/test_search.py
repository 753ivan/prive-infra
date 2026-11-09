import pytest

from app.services import search


def test_status_keys_updated():
    s = search.status()
    assert {"searxng", "dataforseo", "brave", "serpapi"} <= set(s)
    assert "google_cse" not in s  # Google CSE выведен


def test_providers_set():
    assert set(search.PROVIDERS) == {"searxng", "dataforseo", "brave", "serpapi_yandex"}


def test_new_providers_not_configured():
    # без ключей/URL — NotConfigured (без сетевых вызовов)
    for name in ("searxng", "dataforseo"):
        with pytest.raises(search.NotConfigured):
            search.PROVIDERS[name]("тест")
