"""A/B-compare extraction arms over real documents, and gate a change on the result.

THREE ARM MODES, one harness:

  json-mode gate (default, the original issue #10 use)
      Runs each document with response_format off, then on.

  model comparison (--model-a / --model-b)
      Runs each document under two model ids, with json mode fixed at the
      current `llm_json_mode` setting. This is the arm the "switch extraction
      models" TODO calls for: the question is whether a new model still reads
      the documents already in the corpus.

  control (--control)
      Runs the production configuration twice. At temperature 1.0 two runs of
      the SAME thing disagree by sampling alone, so this is the noise floor any
      other disagreement has to be measured against.

In every mode the diff is between FRESH runs, which isolates the arm being tested
from months of unrelated model and prompt drift. Stored DB values are printed as
a reference column only.

MEASURING A CODE CHANGE (issue #67). An in-branch A/B cannot compare against
what production does today once the change touches the prompt itself. So:
  1. `--control --ids ... --save base.compare.json`, run from a master worktree;
  2. the same, run from the branch, `--save cand.compare.json`;
  3. `--compare base.compare.json cand.compare.json` applies the gate (offline,
     no LLM calls). It refuses files that are not comparable: not --control
     runs, or a different model, temperature or json mode on each side.
Dates are gated only against `--verified-dates FILE` ({"id": "YYYY-MM-DD"},
dates a person has checked on the paper). Without it, the year check assumes
a receipt's true year is the year it was uploaded. That is wrong for every
backlog upload, so it is reported but cannot block.

A --save file holds full extractions: OCR text, tax IDs, card digits. It is
written after every document (a crash loses nothing already paid for), and
only ever written to a `*.compare.json` name: anything else is refused, so a
typo can never replace receiptory.db or .env (os.replace would). Inside the
repository the name must also be gitignored, which `*.compare.json` is.
This script imports nothing that exists only on the branch, so the branch's copy,
dropped into a master worktree, runs unchanged against master's code.

The script issues no writes of its own, but init_db() is not free: it creates
the DB file if absent and applies unapplied migrations. The existence guard in
main() refuses to run against a missing DB so a wrong --data-dir can never
silently create a fresh database.

Run from a host checkout with backend deps (scripts/ is not baked into the
Docker image). In a worktree, which has no venv, use the main checkout's
interpreter and an absolute --data-dir; the API key resolves from the database,
so no .env is needed:
    .venv/bin/python scripts/compare_json_mode.py --data-dir /abs/data [--sample-size 18]
    .venv/bin/python scripts/compare_json_mode.py --model-a gemini/gemini-3.5-flash \
                                                  --model-b gemini/gemini-3.8-flash
    .venv/bin/python scripts/compare_json_mode.py --data-dir /abs/data --control \
                                                  --ids 12,40,321 --save base.compare.json
    .venv/bin/python scripts/compare_json_mode.py --compare base.compare.json cand.compare.json \
                                                  [--verified-dates dates.json]
"""

import argparse
import dataclasses
import itertools
import json
import logging
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv

# litellm's .env auto-load is import-order dependent; load explicitly so
# RECEIPTORY_* settings (notably the API key) are present regardless.
load_dotenv(os.path.join(REPO_ROOT, ".env"))

from backend.database import init_db, get_connection
from backend.config import get_setting, resolve_llm_api_key
from backend.storage import get_file_path, get_filed_path, render_all_pages_to_memory
from backend.processing import extract as extract_module
from backend.processing.extract import extract_document
from backend.processing.pipeline import estimate_cost

COMPARE_FIELDS = ["vendor_name", "receipt_date", "total_amount", "tax_amount", "category_name", "document_type"]

# extract_document's error text for a response cut off at max_tokens. Matched,
# not imported, because master's copy of extract.py must satisfy it too.
TRUNCATION_MARKER = "truncated at max_tokens"
# Saved but not gated. raw_extracted_text differs between ANY two runs at
# temperature 1.0 (line breaks, spacing), so its noise floor is every document;
# parse_salvaged is parse metadata, and a drop is the point of a schema.
UNGATED_FIELDS = frozenset({"raw_extracted_text", "parse_salvaged"})
# A field blocks the merge when it disagrees across trees on at least this many
# more documents than the noisier tree disagrees with itself.
DRIFT_MARGIN = 2
SAVE_SUFFIX = ".compare.json"

_DOC_COLUMNS = """d.id, d.original_filename, d.file_hash, d.stored_filename, d.language,
                  d.vendor_name, d.receipt_date, d.total_amount, d.tax_amount, d.document_type,
                  d.submission_date, c.name AS category_name"""


def pick_sample(limit: int) -> list[dict]:
    """Stratified sample: round-robin across (document_type, vendor) buckets."""
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT {_DOC_COLUMNS}
               FROM documents d LEFT JOIN categories c ON d.category_id = c.id
               WHERE d.status IN ('processed', 'needs_review') AND d.is_deleted = 0
               ORDER BY d.id DESC"""
        ).fetchall()
    return stratify([dict(r) for r in rows], limit)


def stratify(rows: list[dict], limit: int) -> list[dict]:
    """Round-robin across (document_type, vendor) buckets, interleaving TYPES.

    Plain alphabetical bucket order put every expense_receipt vendor ahead of
    the first issued_invoice bucket, so on the owner's corpus an 18-document
    sample was 18 expense receipts (measured 2026-09-23): the gate never saw an
    issued invoice, and document_type decides which side of the tax return a
    document lands on. Interleaving takes one bucket per type in turn.
    """
    buckets: dict[tuple, list] = defaultdict(list)
    for r in rows:
        buckets[(r["document_type"], r["vendor_name"])].append(r)
    per_type: dict[str, list[tuple]] = defaultdict(list)
    for key in sorted(buckets, key=lambda k: (str(k[0]), str(k[1]))):
        per_type[str(key[0])].append(key)
    order = [key for group in itertools.zip_longest(*per_type.values()) for key in group if key is not None]
    sample: list[dict] = []
    while len(sample) < limit and any(buckets.values()):
        for key in order:
            if buckets[key] and len(sample) < limit:
                sample.append(buckets[key].pop(0))
    return sample


def pick_ids(ids: list[int]) -> tuple[list[dict], list[int]]:
    """The given documents, in the given order, plus the ids that were not usable
    (missing, deleted, or never extracted). Pinning ids is what lets two runs,
    from two different trees, see exactly the same documents."""
    placeholders = ",".join("?" * len(ids))
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT {_DOC_COLUMNS}
               FROM documents d LEFT JOIN categories c ON d.category_id = c.id
               WHERE d.id IN ({placeholders}) AND d.status IN ('processed', 'needs_review') AND d.is_deleted = 0""",
            ids,
        ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id], [i for i in ids if i not in by_id]


def resolve_pdf(doc: dict, data_dir: str) -> str | None:
    """Prefer the filed PDF (what the pipeline extracted from), then converted, then a PDF original."""
    if doc["stored_filename"]:
        # Through the resolver like every other consumer of this column. The
        # value is LLM-derived and, on a restored database, untrusted: a bare
        # join discards the prefix for an absolute path. Harmless here (the
        # result is only read), but leaving one of four call sites unguarded is
        # how the invariant rots.
        try:
            filed = get_filed_path(doc["stored_filename"], data_dir)
        except ValueError:
            filed = None
        if filed and os.path.exists(filed):
            return filed
    converted = get_file_path("converted", doc["file_hash"], ".pdf", data_dir)
    if os.path.exists(converted):
        return converted
    ext = os.path.splitext(doc["original_filename"])[1].lower()
    if ext == ".pdf":
        original = get_file_path("original", doc["file_hash"], ext, data_dir)
        if os.path.exists(original):
            return original
    return None


def extraction_args() -> dict:
    # Its own category query, not pipeline.extraction_categories(): that helper
    # does not exist on master, and this script must run against master's code.
    with get_connection() as conn:
        cats = conn.execute("SELECT name, description, section FROM categories WHERE is_deleted = 0 AND is_system = 0").fetchall()
    return dict(
        model=get_setting("llm_model"),
        # NOT get_setting("llm_api_key"): that column is empty since the
        # named-keyring migration, and reading it made this script abort with
        # "no API key" before any LLM call. resolve_llm_api_key() follows the
        # llm_api_key_ref -> llm_api_keys selection the app itself uses.
        api_key=resolve_llm_api_key(),
        business_names=get_setting("business_names"),
        business_addresses=get_setting("business_addresses"),
        business_tax_ids=get_setting("business_tax_ids"),
        expense_categories=[{"name": c["name"], "description": c["description"] or ""} for c in cats if c["section"] == "expense"],
        issued_categories=[{"name": c["name"], "description": c["description"] or ""} for c in cats if c["section"] == "issued"],
        temperature=get_setting("llm_temperature"),
        max_tokens=get_setting("llm_max_tokens"),
        # As the pipeline passes them. Left out, the harness ran with no parse
        # retry and no reasoning while production has one retry configured, so
        # it measured a configuration nothing actually runs.
        parse_retries=get_setting("llm_parse_retries"),
        reasoning_effort=get_setting("llm_reasoning_effort"),
    )


def parse_ids(text: str) -> list[int]:
    """`--ids 12, 40,321` -> [12, 40, 321]. Rejects anything that is not an id."""
    ids = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"not a document id: {part!r}")
        ids.append(int(part))
    if not ids:
        raise ValueError("no document ids given")
    return ids


def build_arms(args: argparse.Namespace, json_mode) -> tuple[list[tuple[str, dict]], str]:
    """One arm = (label, per-run overrides of extraction_args), plus the gate note to print."""
    if args.model_a:
        # Labels are disambiguated even when the two models are identical.
        # Running a model against ITSELF is the control arm that says how much
        # of any disagreement is just nondeterminism, and keying per-arm token
        # counts by a colliding label silently doubled both arms' cost.
        same = args.model_a == args.model_b
        arms = [(f"A {args.model_a}" if same else args.model_a, {"model": args.model_a, "json_mode": json_mode}),
                (f"B {args.model_b}" if same else args.model_b, {"model": args.model_b, "json_mode": json_mode})]
        return arms, "Gate: systematic disagreement means the candidate reads these documents differently; one-off nondeterminism does not."
    if args.control:
        arms = [("run 1", {"json_mode": json_mode}), ("run 2", {"json_mode": json_mode})]
        return arms, "Control: the two runs are identical, so every difference above is sampling noise. Save it and --compare."
    arms = [("OFF", {"json_mode": False}), ("ON", {"json_mode": True})]
    return arms, "Gate: eyeball any off!=on rows above; systematic drift blocks the merge, one-off nondeterminism does not."


def run_arm(pages: list[bytes], call_args: dict) -> tuple[dict, int, int]:
    """One extraction -> (outcome, tokens_in, tokens_out).

    A failure is an OUTCOME, not a skipped document. The documents that fail to
    parse are the very thing a JSON-handling change is about, so dropping them
    from both sides would hide exactly the regression being looked for. A failed
    call's tokens are unknown (extract_document discards them on a hard fail).
    """
    try:
        result = extract_document(page_images=pages, **call_args)
    except Exception as e:
        message = str(e)
        return {"error": message[:500], "truncated": TRUNCATION_MARKER in message}, 0, 0
    # output_mode: what the successful call actually sent. None on master's
    # code, which predates the field; "json_object_fallback" when a requested
    # schema was rejected, which compare_results refuses to count as measured.
    return {"fields": dataclasses.asdict(result.extraction), "output_mode": getattr(result, "output_mode", None)}, result.tokens_in, result.tokens_out


def _year(value) -> str | None:
    return value[:4] if isinstance(value, str) and len(value) >= 4 else None


def compare_results(base: dict, cand: dict, verified: dict[str, str] | None = None) -> dict:
    """The merge gate over two --save files. Pure: no database, no LLM.

    Both files must hold two runs per document (--control). Per field, over the
    documents where all four runs succeeded:
      w_base / w_cand  documents where a tree's two runs disagree (noise floor)
      x                documents where base run 1 and cand run 1 disagree
    A field blocks when x exceeds max(w_base, w_cand) by DRIFT_MARGIN or more.
    It also blocks on more failures or truncations in cand than in base.

    Dates. With `verified` ({doc_id: "YYYY-MM-DD"}, checked on the paper), every
    verified document scores each run's receipt_date against the truth, both
    its year and the exact date, and cand doing worse than base on either
    blocks. Without it, the check falls back to documents whose stored year
    differs from the year they were uploaded, and counts a run as correct when
    it reads the upload year. That assumption is wrong for every backlog upload
    (a December receipt filed in January scores a correct reading as wrong),
    so that fallback is reported and never blocks.
    """
    ids = sorted(set(base["docs"]) & set(cand["docs"]), key=int)
    report: dict = {
        "docs": len(ids),
        "only_base": sorted(set(base["docs"]) - set(cand["docs"]), key=int),
        "only_cand": sorted(set(cand["docs"]) - set(base["docs"]), key=int),
        "failures": {}, "truncations": {}, "salvaged": {}, "fallbacks": {}, "fields": {}, "blocks": [],
    }

    def runs(data: dict, doc_id: str) -> list[dict]:
        return data["docs"][doc_id]["runs"][:2]

    for name, data in (("base", base), ("cand", cand)):
        all_runs = [r for i in ids for r in runs(data, i)]
        report["failures"][name] = sum("error" in r for r in all_runs)
        report["truncations"][name] = sum(bool(r.get("truncated")) for r in all_runs)
        report["salvaged"][name] = sum(bool(r.get("fields", {}).get("parse_salvaged")) for r in all_runs)
        report["fallbacks"][name] = sum(r.get("output_mode") == "json_object_fallback" for r in all_runs)
        modes: dict = defaultdict(int)
        for r in all_runs:
            if "fields" in r:
                modes[str(r.get("output_mode"))] += 1
        report.setdefault("output_modes", {})[name] = dict(sorted(modes.items()))
    if report["fallbacks"]["cand"]:
        # The provider rejected the schema and the run silently used JSON mode:
        # a PASS would claim to have measured a schema that was never sent.
        report["blocks"].append(f"fallbacks: {report['fallbacks']['cand']} candidate run(s) had their schema rejected and fell back to json_object, so the schema was not measured on them")
    for kind in ("failures", "truncations"):
        if report[kind]["cand"] > report[kind]["base"]:
            report["blocks"].append(f"{kind}: {report[kind]['cand']} on the candidate vs {report[kind]['base']} on the baseline")

    complete = [i for i in ids if all("fields" in r for r in runs(base, i) + runs(cand, i))]
    report["compared"] = len(complete)
    # A gate that compared nothing has measured nothing: with every run failing
    # on both sides (a dead key, a quota), the failure counts tie and no field
    # can drift, so without this it would print PASS.
    if len(complete) < max(1, (len(ids) + 1) // 2):
        report["blocks"].append(f"compared: all four runs succeeded on only {len(complete)} of {len(ids)} documents, too few to measure anything")

    def value(data: dict, doc_id: str, run: int, field: str):
        return data["docs"][doc_id]["runs"][run]["fields"].get(field)

    fields = sorted({f for i in complete for r in runs(base, i) + runs(cand, i) for f in r["fields"]} - UNGATED_FIELDS)
    for field in fields:
        w_base = sum(value(base, i, 0, field) != value(base, i, 1, field) for i in complete)
        w_cand = sum(value(cand, i, 0, field) != value(cand, i, 1, field) for i in complete)
        x = sum(value(base, i, 0, field) != value(cand, i, 0, field) for i in complete)
        noise = max(w_base, w_cand)
        # less_stable is reported, never blocking (/ship decision D2): with one
        # run pair per document, a noise count moves by 2-3 on chance alone.
        report["fields"][field] = {"w_base": w_base, "w_cand": w_cand, "x": x, "excess": x - noise,
                                   "less_stable": w_cand - w_base >= DRIFT_MARGIN}
        if x - noise >= DRIFT_MARGIN:
            report["blocks"].append(f"{field}: differs across trees on {x} of {len(complete)} documents; noise floor is {noise}")

    if verified:
        dated = [i for i in complete if i in verified]
        truth = {i: verified[i] for i in dated}
        basis = "verified"
    else:
        # Both years must be present: an undated document is not "misdated".
        dated = [i for i in complete
                 if _year(base["docs"][i].get("stored_receipt_date")) and _year(base["docs"][i].get("submission_date"))
                 and _year(base["docs"][i]["stored_receipt_date"]) != _year(base["docs"][i]["submission_date"])]
        truth = {i: base["docs"][i]["submission_date"] for i in dated}
        basis = "upload-year"
    scores: dict = {"basis": basis, "docs": len(dated), "of": 2 * len(dated)}
    for name, data in (("base", base), ("cand", cand)):
        reads = [value(data, i, run, "receipt_date") for i in dated for run in (0, 1)]
        truths = [truth[i] for i in dated for _run in (0, 1)]
        scores[name] = {
            "year": sum(_year(r) is not None and _year(r) == _year(t) for r, t in zip(reads, truths)),
            "exact": sum(isinstance(r, str) and r[:10] == t[:10] for r, t in zip(reads, truths)),
        }
    report["dates"] = scores
    if basis == "verified":
        for kind in ("year", "exact"):
            if scores["cand"][kind] < scores["base"][kind]:
                report["blocks"].append(f"receipt_date {kind}: {scores['cand'][kind]} of {scores['of']} verified reads correct on the candidate vs {scores['base'][kind]} on the baseline")
    return report


def load_verified_dates(path: str) -> dict[str, str]:
    """{doc_id: "YYYY-MM-DD"} from a JSON file, each date validated."""
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, dict) or not raw:
        raise ValueError("expected a non-empty JSON object of {document id: \"YYYY-MM-DD\"}")
    verified = {}
    for doc_id, value in raw.items():
        if not str(doc_id).isdigit() or not isinstance(value, str):
            raise ValueError(f"bad entry {doc_id!r}: {value!r}")
        date.fromisoformat(value)  # raises ValueError on a malformed date
        verified[str(doc_id)] = value
    return verified


def comparability_problems(base: dict, cand: dict) -> list[str]:
    """Why two --save files cannot be compared, or [] if they can.

    The gate reads each file's two runs as a noise floor, so both files must be
    --control runs (two arms with identical settings), and the two trees must
    run the same model, temperature and json mode. Only the code may differ.
    """
    problems = []
    arm_settings = {}
    for name, data in (("base", base), ("cand", cand)):
        arms = data.get("meta", {}).get("arms") or []
        settings = [{k: v for k, v in arm.items() if k != "label"} for arm in arms]
        if len(settings) != 2 or settings[0] != settings[1]:
            problems.append(f"{name} is not a --control run: its arms {[a.get('label') for a in arms]} are not two identical configurations, so their disagreement is not a noise floor")
        else:
            arm_settings[name] = settings[0]
    base_meta, cand_meta = base.get("meta", {}), cand.get("meta", {})
    for key in ("model", "temperature"):
        if base_meta.get(key) != cand_meta.get(key):
            problems.append(f"{key} differs: base {base_meta.get(key)!r}, cand {cand_meta.get(key)!r}")
    if len(arm_settings) == 2 and arm_settings["base"] != arm_settings["cand"]:
        problems.append(f"arm settings differ: base {arm_settings['base']}, cand {arm_settings['cand']}")
    return problems


def unsafe_save_path(path: str) -> str | None:
    """Why `path` must not receive a --save file, or None if it may.

    Only `*.compare.json` names: _write_save replaces the target whole, and a
    gitignored path is not a safe one (data/receiptory.db and .env are both
    ignored). A save file also holds full extractions (OCR text, tax IDs, card
    digits), so inside the repository it must be gitignored too. If git cannot
    answer, refuse.
    """
    if not os.path.basename(path).endswith(SAVE_SUFFIX):
        return f"{path} does not end in {SAVE_SUFFIX}; the save replaces its target whole, so only harness result names are accepted"
    # realpath on both sides: a symlinked directory or a second mount of the
    # repository must not smuggle the file past the containment check.
    target, root = os.path.realpath(path), os.path.realpath(REPO_ROOT)
    if os.path.commonpath([target, root]) != root:
        return None
    try:
        ignored = subprocess.run(["git", "-C", root, "check-ignore", "-q", target], capture_output=True).returncode == 0
    except OSError:
        ignored = False
    if ignored:
        return None
    return f"{path} is inside the repository and not gitignored; it would hold document text and tax IDs. Use a path outside the repo, or a *.compare.json name"


def format_report(report: dict) -> str:
    lines = [f"Documents in both files: {report['docs']}; all four runs succeeded on {report['compared']}."]
    if report["only_base"] or report["only_cand"]:
        lines.append(f"  Not compared, in one file only: base {report['only_base']}, cand {report['only_cand']}")
    lines.append(f"  {'field':<22} {'noise base':>10} {'noise cand':>10} {'across':>7} {'excess':>7}")
    for field, row in report["fields"].items():
        flag = "  <-- BLOCKS" if row["excess"] >= DRIFT_MARGIN else ("  (less stable)" if row.get("less_stable") else "")
        lines.append(f"  {field:<22} {row['w_base']:>10} {row['w_cand']:>10} {row['x']:>7} {row['excess']:>7}{flag}")
    # What each tree actually sent: a PASS only measures a schema if cand says json_schema.
    lines.append(f"  output modes           base {report['output_modes']['base']}, cand {report['output_modes']['cand']}")
    for kind in ("failures", "truncations", "salvaged", "fallbacks"):
        lines.append(f"  {kind:<22} base {report[kind]['base']}, cand {report[kind]['cand']}")
    d = report["dates"]
    if d["basis"] == "verified":
        lines.append(f"  receipt_date vs {d['docs']} verified dates: year base {d['base']['year']}/{d['of']}, cand {d['cand']['year']}/{d['of']}; "
                     f"exact base {d['base']['exact']}/{d['of']}, cand {d['cand']['exact']}/{d['of']}")
    else:
        lines.append(f"  receipt_date year on {d['docs']} documents stored with a non-upload year: base {d['base']['year']}/{d['of']}, "
                     f"cand {d['cand']['year']}/{d['of']} (assumes the upload year is the true year; informational, pass --verified-dates to gate)")
    lines.append("VERDICT: " + ("BLOCK\n  - " + "\n  - ".join(report["blocks"]) if report["blocks"] else "PASS"))
    return "\n".join(lines)


def _provenance(arms: list[tuple[str, dict]], base_args: dict) -> dict:
    try:
        commit = subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        commit = "unknown"
    return {
        "commit": commit,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": base_args["model"],
        "temperature": base_args["temperature"],
        "arms": [{"label": label, **overrides} for label, overrides in arms],
        # Whether this tree's extract.py can send a schema at all (#67). What it
        # actually sent also depends on the model, which is recorded above.
        "schema_capable_code": hasattr(extract_module, "response_format_kwargs"),
    }


def fmt(v) -> str:
    return "—" if v is None else str(v)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=os.environ.get("RECEIPTORY_DATA_DIR", "data"))
    parser.add_argument("--sample-size", type=int, default=18)
    parser.add_argument("--ids", help="Comma-separated document ids to run instead of a sample, in this order.")
    parser.add_argument("--model-a", help="Compare two MODELS instead of json-mode off/on. Baseline arm.")
    parser.add_argument("--model-b", help="Candidate model. Requires --model-a.")
    parser.add_argument("--control", action="store_true", help="Run the production configuration twice: the noise floor.")
    parser.add_argument("--temperature", type=float,
                        help="Override llm_temperature for BOTH arms. The stored value is 1.0, "
                             "which makes extraction non-reproducible and confounds any A/B: "
                             "run the same model against itself at 0 vs 1 to see how much of a "
                             "disagreement is the arm and how much is sampling.")
    parser.add_argument("--save", metavar="FILE", help="Write every arm's full extraction per document, for --compare.")
    parser.add_argument("--compare", nargs=2, metavar=("BASE", "CAND"), help="Apply the merge gate to two --save files. No LLM calls.")
    parser.add_argument("--verified-dates", metavar="FILE", help='With --compare: {"doc id": "YYYY-MM-DD"} dates checked on the paper. Only these can gate receipt_date.')
    args = parser.parse_args()

    if args.verified_dates and not args.compare:
        print("--verified-dates is a --compare option. Aborting.")
        return 2
    if args.compare:
        if args.ids or args.model_a or args.model_b or args.control or args.save:
            print("--compare reads two saved files; it takes no run options. Aborting.")
            return 2
        with open(args.compare[0]) as f:
            base = json.load(f)
        with open(args.compare[1]) as f:
            cand = json.load(f)
        print(f"base: {base.get('meta', {})}\ncand: {cand.get('meta', {})}")
        problems = comparability_problems(base, cand)
        if problems:
            print("Not comparable, no verdict:\n  - " + "\n  - ".join(problems))
            return 2
        try:
            verified = load_verified_dates(args.verified_dates) if args.verified_dates else None
        except (OSError, ValueError) as e:
            print(f"--verified-dates: {e}. Aborting.")
            return 2
        report = compare_results(base, cand, verified)
        print(format_report(report))
        return 1 if report["blocks"] else 0

    if bool(args.model_a) != bool(args.model_b):
        print("--model-a and --model-b must be given together. Aborting.")
        return 2
    if args.control and args.model_a:
        print("--control runs the production model twice; it cannot be combined with --model-a/--model-b. Aborting.")
        return 2
    try:
        ids = parse_ids(args.ids) if args.ids else None
    except ValueError as e:
        print(f"--ids: {e}. Aborting.")
        return 2
    if args.save and (reason := unsafe_save_path(args.save)):
        print(f"--save: {reason}. Aborting.")
        return 2

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    data_dir = os.path.abspath(args.data_dir)
    db_path = os.path.join(data_dir, "receiptory.db")
    if not os.path.exists(db_path):
        # init_db would CREATE a fresh DB + schema here — refuse instead so a
        # wrong --data-dir can never masquerade as an empty corpus.
        print(f"No database at {db_path} — check --data-dir / RECEIPTORY_DATA_DIR. Aborting.")
        return 2
    # init_db also APPLIES unapplied migrations. Running this script from a
    # newer checkout must never schema-upgrade a production DB out from under
    # an older running container — refuse instead.
    import glob
    import sqlite3
    migration_count = len(glob.glob(os.path.join(REPO_ROOT, "migrations", "*.sql")))
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as ro_conn:
        try:
            db_version = ro_conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        except sqlite3.OperationalError:
            db_version = 0
    if db_version < migration_count:
        print(f"DB schema version {db_version} is behind this checkout's {migration_count} migrations — deploy first, then run the comparison. Aborting.")
        return 2
    init_db(db_path)

    if ids:
        sample, unusable = pick_ids(ids)
        if unusable:
            print(f"Not usable (missing, deleted or never extracted), skipped: {unusable}")
    else:
        sample = pick_sample(args.sample_size)
    if not sample:
        print("No processed documents found — nothing to compare.")
        return 1
    base_args = extraction_args()
    if args.temperature is not None:
        base_args["temperature"] = args.temperature
    if not base_args["api_key"]:
        print("No LLM API key resolved (llm_api_key_ref -> llm_api_keys, or the legacy env key). Aborting before any LLM call.")
        return 2
    dpi = get_setting("page_render_dpi")

    arms, gate_note = build_arms(args, get_setting("llm_json_mode"))
    labels = [a[0] for a in arms]
    # Tokens are counted PER ARM: the two models can have different rates, so a
    # single pooled total would price the run at whichever model was asked last.
    arm_tokens: dict[str, list[int]] = {label: [0, 0] for label in labels}

    docs_with_diffs = 0
    field_diff_counts: dict[str, int] = defaultdict(int)
    total_in = total_out = 0
    skipped: list[str] = []
    failed_runs = 0
    saved_docs: dict[str, dict] = {}

    sleep_interval = get_setting("llm_sleep_interval")
    meta = _provenance(arms, base_args)

    for doc in sample:
        pdf = resolve_pdf(doc, data_dir)
        if pdf is None:
            skipped.append(f"#{doc['id']} ({doc['original_filename']}): no PDF found")
            continue
        try:
            pages = render_all_pages_to_memory(pdf, dpi=dpi)
        except Exception as e:
            skipped.append(f"#{doc['id']} ({doc['original_filename']}): could not render: {e}")
            continue
        outcomes: dict[str, dict] = {}
        for label, overrides in arms:
            outcome, tin, tout = run_arm(pages, {**base_args, **overrides})
            outcomes[label] = outcome
            total_in += tin
            total_out += tout
            arm_tokens[label][0] += tin
            arm_tokens[label][1] += tout
            if sleep_interval > 0:
                time.sleep(sleep_interval)  # mirror the queue's provider rate-limit pacing
        saved_docs[str(doc["id"])] = {
            "submission_date": doc.get("submission_date"),
            "stored_receipt_date": doc.get("receipt_date"),
            "runs": [outcomes[label] for label in labels],
        }
        if args.save:
            # After every document: the runs are paid for as they happen.
            _write_save(args.save, meta, saved_docs)

        first, second = outcomes[labels[0]], outcomes[labels[1]]
        print(f"\nDoc #{doc['id']}  {doc['original_filename']}  ({doc['document_type']}, {doc['language'] or '?'})", end="")
        failures = [(label, o) for label, o in outcomes.items() if "error" in o]
        if failures:
            failed_runs += len(failures)
            print("  [FAILED]")
            for label, o in failures:
                print(f"  {label}: {'TRUNCATED: ' if o['truncated'] else ''}{o['error'][:200]}")
            continue
        a, b = first["fields"], second["fields"]
        diffs = [f for f in COMPARE_FIELDS if a.get(f) != b.get(f)]
        if diffs:
            docs_with_diffs += 1
            for f in diffs:
                field_diff_counts[f] += 1
        print(f"  [{'DIFF' if diffs else 'same'}]")
        print(f"  {'field':<16} {labels[0]:<32.32} {labels[1]:<32.32} {'DB(ref)':<32}")
        for f in COMPARE_FIELDS:
            flag = "  <-- DIFFERS" if f in diffs else ""
            print(f"  {f:<16} {fmt(a.get(f)):<32.32} {fmt(b.get(f)):<32.32} {fmt(doc.get(f)):<32.32}{flag}")

    print("\n" + "=" * 72)
    run_docs = len(saved_docs)
    print(f"SUMMARY: {run_docs - docs_with_diffs}/{run_docs} docs AGREE on every compared field "
          f"({docs_with_diffs} differ, {failed_runs} failed run(s)) — {labels[0]} vs {labels[1]}")
    for f, n in sorted(field_diff_counts.items()):
        print(f"  {f}: {n} diff(s)")
    if skipped:
        print(f"Skipped {len(skipped)}: " + "; ".join(skipped))
    total_cost = 0.0
    for label, overrides in arms:
        priced = overrides.get("model", base_args["model"])
        tin, tout = arm_tokens[label]
        c = estimate_cost(priced, tin, tout)
        total_cost += c
        print(f"  {label}: {tin} in / {tout} out (~${c:.4f} at {priced} rates)")
    print(f"Tokens: {total_in} in / {total_out} out across {len(arms) * run_docs} extractions (~${total_cost:.4f} total)")
    print(gate_note)
    if args.save:
        _write_save(args.save, meta, saved_docs)
        print(f"Saved {run_docs} documents to {args.save}")
    return 0


def _write_save(path: str, meta: dict, docs: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump({"meta": meta, "docs": docs}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # a crash mid-write leaves the previous complete file


if __name__ == "__main__":
    sys.exit(main())
