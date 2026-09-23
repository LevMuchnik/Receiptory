"""Tests for scripts/compare_json_mode.py: the A/B harness and the merge gate.

The harness decides whether an extraction change ships (issue #67), so the parts
that do not need an LLM are pinned here: argument handling, arm construction,
and the gate arithmetic in compare_results. scripts/ is not in the Docker image;
this runs from a checkout, like the script itself.
"""

import argparse
import json
import os
import sys
from unittest.mock import patch

import pytest

from backend.processing.extract import ExtractionResult, LLMExtractionResult, ParseFailure
from scripts.compare_json_mode import (
    DRIFT_MARGIN, REPO_ROOT, build_arms, comparability_problems, compare_results, format_report,
    load_verified_dates, main, parse_ids, run_arm, stratify, unsafe_save_path, _write_save,
)


def _ok(**fields) -> dict:
    base = {"receipt_date": "2026-09-19", "vendor_name": "Shop", "total_amount": 30.0,
            "raw_extracted_text": "text", "parse_salvaged": False}
    return {"fields": {**base, **fields}}


def _failed(truncated: bool = False) -> dict:
    return {"error": "LLM response truncated at max_tokens=16384" if truncated else "Failed to parse", "truncated": truncated}


def _doc(*runs, submitted="2026-09-19T10:00:00Z", stored="2026-09-19") -> dict:
    return {"submission_date": submitted, "stored_receipt_date": stored, "runs": list(runs)}


def _file(docs: dict) -> dict:
    return {"meta": {}, "docs": docs}


def _same_everywhere(n: int) -> dict:
    return _file({str(i): _doc(_ok(), _ok()) for i in range(1, n + 1)})


# --- parse_ids / build_arms / argument validation ---

def test_parse_ids_accepts_spaces_and_a_trailing_comma():
    assert parse_ids("12, 40,321,") == [12, 40, 321]


@pytest.mark.parametrize("text", ["", " , ", "12,abc", "-3", "1.5"])
def test_parse_ids_rejects_anything_that_is_not_an_id(text):
    with pytest.raises(ValueError):
        parse_ids(text)


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"model_a": None, "model_b": None, "control": False, **kw})


def test_control_runs_the_production_configuration_twice():
    arms, _ = build_arms(_args(control=True), json_mode=True)
    assert arms == [("run 1", {"json_mode": True}), ("run 2", {"json_mode": True})]


def test_default_arms_are_json_mode_off_then_on():
    arms, _ = build_arms(_args(), json_mode=True)
    assert arms == [("OFF", {"json_mode": False}), ("ON", {"json_mode": True})]


def test_a_model_against_itself_gets_distinct_labels():
    arms, _ = build_arms(_args(model_a="m", model_b="m"), json_mode=True)
    assert [label for label, _ in arms] == ["A m", "B m"]


@pytest.mark.parametrize("argv, message", [
    (["--model-a", "x"], "must be given together"),
    (["--control", "--model-a", "x", "--model-b", "y"], "cannot be combined"),
    (["--ids", "1,zz"], "--ids:"),
    (["--compare", "a.json", "b.json", "--control"], "takes no run options"),
])
def test_bad_argument_combinations_abort_before_touching_anything(argv, message, monkeypatch, tmp_path, capsys):
    # The empty --data-dir ALSO exits 2 (no database), so the exit code alone
    # cannot show that the argument guard fired: its own message must.
    monkeypatch.setattr(sys, "argv", ["compare", "--data-dir", str(tmp_path), *argv])
    assert main() == 2
    out = capsys.readouterr().out
    assert message in out and "No database" not in out
    assert not (tmp_path / "receiptory.db").exists()


# --- compare_results: the merge gate ---

def test_identical_trees_pass():
    report = compare_results(_same_everywhere(5), _same_everywhere(5))
    assert report["blocks"] == []
    assert "VERDICT: PASS" in format_report(report)


def test_disagreement_at_the_noise_floor_does_not_block():
    """Base disagrees with itself on vendor_name in 2 documents, so 3 across
    trees is one over the floor, below the margin."""
    base = _same_everywhere(6)
    for i in ("1", "2"):
        base["docs"][i]["runs"][1] = _ok(vendor_name="Shop Ltd")
    cand = _same_everywhere(6)
    for i in ("1", "2", "3"):
        cand["docs"][i] = _doc(_ok(vendor_name="SHOP"), _ok(vendor_name="SHOP"))
    row = compare_results(base, cand)["fields"]["vendor_name"]
    assert (row["w_base"], row["w_cand"], row["x"]) == (2, 0, 3)
    assert row["excess"] == 1 < DRIFT_MARGIN
    assert compare_results(base, cand)["blocks"] == []


def test_drift_beyond_the_noise_floor_blocks():
    """tax_amount stable within each tree but different across them: that is
    the systematic shift the gate exists to catch."""
    base = _file({str(i): _doc(_ok(tax_amount=4.36), _ok(tax_amount=4.36)) for i in range(1, 6)})
    cand = _file({str(i): _doc(_ok(tax_amount=None), _ok(tax_amount=None)) for i in range(1, 6)})
    report = compare_results(base, cand)
    assert report["fields"]["tax_amount"]["excess"] == 5
    assert any(b.startswith("tax_amount") for b in report["blocks"])
    assert "VERDICT: BLOCK" in format_report(report)


def test_ungated_fields_never_block():
    base = _file({str(i): _doc(_ok(raw_extracted_text="a", parse_salvaged=True), _ok(raw_extracted_text="b", parse_salvaged=True)) for i in range(1, 6)})
    cand = _file({str(i): _doc(_ok(raw_extracted_text="c"), _ok(raw_extracted_text="d")) for i in range(1, 6)})
    report = compare_results(base, cand)
    assert "raw_extracted_text" not in report["fields"]
    assert "parse_salvaged" not in report["fields"]
    assert report["salvaged"] == {"base": 10, "cand": 0}
    assert report["blocks"] == []


def test_only_documents_in_both_files_are_compared():
    base = _same_everywhere(3)
    cand = _same_everywhere(2)
    cand["docs"]["9"] = _doc(_ok(), _ok())
    report = compare_results(base, cand)
    assert report["docs"] == 2
    assert report["only_base"] == ["3"]
    assert report["only_cand"] == ["9"]


def test_a_failed_run_is_counted_not_dropped_and_more_failures_block():
    """A failure is exactly what a JSON-handling change is about. It must count
    against the tree it happened in and keep the document out of the field
    comparison, not vanish from both sides."""
    base = _same_everywhere(4)
    cand = _same_everywhere(4)
    cand["docs"]["2"]["runs"][1] = _failed()
    report = compare_results(base, cand)
    assert report["failures"] == {"base": 0, "cand": 1}
    assert report["compared"] == 3
    assert any(b.startswith("failures") for b in report["blocks"])


def test_fewer_failures_on_the_candidate_is_not_a_block():
    base = _same_everywhere(4)
    base["docs"]["1"]["runs"][0] = _failed()
    report = compare_results(base, _same_everywhere(4))
    assert report["failures"] == {"base": 1, "cand": 0}
    assert report["blocks"] == []


def test_more_truncations_block_even_at_equal_failures():
    base = _same_everywhere(3)
    base["docs"]["1"]["runs"][0] = _failed(truncated=False)
    cand = _same_everywhere(3)
    cand["docs"]["1"]["runs"][0] = _failed(truncated=True)
    report = compare_results(base, cand)
    assert report["failures"] == {"base": 1, "cand": 1}
    assert report["truncations"] == {"base": 0, "cand": 1}
    assert [b.split(":")[0] for b in report["blocks"]] == ["truncations"]


def _misdated_file(read_as: str) -> dict:
    # Stored year 2019 on a 2026 upload: the document #63 is about.
    return _file({"321": _doc(_ok(receipt_date=read_as), _ok(receipt_date=read_as), stored="2019-09-26")})


def test_verified_dates_score_year_and_exact_date_and_can_block():
    verified = {"321": "2026-09-19"}
    good = compare_results(_misdated_file("2019-09-26"), _misdated_file("2026-09-19"), verified)
    assert good["dates"] == {"basis": "verified", "docs": 1, "of": 2, "base": {"year": 0, "exact": 0}, "cand": {"year": 2, "exact": 2}}
    assert good["blocks"] == []
    worse = compare_results(_misdated_file("2026-09-19"), _misdated_file("2019-09-26"), verified)
    assert {b.split(":")[0] for b in worse["blocks"]} == {"receipt_date year", "receipt_date exact"}


def test_a_right_year_with_the_wrong_day_counts_for_year_but_not_exact():
    """The DD/MM swap can keep the year and move the day."""
    report = compare_results(_misdated_file("2026-09-19"), _misdated_file("2026-09-26"), {"321": "2026-09-19"})
    assert report["dates"]["cand"] == {"year": 2, "exact": 0}
    assert [b.split(":")[0] for b in report["blocks"]] == ["receipt_date exact"]


def test_a_backlog_receipt_read_correctly_is_not_penalised_when_verified():
    """The upload-year fallback would score this correct 2025 read as WRONG."""
    backlog = lambda read_as: _file({"7": _doc(_ok(receipt_date=read_as), _ok(receipt_date=read_as), submitted="2026-01-05T09:00:00Z", stored="2025-12-28")})
    report = compare_results(backlog("2026-12-28"), backlog("2025-12-28"), {"7": "2025-12-28"})
    assert report["dates"]["cand"]["year"] == 2 > report["dates"]["base"]["year"] == 0
    assert report["blocks"] == []


def test_without_verified_dates_the_upload_year_check_reports_but_never_blocks():
    report = compare_results(_misdated_file("2026-09-19"), _misdated_file("2019-09-26"))
    assert report["dates"]["basis"] == "upload-year"
    assert (report["dates"]["base"]["year"], report["dates"]["cand"]["year"]) == (2, 0)
    assert report["blocks"] == []
    assert "informational" in format_report(report)


def test_an_undated_or_correctly_dated_document_is_not_misdated():
    undated = _file({"1": _doc(_ok(receipt_date=None), _ok(receipt_date=None), stored=None)})
    assert compare_results(undated, undated)["dates"]["docs"] == 0
    assert compare_results(_same_everywhere(3), _same_everywhere(3))["dates"]["docs"] == 0


def test_load_verified_dates_validates_every_entry(tmp_path):
    good = tmp_path / "good.json"; good.write_text(json.dumps({"321": "2026-09-19"}))
    assert load_verified_dates(str(good)) == {"321": "2026-09-19"}
    for bad in ({}, {"abc": "2026-09-19"}, {"321": "19/09/26"}, {"321": 20260919}, ["2026-09-19"]):
        path = tmp_path / "bad.json"; path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            load_verified_dates(str(path))


def test_excess_of_exactly_the_margin_blocks():
    """The boundary: two documents over a zero noise floor is a block, not a pass."""
    base = _same_everywhere(5)
    cand = _same_everywhere(5)
    for i in ("1", "2"):
        cand["docs"][i] = _doc(_ok(total_amount=31.0), _ok(total_amount=31.0))
    assert compare_results(base, cand)["fields"]["total_amount"]["excess"] == DRIFT_MARGIN == 2
    assert any(b.startswith("total_amount") for b in compare_results(base, cand)["blocks"])


# --- run_arm: a failure is an outcome, not a skipped document ---


def test_run_arm_returns_every_extraction_field_and_the_tokens():
    result = LLMExtractionResult(extraction=ExtractionResult(receipt_date="2026-09-19", category_name="Travel"), tokens_in=7, tokens_out=3, model="m")
    with patch("scripts.compare_json_mode.extract_document", return_value=result):
        outcome, tokens_in, tokens_out = run_arm([b"png"], {})
    assert outcome["fields"]["receipt_date"] == "2026-09-19"
    assert outcome["fields"]["category_name"] == "Travel"
    assert "raw_extracted_text" in outcome["fields"]  # all fields, not just COMPARE_FIELDS
    assert (tokens_in, tokens_out) == (7, 3)


@pytest.mark.parametrize("error, truncated", [
    (ValueError("LLM response truncated at max_tokens=16384 — increase the llm_max_tokens setting"), True),
    (ParseFailure("Failed to parse LLM response as JSON: Expecting ',' delimiter"), False),
])
def test_run_arm_records_a_failure_instead_of_raising(error, truncated):
    """The old harness caught per DOCUMENT and skipped it on both sides, so a
    document that failed to parse vanished from the comparison entirely."""
    with patch("scripts.compare_json_mode.extract_document", side_effect=error):
        outcome, tokens_in, tokens_out = run_arm([b"png"], {})
    assert outcome == {"error": str(error), "truncated": truncated}
    assert (tokens_in, tokens_out) == (0, 0)


# --- stratify: the default sample must see every document type ---


def test_stratify_interleaves_document_types():
    """Alphabetically, 'expense_receipt' vendors all sort before the first
    'issued_invoice' bucket, so the old order filled a small sample with
    expense receipts only."""
    rows = [{"id": i, "document_type": "expense_receipt", "vendor_name": f"v{i:02d}"} for i in range(20)]
    rows += [{"id": 100 + i, "document_type": "issued_invoice", "vendor_name": f"client{i}"} for i in range(3)]
    rows += [{"id": 200, "document_type": "other_document", "vendor_name": "bank"}]
    sample = stratify(rows, 6)
    assert [r["document_type"] for r in sample[:3]] == ["expense_receipt", "issued_invoice", "other_document"]
    assert {r["document_type"] for r in sample} == {"expense_receipt", "issued_invoice", "other_document"}


def test_stratify_takes_every_row_when_the_limit_allows_and_never_repeats():
    rows = [{"id": i, "document_type": t, "vendor_name": v} for i, (t, v) in enumerate(
        [("expense_receipt", "a"), ("expense_receipt", "a"), ("issued_invoice", "c"), (None, None)])]
    sample = stratify(rows, 10)
    assert sorted(r["id"] for r in sample) == [0, 1, 2, 3]


# --- --compare refuses files it cannot interpret, and its exit code is the gate ---

def _meta(model="gemini/x", temperature=1.0, arms=None) -> dict:
    return {"model": model, "temperature": temperature,
            "arms": arms if arms is not None else [{"label": "run 1", "json_mode": True}, {"label": "run 2", "json_mode": True}]}


def test_two_control_runs_of_the_same_configuration_are_comparable():
    assert comparability_problems({"meta": _meta()}, {"meta": _meta()}) == []


@pytest.mark.parametrize("base_meta, cand_meta, fragment", [
    (_meta(arms=[{"label": "OFF", "json_mode": False}, {"label": "ON", "json_mode": True}]), _meta(), "not a --control run"),
    (_meta(arms=[{"label": "A m", "model": "a"}, {"label": "B m", "model": "b"}]), _meta(), "not a --control run"),
    (_meta(model="gemini/x"), _meta(model="gemini/y"), "model differs"),
    (_meta(temperature=1.0), _meta(temperature=0.0), "temperature differs"),
    (_meta(), _meta(arms=[{"label": "run 1", "json_mode": False}, {"label": "run 2", "json_mode": False}]), "arm settings differ"),
    ({}, _meta(), "not a --control run"),
])
def test_files_that_cannot_be_compared_are_refused(base_meta, cand_meta, fragment):
    """An OFF/ON or model A/B file read as a noise floor would hide real drift
    inside inflated noise, and the gate would print PASS."""
    problems = comparability_problems({"meta": base_meta}, {"meta": cand_meta})
    assert any(fragment in p for p in problems), problems


def _write(tmp_path, name, docs_file, meta=None) -> str:
    path = tmp_path / name
    path.write_text(json.dumps({**docs_file, "meta": meta or _meta()}))
    return str(path)


def test_compare_exit_code_is_the_gate(monkeypatch, tmp_path):
    base = _file({str(i): _doc(_ok(tax_amount=4.36), _ok(tax_amount=4.36)) for i in range(1, 6)})
    drift = _file({str(i): _doc(_ok(tax_amount=None), _ok(tax_amount=None)) for i in range(1, 6)})
    b, c, same = _write(tmp_path, "b.json", base), _write(tmp_path, "c.json", drift), _write(tmp_path, "p.json", base)
    monkeypatch.setattr(sys, "argv", ["compare", "--compare", b, c])
    assert main() == 1
    monkeypatch.setattr(sys, "argv", ["compare", "--compare", b, same])
    assert main() == 0


def test_compare_refuses_incomparable_files_with_exit_2(monkeypatch, tmp_path, capsys):
    base = _write(tmp_path, "b.json", _same_everywhere(3))
    offon = _write(tmp_path, "c.json", _same_everywhere(3), _meta(arms=[{"label": "OFF", "json_mode": False}, {"label": "ON", "json_mode": True}]))
    monkeypatch.setattr(sys, "argv", ["compare", "--compare", base, offon])
    assert main() == 2
    assert "no verdict" in capsys.readouterr().out


def test_verified_dates_is_only_a_compare_option(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["compare", "--data-dir", str(tmp_path), "--verified-dates", "d.json"])
    assert main() == 2
    out = capsys.readouterr().out
    assert "is a --compare option" in out and "No database" not in out


# --- --save: never into a file git could commit, and written as it goes ---

def test_a_save_path_outside_the_repository_is_allowed(tmp_path):
    assert unsafe_save_path(str(tmp_path / "base.compare.json")) is None


def _git_check_ignore(returncode):
    # Hermetic: the verdict comes from `git check-ignore`, whose exit code is
    # all the guard reads. The real .gitignore rule is pinned separately below.
    return patch("scripts.compare_json_mode.subprocess.run", return_value=type("R", (), {"returncode": returncode})())


def test_an_unignored_save_path_inside_the_repository_is_refused():
    # A result name git does NOT ignore (e.g. a .gitignore that lost the rule):
    # the suffix rule passes, so this is the git check doing the refusing.
    with _git_check_ignore(1):
        reason = unsafe_save_path(os.path.join(REPO_ROOT, "base.compare.json"))
    assert reason and "not gitignored" in reason


def test_an_ignored_save_path_inside_the_repository_is_allowed():
    with _git_check_ignore(0):
        assert unsafe_save_path(os.path.join(REPO_ROOT, "base.compare.json")) is None


def test_a_symlink_into_the_repository_is_still_checked(tmp_path):
    """abspath would see a path outside the repo and skip the git check."""
    link = tmp_path / "into-repo"
    link.symlink_to(REPO_ROOT)
    # A *.compare.json name, so the suffix rule passes and only the realpath
    # containment check stands between this path and the un-ignored tree.
    with _git_check_ignore(1):
        reason = unsafe_save_path(str(link / "leak.compare.json"))
    assert reason and "not gitignored" in reason


def test_gitignore_covers_compare_json_results():
    text = open(os.path.join(REPO_ROOT, ".gitignore")).read().splitlines()
    assert "*.compare.json" in text and "*.compare.json.tmp" in text


def test_main_refuses_an_unsafe_save_path_before_any_work(monkeypatch, tmp_path, capsys):
    # The empty --data-dir would ALSO exit 2 (no database), so the exit code
    # alone cannot tell the save-path guard fired; the message can.
    monkeypatch.setattr(sys, "argv", ["compare", "--data-dir", str(tmp_path), "--control", "--save", os.path.join(REPO_ROOT, "leak.json")])
    assert main() == 2
    out = capsys.readouterr().out
    assert "--save:" in out
    assert "No database" not in out
    assert not os.path.exists(os.path.join(REPO_ROOT, "leak.json"))


def test_write_save_replaces_the_file_whole(tmp_path):
    path = str(tmp_path / "run.compare.json")
    _write_save(path, {"model": "m"}, {"1": {"runs": []}})
    _write_save(path, {"model": "m"}, {"1": {"runs": []}, "2": {"runs": []}})
    assert set(json.loads(open(path).read())["docs"]) == {"1", "2"}
    assert not os.path.exists(path + ".tmp")


# --- the run path: database selection, the saved file, and the round trip into --compare ---

def _insert_doc(conn, doc_id, status="processed", is_deleted=0, receipt_date="2026-09-19", document_type="expense_receipt"):
    conn.execute(
        "INSERT INTO documents (id, original_filename, file_hash, file_size_bytes, status, is_deleted, receipt_date, document_type, vendor_name, submission_date) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'Shop', '2026-09-20T08:00:00Z')",
        (doc_id, f"doc{doc_id}.pdf", f"hash{doc_id}", status, is_deleted, receipt_date, document_type),
    )


def test_pick_ids_keeps_the_given_order_and_names_what_it_skipped(db_path):
    """Pinned ids are what make two trees see the same documents, so the order
    is the caller's, and a pending, deleted or missing id is reported, not
    silently dropped."""
    from backend.database import get_connection
    from scripts.compare_json_mode import pick_ids
    with get_connection() as conn:
        _insert_doc(conn, 1)
        _insert_doc(conn, 2, status="needs_review")
        _insert_doc(conn, 3, status="pending")
        _insert_doc(conn, 4, is_deleted=1)
        conn.commit()
    sample, unusable = pick_ids([2, 99, 1, 3, 4])
    assert [d["id"] for d in sample] == [2, 1]
    assert unusable == [99, 3, 4]
    # submission_date is what the upload-year date check reads from the save file.
    assert sample[0]["submission_date"] == "2026-09-20T08:00:00Z"


def test_pick_sample_reads_only_extracted_documents_with_their_upload_date(db_path):
    from backend.database import get_connection
    from scripts.compare_json_mode import pick_sample
    with get_connection() as conn:
        _insert_doc(conn, 1)
        _insert_doc(conn, 2, status="failed")
        _insert_doc(conn, 3, is_deleted=1)
        conn.commit()
    sample = pick_sample(10)
    assert [d["id"] for d in sample] == [1]
    assert sample[0]["submission_date"] == "2026-09-20T08:00:00Z"


def test_extraction_args_match_what_the_pipeline_passes(db_path, monkeypatch):
    """The API key follows the keyring resolver (the legacy llm_api_key column
    is empty since the named-keyring migration), and the parse-retry and
    reasoning settings go in: without them the harness measured a configuration
    production does not run."""
    from backend.config import init_settings, set_setting
    from scripts.compare_json_mode import extraction_args
    init_settings()
    set_setting("llm_parse_retries", 3)
    set_setting("llm_reasoning_effort", "low")
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "resolved-key")
    args = extraction_args()
    assert args["api_key"] == "resolved-key"
    assert (args["parse_retries"], args["reasoning_effort"]) == (3, "low")
    assert args["expense_categories"] and all(set(c) == {"name", "description"} for c in args["expense_categories"])


def _run_main(monkeypatch, argv, extract_side_effect):
    monkeypatch.setattr(sys, "argv", ["compare", *argv])
    with patch("scripts.compare_json_mode.resolve_pdf", return_value="/nonexistent/doc.pdf"), \
         patch("scripts.compare_json_mode.render_all_pages_to_memory", return_value=[b"png"]), \
         patch("scripts.compare_json_mode.extract_document", side_effect=extract_side_effect) as mock_extract:
        code = main()
    return code, mock_extract


def test_a_control_save_round_trips_into_compare(db_path, tmp_data_dir, tmp_path, monkeypatch, capsys):
    """The file main() writes must be the file --compare accepts: two identical
    arms in meta, both runs per document, a failure saved as an outcome, and
    the dates the date check reads."""
    from backend.config import init_settings
    from backend.database import get_connection
    init_settings()
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "k")
    with get_connection() as conn:
        _insert_doc(conn, 1, receipt_date="2019-09-26")
        _insert_doc(conn, 2)
        conn.commit()
    ok = LLMExtractionResult(extraction=ExtractionResult(receipt_date="2026-09-19", vendor_name="Shop"), tokens_in=5, tokens_out=2, model="m")
    save = str(tmp_path / "out" / "run.compare.json")
    os.makedirs(os.path.dirname(save))
    code, mock_extract = _run_main(
        monkeypatch,
        ["--data-dir", str(tmp_data_dir), "--control", "--ids", "2,1,77", "--temperature", "0", "--save", save],
        [ok, ok, ok, ParseFailure("Failed to parse LLM response as JSON")],
    )
    assert code == 0
    assert mock_extract.call_count == 4
    assert all(c.kwargs["temperature"] == 0.0 and c.kwargs["api_key"] == "k" for c in mock_extract.call_args_list)
    out = capsys.readouterr().out
    assert "skipped: [77]" in out and "[FAILED]" in out

    saved = json.loads(open(save).read())
    assert list(saved["docs"]) == ["2", "1"]
    assert saved["meta"]["temperature"] == 0.0
    assert saved["meta"]["arms"] == [{"label": "run 1", "json_mode": True}, {"label": "run 2", "json_mode": True}]
    assert saved["docs"]["1"]["runs"][1] == {"error": "Failed to parse LLM response as JSON", "truncated": False}
    assert saved["docs"]["1"]["stored_receipt_date"] == "2019-09-26"
    assert saved["docs"]["1"]["submission_date"] == "2026-09-20T08:00:00Z"
    assert not os.path.exists(save + ".tmp")

    assert comparability_problems(saved, saved) == []
    report = compare_results(saved, saved)
    assert report["failures"] == {"base": 1, "cand": 1} and report["compared"] == 1
    monkeypatch.setattr(sys, "argv", ["compare", "--compare", save, save])
    assert main() == 0


def test_main_aborts_before_any_llm_call_without_an_api_key(db_path, tmp_data_dir, monkeypatch, capsys):
    from backend.config import init_settings
    from backend.database import get_connection
    init_settings()
    with get_connection() as conn:
        _insert_doc(conn, 1)
        conn.commit()
    code, mock_extract = _run_main(monkeypatch, ["--data-dir", str(tmp_data_dir), "--control", "--ids", "1"], [])
    assert code == 2
    assert "No LLM API key" in capsys.readouterr().out
    mock_extract.assert_not_called()


def test_compare_aborts_on_an_unreadable_verified_dates_file(monkeypatch, tmp_path, capsys):
    base = _write(tmp_path, "b.json", _same_everywhere(3))
    bad = tmp_path / "dates.json"; bad.write_text(json.dumps({"1": "19/09/26"}))
    monkeypatch.setattr(sys, "argv", ["compare", "--compare", base, base, "--verified-dates", str(bad)])
    assert main() == 2
    out = capsys.readouterr().out
    assert "--verified-dates:" in out and "VERDICT" not in out


def test_format_report_prints_verified_date_scores_and_one_sided_documents():
    base = _misdated_file("2019-09-26")
    base["docs"]["5"] = _doc(_ok(), _ok())
    report = compare_results(base, _misdated_file("2026-09-19"), {"321": "2026-09-19"})
    text = format_report(report)
    assert "in one file only: base ['5'], cand []" in text
    assert "vs 1 verified dates: year base 0/2, cand 2/2; exact base 0/2, cand 2/2" in text
    assert "informational" not in text


def test_a_save_path_is_refused_when_git_cannot_answer():
    """Inside the repository, 'git is not installed' must not read as 'ignored'."""
    with patch("scripts.compare_json_mode.subprocess.run", side_effect=OSError("git not found")):
        reason = unsafe_save_path(os.path.join(REPO_ROOT, "base.compare.json"))
    assert reason and "not gitignored" in reason


# --- a candidate run that fell back to JSON mode has not measured the schema ---

def _run_with_mode(mode: str | None, **fields) -> dict:
    run = _ok(**fields)
    run["output_mode"] = mode
    return run


def test_a_candidate_that_fell_back_to_json_object_blocks():
    """The provider rejected the schema, the run silently used JSON mode, and
    every field agreed with master: without this check that reads as PASS."""
    base = _file({str(i): _doc(_run_with_mode(None), _run_with_mode(None)) for i in range(1, 4)})
    cand = _file({str(i): _doc(_run_with_mode("json_object_fallback"), _run_with_mode("json_schema")) for i in range(1, 4)})
    report = compare_results(base, cand)
    assert report["fallbacks"] == {"base": 0, "cand": 3}
    assert [b.split(":")[0] for b in report["blocks"]] == ["fallbacks"]


def test_runs_that_used_the_schema_do_not_block():
    base = _file({"1": _doc(_run_with_mode(None), _run_with_mode(None))})
    cand = _file({"1": _doc(_run_with_mode("json_schema"), _run_with_mode("json_schema"))})
    assert compare_results(base, cand)["blocks"] == []


def test_run_arm_records_the_output_mode_and_none_on_older_code():
    result = LLMExtractionResult(extraction=ExtractionResult(), tokens_in=1, tokens_out=1, model="m", output_mode="json_object_fallback")
    with patch("scripts.compare_json_mode.extract_document", return_value=result):
        assert run_arm([b"png"], {})[0]["output_mode"] == "json_object_fallback"
    class MasterResult:  # master's LLMExtractionResult has no output_mode
        extraction, tokens_in, tokens_out = ExtractionResult(), 1, 1
    with patch("scripts.compare_json_mode.extract_document", return_value=MasterResult()):
        assert run_arm([b"png"], {})[0]["output_mode"] is None


def test_provenance_meta_is_what_the_comparability_check_reads():
    """Both are written by the harness, so they must agree on key names: a
    renamed 'model' key would make every model comparison look identical."""
    from scripts.compare_json_mode import _provenance
    arms, _ = build_arms(_args(control=True), json_mode=True)
    with patch("scripts.compare_json_mode.subprocess.run", return_value=type("R", (), {"stdout": "abc123\n"})()):
        a = _provenance(arms, {"model": "gemini/x", "temperature": 1.0})
        b = _provenance(arms, {"model": "gemini/y", "temperature": 1.0})
    assert comparability_problems({"meta": a}, {"meta": a}) == []
    assert any("model differs" in p for p in comparability_problems({"meta": a}, {"meta": b}))



@pytest.mark.parametrize("victim", ["data/receiptory.db", ".env", "/tmp/anything.json", "/tmp/results"])
def test_a_save_never_targets_anything_but_a_compare_json_name(victim):
    """data/receiptory.db and .env are gitignored, so "ignored" is not "safe":
    _write_save replaces its target whole, and one typo would wipe the
    production database."""
    path = victim if os.path.isabs(victim) else os.path.join(REPO_ROOT, victim)
    with _git_check_ignore(0):  # even when git says ignored
        reason = unsafe_save_path(path)
    assert reason and ".compare.json" in reason


def test_a_gate_that_compared_too_few_documents_blocks():
    """Every run failed on both sides (a dead key): failures tie, no field can
    drift, and without this the verdict was PASS."""
    dead = _file({str(i): _doc(_failed(), _failed()) for i in range(1, 11)})
    report = compare_results(dead, dead)
    assert report["compared"] == 0
    assert [b.split(":")[0] for b in report["blocks"]] == ["compared"]
    half = _file({**{str(i): _doc(_ok(), _ok()) for i in range(1, 5)}, **{str(i): _doc(_failed(), _failed()) for i in range(5, 11)}})
    assert any(b.startswith("compared") for b in compare_results(half, half)["blocks"])  # 4 of 10


def test_a_noisier_candidate_is_flagged_but_does_not_block():
    """/ship decision D2: reported, never blocking, at one run pair per document."""
    base = _same_everywhere(6)
    cand = _file({str(i): _doc(_ok(vendor_name=f"a{i}"), _ok(vendor_name=f"b{i}")) for i in range(1, 7)})
    report = compare_results(base, cand)
    row = report["fields"]["vendor_name"]
    assert row["less_stable"] and row["w_cand"] - row["w_base"] >= DRIFT_MARGIN
    assert not any(b.startswith("vendor_name") for b in report["blocks"])
    assert "(less stable)" in format_report(report)


def test_the_report_shows_what_each_tree_actually_sent():
    base = _file({"1": _doc(_run_with_mode(None), _run_with_mode(None))})
    cand = _file({"1": _doc(_run_with_mode("json_object"), _run_with_mode("json_object"))})
    report = compare_results(base, cand)
    assert report["output_modes"] == {"base": {"None": 2}, "cand": {"json_object": 2}}
    assert "output modes" in format_report(report)
