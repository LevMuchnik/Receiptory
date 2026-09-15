import json
import pytest
from unittest.mock import patch
from backend.processing.pipeline import process_document
from backend.database import get_connection
from backend.config import set_setting, init_settings
from backend.processing.extract import ExtractionResult, LLMExtractionResult

MOCK_EXTRACTION = ExtractionResult(receipt_date="2026-01-15", document_title="Tax Invoice", vendor_name="Office Depot", vendor_tax_id="515234567", vendor_receipt_id="INV-001", description="Office supplies", line_items=[{"description": "Paper", "quantity": 1, "unit_price": 25.0}], subtotal=25.0, tax_amount=4.25, total_amount=29.25, currency="ILS", payment_method="credit_card", payment_identifier="4580", language="he", additional_fields=[], raw_extracted_text="Office Depot Tax Invoice ...", document_type="expense_receipt", category_name="office_supplies", extraction_confidence=0.95)

MOCK_LLM_RESULT = LLMExtractionResult(extraction=MOCK_EXTRACTION, tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")

@pytest.fixture
def setup_db(db_path, tmp_data_dir):
    init_settings()
    set_setting("llm_api_key", "test-key")
    return str(tmp_data_dir)

@pytest.fixture
def pending_doc(setup_db, sample_pdf_path):
    import shutil
    from backend.storage import compute_file_hash, save_original
    file_hash = compute_file_hash(sample_pdf_path)
    save_original(sample_pdf_path, file_hash, ".pdf", setup_db)
    with get_connection() as conn:
        conn.execute("INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel) VALUES (?, ?, ?, 'pending', 'web_upload')", ("test.pdf", file_hash, 1234))
        doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return doc_id

@patch("backend.processing.pipeline.extract_document")
def test_process_document_success(mock_extract, pending_doc, setup_db):
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "processed"
    assert doc["vendor_name"] == "Office Depot"
    assert doc["total_amount"] == 29.25
    assert doc["extraction_confidence"] == 0.95
    assert doc["processing_model"] == "gemini/gemini-3-flash-preview"
    assert doc["processing_tokens_in"] == 1000
    assert doc["stored_filename"] is not None

@patch("backend.processing.pipeline.extract_document")
def test_process_document_low_confidence(mock_extract, pending_doc, setup_db):
    low_conf = LLMExtractionResult(extraction=ExtractionResult(**{**MOCK_EXTRACTION.__dict__, "extraction_confidence": 0.3}), tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")
    mock_extract.return_value = low_conf
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "needs_review"

@patch("backend.processing.pipeline.extract_document")
def test_process_document_failure(mock_extract, pending_doc, setup_db):
    mock_extract.side_effect = Exception("LLM timeout")
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status, processing_error, processing_attempts FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "failed"
    assert "LLM timeout" in doc["processing_error"]
    assert doc["processing_attempts"] == 1

@patch("backend.processing.pipeline.extract_document")
def test_process_document_type_override(mock_extract, pending_doc, setup_db):
    set_setting("business_tax_ids", ["515234567"])
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT document_type FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["document_type"] == "issued_invoice"


@patch("backend.processing.pipeline.extract_document")
def test_process_document_missing_confidence_needs_review(mock_extract, pending_doc, setup_db):
    # Missing confidence is not full confidence — route to a human.
    no_conf = LLMExtractionResult(extraction=ExtractionResult(**{**MOCK_EXTRACTION.__dict__, "extraction_confidence": None}), tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")
    mock_extract.return_value = no_conf
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "needs_review"


@patch("backend.processing.pipeline.extract_document")
def test_process_document_flags_a_total_that_disagrees_with_subtotal_plus_tax(mock_extract, pending_doc, setup_db):
    # Doc #314 (2026-09-12): the Naya receipt filed at 824.00 -- the card charge
    # including an 88.00 tip -- while reporting subtotal 623.73 and tax 112.27,
    # which add to 736.00. Confidence was 0.98, so nothing stopped it. The same
    # receipt scanned twice more came in at 736.00.
    mismatched = LLMExtractionResult(
        extraction=ExtractionResult(**{**MOCK_EXTRACTION.__dict__, "subtotal": 623.73, "tax_amount": 112.27, "total_amount": 824.00, "extraction_confidence": 0.98}),
        tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")
    mock_extract.return_value = mismatched
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status, review_reason, total_amount FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "needs_review"
    # The number is filed as extracted; the flag is what makes it visible.
    assert doc["total_amount"] == 824.00
    # Structure, not membership: swapping the sum and the stated total in the
    # f-string, or dropping the signed gap and the actionable half, would keep a
    # membership-only assertion green.
    reason = doc["review_reason"]
    assert "subtotal 623.73 + tax 112.27 = 736.00" in reason
    assert "total reads 824.00" in reason
    assert "+88.00" in reason
    assert "tip" in reason.lower()


@patch("backend.processing.pipeline.extract_document")
def test_process_document_joins_both_review_reasons(mock_extract, pending_doc, setup_db):
    # Both gates can fire on one document, and the concatenation branch was
    # otherwise dead in test: the mismatch case runs at 0.98 confidence and the
    # low-confidence case uses consistent totals.
    both = LLMExtractionResult(
        extraction=ExtractionResult(**{**MOCK_EXTRACTION.__dict__, "subtotal": 623.73, "tax_amount": 112.27, "total_amount": 824.00, "extraction_confidence": 0.10}),
        tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")
    mock_extract.return_value = both
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        reason = conn.execute("SELECT review_reason FROM documents WHERE id = ?", (pending_doc,)).fetchone()["review_reason"]
    assert "confidence" in reason.lower()
    assert "Totals disagree" in reason


@patch("backend.processing.pipeline.extract_document")
def test_process_document_leaves_consistent_totals_alone(mock_extract, pending_doc, setup_db):
    consistent = LLMExtractionResult(
        extraction=ExtractionResult(**{**MOCK_EXTRACTION.__dict__, "subtotal": 623.73, "tax_amount": 112.27, "total_amount": 736.00, "extraction_confidence": 0.98}),
        tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")
    mock_extract.return_value = consistent
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status, review_reason FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "processed"
    assert doc["review_reason"] is None


@patch("backend.processing.pipeline.extract_document")
def test_process_document_records_why_a_low_confidence_doc_needs_review(mock_extract, pending_doc, setup_db):
    # The reason field serves BOTH gates; a low-confidence document was equally
    # unexplained before it existed.
    low = LLMExtractionResult(
        extraction=ExtractionResult(**{**MOCK_EXTRACTION.__dict__, "extraction_confidence": 0.10}),
        tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")
    mock_extract.return_value = low
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status, review_reason FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "needs_review"
    assert "confidence" in doc["review_reason"].lower()


@patch("backend.processing.pipeline.extract_document")
def test_process_document_threads_json_mode_setting(mock_extract, pending_doc, setup_db):
    set_setting("llm_json_mode", False)
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    assert mock_extract.call_args.kwargs["json_mode"] is False


@patch("backend.processing.pipeline.extract_document")
def test_process_document_threads_parse_retries_setting(mock_extract, pending_doc, setup_db):
    # Issue #12: the pipeline must forward llm_parse_retries into extract_document.
    # A future edit dropping the kwarg would silently revert to 0 retries — this
    # one-line assertion catches that wiring regression.
    set_setting("llm_parse_retries", 3)
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    assert mock_extract.call_args.kwargs["parse_retries"] == 3


@patch("backend.processing.pipeline.extract_document")
def test_process_document_threads_reasoning_effort_setting(mock_extract, pending_doc, setup_db):
    # Issue #13: the pipeline must forward llm_reasoning_effort into
    # extract_document. Dropping the kwarg would silently revert to "none".
    set_setting("llm_reasoning_effort", "high")
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    assert mock_extract.call_args.kwargs["reasoning_effort"] == "high"


def test_estimate_cost_uses_registry():
    from backend.processing.pipeline import estimate_cost
    # gpt-4o is $2.50 / $10.00 per 1M in litellm's registry.
    assert estimate_cost("gpt-4o", 1000, 2000) == pytest.approx((1000 * 2.50 + 2000 * 10.00) / 1_000_000)


def test_estimate_cost_resolves_provider_prefixed_model():
    from backend.processing.pipeline import estimate_cost
    # litellm resolves the provider-prefixed id to a real, non-zero price.
    assert estimate_cost("gemini/gemini-3-flash-preview", 1000, 2000) > 0


def test_estimate_cost_unknown_model_falls_back():
    from backend.processing.pipeline import estimate_cost
    # Unmappable model -> litellm.cost_per_token raises -> generic $1 / $3 per 1M,
    # matching the old default tuple. Guards the wrong-price collision that a naive
    # prefix strip caused (azure/* landing on a cheaper bare registry row).
    assert estimate_cost("totally/unknown-model", 1000, 2000) == pytest.approx((1000 * 1.0 + 2000 * 3.0) / 1_000_000)


def test_estimate_cost_falls_back_when_resolver_raises(monkeypatch):
    import litellm
    from backend.processing import pipeline
    # Any resolver failure (unmapped model, partial-cost row, litellm internals)
    # must fall back to the generic estimate rather than crash the pipeline.
    def boom(*a, **k):
        raise Exception("This model isn't mapped yet")
    monkeypatch.setattr(litellm, "cost_per_token", boom)
    assert pipeline.estimate_cost("some/model", 1000, 2000) == pytest.approx((1000 * 1.0 + 2000 * 3.0) / 1_000_000)


@patch("backend.processing.extract.litellm_completion")
def test_process_document_with_trailing_junk_response(mock_completion, pending_doc, setup_db):
    # End-to-end regression for issue #10 (docs #70/#215): the LLM emits a
    # complete JSON object then keeps generating. The document must end up
    # 'processed', not 'failed' — this patches the raw LLM call so the real
    # parse ladder runs inside the pipeline.
    from tests.conftest import SAMPLE_LLM_RESPONSE, mock_llm_response
    mock_completion.return_value = mock_llm_response(content=SAMPLE_LLM_RESPONSE + "\n\nAs requested, all fields were extracted from the document image.")
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status, vendor_name, total_amount, processing_error FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "processed"
    assert doc["vendor_name"] == "Office Depot"
    assert doc["total_amount"] == 354.51
    assert doc["processing_error"] is None


# ---------------------------------------------------------------------------
# Superseded filed/ copies -- issue #54
#
# generate_stored_filename builds the name from receipt_date and
# vendor_receipt_id, both verbatim LLM output, so a reprocess that reads the
# document differently writes a NEW name. Measured on the live install before
# this landed: 315 files in filed/ against 310 referenced names, and in every
# case it was the YEAR that moved, not the receipt id.
#
# Note the harness: every other test here sets mock_extract.return_value, one
# fixed result. A rename needs side_effect=[first, second]; return_value twice
# exercises the no-change branch only and would pass with the unlink deleted.
# ---------------------------------------------------------------------------

import os as _os

import backend.processing.pipeline as pipeline_mod

from dataclasses import replace as _replace

# Only the YEAR changes, which is what the live install actually showed: the
# receipt ids were stable and the year moved (issue #63).
SECOND_EXTRACTION = _replace(MOCK_EXTRACTION, receipt_date="2024-01-15")
SECOND_LLM_RESULT = _replace(MOCK_LLM_RESULT, extraction=SECOND_EXTRACTION)


def _filed(data_dir):
    d = _os.path.join(data_dir, "storage", "filed")
    return sorted(_os.listdir(d)) if _os.path.isdir(d) else []


def _stored_name(doc_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT stored_filename FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()["stored_filename"]


@patch("backend.processing.pipeline.extract_document")
def test_a_reprocess_that_renames_removes_the_superseded_copy(mock_extract, pending_doc, setup_db):
    mock_extract.side_effect = [MOCK_LLM_RESULT, SECOND_LLM_RESULT]

    process_document(pending_doc, setup_db)
    first = _stored_name(pending_doc)
    assert _filed(setup_db) == [first]

    process_document(pending_doc, setup_db)
    second = _stored_name(pending_doc)

    assert second != first, "the fixture did not actually produce a rename"
    assert _filed(setup_db) == [second], "the old filed/ copy was orphaned"


@patch("backend.processing.pipeline.extract_document")
def test_a_reprocess_that_changes_nothing_keeps_its_file(mock_extract, pending_doc, setup_db):
    """The guard that stops the unlink deleting the file it just wrote.

    Drop `previous == current` and this test is what fails: save_filed rewrites
    the same name, then the cleanup removes it, and the document ends up with a
    row pointing at nothing.
    """
    mock_extract.return_value = MOCK_LLM_RESULT

    process_document(pending_doc, setup_db)
    process_document(pending_doc, setup_db)

    name = _stored_name(pending_doc)
    assert _filed(setup_db) == [name]
    assert _os.path.exists(_os.path.join(setup_db, "storage", "filed", name))


@patch("backend.processing.pipeline.extract_document")
def test_the_first_processing_removes_nothing(mock_extract, pending_doc, setup_db):
    """previous is NULL on a first pass. Nothing to supersede."""
    mock_extract.return_value = MOCK_LLM_RESULT

    with patch("backend.processing.pipeline.remove_filed") as rm:
        process_document(pending_doc, setup_db)

    rm.assert_not_called()


@patch("backend.processing.pipeline.extract_document")
def test_the_row_already_names_the_new_file_when_the_old_one_is_unlinked(
    mock_extract, pending_doc, setup_db
):
    """The ordering IS the crash-safety design, and nothing else would notice it
    being reversed.

        save_filed(new) -> UPDATE stored_filename = new -> unlink(old)

    Every crash point in that sequence leaves a harmless spare file. Swap the
    last two and a crash leaves a row naming a file that is gone, which
    verify_backup correctly reports as damage. Every other test in this file
    passes with the statements swapped.
    """
    mock_extract.side_effect = [MOCK_LLM_RESULT, SECOND_LLM_RESULT]
    process_document(pending_doc, setup_db)
    first = _stored_name(pending_doc)

    observed = {}
    real_remove = pipeline_mod.remove_filed

    def spy(name, data_dir):
        observed["row_said"] = _stored_name(pending_doc)
        observed["removing"] = name
        return real_remove(name, data_dir)

    with patch("backend.processing.pipeline.remove_filed", spy):
        process_document(pending_doc, setup_db)

    second = _stored_name(pending_doc)
    assert observed["removing"] == first
    assert observed["row_said"] == second, (
        "the old file was unlinked BEFORE the row was updated -- a crash there "
        "leaves a document whose filed copy is gone"
    )


@patch("backend.processing.pipeline.extract_document")
def test_a_file_another_row_still_references_is_kept(mock_extract, pending_doc, setup_db):
    """Needs an 8-hex-prefix collision that also matches on date and receipt id
    to happen for real. It is here because the operation is an irreversible
    delete, so the safety should not rest on a birthday-bound argument that a
    future change to the filename scheme would silently invalidate."""
    mock_extract.side_effect = [MOCK_LLM_RESULT, SECOND_LLM_RESULT]
    process_document(pending_doc, setup_db)
    first = _stored_name(pending_doc)

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, "
            "submission_channel, stored_filename) VALUES (?, ?, ?, 'processed', 'web_upload', ?)",
            ("other.pdf", "f" * 64, 10, first),
        )

    process_document(pending_doc, setup_db)

    assert first in _filed(setup_db), "deleted a file another document still points at"


@patch("backend.processing.pipeline.extract_document")
def test_a_previous_name_that_escapes_filed_is_never_unlinked(mock_extract, pending_doc, setup_db, tmp_path):
    """A restored database is untrusted input, and this path is an os.unlink
    running as root. The document must still complete."""
    mock_extract.return_value = MOCK_LLM_RESULT
    victim = tmp_path / "precious"
    victim.write_text("do not delete me")

    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET stored_filename = ? WHERE id = ?", (str(victim), pending_doc)
        )

    process_document(pending_doc, setup_db)

    assert victim.exists(), "an absolute stored_filename reached os.unlink"
    with get_connection() as conn:
        assert conn.execute(
            "SELECT status FROM documents WHERE id = ?", (pending_doc,)
        ).fetchone()["status"] == "processed"


@patch("backend.processing.pipeline.extract_document")
def test_an_unlink_that_fails_does_not_fail_the_document(mock_extract, pending_doc, setup_db):
    """Filing has already succeeded and the row is already correct, so a stale
    copy that cannot be removed is untidy, not a failed document."""
    mock_extract.side_effect = [MOCK_LLM_RESULT, SECOND_LLM_RESULT]
    process_document(pending_doc, setup_db)

    def boom(name, data_dir):
        raise OSError("read-only file system")

    with patch("backend.processing.pipeline.remove_filed", boom):
        process_document(pending_doc, setup_db)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, stored_filename FROM documents WHERE id = ?", (pending_doc,)
        ).fetchone()
    assert row["status"] == "processed"
    assert row["stored_filename"] is not None


@patch("backend.processing.pipeline.extract_document")
def test_a_raise_between_save_filed_and_the_update_orphans_the_new_file(
    mock_extract, pending_doc, setup_db
):
    """CHARACTERISATION of the accepted window, gstack-shortcut(dec-333b80f5).

    Not desired behaviour -- accepted behaviour, pinned so a change in either
    direction is visible. save_filed writes the new name ~18 lines above the
    UPDATE that records it. A raise in between is caught by process_document,
    which sets status='failed' without touching stored_filename, so the file
    just written is orphaned permanently and the cleanup never runs (it only
    fires after a COMMITTED rename).

    If this test starts failing because the file is gone, the window was closed:
    delete the shortcut marker, the ledger entry and this test.
    """
    mock_extract.return_value = MOCK_LLM_RESULT

    with patch("backend.processing.pipeline.estimate_cost", side_effect=RuntimeError("boom")):
        process_document(pending_doc, setup_db)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, stored_filename FROM documents WHERE id = ?", (pending_doc,)
        ).fetchone()

    assert row["status"] == "failed"
    assert row["stored_filename"] is None, "the UPDATE committed after all"
    assert _filed(setup_db) != [], (
        "the accepted orphan window has closed -- update dec-333b80f5 and delete this test"
    )


@patch("backend.processing.pipeline.extract_document")
def test_a_database_error_during_cleanup_does_not_fail_a_processed_document(
    mock_extract, pending_doc, setup_db
):
    """The cleanup runs AFTER the UPDATE commits, so anything escaping it would
    reach process_document's `except Exception`, rewrite status='failed' for a
    document that actually succeeded, send a failure notification, and get the
    document requeued -- and a reprocess is what creates orphans in the first
    place. The shared-name SELECT can raise sqlite3.Error ('database is locked')
    under WAL contention with the backup thread this PR serialises against, so
    catching only OSError around the unlink was not enough.
    """
    import sqlite3

    mock_extract.side_effect = [MOCK_LLM_RESULT, SECOND_LLM_RESULT]
    process_document(pending_doc, setup_db)

    # Raise from inside the cleanup specifically. Counting get_connection calls
    # does not work: _run_pipeline opens several before the UPDATE (the category
    # lookups), so an nth-call trap fires during processing instead and the
    # document legitimately fails -- which is what the first version of this
    # test actually measured.
    def boom(doc_id, previous, data_dir):
        raise sqlite3.OperationalError("database is locked")

    with patch("backend.processing.pipeline._remove_superseded", boom):
        process_document(pending_doc, setup_db)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, stored_filename FROM documents WHERE id = ?", (pending_doc,)
        ).fetchone()
    assert row["status"] == "processed", (
        "a locked database during cleanup marked a successfully processed "
        "document as failed"
    )
    assert row["stored_filename"] is not None
