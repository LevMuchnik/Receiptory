"""Exchange rates and document currency conversion (issue #72).

Rates come from the Bank of Israel's representative rates (שער יציג), which
publish 14 currencies against ILS. A currency the BoI does not publish (PLN,
CZK, ...) is crossed through EUR with the European Central Bank's reference
rate: CCY->ILS = (EUR->ILS from the BoI) / (CCY per EUR from the ECB). Both
are free and need no key.

Contract, read by the dashboard, the export and #73's Taxes tab:
- documents.conversion_rate turns one unit of the document's currency into
  documents.conversion_ref, at receipt_date or the last rate published before
  it. It is exactly 1.0 when the document is already in the reference
  currency, and NULL (never 0) when unknown.
- A rate is only valid while conversion_ref equals the current
  reference_currency; readers treat any other row as unconverted.

These are estimates (owner, 2026-10-18): the latest published rate on or
before the date is used for any date, and a date after today is clamped to
today, so a misread future year still converts.

Never call the network while holding a database connection: apply_conversion
reads a snapshot, fetches with no connection open, then writes only if the
currency and date are still the ones it read.
"""
import asyncio
import csv
import io
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from backend.config import get_setting
from backend.database import get_connection

logger = logging.getLogger(__name__)

BOI_URL = "https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0/RER_{ccy}_ILS"
ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{ccy}.EUR.SP00.A"
# The RER_<CCY>_ILS series the BoI publishes (checked 2026-10-18).
BOI_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "ZAR", "JOD", "EGP", "LBP"})
# One fetch covers this many days before the date: no publication gap
# (weekend plus holidays) is longer.
LOOKBACK_DAYS = 14
HTTP_TIMEOUT = 10.0
# The rates task: backfill, retries for unreachable sites, and recomputes
# after a reference-currency change (owner, 2026-10-18).
RATES_TASK_INTERVAL_S = 3 * 3600
_ISRAEL = ZoneInfo("Asia/Jerusalem")
_CODE_RE = re.compile(r"[A-Z]{3}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ALIASES = {"NIS": "ILS", "₪": "ILS", 'ש"ח': "ILS", "ש״ח": "ILS", "שח": "ILS", "$": "USD", "€": "EUR", "£": "GBP"}


class RateUnavailable(Exception):
    """A rate site could not be reached or answered with an error. Not the
    same as "no rate exists", which is None."""


def today_israel() -> date:
    return datetime.now(_ISRAEL).date()


def normalize_code(value) -> str | None:
    """An ISO 4217 code from what the model or a person wrote, or None."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    code = _ALIASES.get(code, code)
    return code if _CODE_RE.fullmatch(code) else None


def parse_csv(text: str) -> list[tuple[str, float]]:
    """(date, value per unit) rows from a BoI or ECB CSV. Both name their
    columns TIME_PERIOD, OBS_VALUE and UNIT_MULT; the value is quoted per
    10**UNIT_MULT units (JPY per 100, LBP per 10)."""
    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        try:
            value = float(rec.get("OBS_VALUE") or "")
            mult = int(rec.get("UNIT_MULT") or 0)
        except ValueError:
            continue
        period = (rec.get("TIME_PERIOD") or "").strip()
        if not _DATE_RE.fullmatch(period) or not math.isfinite(value) or value <= 0:
            continue
        rows.append((period, value / 10**mult))
    return rows


def _get_csv(url: str, params: dict) -> list[tuple[str, float]]:
    try:
        resp = httpx.get(url, params=params, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as e:
        raise RateUnavailable(f"{url}: {e}") from e
    # Both sites answer 404 for "no observations in this window" (a holiday
    # stretch) and for a series they do not have: that is no rate, not an outage.
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise RateUnavailable(f"{url}: HTTP {resp.status_code}")
    return parse_csv(resp.text)


def fetch_boi(ccy: str, start: str, end: str) -> list[tuple[str, float]]:
    """ILS per unit of ccy, one row per publication day in [start, end]."""
    return _get_csv(BOI_URL.format(ccy=ccy), {"startperiod": start, "endperiod": end, "format": "csv"})


def fetch_ecb(ccy: str, start: str, end: str) -> list[tuple[str, float]]:
    """Units of ccy per EUR, one row per publication day in [start, end]."""
    return _get_csv(ECB_URL.format(ccy=ccy), {"startPeriod": start, "endPeriod": end, "format": "csvdata"})


def _cached(source: str, ccy: str, start: str, end: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT rate_date, value FROM exchange_rates WHERE source = ? AND currency = ? AND rate_date BETWEEN ? AND ? "
            "ORDER BY rate_date DESC LIMIT 1",
            (source, ccy, start, end),
        ).fetchone()


def _latest(source: str, ccy: str, day: str) -> tuple[float, str] | None:
    """The latest published value on or before `day`, from the cache or one
    fetch of the LOOKBACK_DAYS window. Raises RateUnavailable."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM exchange_rates WHERE source = ? AND currency = ? AND rate_date = ?", (source, ccy, day)
        ).fetchone()
    if row:
        return row["value"], day
    start = (date.fromisoformat(day) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    fetch = fetch_boi if source == "boi" else fetch_ecb
    rows = fetch(ccy, start, day)  # no connection is open here
    if rows:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO exchange_rates (source, currency, rate_date, value, fetched_at) VALUES (?, ?, ?, ?, ?)",
                [(source, ccy, d, v, now) for d, v in rows],
            )
    hit = _cached(source, ccy, start, day)
    return (hit["value"], hit["rate_date"]) if hit else None


def rate_to_ils(ccy: str, day: str) -> tuple[float, str, str] | None:
    """(ILS per unit of ccy, publication date, source) on or before `day`,
    clamped to today. None when no rate exists; raises RateUnavailable."""
    day = min(day, today_israel().isoformat())
    if ccy == "ILS":
        return 1.0, day, "identity"
    if ccy in BOI_CURRENCIES:
        hit = _latest("boi", ccy, day)
        return (hit[0], hit[1], "boi") if hit else None
    per_eur = _latest("ecb", ccy, day)
    if per_eur is None:
        return None
    eur = _latest("boi", "EUR", day)
    if eur is None:
        return None
    return eur[0] / per_eur[0], min(eur[1], per_eur[1]), "boi+ecb"


def rate(ccy, ref, day) -> tuple[float, str | None, str] | None:
    """(rate per unit of ccy in ref, publication date, source), or None.
    Raises RateUnavailable when a site could not be reached."""
    ccy, ref = normalize_code(ccy), normalize_code(ref)
    if ccy is None or ref is None:
        return None
    if ccy == ref:
        return 1.0, None, "identity"
    if not isinstance(day, str) or not _DATE_RE.fullmatch(day):
        return None
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    x = rate_to_ils(ccy, day)
    if x is None:
        return None
    if ref == "ILS":
        return x
    r = rate_to_ils(ref, day)
    if r is None:
        return None
    return x[0] / r[0], min(x[1], r[1]), x[2] if x[2] == r[2] else f"{x[2]}/{r[2]}"


def reference_currency() -> str | None:
    return normalize_code(get_setting("reference_currency"))


def apply_conversion(doc_id: int) -> str:
    """Compute and store one document's conversion. Returns 'converted',
    'no_rate', 'unavailable' (a site was unreachable; the rate is left NULL
    for the rates task to retry) or 'superseded' (the currency or date changed
    while the rate was fetched, so this result was dropped)."""
    ref = reference_currency()
    with get_connection() as conn:
        doc = conn.execute("SELECT currency, receipt_date FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if doc is None:
        return "superseded"
    status = "converted"
    try:
        found = rate(doc["currency"], ref, doc["receipt_date"])
    except RateUnavailable as e:
        logger.warning("No exchange rate for document %d (%s on %s): %s", doc_id, doc["currency"], doc["receipt_date"], e)
        found, status = None, "unavailable"
    if found is None and status == "converted":
        status = "no_rate"
    value, rate_date = (found[0], found[1]) if found else (None, None)
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE documents SET conversion_rate = ?, conversion_rate_date = ?, conversion_ref = ?, "
            "converted_amount = CASE WHEN ? IS NULL OR total_amount IS NULL THEN NULL ELSE ROUND(total_amount * ?, 2) END "
            "WHERE id = ? AND currency IS ? AND receipt_date IS ?",
            (value, rate_date, ref if found else None, value, value, doc_id, doc["currency"], doc["receipt_date"]),
        )
        if cur.rowcount == 0:
            return "superseded"
    return status


def refresh_converted_amount(doc_id: int) -> None:
    """After an amount-only edit: the stored rate still applies."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET converted_amount = CASE WHEN conversion_rate IS NULL OR total_amount IS NULL THEN NULL "
            "ELSE ROUND(total_amount * conversion_rate, 2) END WHERE id = ?",
            (doc_id,),
        )


def convert_pending() -> dict:
    """One pass of the rates task: documents with a currency and no usable
    rate (none yet, or computed for another reference). The first unreachable
    site ends the pass; the next pass retries."""
    ref = reference_currency()
    with get_connection() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM documents WHERE is_deleted = 0 AND currency IS NOT NULL "
            "AND (conversion_rate IS NULL OR conversion_ref IS NOT ?) ORDER BY id",
            (ref,),
        )]
    counts = {"converted": 0, "no_rate": 0, "superseded": 0, "unavailable": 0}
    for doc_id in ids:
        status = apply_conversion(doc_id)
        counts[status] += 1
        if status == "unavailable":
            break
    return counts


async def run_rates_task() -> None:
    """Runs convert_pending at startup and then every RATES_TASK_INTERVAL_S,
    in a worker thread: a hung rate site must not block the event loop, and
    this task is separate from the processing queue so it never delays a
    receipt."""
    logger.info("Exchange-rate task started")
    while True:
        try:
            counts = await asyncio.to_thread(convert_pending)
            if any(counts.values()):
                logger.info("Exchange-rate pass: %s", counts)
        except Exception:
            logger.exception("Exchange-rate pass failed")
        await asyncio.sleep(RATES_TASK_INTERVAL_S)
