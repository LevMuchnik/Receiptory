import os
import pytest
from pathlib import Path
from backend.storage import (save_original, save_converted, save_filed, get_file_path, render_page, clear_page_cache, compute_file_hash)

def test_compute_file_hash(sample_pdf_path):
    h = compute_file_hash(sample_pdf_path)
    assert len(h) == 64
    assert compute_file_hash(sample_pdf_path) == h

def test_save_and_get_original(tmp_data_dir, sample_pdf_path):
    file_hash = compute_file_hash(sample_pdf_path)
    saved = save_original(sample_pdf_path, file_hash, ".pdf", str(tmp_data_dir))
    assert os.path.exists(saved)
    assert file_hash in saved
    retrieved = get_file_path("original", file_hash, ".pdf", str(tmp_data_dir))
    assert retrieved == saved

def test_save_filed(tmp_data_dir, sample_pdf_path):
    stored_name = "2026-01-15-INV001-abc123.pdf"
    filed_path = save_filed(sample_pdf_path, stored_name, str(tmp_data_dir))
    assert os.path.exists(filed_path)
    assert stored_name in filed_path

def test_render_page(tmp_data_dir, sample_pdf_path):
    png_bytes = render_page(sample_pdf_path, page_num=0, dpi=150)
    assert len(png_bytes) > 0
    assert png_bytes[:4] == b'\x89PNG'

def test_render_page_cached(tmp_data_dir, sample_pdf_path):
    cache_dir = str(tmp_data_dir / "storage" / "page_cache")
    png1 = render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    png2 = render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    assert png1 == png2
    assert os.path.exists(os.path.join(cache_dir, "1", "page_0.png"))

def test_clear_page_cache(tmp_data_dir, sample_pdf_path):
    cache_dir = str(tmp_data_dir / "storage" / "page_cache")
    render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    clear_page_cache(cache_dir, doc_id=1)
    assert not os.path.exists(os.path.join(cache_dir, "1"))
