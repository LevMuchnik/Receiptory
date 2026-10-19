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
currency, date and stored conversion are still the ones it read. Whoever
writes a document's currency or date adds conversion_update()'s SET clauses
to that UPDATE (pipeline, PATCH): they keep the conversion only while the row
keeps its currency and date, so a stored rate is always for the current
currency and date, and a failed conversion leaves NULL for the rates task,
never the old currency's rate.
"""
import asyncio
import csv
import io
import logging
import math
import re
import time
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
# The ECB's current reference rates (checked 2026-10-18; BGN ended 2025-12-31).
ECB_CURRENCIES = frozenset({"AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "GBP", "HKD", "HUF", "IDR", "ILS", "INR",
                            "ISK", "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB",
                            "TRY", "USD", "ZAR"})
# What reference_currency may be: a code these sources can convert into today.
REFERENCE_CURRENCIES = frozenset({"ILS"}) | BOI_CURRENCIES | ECB_CURRENCIES
# Every code a document can be converted from: also the ECB's discontinued
# series, which still answer for their years (BGN until 2025). Any other code
# is never looked up.
KNOWN_CURRENCIES = REFERENCE_CURRENCIES | frozenset({"ARS", "BGN", "CYP", "DZD", "EEK", "GRD", "HRK", "LTL", "LVL",
                                                      "MAD", "MTL", "RUB", "SIT", "SKK", "TWD"})
# One fetch covers this many days before the date: no publication gap
# (weekend plus holidays) is longer.
LOOKBACK_DAYS = 14
# Seconds; connecting gets less, so an unreachable site fails fast. httpx
# applies these per read, so a whole request also has a deadline and a size
# cap: a 14-day CSV is a few KB.
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
REQUEST_DEADLINE_S = 20
MAX_RESPONSE_BYTES = 1_000_000
# After a site was unreachable, lookups that need it fail at once for this
# long instead of each paying the timeouts (the queue, an edit); the rates
# task retries later.
OUTAGE_BACKOFF_S = 600
# A window a source answered with no rates is not asked again for a day.
EMPTY_ANSWER_TTL_S = 24 * 3600
# The rates task: backfill, retries for unreachable sites, and recomputes
# after a reference-currency change (owner, 2026-10-18). A pass that met an
# unreachable site runs again sooner, once the backoff below has passed.
RATES_TASK_INTERVAL_S = 3 * 3600
OUTAGE_RETRY_S = 15 * 60
_ISRAEL = ZoneInfo("Asia/Jerusalem")
_CODE_RE = re.compile(r"[A-Z]{3}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# Neither source publishes anything older, and date arithmetic near year 1
# overflows: an earlier receipt_date is a misread, not a rate to look up.
_EARLIEST_DATE = "1900-01-01"
# A rate outside this range is a parsing accident, not a currency (LBP, the
# smallest per unit, is about 4e-5 ILS); one that underflows to 0 would divide
# by zero in a cross rate.
_MIN_RATE, _MAX_RATE = 1e-6, 1e6
# Beyond this a converted amount is a misread, and the product of a huge total
# and a rate can overflow to inf, which JSON cannot carry.
_MAX_AMOUNT = 1e15
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
        # A real UNIT_MULT is 0-2; anything wild would overflow the division.
        if not _DATE_RE.fullmatch(period) or not math.isfinite(value) or not 0 <= mult <= 9:
            continue
        per_unit = value / 10**mult
        if _MIN_RATE <= per_unit <= _MAX_RATE:
            rows.append((period, per_unit))
    return rows


def _get_csv(url: str, params: dict) -> list[tuple[str, float]]:
    deadline = time.monotonic() + REQUEST_DEADLINE_S
    try:
        with httpx.stream("GET", url, params=params, timeout=HTTP_TIMEOUT) as resp:
            # Both sites answer 404 for "no observations in this window" (a
            # holiday stretch) and for a series they do not have: that is no
            # rate, not an outage.
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                raise RateUnavailable(f"{url}: HTTP {resp.status_code}")
            body, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise RateUnavailable(f"{url}: response over {MAX_RESPONSE_BYTES} bytes")
                if time.monotonic() > deadline:
                    raise RateUnavailable(f"{url}: response took over {REQUEST_DEADLINE_S}s")
                body.append(chunk)
            text = b"".join(body).decode(resp.encoding or "utf-8", errors="replace")
    except httpx.HTTPError as e:
        raise RateUnavailable(f"{url}: {e}") from e
    # An empty 200 is a window with no rates (the ECB answers some that way);
    # any other 200 that is not the rates CSV (a maintenance or bot-check
    # page) is an outage to retry, not "no rates" to remember for a day.
    if not text.strip():
        return []
    header = text.splitlines()[0].split(",")
    if "TIME_PERIOD" not in header or "OBS_VALUE" not in header:
        raise RateUnavailable(f"{url}: HTTP 200 without a rates CSV")
    try:
        return parse_csv(text)
    except csv.Error as e:
        raise RateUnavailable(f"{url}: unreadable CSV: {e}") from e


def fetch_boi(ccy: str, start: str, end: str) -> list[tuple[str, float]]:
    """ILS per unit of ccy, one row per publication day in [start, end]."""
    return _get_csv(BOI_URL.format(ccy=ccy), {"startperiod": start, "endperiod": end, "format": "csv"})


def fetch_ecb(ccy: str, start: str, end: str) -> list[tuple[str, float]]:
    """Units of ccy per EUR, one row per publication day in [start, end]."""
    return _get_csv(ECB_URL.format(ccy=ccy), {"startPeriod": start, "endPeriod": end, "format": "csvdata"})


# Per process: when each source may be asked again after an outage, and the
# windows a source answered with no rates (until when not to ask again).
_backoff_until: dict[str, float] = {}
_empty_until: dict[tuple, float] = {}


def _fetch(source: str, ccy: str, start: str, end: str) -> list[tuple[str, float]]:
    now = time.monotonic()
    if now < _backoff_until.get(source, 0):
        raise RateUnavailable(f"{source} was unreachable less than {OUTAGE_BACKOFF_S}s ago")
    if now < _empty_until.get((source, ccy, start, end), 0):
        return []
    fetch = fetch_boi if source == "boi" else fetch_ecb
    try:
        rows = fetch(ccy, start, end)
    except RateUnavailable:
        _backoff_until[source] = time.monotonic() + OUTAGE_BACKOFF_S
        raise
    if not rows:
        _empty_until[(source, ccy, start, end)] = time.monotonic() + EMPTY_ANSWER_TTL_S
        # No publication gap is LOOKBACK_DAYS long: the code is unknown to
        # this source, the date is before its series, or the API moved. Not
        # cached in the database (it may be the last), so say so.
        logger.warning("%s returned no rates for %s in %s..%s", source, ccy, start, end)
    return rows


def _cached(source: str, ccy: str, start: str, end: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT rate_date, value FROM exchange_rates WHERE source = ? AND currency = ? AND rate_date BETWEEN ? AND ? "
            "ORDER BY rate_date DESC LIMIT 1",
            (source, ccy, start, end),
        ).fetchone()


def _latest(source: str, ccy: str, day: str) -> tuple[float, str] | None:
    """The latest published value on or before `day`. From the cache when it
    holds `day` itself, or when `day` lies in a window that was fetched (every
    rate published in it is cached, so the latest cached one in it is the
    latest published); otherwise from one fetch of the LOOKBACK_DAYS window.
    Cached rows on both sides of `day` prove nothing: two fetches that do not
    overlap leave unfetched days between them. Raises RateUnavailable."""
    start = (date.fromisoformat(day) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    with get_connection() as conn:
        exact = conn.execute(
            "SELECT value FROM exchange_rates WHERE source = ? AND currency = ? AND rate_date = ?", (source, ccy, day)
        ).fetchone()
        if exact:
            return exact["value"], day
        window = conn.execute(
            "SELECT MIN(start_date) AS start FROM exchange_rate_fetches "
            "WHERE source = ? AND currency = ? AND start_date <= ? AND end_date >= ?",
            (source, ccy, day, day),
        ).fetchone()["start"]
    if window:
        hit = _cached(source, ccy, window, day)
        if hit:
            return hit["value"], hit["rate_date"]
    rows = _fetch(source, ccy, start, day)  # no connection is open here
    if rows:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO exchange_rates (source, currency, rate_date, value, fetched_at) VALUES (?, ?, ?, ?, ?)",
                [(source, ccy, d, v, now) for d, v in rows],
            )
            # Today's rate may not be published yet: the window is complete
            # only up to yesterday.
            complete = min(day, (today_israel() - timedelta(days=1)).isoformat())
            conn.execute(
                "INSERT OR IGNORE INTO exchange_rate_fetches (source, currency, start_date, end_date, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, ccy, start, complete, now),
            )
    hit = _cached(source, ccy, start, day)
    return (hit["value"], hit["rate_date"]) if hit else None


def rate_to_ils(ccy: str, day: str) -> tuple[float, str] | None:
    """(ILS per unit of ccy, publication date) on or before `day`, clamped to
    today. None when no rate exists; raises RateUnavailable."""
    day = min(day, today_israel().isoformat())
    if ccy == "ILS":
        return 1.0, day
    if ccy not in KNOWN_CURRENCIES:
        return None
    if ccy in BOI_CURRENCIES:
        return _latest("boi", ccy, day)
    per_eur = _latest("ecb", ccy, day)
    if per_eur is None:
        return None
    eur = _latest("boi", "EUR", day)
    if eur is None:
        return None
    return _checked(eur[0] / per_eur[0], min(eur[1], per_eur[1]))


def _checked(value: float, rate_date: str) -> tuple[float, str] | None:
    return (value, rate_date) if _MIN_RATE <= value <= _MAX_RATE else None


def rate(ccy, ref, day) -> tuple[float, str | None] | None:
    """(rate per unit of ccy in ref, publication date), or None.
    Raises RateUnavailable when a site could not be reached."""
    ccy, ref = normalize_code(ccy), normalize_code(ref)
    if ccy is None or ref is None:
        return None
    if ccy == ref:
        return 1.0, None
    if not isinstance(day, str) or not _DATE_RE.fullmatch(day) or day < _EARLIEST_DATE:
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
    return _checked(x[0] / r[0], min(x[1], r[1]))


# The converted amount in reference currency `:ref` for SQL readers (stats,
# #73's tax totals), table alias `d`: the stored conversion while it is for the
# current reference, a document already in the reference currency at face
# value, and NULL (unconverted) otherwise. effective_conversion is the same
# rule for Python readers; keep the two together.
# Both rules trim the same characters (SQLite's trim() alone strips only
# spaces; model output can carry a newline or a tab).
_TRIM = " \t\r\n"
CONVERTED_AMOUNT_SQL = (
    "CASE WHEN d.conversion_ref = :ref THEN d.converted_amount "
    "WHEN upper(trim(d.currency, ' ' || char(9) || char(13) || char(10))) = :ref THEN d.total_amount END"
)
CONVERSION_COLUMNS = ("conversion_rate", "converted_amount", "conversion_rate_date", "conversion_ref")


def effective_conversion(row, ref) -> dict:
    """The four conversion columns as readers present them (CONVERTED_AMOUNT_SQL's rule)."""
    if row["conversion_ref"] == ref and ref is not None:
        return {c: row[c] for c in CONVERSION_COLUMNS}
    if ref is not None and (row["currency"] or "").strip(_TRIM).upper() == ref:
        return {"conversion_rate": 1.0, "converted_amount": row["total_amount"], "conversion_rate_date": None, "conversion_ref": ref}
    return dict.fromkeys(CONVERSION_COLUMNS)


_KEEP = object()


def conversion_update(currency=_KEEP, receipt_date=_KEEP, total_amount=_KEEP) -> tuple[str, list]:
    """SET clauses (and their parameters, in order) for an UPDATE that writes
    these columns: they keep the stored conversion while the UPDATE leaves the
    row's currency and date as they are, re-multiplied by the new total, and
    clear it otherwise. They compare with the row being written (SET
    expressions read the old row), never with an earlier read, so a write that
    raced an edit cannot keep the edit's rate on another currency. A column
    left out keeps its value."""
    params = []

    def val(column, value):
        if value is _KEEP:
            return column
        params.append(value)
        return "?"

    def same():
        return f"currency IS {val('currency', currency)} AND receipt_date IS {val('receipt_date', receipt_date)}"

    clauses = [f"{col} = CASE WHEN {same()} THEN {col} END"
               for col in ("conversion_rate", "conversion_rate_date", "conversion_ref")]
    # abs() of NULL is NULL and of inf fails the bound: both store NULL
    clauses.append(
        f"converted_amount = CASE WHEN {same()} AND abs({val('total_amount', total_amount)} * conversion_rate) < {_MAX_AMOUNT:.0e} "
        f"THEN ROUND({val('total_amount', total_amount)} * conversion_rate, 2) END"
    )
    return ", ".join(clauses), params


def _converted(total, value):
    if total is None or value is None or not abs(total * value) < _MAX_AMOUNT:
        return None
    return round(total * value, 2)


def reference_currency() -> str | None:
    """The reference code, or None when it is not one the sources can convert
    into (PATCH /settings refuses those, but .env, an older saved value or a
    restored backup can still hold one): nothing then converts, and no stored
    rate is overwritten."""
    code = normalize_code(get_setting("reference_currency"))
    if code not in REFERENCE_CURRENCIES:
        return None
    return code


def apply_conversion(doc_id: int) -> str:
    """Compute and store one document's conversion. Returns:
    - 'converted': stored, or already current (a stored rate whose
      conversion_ref is the reference is never refetched: every write of the
      currency or date clears it, see conversion_update);
    - 'no_rate': none exists (no currency, foreign without a date, unknown
      code, before the series); NULL is stored;
    - 'unavailable': a site was unreachable; nothing is written, so a good rate
      survives an outage and a missing one waits for the rates task;
    - 'superseded': the document (or the reference) changed, or the document
      was deleted, while the rate was fetched, so this result was dropped.
    An invalid reference_currency setting converts nothing ('no_rate', no write)."""
    ref = reference_currency()
    with get_connection() as conn:
        doc = conn.execute(
            "SELECT currency, receipt_date, total_amount, conversion_rate, conversion_rate_date, conversion_ref, "
            "converted_amount FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
    if doc is None:
        return "superseded"
    if ref is None:
        return "no_rate"
    if doc["conversion_rate"] is not None and doc["conversion_ref"] == ref:
        return "converted"
    try:
        found = rate(doc["currency"], ref, doc["receipt_date"])
    except RateUnavailable as e:
        logger.warning("No exchange rate for document %d (%s on %s): %s", doc_id, doc["currency"], doc["receipt_date"], e)
        return "unavailable"
    # The reference can change while the rate is fetched (the CAS below cannot
    # see it: an env var can set it); the new reference's pass may have run.
    if reference_currency() != ref:
        return "superseded"
    value, rate_date = found if found else (None, None)
    new_ref = ref if found else None
    total = doc["total_amount"]
    converted = _converted(total, value)
    if (value, rate_date, new_ref, converted) == (doc["conversion_rate"], doc["conversion_rate_date"], doc["conversion_ref"], doc["converted_amount"]):
        return "converted" if found else "no_rate"  # unchanged: no write, no FTS re-index
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE documents SET conversion_rate = ?, conversion_rate_date = ?, conversion_ref = ?, converted_amount = ? "
            "WHERE id = ? AND currency IS ? AND receipt_date IS ? AND total_amount IS ? "
            "AND conversion_rate IS ? AND conversion_ref IS ?",
            (value, rate_date, new_ref, converted, doc_id, doc["currency"], doc["receipt_date"], total,
             doc["conversion_rate"], doc["conversion_ref"]),
        )
        if cur.rowcount == 0:
            return "superseded"
    return "converted" if found else "no_rate"


def convert_pending() -> dict:
    """One pass of the rates task over documents with a currency and no usable
    rate (none yet, or computed for another reference). A source that turns
    out unreachable is backed off (_fetch), so the rest of the pass skips it
    without a request and the other source keeps converting. One document that
    raises is logged and skipped. An invalid reference_currency setting makes
    the pass a no-op."""
    counts = {"converted": 0, "no_rate": 0, "superseded": 0, "unavailable": 0, "error": 0}
    ref = reference_currency()
    if ref is None:
        logger.warning("reference_currency is not a currency the rate sources publish; exchange-rate pass skipped")
        return counts
    with get_connection() as conn:
        docs = conn.execute(
            "SELECT id, currency FROM documents WHERE is_deleted = 0 AND currency IS NOT NULL "
            "AND (conversion_rate IS NULL OR conversion_ref IS NOT ?) ORDER BY id",
            (ref,),
        ).fetchall()
    for doc in docs:
        try:
            status = apply_conversion(doc["id"])
        except Exception:
            logger.exception("Exchange rate for document %d failed", doc["id"])
            status = "error"
        counts[status] += 1
    return counts


async def run_rates_task() -> None:
    """Runs convert_pending at startup and then every RATES_TASK_INTERVAL_S
    (OUTAGE_RETRY_S after a pass that met an unreachable site), in a worker
    thread: a hung rate site must not block the event loop. The pipeline and
    a PATCH also convert their one document inline; an unreachable site
    delays the first of them by up to REQUEST_DEADLINE_S, and the rest fail at
    once for OUTAGE_BACKOFF_S. This task retries whatever they left."""
    logger.info("Exchange-rate task started")
    while True:
        delay = RATES_TASK_INTERVAL_S
        try:
            counts = await asyncio.to_thread(convert_pending)
            if any(counts.values()):
                logger.info("Exchange-rate pass: %s", counts)
            if counts.get("unavailable"):
                delay = OUTAGE_RETRY_S
        except Exception:
            logger.exception("Exchange-rate pass failed")
        await asyncio.sleep(delay)
