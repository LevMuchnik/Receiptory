import io
import csv
import zipfile
import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection
import shutil
import os


@pytest.fixture
def app(db_path, tmp_data_dir):
    init_settings()
    pw_hash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    return create_app(str(tmp_data_dir), run_background=False)


@pytest.fixture
def authed_client(app):
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    return client


@pytest.fixture
def docs_with_files(authed_client, db_path, tmp_data_dir, sample_pdf_path):
    """Create documents with actual filed PDFs."""
    filed_dir = os.path.join(str(tmp_data_dir), "storage", "filed")
    os.makedirs(filed_dir, exist_ok=True)

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('r1.pdf', 'h1', 100, 'processed', '2026-01-15-INV001-h1.pdf', '2026-01-15', 'Vendor A', 100.0, 4, 'web_upload')"""
        )
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('r2.pdf', 'h2', 200, 'processed', '2026-01-20-INV002-h2.pdf', '2026-01-20', 'Vendor B', 200.0, 5, 'web_upload')"""
        )

    # Create filed PDFs
    shutil.copy2(sample_pdf_path, os.path.join(filed_dir, "2026-01-15-INV001-h1.pdf"))
    shutil.copy2(sample_pdf_path, os.path.join(filed_dir, "2026-01-20-INV002-h2.pdf"))


def test_export_zip_structure(authed_client, docs_with_files):
    # Explicit date_basis="receipt" preserves original receipt-date semantics
    resp = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("metadata.csv" in n for n in names)
    assert any(".pdf" in n for n in names)


def test_export_csv_content(authed_client, docs_with_files):
    resp = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"})
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    csv_content = zf.read("metadata.csv").decode("utf-8")
    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["vendor_name"] in ("Vendor A", "Vendor B")


def test_export_updates_last_exported(authed_client, docs_with_files):
    authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"})
    with get_connection() as conn:
        docs = conn.execute("SELECT last_exported_date FROM documents WHERE status = 'processed'").fetchall()
    for doc in docs:
        assert doc["last_exported_date"] is not None


def _write_dummy_pdf(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n%%EOF\n")


def test_export_ingestion_basis(authed_client, db_path, tmp_data_dir):
    """Doc with submission_date in range but receipt_date out of range appears in ingestion export only."""
    filed_dir = os.path.join(str(tmp_data_dir), "storage", "filed")

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, submission_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('ing.pdf', 'h3', 100, 'processed', '2026-03-01-ING-h3.pdf',
               '2025-11-15', '2026-03-01T10:00:00Z', 'Ingested Vendor', 50.0, 4, 'web_upload')"""
        )
    _write_dummy_pdf(os.path.join(filed_dir, "2026-03-01-ING-h3.pdf"))

    # Visible with ingestion basis (submission_date is in March 2026)
    resp = authed_client.post("/api/export", json={"date_basis": "ingestion", "date_from": "2026-03-01", "date_to": "2026-03-31"})
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    csv_content = zf.read("metadata.csv").decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(csv_content)))
    assert any(r["vendor_name"] == "Ingested Vendor" for r in rows)

    # NOT visible with receipt basis (receipt_date is Nov 2025, outside March 2026)
    resp2 = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-03-01", "date_to": "2026-03-31"})
    assert resp2.status_code == 200
    zf2 = zipfile.ZipFile(io.BytesIO(resp2.content))
    csv2 = zf2.read("metadata.csv").decode("utf-8")
    rows2 = list(csv.DictReader(io.StringIO(csv2)))
    assert not any(r["vendor_name"] == "Ingested Vendor" for r in rows2)


def test_export_receipt_basis(authed_client, db_path, tmp_data_dir):
    """Doc with receipt_date in range but submission_date out of range appears in receipt export only."""
    filed_dir = os.path.join(str(tmp_data_dir), "storage", "filed")

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, submission_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('rcpt.pdf', 'h4', 100, 'processed', '2026-04-01-RCPT-h4.pdf',
               '2026-04-10', '2025-12-01T08:00:00Z', 'Receipt Vendor', 75.0, 4, 'web_upload')"""
        )
    _write_dummy_pdf(os.path.join(filed_dir, "2026-04-01-RCPT-h4.pdf"))

    # Visible with receipt basis (receipt_date is April 2026)
    resp = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-04-01", "date_to": "2026-04-30"})
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp.content)).read("metadata.csv").decode())))
    assert any(r["vendor_name"] == "Receipt Vendor" for r in rows)

    # NOT visible with ingestion basis (submission_date is Dec 2025)
    resp2 = authed_client.post("/api/export", json={"date_basis": "ingestion", "date_from": "2026-04-01", "date_to": "2026-04-30"})
    assert resp2.status_code == 200
    rows2 = list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp2.content)).read("metadata.csv").decode())))
    assert not any(r["vendor_name"] == "Receipt Vendor" for r in rows2)


def test_export_null_receipt_date_ingestion(authed_client, db_path, tmp_data_dir):
    """Doc with NULL receipt_date and a valid submission_date appears in ingestion export."""
    filed_dir = os.path.join(str(tmp_data_dir), "storage", "filed")

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, submission_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('null_rcpt.pdf', 'h5', 100, 'processed', '2026-05-01-NULL-h5.pdf',
               NULL, '2026-05-01T09:00:00Z', 'No Date Vendor', 25.0, 4, 'web_upload')"""
        )
    _write_dummy_pdf(os.path.join(filed_dir, "2026-05-01-NULL-h5.pdf"))

    # Appears with ingestion basis despite NULL receipt_date
    resp = authed_client.post("/api/export", json={"date_basis": "ingestion", "date_from": "2026-05-01", "date_to": "2026-05-31"})
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp.content)).read("metadata.csv").decode())))
    assert any(r["vendor_name"] == "No Date Vendor" for r in rows)

    # With the Issue Date basis it falls under its upload date (owner,
    # 2026-10-22): it used to be silently left out of every such export.
    resp2 = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-05-01", "date_to": "2026-05-31"})
    assert resp2.status_code == 200
    rows2 = list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp2.content)).read("metadata.csv").decode())))
    assert any(r["vendor_name"] == "No Date Vendor" for r in rows2)
    # ...and only there: not in a neighbouring month.
    resp3 = authed_client.post("/api/export", json={"preset": "month", "month": "2026-04", "date_basis": "receipt"})
    rows3 = list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp3.content)).read("metadata.csv").decode())))
    assert not any(r["vendor_name"] == "No Date Vendor" for r in rows3)


def test_export_date_to_boundary_ingestion(authed_client, db_path, tmp_data_dir):
    """Doc ingested at 23:59:59 UTC on date_to day IS included (exclusive upper bound < next_day)."""
    filed_dir = os.path.join(str(tmp_data_dir), "storage", "filed")

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, submission_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('late.pdf', 'h6', 100, 'processed', '2026-06-30-LATE-h6.pdf',
               '2026-06-30', '2026-06-30T23:59:59Z', 'Late Vendor', 10.0, 4, 'web_upload')"""
        )
    _write_dummy_pdf(os.path.join(filed_dir, "2026-06-30-LATE-h6.pdf"))

    # date_to="2026-06-30" with ingestion basis: uses < "2026-07-01", so 23:59:59 is included
    resp = authed_client.post("/api/export", json={"date_basis": "ingestion", "date_from": "2026-06-01", "date_to": "2026-06-30"})
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp.content)).read("metadata.csv").decode())))
    assert any(r["vendor_name"] == "Late Vendor" for r in rows)


def test_export_order_by_matches_basis(authed_client, db_path, tmp_data_dir):
    """Rows are sorted by submission_date for ingestion basis, receipt_date for receipt basis."""
    filed_dir = os.path.join(str(tmp_data_dir), "storage", "filed")

    with get_connection() as conn:
        # Doc A: ingested first, receipt date second
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, submission_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('a.pdf', 'ha', 100, 'processed', '2026-07-01-A-ha.pdf',
               '2026-07-10', '2026-07-01T08:00:00Z', 'Alpha', 10.0, 4, 'web_upload')"""
        )
        # Doc B: ingested second, receipt date first
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, submission_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('b.pdf', 'hb', 100, 'processed', '2026-07-01-B-hb.pdf',
               '2026-07-01', '2026-07-10T08:00:00Z', 'Beta', 20.0, 4, 'web_upload')"""
        )
    _write_dummy_pdf(os.path.join(filed_dir, "2026-07-01-A-ha.pdf"))
    _write_dummy_pdf(os.path.join(filed_dir, "2026-07-01-B-hb.pdf"))

    # Ingestion basis: Alpha (ingested July 1) before Beta (ingested July 10)
    resp = authed_client.post("/api/export", json={"date_basis": "ingestion", "date_from": "2026-07-01", "date_to": "2026-07-31"})
    rows = list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp.content)).read("metadata.csv").decode())))
    vendors_ingestion = [r["vendor_name"] for r in rows]
    assert vendors_ingestion == ["Alpha", "Beta"]

    # Receipt basis: Beta (receipt July 1) before Alpha (receipt July 10)
    resp2 = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-07-01", "date_to": "2026-07-31"})
    rows2 = list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp2.content)).read("metadata.csv").decode())))
    vendors_receipt = [r["vendor_name"] for r in rows2]
    assert vendors_receipt == ["Beta", "Alpha"]


def test_export_invalid_date_to(authed_client):
    """Malformed date_to returns 422, not 500."""
    resp = authed_client.post("/api/export", json={"date_basis": "ingestion", "date_from": "2026-01-01", "date_to": "not-a-date"})
    assert resp.status_code == 422


def test_export_invalid_date_from(authed_client):
    """Malformed date_from returns 422, not silent empty results."""
    resp = authed_client.post("/api/export", json={"date_basis": "ingestion", "date_from": "not-a-date"})
    assert resp.status_code == 422


def test_export_invalid_month_format(authed_client):
    """Malformed month returns 422, not 500."""
    resp = authed_client.post("/api/export", json={"preset": "month", "month": "2026/07"})
    assert resp.status_code == 422


def test_export_month_single_digit(authed_client):
    """month='2026-1' is accepted — strptime is lenient, strftime zero-pads output."""
    resp = authed_client.post("/api/export", json={"preset": "month", "month": "2026-1"})
    assert resp.status_code == 200


def test_export_omits_a_row_whose_stored_filename_escapes_filed(authed_client, docs_with_files, tmp_path):
    """Was an arbitrary root read straight into the export zip.

    os.path.join(filed_dir, "/etc/shadow") returns "/etc/shadow" -- the prefix
    is silently discarded -- os.path.exists then says yes, and ZipFile.write
    copied it in under the arcname "<section>/<cat>//etc/shadow". The row is
    untrusted because a restored database is untrusted, which is exactly the
    lesson #45 cost us on scanner_test_frames.frame_path.

    The bad row is skipped and the export still succeeds: one poisoned row must
    not take the whole export down either.
    """
    from backend.database import get_connection

    secret = tmp_path / "secret.txt"
    secret.write_text("root password hash")

    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET stored_filename = ? WHERE original_filename = 'r2.pdf'",
            (str(secret),),
        )

    resp = authed_client.post(
        "/api/export",
        json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"},
    )

    assert resp.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert not any("secret.txt" in n for n in names), "an absolute path was read into the zip"
    assert not any(n.endswith(str(secret)) for n in names)
    # The healthy document still exports.
    assert any("2026-01-15-INV001-h1.pdf" in n for n in names)
    assert any("metadata.csv" in n for n in names)


def test_export_arcname_cannot_escape_the_extraction_directory(authed_client, docs_with_files):
    """Zip-slip. The arcname is section/category/filename and only the filename
    was hardened; categories.name is a bare str in CategoryCreate with no
    validation. Verified: zipfile.ZipInfo.from_file normpaths the arcname and
    strips a LEADING separator but keeps ".." intact, so
    "expense/../../../tmp/evil/x.pdf" is stored as "../../tmp/evil/x.pdf" --
    which unzip writes outside wherever you extracted."""
    from backend.database import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE categories SET name = ? WHERE id = 4", ("../../../../tmp/evil",))
        conn.execute("UPDATE categories SET section = ? WHERE id = 4", ("../..",))

    resp = authed_client.post(
        "/api/export",
        json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"},
    )

    assert resp.status_code == 200
    for n in zipfile.ZipFile(io.BytesIO(resp.content)).namelist():
        assert not n.startswith("/"), f"absolute arcname: {n!r}"
        assert ".." not in n.split("/"), f"zip-slip arcname: {n!r}"


def test_a_document_whose_pdf_was_omitted_is_not_marked_exported(authed_client, docs_with_files, tmp_path):
    """last_exported_date is what preset='since_last_export' filters on, so
    stamping a document whose PDF never made it into the zip retires it from
    every future incremental export, silently and permanently."""
    from backend.database import get_connection

    secret = tmp_path / "secret.txt"
    secret.write_text("not a receipt")
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET stored_filename = ? WHERE original_filename = 'r2.pdf'",
            (str(secret),),
        )

    resp = authed_client.post(
        "/api/export",
        json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert resp.status_code == 200

    with get_connection() as conn:
        rows = {
            r["original_filename"]: r["last_exported_date"]
            for r in conn.execute(
                "SELECT original_filename, last_exported_date FROM documents"
            )
        }
    assert rows["r1.pdf"] is not None, "the delivered document should be stamped"
    assert rows["r2.pdf"] is None, (
        "a document whose PDF was omitted was marked exported -- "
        "since_last_export will never offer it again"
    )


def test_a_document_whose_filed_copy_is_missing_is_not_marked_exported(
    authed_client, docs_with_files, tmp_data_dir
):
    """The OTHER omitted branch, and the one that actually fires in production:
    the row is fine, the file is gone -- lost to exactly the rename churn this
    PR is about. Mutation-proven gap: replacing that omitted.add with `pass`
    left tests/test_export.py at 15/15 green."""
    from backend.database import get_connection

    os.unlink(os.path.join(str(tmp_data_dir), "storage", "filed", "2026-01-20-INV002-h2.pdf"))

    resp = authed_client.post(
        "/api/export",
        json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert resp.status_code == 200

    with get_connection() as conn:
        rows = {
            r["original_filename"]: r["last_exported_date"]
            for r in conn.execute(
                "SELECT original_filename, last_exported_date FROM documents"
            )
        }
    assert rows["r1.pdf"] is not None
    assert rows["r2.pdf"] is None, (
        "a document whose filed copy was missing was stamped as exported"
    )


def test_two_categories_never_share_an_export_folder(authed_client, docs_with_files):
    """_slugify is many-to-one: "Travel/Meals" and "Travel Meals" both become
    "Travel_Meals". Without the category id in the folder name, two distinct
    categories merge into one heading in the archive."""
    from backend.database import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category_id FROM documents WHERE category_id IS NOT NULL"
        ).fetchall()
        ids = [r["category_id"] for r in rows]
        assert len(ids) >= 2, "fixture needs two categories to collide"
        conn.execute("UPDATE categories SET name = ? WHERE id = ?", ("Travel/Meals", ids[0]))
        conn.execute("UPDATE categories SET name = ? WHERE id = ?", ("Travel Meals", ids[1]))

    resp = authed_client.post(
        "/api/export",
        json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert resp.status_code == 200

    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    folders = {n.rsplit("/", 1)[0] for n in names if n.endswith(".pdf")}
    assert len(folders) == 2, f"two categories collapsed into one folder: {folders}"


def test_export_has_the_converted_columns(authed_client, docs_with_files, monkeypatch):
    """#72: the export carries the conversion, and only for the current reference."""
    with get_connection() as conn:
        conn.execute("UPDATE documents SET currency = 'USD', conversion_rate = 3.0, converted_amount = 300.0, "
                     "conversion_rate_date = '2026-01-15', conversion_ref = 'ILS' WHERE file_hash = 'h1'")
        conn.execute("UPDATE documents SET currency = 'EUR', conversion_rate = 0.9, converted_amount = 180.0, "
                     "conversion_rate_date = '2026-01-20', conversion_ref = 'USD' WHERE file_hash = 'h2'")
    resp = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"})
    rows = {r["vendor_name"]: r for r in csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp.content)).read("metadata.csv").decode("utf-8")))}
    a, b = rows["Vendor A"], rows["Vendor B"]
    assert (a["converted_amount"], a["conversion_rate"], a["conversion_rate_date"], a["conversion_ref"]) == ("300.0", "3.0", "2026-01-15", "ILS")
    # computed for another reference: stale, so not exported as a conversion
    assert (b["converted_amount"], b["conversion_rate"], b["conversion_ref"]) == ("", "", "")



def test_export_appends_the_conversion_columns_and_writes_them_to_excel(authed_client, docs_with_files):
    """New columns go last, so a sheet that reads the export by position keeps working."""
    import openpyxl
    with get_connection() as conn:
        conn.execute("UPDATE documents SET currency = 'USD', conversion_rate = 3.0, converted_amount = 300.0, "
                     "conversion_rate_date = '2026-01-15', conversion_ref = 'ILS' WHERE file_hash = 'h1'")
    resp = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"})
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    header = next(csv.reader(io.StringIO(zf.read("metadata.csv").decode("utf-8"))))
    assert header[-5:] == ["converted_amount", "conversion_rate", "conversion_rate_date", "conversion_ref", "exported_file"]
    ws = openpyxl.load_workbook(io.BytesIO(zf.read("metadata.xlsx"))).active
    rows = list(ws.iter_rows(values_only=True))
    assert list(rows[0][-5:]) == header[-5:]
    assert (300.0, 3.0, "2026-01-15", "ILS") in [tuple(r[-5:-1]) for r in rows[1:]]
    # exported_file names the row's file in the zip, in both sheets
    assert {r[-1] for r in rows[1:]} == {n for n in zf.namelist() if n.endswith(".pdf")}



def test_since_last_export_delivers_every_document_once_converted_or_not(authed_client, docs_with_files):
    """Owner (2026-10-19): documents are the key; the accountant works from
    them, and a conversion is only an extra column. A document without a rate
    is exported and stamped like any other, never held back or resent."""
    with get_connection() as conn:
        conn.execute("UPDATE documents SET currency = 'USD' WHERE file_hash = 'h1'")  # no rate (yet)
        conn.execute("UPDATE documents SET currency = 'ILS' WHERE file_hash = 'h2'")  # rate 1, nothing stored
    first = authed_client.post("/api/export", json={"preset": "since_last_export"})
    rows = {r["vendor_name"]: r for r in csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(first.content)).read("metadata.csv").decode("utf-8")))}
    assert set(rows) == {"Vendor A", "Vendor B"}
    assert (rows["Vendor A"]["total_amount"], rows["Vendor A"]["currency"], rows["Vendor A"]["converted_amount"]) == ("100.0", "USD", "")
    assert (rows["Vendor B"]["converted_amount"], rows["Vendor B"]["conversion_rate"]) == ("200.0", "1.0")
    with get_connection() as conn:
        assert all(r[0] is not None for r in conn.execute("SELECT last_exported_date FROM documents"))
    second = authed_client.post("/api/export", json={"preset": "since_last_export"})
    assert list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(second.content)).read("metadata.csv").decode("utf-8")))) == []


def test_export_fills_a_reference_currency_row_before_its_rate_is_stored(authed_client, docs_with_files):
    with get_connection() as conn:
        conn.execute("UPDATE documents SET currency = 'ILS' WHERE file_hash = 'h1'")
    resp = authed_client.post("/api/export", json={"date_basis": "receipt", "date_from": "2026-01-01", "date_to": "2026-12-31"})
    rows = {r["vendor_name"]: r for r in csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(resp.content)).read("metadata.csv").decode("utf-8")))}
    assert (rows["Vendor A"]["converted_amount"], rows["Vendor A"]["conversion_rate"], rows["Vendor A"]["conversion_ref"]) == ("100.0", "1.0", "ILS")


# --- Every non-deleted document is exported (owner, 2026-10-19: documents are
# the key). Real sha256 hashes from here on: the fixtures above use 'h1'/'h2',
# which find_original refuses, so their omission tests never reach the
# original fallback.

import hashlib


def _add_doc(tmp_data_dir, *, name, status="processed", content=b"%PDF-1.4 original\n", ext=".pdf",
             stored=None, filed=False, original=True, receipt_date=None,
             submission_date="2026-09-03T07:18:00Z", deleted=0, category_id=None):
    file_hash = hashlib.sha256(content + name.encode()).hexdigest()
    storage = os.path.join(str(tmp_data_dir), "storage")
    if original:
        with open(os.path.join(storage, "originals", file_hash + ext), "wb") as f:
            f.write(content)
    if filed:
        _write_dummy_pdf(os.path.join(storage, "filed", stored))
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, stored_filename,
               receipt_date, submission_date, vendor_name, category_id, submission_channel, is_deleted)
               VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, 'email', ?)""",
            (name, file_hash, status, stored, receipt_date, submission_date, name, category_id, deleted),
        )
        return cur.lastrowid, file_hash


def _export(client, body):
    resp = client.post("/api/export", json=body)
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    rows = {r["vendor_name"]: r for r in csv.DictReader(io.StringIO(zf.read("metadata.csv").decode("utf-8")))}
    return resp, zf, rows


def _stamp(doc_id):
    with get_connection() as conn:
        return conn.execute("SELECT last_exported_date FROM documents WHERE id = ?", (doc_id,)).fetchone()[0]


SEPT = {"preset": "month", "month": "2026-09", "date_basis": "ingestion"}


def test_a_failed_document_with_no_filed_copy_is_exported_as_its_original(authed_client, db_path, tmp_data_dir):
    """The live case (doc 274): a failed .mht invoice, never filed. It used to
    be dropped by the status filter, and by the missing filed copy."""
    doc_id, file_hash = _add_doc(tmp_data_dir, name="חשבונית מס 704012515.mht", status="failed",
                                 content=b"MIME-Version: 1.0\n", ext=".mht")
    resp, zf, rows = _export(authed_client, SEPT)
    row = rows["חשבונית מס 704012515.mht"]
    assert row["status"] == "failed"
    arcname = row["exported_file"]
    assert arcname.startswith(f"other/0-uncategorized/2026-09-03-{file_hash[:8]}-")
    assert arcname.endswith(".mht")
    assert zf.read(arcname) == b"MIME-Version: 1.0\n"
    assert _stamp(doc_id) is not None, "a delivered document is stamped, processed or not"
    assert resp.headers["X-Export-Omitted"] == "0"


def test_a_needs_review_document_is_exported_with_its_filed_copy(authed_client, db_path, tmp_data_dir):
    _add_doc(tmp_data_dir, name="review.pdf", status="needs_review",
             stored="2026-09-03-77-aaaaaaaa.pdf", filed=True)
    _, zf, rows = _export(authed_client, SEPT)
    assert rows["review.pdf"]["status"] == "needs_review"
    assert rows["review.pdf"]["exported_file"] == "other/0-uncategorized/2026-09-03-77-aaaaaaaa.pdf"
    assert zf.read(rows["review.pdf"]["exported_file"]) == b"%PDF-1.4\n%%EOF\n"


def test_a_missing_filed_copy_falls_back_to_the_original(authed_client, db_path, tmp_data_dir):
    """Name borrowed from the filed name, extension from the file on disk."""
    doc_id, _ = _add_doc(tmp_data_dir, name="scan.jpg", content=b"\xff\xd8jpeg", ext=".jpg",
                         stored="2026-09-01-55-bbbbbbbb.pdf", filed=False)
    _, zf, rows = _export(authed_client, SEPT)
    assert rows["scan.jpg"]["exported_file"] == "other/0-uncategorized/2026-09-01-55-bbbbbbbb.jpg"
    assert zf.read(rows["scan.jpg"]["exported_file"]) == b"\xff\xd8jpeg"
    assert _stamp(doc_id) is not None


def test_an_escaping_stored_filename_is_delivered_as_its_original(authed_client, db_path, tmp_data_dir, tmp_path):
    """The containment check still refuses the path; the document is not lost."""
    secret = tmp_path / "secret.txt"
    secret.write_text("not a receipt")
    _add_doc(tmp_data_dir, name="evil.pdf", stored=str(secret), content=b"%PDF real\n")
    _, zf, rows = _export(authed_client, SEPT)
    arcname = rows["evil.pdf"]["exported_file"]
    assert zf.read(arcname) == b"%PDF real\n"
    assert all(b"not a receipt" != zf.read(n) for n in zf.namelist())


def test_a_filed_copy_that_vanishes_mid_export_falls_back_to_the_original(
    authed_client, db_path, tmp_data_dir, monkeypatch
):
    """A concurrent reprocess renames the filed copy between exists() and write()."""
    doc_id, _ = _add_doc(tmp_data_dir, name="race.pdf", content=b"%PDF original\n",
                         stored="2026-09-03-9-cccccccc.pdf", filed=True)
    real_write = zipfile.ZipFile.write

    def write(self, filename, arcname=None, *a, **kw):
        if str(filename).endswith("2026-09-03-9-cccccccc.pdf") and "filed" in str(filename):
            raise FileNotFoundError(filename)
        return real_write(self, filename, arcname, *a, **kw)

    monkeypatch.setattr(zipfile.ZipFile, "write", write)
    resp, zf, rows = _export(authed_client, SEPT)
    assert zf.read(rows["race.pdf"]["exported_file"]) == b"%PDF original\n"
    assert _stamp(doc_id) is not None
    assert resp.headers["X-Export-Omitted"] == "0"


def test_a_document_with_no_file_at_all_is_reported_and_left_unstamped(authed_client, db_path, tmp_data_dir):
    doc_id, _ = _add_doc(tmp_data_dir, name="gone.pdf", original=False, stored="2026-09-03-1-dddddddd.pdf")
    resp, zf, rows = _export(authed_client, SEPT)
    assert rows["gone.pdf"]["exported_file"] == ""
    assert resp.headers["X-Export-Omitted"] == "1"
    assert _stamp(doc_id) is None, "an undelivered document must stay eligible for since_last_export"


def test_a_deleted_document_is_never_exported(authed_client, db_path, tmp_data_dir):
    doc_id, _ = _add_doc(tmp_data_dir, name="deleted.pdf", deleted=1, stored="2026-09-03-2-eeeeeeee.pdf", filed=True)
    for body in (SEPT, {"preset": "since_last_export"}):
        _, _, rows = _export(authed_client, body)
        assert "deleted.pdf" not in rows
    assert _stamp(doc_id) is None


def test_an_explicit_status_filter_still_narrows_the_export(authed_client, db_path, tmp_data_dir):
    _add_doc(tmp_data_dir, name="ok.pdf", stored="2026-09-03-3-ffffffff.pdf", filed=True)
    _add_doc(tmp_data_dir, name="bad.pdf", status="failed")
    _, _, rows = _export(authed_client, {**SEPT, "status": "failed"})
    assert set(rows) == {"bad.pdf"}


def test_two_originals_with_the_same_name_do_not_collide(authed_client, db_path, tmp_data_dir):
    _add_doc(tmp_data_dir, name="receipt.pdf", status="pending", content=b"one")
    with get_connection() as conn:
        conn.execute("UPDATE documents SET vendor_name = 'first' WHERE vendor_name = 'receipt.pdf'")
    _add_doc(tmp_data_dir, name="receipt.pdf", status="pending", content=b"two")
    _, zf, rows = _export(authed_client, SEPT)
    a, b = rows["first"]["exported_file"], rows["receipt.pdf"]["exported_file"]
    assert a != b
    assert {zf.read(a), zf.read(b)} == {b"one", b"two"}


@pytest.mark.parametrize("receipt_date", ["", "2026/01/15", "unknown"])
def test_a_malformed_issue_date_falls_under_the_upload_date(authed_client, db_path, tmp_data_dir, receipt_date):
    """receipt_date is model output stored verbatim: not NULL is not a date."""
    _add_doc(tmp_data_dir, name="odd.pdf", receipt_date=receipt_date)
    _, _, rows = _export(authed_client, {"preset": "month", "month": "2026-09", "date_basis": "receipt"})
    assert "odd.pdf" in rows
    _, _, rows = _export(authed_client, {"preset": "month", "month": "2026-01", "date_basis": "receipt"})
    assert "odd.pdf" not in rows


def test_a_valid_issue_date_still_decides_the_issue_date_period(authed_client, db_path, tmp_data_dir):
    _add_doc(tmp_data_dir, name="dated.pdf", receipt_date="2026-01-15")
    _, _, rows = _export(authed_client, {"preset": "month", "month": "2026-01", "date_basis": "receipt"})
    assert "dated.pdf" in rows
    _, _, rows = _export(authed_client, {"preset": "month", "month": "2026-09", "date_basis": "receipt"})
    assert "dated.pdf" not in rows


def test_an_undated_export_is_named_after_the_upload_dates(authed_client, db_path, tmp_data_dir):
    """The zip name's derived range uses the same period rule as the WHERE."""
    _add_doc(tmp_data_dir, name="nodate.pdf")
    resp = authed_client.post("/api/export", json={"date_basis": "receipt"})
    assert "2026_09_03" in resp.headers["Content-Disposition"]


def test_a_re_export_keeps_the_first_export_date(authed_client, db_path, tmp_data_dir):
    doc_id, _ = _add_doc(tmp_data_dir, name="twice.pdf", stored="2026-09-03-4-abababab.pdf", filed=True)
    _export(authed_client, SEPT)
    with get_connection() as conn:
        conn.execute("UPDATE documents SET last_exported_date = '2026-09-30T00:00:00Z' WHERE id = ?", (doc_id,))
    _export(authed_client, SEPT)
    assert _stamp(doc_id) == "2026-09-30T00:00:00Z"
