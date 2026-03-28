import os
import tempfile
import pytest
from pathlib import Path
from backend.database import init_db, get_connection

@pytest.fixture
def tmp_data_dir(tmp_path):
    for subdir in ["storage/originals", "storage/converted", "storage/filed", "storage/page_cache", "logs"]:
        (tmp_path / subdir).mkdir(parents=True)
    return tmp_path

@pytest.fixture(autouse=True)
def _reset_test_env(monkeypatch):
    """Reset database global and clear RECEIPTORY_ env vars for test isolation.
    litellm auto-loads .env on import, and dotenv.load_dotenv in create_app
    also loads .env. We set vars to empty string (not delete) so load_dotenv
    with override=False won't re-populate them."""
    import backend.database as _db_mod
    _db_mod._db_path = None
    for key in list(os.environ):
        if key.startswith("RECEIPTORY_"):
            monkeypatch.setenv(key, "")
    # Ensure DEV mode is on for tests (prevents static file mount from intercepting API routes)
    monkeypatch.setenv("RECEIPTORY_DEV", "1")
    yield
    _db_mod._db_path = None


@pytest.fixture
def db_path(tmp_data_dir):
    path = str(tmp_data_dir / "receiptory.db")
    init_db(path)
    return path

@pytest.fixture
def db_conn(db_path):
    with get_connection() as conn:
        yield conn

@pytest.fixture
def sample_pdf_path():
    return str(Path(__file__).parent.parent / "test_documents" / "Receipt - esim.pdf")
