import os
import tempfile
from unittest import mock

import pytest

os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["ADMIN_PASSWORD"] = "test"
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import telegram as tg  # noqa: E402


@pytest.fixture(scope="session")
def client():
    def fake_dl(file_id, dest):
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / f"{file_id}.jpg"
        p.write_bytes(b"x")
        return str(p.relative_to(tg.settings.storage_dir)), "sha" + file_id

    with mock.patch.object(tg, "send_message", lambda *a, **k: True), mock.patch.object(tg, "download_file", fake_dl), \
         mock.patch.object(tg, "answer_callback", lambda *a, **k: None), mock.patch.object(tg, "send_document", lambda *a, **k: True):
        c = TestClient(app)
        c.auth = ("operator", "test")
        with c:
            yield c
