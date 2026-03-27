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
