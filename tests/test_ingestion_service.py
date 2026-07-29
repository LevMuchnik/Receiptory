from PIL import Image

from backend.database import get_connection


def _write_jpeg(file_path):
    Image.new("RGB", (8, 8), color=(240, 240, 240)).save(file_path, "JPEG")


def test_ingest_local_file_creates_pending_document(db_path, tmp_data_dir):
    from backend.ingestion.service import ingest_local_file

    source = tmp_data_dir / "incoming.jpg"
    _write_jpeg(source)

    result = ingest_local_file(
        str(source),
        filename="worker-receipt.jpg",
        data_dir=str(tmp_data_dir),
        submission_channel="remote_intake",
        sender_identifier="worker:test",
    )

    assert result.status == "accepted"
    assert result.document_id > 0
    assert (tmp_data_dir / "storage" / "originals" / f"{result.file_hash}.jpg").exists()
    with get_connection() as conn:
        document = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (result.document_id,)
        ).fetchone()
    assert document["original_filename"] == "worker-receipt.jpg"
    assert document["submission_channel"] == "remote_intake"
    assert document["sender_identifier"] == "worker:test"
    assert document["status"] == "pending"


def test_ingest_local_file_returns_existing_duplicate(db_path, tmp_data_dir):
    from backend.ingestion.service import ingest_local_file

    source = tmp_data_dir / "incoming.jpg"
    _write_jpeg(source)

    first = ingest_local_file(
        str(source),
        filename="first.jpg",
        data_dir=str(tmp_data_dir),
        submission_channel="web_upload",
    )
    second = ingest_local_file(
        str(source),
        filename="second.jpg",
        data_dir=str(tmp_data_dir),
        submission_channel="remote_intake",
    )

    assert first.status == "accepted"
    assert second.status == "duplicate"
    assert second.document_id == first.document_id
    assert second.file_hash == first.file_hash
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1
