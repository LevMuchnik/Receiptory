"""Issue #10 merge gate: A/B-compare extraction with JSON mode off vs on.

Runs each sampled processed document through extract_document() twice —
once without response_format (off) and once with json_object mode (on) —
and diffs key fields between the two FRESH runs. That off-vs-on diff is
the merge gate: it isolates the JSON-mode delta from months of unrelated
model/prompt drift. Stored DB values are printed as a reference column
only.

The script issues no writes of its own, but init_db() is not free: it
creates the DB file if absent and applies unapplied migrations. The
existence guard in main() refuses to run against a missing DB so a wrong
--data-dir can never silently create a fresh database.

Run from a host environment with backend deps (scripts/ is not baked into
the Docker image), pointed at the production data dir:
    python scripts/compare_json_mode.py [--data-dir data] [--sample-size 18]
"""

import argparse
import logging
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# litellm's .env auto-load is import-order dependent; load explicitly so
# RECEIPTORY_* settings (notably the API key) are present regardless.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from backend.database import init_db, get_connection
from backend.config import get_setting
from backend.storage import get_file_path, render_all_pages_to_memory
from backend.processing.extract import extract_document
from backend.processing.pipeline import estimate_cost

COMPARE_FIELDS = ["vendor_name", "receipt_date", "total_amount", "tax_amount", "category_name", "document_type"]


def pick_sample(limit: int) -> list[dict]:
    """Stratified sample: round-robin across (document_type, vendor) buckets."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT d.id, d.original_filename, d.file_hash, d.stored_filename, d.language,
                      d.vendor_name, d.receipt_date, d.total_amount, d.tax_amount, d.document_type,
                      c.name AS category_name
               FROM documents d LEFT JOIN categories c ON d.category_id = c.id
               WHERE d.status IN ('processed', 'needs_review') AND d.is_deleted = 0
               ORDER BY d.id DESC"""
        ).fetchall()
    buckets: dict[tuple, list] = defaultdict(list)
    for r in rows:
        buckets[(r["document_type"], r["vendor_name"])].append(dict(r))
    sample: list[dict] = []
    while len(sample) < limit and any(buckets.values()):
        for key in sorted(buckets, key=lambda k: (str(k[0]), str(k[1]))):
            if buckets[key] and len(sample) < limit:
                sample.append(buckets[key].pop(0))
    return sample


def resolve_pdf(doc: dict, data_dir: str) -> str | None:
    """Prefer the filed PDF (what the pipeline extracted from), then converted, then a PDF original."""
    if doc["stored_filename"]:
        filed = os.path.join(data_dir, "storage", "filed", doc["stored_filename"])
        if os.path.exists(filed):
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
    with get_connection() as conn:
        cats = conn.execute("SELECT name, description, section FROM categories WHERE is_deleted = 0 AND is_system = 0").fetchall()
    return dict(
        model=get_setting("llm_model"),
        api_key=get_setting("llm_api_key"),
        business_names=get_setting("business_names"),
        business_addresses=get_setting("business_addresses"),
        business_tax_ids=get_setting("business_tax_ids"),
        expense_categories=[{"name": c["name"], "description": c["description"] or ""} for c in cats if c["section"] == "expense"],
        issued_categories=[{"name": c["name"], "description": c["description"] or ""} for c in cats if c["section"] == "issued"],
        temperature=get_setting("llm_temperature"),
        max_tokens=get_setting("llm_max_tokens"),
    )


def fmt(v) -> str:
    return "—" if v is None else str(v)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.environ.get("RECEIPTORY_DATA_DIR", "data"))
    parser.add_argument("--sample-size", type=int, default=18)
    args = parser.parse_args()

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
    migration_count = len(glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations", "*.sql")))
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as ro_conn:
        try:
            db_version = ro_conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        except sqlite3.OperationalError:
            db_version = 0
    if db_version < migration_count:
        print(f"DB schema version {db_version} is behind this checkout's {migration_count} migrations — deploy first, then run the comparison. Aborting.")
        return 2
    init_db(db_path)

    sample = pick_sample(args.sample_size)
    if not sample:
        print("No processed documents found — nothing to compare.")
        return 1
    base_args = extraction_args()
    if not base_args["api_key"]:
        print("No LLM API key found (RECEIPTORY_LLM_API_KEY env / .env, or llm_api_key DB setting). Aborting before any LLM call.")
        return 2
    dpi = get_setting("page_render_dpi")

    docs_with_diffs = 0
    field_diff_counts: dict[str, int] = defaultdict(int)
    total_in = total_out = 0
    skipped: list[str] = []

    sleep_interval = get_setting("llm_sleep_interval")

    for doc in sample:
        pdf = resolve_pdf(doc, data_dir)
        if pdf is None:
            skipped.append(f"#{doc['id']} ({doc['original_filename']}): no PDF found")
            continue
        try:
            pages = render_all_pages_to_memory(pdf, dpi=dpi)
            results = {}
            for label, json_mode in (("OFF", False), ("ON", True)):
                llm_result = extract_document(page_images=pages, json_mode=json_mode, **base_args)
                results[label] = llm_result.extraction
                total_in += llm_result.tokens_in
                total_out += llm_result.tokens_out
                if sleep_interval > 0:
                    time.sleep(sleep_interval)  # mirror the queue's provider rate-limit pacing
        except Exception as e:
            # One bad doc must not discard the whole run (and its token spend).
            skipped.append(f"#{doc['id']} ({doc['original_filename']}): {e}")
            continue

        off, on = results["OFF"], results["ON"]
        diffs = [f for f in COMPARE_FIELDS if getattr(off, f) != getattr(on, f)]
        if diffs:
            docs_with_diffs += 1
            for f in diffs:
                field_diff_counts[f] += 1

        marker = "DIFF" if diffs else "same"
        print(f"\nDoc #{doc['id']}  {doc['original_filename']}  ({doc['document_type']}, {doc['language'] or '?'})  [{marker}]")
        print(f"  {'field':<16} {'OFF':<32} {'ON':<32} {'DB(ref)':<32}")
        for f in COMPARE_FIELDS:
            flag = "  <-- off!=on" if f in diffs else ""
            print(f"  {f:<16} {fmt(getattr(off, f)):<32.32} {fmt(getattr(on, f)):<32.32} {fmt(doc.get(f)):<32.32}{flag}")

    model = base_args["model"]
    cost = estimate_cost(model, total_in, total_out)
    print("\n" + "=" * 72)
    print(f"SUMMARY: {docs_with_diffs}/{len(sample) - len(skipped)} docs with off-vs-on diffs")
    for f, n in sorted(field_diff_counts.items()):
        print(f"  {f}: {n} diff(s)")
    if skipped:
        print(f"Skipped {len(skipped)}: " + "; ".join(skipped))
    print(f"Tokens: {total_in} in / {total_out} out across {2 * (len(sample) - len(skipped))} extractions (~${cost:.2f} at {model} rates)")
    print("Gate: eyeball any off!=on rows above — systematic drift blocks the merge; one-off nondeterminism does not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
