"""notifier._get_thumbnail locates the original the way the download does."""
import shutil

from backend.notifications.notifier import _get_thumbnail

_H = "ab" * 32


def test_thumbnail_renders_an_original_whose_extension_the_name_does_not_predict(monkeypatch, tmp_data_dir, sample_pdf_path):
    """gmail.py can store originals/<hash>.PDF; the old guess (lower-cased
    extension of original_filename) looked for <hash>.pdf and sent no image."""
    monkeypatch.setenv("RECEIPTORY_DATA_DIR", str(tmp_data_dir))
    shutil.copy(sample_pdf_path, tmp_data_dir / "storage" / "originals" / f"{_H}.PDF")
    png = _get_thumbnail({"file_hash": _H, "original_filename": "SCAN.PDF"})
    assert png is not None and png.startswith(b"\x89PNG")

