"""Issue #72: exchange rates and document conversion.

No test touches the network: the two fetchers are replaced at
backend.currency, and the CSV parsers get the bytes the live endpoints
returned on 2026-10-18 (trimmed to the columns they read).
"""
import threading
from datetime import date

import pytest

from backend import currency
from backend.config import set_setting

# conftest replaces both fetchers for every test; these are the real ones,
# taken at import, for the tests of the HTTP boundary itself.
_REAL_FETCH_BOI = currency.fetch_boi
_REAL_FETCH_ECB = currency.fetch_ecb
from backend.database import get_connection

BOI_USD_CSV = """SERIES_CODE,FREQ,BASE_CURRENCY,COUNTER_CURRENCY,UNIT_MEASURE,DATA_TYPE,DATA_SOURCE,TIME_COLLECT,CONF_STATUS,PUB_WEBSITE,UNIT_MULT,COMMENTS,TIME_PERIOD,OBS_VALUE,RELEASE_STATUS
RER_USD_ILS,D,USD,ILS,ILS,OF00,BOI_MRKT,V,F,Y,0,,2026-10-08,3.084,YP
RER_USD_ILS,D,USD,ILS,ILS,OF00,BOI_MRKT,V,F,Y,0,,2026-10-09,3.055,YP
RER_USD_ILS,D,USD,ILS,ILS,OF00,BOI_MRKT,V,F,Y,0,,2026-10-12,3.078,YP
"""
BOI_JPY_CSV = """SERIES_CODE,UNIT_MULT,TIME_PERIOD,OBS_VALUE
RER_JPY_ILS,2,2026-10-13,1.9446
"""
BOI_LBP_CSV = """SERIES_CODE,UNIT_MULT,TIME_PERIOD,OBS_VALUE
RER_LBP_ILS,1,2026-10-15,0.0003
"""
ECB_CZK_CSV = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS,UNIT,UNIT_MULT
EXR.D.CZK.EUR.SP00.A,D,CZK,EUR,SP00,A,2026-10-14,24.357,A,CZK,0
"""


def test_parse_boi_applies_unit_mult():
    assert currency.parse_csv(BOI_USD_CSV) == [("2026-10-08", 3.084), ("2026-10-09", 3.055), ("2026-10-12", 3.078)]
    # JPY is quoted per 100 yen and LBP per 10 pounds: the stored rate is per unit
    assert currency.parse_csv(BOI_JPY_CSV) == [("2026-10-13", pytest.approx(0.019446))]
    assert currency.parse_csv(BOI_LBP_CSV) == [("2026-10-15", pytest.approx(0.00003))]


def test_parse_ecb_and_empty():
    assert currency.parse_csv(ECB_CZK_CSV) == [("2026-10-14", 24.357)]
    assert currency.parse_csv("") == []
    # a row with a missing or non-numeric value is skipped, not a crash
    assert currency.parse_csv("TIME_PERIOD,OBS_VALUE,UNIT_MULT\n2026-10-14,,0\n2026-10-15,NaN?,0\n") == []


@pytest.mark.parametrize("raw,code", [
    ("usd", "USD"), (" ILS ", "ILS"), ("NIS", "ILS"), ("₪", "ILS"), ('ש"ח', "ILS"),
    ("$", "USD"), ("€", "EUR"), ("£", "GBP"),
    ("dollars", None), ("", None), (None, None), (12, None),
])
def test_normalize_code(raw, code):
    assert currency.normalize_code(raw) == code


class FakeSources:
    """Stands in for both rate sites. Each series is {date: value}; a fetch
    returns the rows inside the requested window, like the real APIs."""

    def __init__(self, boi=None, ecb=None, fail=False):
        self.boi = boi or {}
        self.ecb = ecb or {}
        self.fail = fail
        self.calls = []

    def _window(self, series, start, end):
        return sorted((d, v) for d, v in series.items() if start <= d <= end)

    def fetch_boi(self, ccy, start, end):
        self.calls.append(("boi", ccy, start, end))
        if self.fail:
            raise currency.RateUnavailable("boom")
        return self._window(self.boi.get(ccy, {}), start, end)

    def fetch_ecb(self, ccy, start, end):
        self.calls.append(("ecb", ccy, start, end))
        if self.fail:
            raise currency.RateUnavailable("boom")
        return self._window(self.ecb.get(ccy, {}), start, end)


@pytest.fixture
def sources(db_path, monkeypatch):
    fake = FakeSources(
        boi={
            "USD": {"2026-10-08": 3.084, "2026-10-09": 3.055, "2026-10-12": 3.078},
            "EUR": {"2026-10-09": 3.50, "2026-10-12": 3.48},
        },
        ecb={"PLN": {"2026-10-09": 4.375, "2026-10-12": 4.35}},
    )
    monkeypatch.setattr(currency, "fetch_boi", fake.fetch_boi)
    monkeypatch.setattr(currency, "fetch_ecb", fake.fetch_ecb)
    monkeypatch.setattr(currency, "today_israel", lambda: date(2026, 10, 18))
    return fake


def test_rate_to_ils_exact_then_cached(sources):
    assert currency.rate_to_ils("USD", "2026-10-12") == (3.078, "2026-10-12")
    calls = len(sources.calls)
    # rows from one fetch serve every later lookup inside the window, without HTTP
    assert currency.rate_to_ils("USD", "2026-10-12") == (3.078, "2026-10-12")
    assert currency.rate_to_ils("USD", "2026-10-09") == (3.055, "2026-10-09")
    assert len(sources.calls) == calls


def test_weekend_takes_the_last_published_rate(sources):
    # 2026-10-10/11 have no publication: Friday's rate applies
    assert currency.rate_to_ils("USD", "2026-10-11") == (3.055, "2026-10-09")


def test_future_date_is_clamped_to_today(sources):
    # a misread year: the latest rate on or before today, not "no rate forever"
    assert currency.rate_to_ils("USD", "2027-10-12") == (3.078, "2026-10-12")
    assert all(call[3] <= "2026-10-18" for call in sources.calls)


def test_no_rate_before_the_window(sources):
    assert currency.rate_to_ils("USD", "2020-01-01") is None


def test_ecb_cross_rate_for_a_currency_the_boi_does_not_publish(sources):
    rate, rate_date = currency.rate_to_ils("PLN", "2026-10-12")
    assert rate == pytest.approx(3.48 / 4.35)
    assert rate_date == "2026-10-12"
    # the BoI is never asked for a series it does not publish
    assert ("boi", "PLN") not in [c[:2] for c in sources.calls]


def test_unreachable_source_raises_rate_unavailable(sources):
    sources.fail = True
    with pytest.raises(currency.RateUnavailable):
        currency.rate_to_ils("USD", "2026-10-12")


def test_rate_between_two_currencies(sources):
    assert currency.rate("ILS", "ILS", None)[0] == 1.0
    assert currency.rate("USD", "USD", None)[0] == 1.0
    assert currency.rate("USD", "ILS", "2026-10-12")[0] == 3.078
    # a non-ILS reference converts through ILS
    assert currency.rate("USD", "EUR", "2026-10-12")[0] == pytest.approx(3.078 / 3.48)
    assert currency.rate("ILS", "USD", "2026-10-12")[0] == pytest.approx(1 / 3.078)
    # a foreign currency needs a date; an unknown code has no rate
    assert currency.rate("USD", "ILS", None) is None
    assert currency.rate("XYZ", "ILS", "2026-10-12") is None
    assert currency.rate("USD", "ILS", "12/10/2026") is None


def _doc(**kw):
    row = {"original_filename": "x.pdf", "file_hash": kw.pop("file_hash", "a" * 64), "file_size_bytes": 1,
           "status": "processed", "submission_channel": "web_upload"}
    row.update(kw)
    with get_connection() as conn:
        conn.execute(f"INSERT INTO documents ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _conv(doc_id):
    with get_connection() as conn:
        return dict(conn.execute(
            "SELECT conversion_rate, converted_amount, conversion_rate_date, conversion_ref FROM documents WHERE id = ?",
            (doc_id,)).fetchone())


def test_contract_foreign_document_gets_the_per_unit_rate(sources):
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=100.0)
    assert currency.apply_conversion(doc) == "converted"
    assert _conv(doc) == {"conversion_rate": 3.078, "converted_amount": 307.8,
                          "conversion_rate_date": "2026-10-12", "conversion_ref": "ILS"}


def test_contract_reference_currency_is_exactly_one_without_a_fetch(sources):
    # no date needed, and no request made
    doc = _doc(currency="ILS", receipt_date=None, total_amount=42.5)
    assert currency.apply_conversion(doc) == "converted"
    assert _conv(doc) == {"conversion_rate": 1.0, "converted_amount": 42.5,
                          "conversion_rate_date": None, "conversion_ref": "ILS"}
    assert sources.calls == []


@pytest.mark.parametrize("fields", [
    {"currency": None, "receipt_date": "2026-10-12"},
    {"currency": "USD", "receipt_date": None},
    {"currency": "dollars", "receipt_date": "2026-10-12"},
    {"currency": "USD", "receipt_date": "2020-01-01"},
])
def test_contract_unknown_rate_is_null_never_zero(sources, fields):
    doc = _doc(total_amount=10.0, **fields)
    assert currency.apply_conversion(doc) == "no_rate"
    assert _conv(doc)["conversion_rate"] is None
    assert _conv(doc)["converted_amount"] is None


def test_contract_unreachable_source_is_null_and_reported(sources):
    sources.fail = True
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0)
    assert currency.apply_conversion(doc) == "unavailable"
    assert _conv(doc)["conversion_rate"] is None


def test_contract_recomputes_after_a_currency_change(sources):
    """The writer that changes the currency clears the conversion in the same
    UPDATE (pipeline, PATCH: tested there); apply_conversion then recomputes.
    A stored, current rate is otherwise never refetched."""
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0)
    currency.apply_conversion(doc)
    with get_connection() as conn:
        conn.execute("UPDATE documents SET currency = 'ILS', conversion_rate = NULL, converted_amount = NULL, "
                     "conversion_rate_date = NULL, conversion_ref = NULL WHERE id = ?", (doc,))
    currency.apply_conversion(doc)
    assert _conv(doc)["conversion_rate"] == 1.0


def test_a_conversion_superseded_by_an_edit_writes_nothing(sources, monkeypatch):
    """The rate is fetched with no connection open; if the currency changed in
    the meantime, the stale result must not overwrite the edit's own rate."""
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0)
    real = sources.fetch_boi

    def fetch_while_edited(ccy, start, end):
        with get_connection() as conn:  # a writer gets in: no lock is held here
            conn.execute("UPDATE documents SET currency = 'ILS', conversion_rate = 1.0 WHERE id = ?", (doc,))
        return real(ccy, start, end)

    monkeypatch.setattr(currency, "fetch_boi", fetch_while_edited)
    assert currency.apply_conversion(doc) == "superseded"
    assert _conv(doc)["conversion_rate"] == 1.0


def test_convert_pending_fills_missing_and_stale_reference(sources, monkeypatch):
    usd = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0, file_hash="b" * 64)
    ils = _doc(currency="ILS", receipt_date="2026-10-12", total_amount=5.0, file_hash="c" * 64)
    none = _doc(currency=None, total_amount=5.0, file_hash="d" * 64)
    assert currency.convert_pending()["converted"] == 2
    assert _conv(usd)["conversion_rate"] == 3.078 and _conv(ils)["conversion_rate"] == 1.0
    assert _conv(none)["conversion_rate"] is None

    # a reference change by env (no settings hook): every rate is recomputed
    monkeypatch.setenv("RECEIPTORY_REFERENCE_CURRENCY", "USD")
    assert currency.convert_pending()["converted"] == 2
    assert _conv(usd)["conversion_rate"] == 1.0
    assert _conv(ils) == {"conversion_rate": pytest.approx(1 / 3.078), "converted_amount": pytest.approx(5 / 3.078, abs=0.01),
                          "conversion_rate_date": "2026-10-12", "conversion_ref": "USD"}
    # nothing left to do
    assert currency.convert_pending()["converted"] == 0


def test_convert_pending_stops_at_the_first_outage(sources):
    for h in "efg":
        _doc(currency="USD", receipt_date="2026-10-12", total_amount=1.0, file_hash=h * 64)
    sources.fail = True
    result = currency.convert_pending()
    assert result == {"converted": 0, "no_rate": 0, "superseded": 0, "unavailable": 1, "error": 0}
    assert len(sources.calls) == 1


def test_reference_setting_is_normalized(sources):
    set_setting("reference_currency", "usd")
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0)
    currency.apply_conversion(doc)
    assert _conv(doc)["conversion_rate"] == 1.0 and _conv(doc)["conversion_ref"] == "USD"


def test_cache_upsert_is_safe_under_concurrent_fetches(sources):
    errors = []

    def lookup():
        try:
            currency.rate_to_ils("USD", "2026-10-12")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=lookup) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


async def test_rates_task_runs_a_pass_then_waits_three_hours(db_path, monkeypatch):
    """The task survives a pass that raises, and sleeps the 3h cadence (R4)."""
    import asyncio

    passes, sleeps = [], []

    def flaky_pass():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("database hiccup")
        return {"converted": 0, "no_rate": 0, "superseded": 0, "unavailable": 0}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(currency, "convert_pending", flaky_pass)
    monkeypatch.setattr(currency.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await currency.run_rates_task()
    assert len(passes) == 2
    assert sleeps == [3 * 3600, 3 * 3600]



def test_parse_skips_an_absurd_unit_mult():
    # 10**99999 would overflow the division; a row like that is not a rate
    assert currency.parse_csv("TIME_PERIOD,OBS_VALUE,UNIT_MULT\n2026-10-14,3.1,99999\n2026-10-15,3.2,0\n") == [("2026-10-15", 3.2)]


def test_a_date_before_1900_has_no_rate(sources):
    assert currency.rate("USD", "ILS", "0001-01-05") is None
    assert sources.calls == []


def test_weekend_is_answered_from_cache_once_a_later_day_is_cached(sources):
    currency.rate_to_ils("USD", "2026-10-12")
    calls = len(sources.calls)
    assert currency.rate_to_ils("USD", "2026-10-11") == (3.055, "2026-10-09")
    assert len(sources.calls) == calls


def test_a_failed_fetch_keeps_a_current_rate(sources):
    """Red team: an outage during a same-currency reprocess must not erase a good rate."""
    doc = _doc(currency="USD", receipt_date="2026-10-11", total_amount=10.0)
    currency.apply_conversion(doc)
    sources.fail = True
    # the stored rate is for the current currency, date and reference: no fetch
    assert currency.apply_conversion(doc) == "converted"
    assert _conv(doc)["conversion_rate"] == 3.055


def test_an_outage_does_not_overwrite_an_existing_rate(sources, monkeypatch):
    doc = _doc(currency="USD", receipt_date="2026-10-11", total_amount=10.0,
               conversion_rate=3.0, converted_amount=30.0, conversion_ref="USD")  # stale reference
    sources.fail = True
    assert currency.apply_conversion(doc) == "unavailable"
    assert _conv(doc)["conversion_rate"] == 3.0  # left for the rates task, not blanked


def test_the_write_checks_the_reference_it_started_from(sources, monkeypatch):
    """Plan R2/D4: a conversion that lost a race to a newer one writes nothing."""
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0)
    real = sources.fetch_boi

    def fetch_while_converted(ccy, start, end):
        with get_connection() as conn:
            conn.execute("UPDATE documents SET conversion_rate = 9.9, converted_amount = 99.0, conversion_ref = 'ILS' WHERE id = ?", (doc,))
        return real(ccy, start, end)

    monkeypatch.setattr(currency, "fetch_boi", fetch_while_converted)
    assert currency.apply_conversion(doc) == "superseded"
    assert _conv(doc)["conversion_rate"] == 9.9


def test_a_bad_document_does_not_stop_the_pass(sources, monkeypatch):
    first = _doc(currency="USD", receipt_date="2026-10-12", total_amount=1.0, file_hash="1" * 64)
    second = _doc(currency="USD", receipt_date="2026-10-12", total_amount=1.0, file_hash="2" * 64)
    real = currency.apply_conversion

    def flaky(doc_id):
        if doc_id == first:
            raise RuntimeError("database is locked")
        return real(doc_id)

    monkeypatch.setattr(currency, "apply_conversion", flaky)
    result = currency.convert_pending()
    assert result["error"] == 1 and result["converted"] == 1
    assert _conv(second)["conversion_rate"] == 3.078


def test_an_ancient_date_does_not_stop_the_pass(sources):
    _doc(currency="USD", receipt_date="0001-01-05", total_amount=1.0, file_hash="3" * 64)
    later = _doc(currency="USD", receipt_date="2026-10-12", total_amount=1.0, file_hash="4" * 64)
    currency.convert_pending()
    assert _conv(later)["conversion_rate"] == 3.078


def test_reference_currency_documents_convert_despite_an_outage(sources):
    _doc(currency="USD", receipt_date="2026-10-12", total_amount=1.0, file_hash="5" * 64)
    ils = _doc(currency="ILS", receipt_date="2026-10-12", total_amount=5.0, file_hash="6" * 64)
    sources.fail = True
    currency.convert_pending()
    assert _conv(ils)["conversion_rate"] == 1.0
    assert len(sources.calls) == 1  # no second request after the outage


def test_an_invalid_reference_setting_changes_nothing(sources):
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0)
    currency.apply_conversion(doc)
    set_setting("reference_currency", "Shekel")
    assert currency.convert_pending() == {"converted": 0, "no_rate": 0, "superseded": 0, "unavailable": 0, "error": 0}
    assert _conv(doc)["conversion_rate"] == 3.078


def test_unchanged_rows_are_not_rewritten(sources):
    """Every UPDATE fires the FTS trigger, which re-indexes the whole document."""
    doc = _doc(currency="XYZ", receipt_date="2026-10-12", total_amount=10.0)
    with get_connection() as conn:
        conn.execute("CREATE TABLE upd_log (n INTEGER)")
        conn.execute("CREATE TRIGGER count_upd AFTER UPDATE ON documents BEGIN INSERT INTO upd_log VALUES (1); END")
    currency.convert_pending()
    currency.convert_pending()
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM upd_log").fetchone()[0] == 0
    assert _conv(doc)["conversion_rate"] is None


class _Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


@pytest.mark.parametrize("status,text,expected", [
    (200, "TIME_PERIOD,OBS_VALUE,UNIT_MULT\n2026-10-15,3.103,0\n", [("2026-10-15", 3.103)]),
    (404, "<html>error</html>", []),  # no observations in the window, or no such series
])
def test_http_status_classification(monkeypatch, status, text, expected):
    monkeypatch.setattr(currency.httpx, "get", lambda url, params, timeout: _Resp(status, text))
    assert _REAL_FETCH_BOI("USD", "2026-10-01", "2026-10-15") == expected


@pytest.mark.parametrize("status", [429, 500, 503])
def test_other_http_errors_are_an_outage(monkeypatch, status):
    monkeypatch.setattr(currency.httpx, "get", lambda url, params, timeout: _Resp(status))
    with pytest.raises(currency.RateUnavailable):
        _REAL_FETCH_ECB("PLN", "2026-10-01", "2026-10-15")


def test_a_connection_error_is_an_outage(monkeypatch):
    import httpx

    def boom(url, params, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(currency.httpx, "get", boom)
    with pytest.raises(currency.RateUnavailable):
        _REAL_FETCH_BOI("USD", "2026-10-01", "2026-10-15")


def test_each_site_gets_its_own_query(monkeypatch):
    seen = []
    monkeypatch.setattr(currency.httpx, "get", lambda url, params, timeout: seen.append((url, params)) or _Resp(404))
    _REAL_FETCH_BOI("USD", "2026-10-01", "2026-10-15")
    _REAL_FETCH_ECB("PLN", "2026-10-01", "2026-10-15")
    assert seen[0] == (currency.BOI_URL.format(ccy="USD"), {"startperiod": "2026-10-01", "endperiod": "2026-10-15", "format": "csv"})
    assert seen[1] == (currency.ECB_URL.format(ccy="PLN"), {"startPeriod": "2026-10-01", "endPeriod": "2026-10-15", "format": "csvdata"})


def test_the_app_starts_the_rates_task(tmp_data_dir, db_path, monkeypatch):
    """The backfill and every retry depend on this task alone (no settings hook)."""
    import asyncio
    from fastapi.testclient import TestClient
    import backend.backup.cloud_auth as cloud_auth
    import backend.backup.scheduler as scheduler
    import backend.ingestion.gmail as gmail
    import backend.ingestion.telegram as telegram
    import backend.ingestion.watched_folder as folder
    import backend.processing.queue as queue
    from backend.main import create_app

    started = []

    async def idle(*_args):
        await asyncio.Event().wait()

    async def rates():
        started.append(True)
        await asyncio.Event().wait()

    async def noop(*_args):
        return None

    for mod, name in [(queue, "run_queue_loop"), (scheduler, "run_backup_scheduler"), (gmail, "run_gmail_poller"), (folder, "run_watched_folder")]:
        monkeypatch.setattr(mod, name, idle)
    monkeypatch.setattr(telegram, "start_telegram_bot", noop)
    monkeypatch.setattr(telegram, "stop_telegram_bot", noop)
    monkeypatch.setattr(cloud_auth, "restore_rclone_config", lambda: None)
    monkeypatch.setattr(currency, "run_rates_task", rates)
    with TestClient(create_app(str(tmp_data_dir), run_background=True)):
        pass
    assert started == [True]


def test_a_gap_between_two_fetches_is_fetched_not_guessed(sources):
    """Re-review F1: two fetch windows that do not overlap leave unfetched
    weekdays between them. A row before the date and one after it do not make
    the gap a weekend: a backlog processed out of date order hit this."""
    sources.boi["USD"] = {"2026-01-12": 3.1, "2026-01-15": 3.2, "2026-01-20": 3.25, "2026-02-02": 3.3}
    currency.rate_to_ils("USD", "2026-01-12")  # caches 2025-12-29 .. 2026-01-12
    currency.rate_to_ils("USD", "2026-02-02")  # caches 2026-01-19 .. 2026-02-02
    assert currency.rate_to_ils("USD", "2026-01-15") == (3.2, "2026-01-15")


def test_a_day_inside_a_fetched_window_needs_no_fetch(sources):
    currency.rate_to_ils("USD", "2026-10-12")
    calls = len(sources.calls)
    # 10-10 and 10-11 are a weekend inside the window fetched for 10-12
    assert currency.rate_to_ils("USD", "2026-10-10") == (3.055, "2026-10-09")
    assert len(sources.calls) == calls


def test_a_window_ending_today_is_not_remembered_as_complete(sources):
    """Fix-delta F3: today's rate is published in the afternoon. A morning
    fetch must not mark today as answered by yesterday's rate."""
    sources.boi["USD"] = {"2026-10-16": 3.0}
    assert currency.rate_to_ils("USD", "2026-10-18") == (3.0, "2026-10-16")
    sources.boi["USD"]["2026-10-18"] = 3.1  # published later that day
    assert currency.rate_to_ils("USD", "2026-10-18") == (3.1, "2026-10-18")
    # the days before today in that window stay answered from the cache
    calls = len(sources.calls)
    assert currency.rate_to_ils("USD", "2026-10-17") == (3.0, "2026-10-16")
    assert len(sources.calls) == calls


def test_an_empty_answer_is_logged_and_not_remembered(sources, caplog):
    """Re-review F7: a 14-day window with no rate means the series moved or the
    code is unknown; say so, and never cache it as "no rate"."""
    assert currency.rate_to_ils("CHF", "2026-10-12") is None
    assert "no rates" in caplog.text
    calls = len(sources.calls)
    assert currency.rate_to_ils("CHF", "2026-10-12") is None
    assert len(sources.calls) == calls + 1


def test_parse_skips_values_that_are_not_a_plausible_rate():
    """Re-review F4: a value that underflows to 0 would divide by zero in a
    cross rate and store 0, which the contract forbids."""
    text = ("TIME_PERIOD,OBS_VALUE,UNIT_MULT\n2026-10-12,1e-320,0\n2026-10-13,5e-7,0\n"
            "2026-10-14,2e6,0\n2026-10-15,3.2,0\n")
    assert currency.parse_csv(text) == [("2026-10-15", 3.2)]


def test_a_huge_total_converts_to_null_not_infinity(sources):
    """Re-review F3: inf in converted_amount makes every page that lists the
    document, and the dashboard sum, fail to serialize (500)."""
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=1e308)
    assert currency.apply_conversion(doc) == "converted"
    conv = _conv(doc)
    assert conv["conversion_rate"] == 3.078
    assert conv["converted_amount"] is None


def _set_with(doc_id, **values):
    sql, params = currency.conversion_update(**values)
    cols = [k for k in values]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE documents SET {', '.join(f'{c} = ?' for c in cols)}{', ' if cols else ''}{sql} WHERE id = ?",
            [values[c] for c in cols] + params + [doc_id],
        )


@pytest.mark.parametrize("values,expected", [
    ({}, (3.0, 30.0, "2026-10-12", "ILS")),
    ({"currency": "USD", "receipt_date": "2026-10-12"}, (3.0, 30.0, "2026-10-12", "ILS")),
    ({"total_amount": 20.0}, (3.0, 60.0, "2026-10-12", "ILS")),
    ({"total_amount": None}, (3.0, None, "2026-10-12", "ILS")),
    ({"total_amount": 1e308}, (3.0, None, "2026-10-12", "ILS")),
    ({"currency": "EUR"}, (None, None, None, None)),
    ({"receipt_date": "2026-10-13"}, (None, None, None, None)),
    ({"currency": None}, (None, None, None, None)),
])
def test_conversion_update_compares_with_the_row_it_writes(db_path, values, expected):
    """Re-review F2: keep the conversion only while this UPDATE leaves the
    currency and date as the row has them, never as an earlier read had them."""
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0, conversion_rate=3.0,
               converted_amount=30.0, conversion_rate_date="2026-10-12", conversion_ref="ILS")
    _set_with(doc, **values)
    conv = _conv(doc)
    assert (conv["conversion_rate"], conv["converted_amount"], conv["conversion_rate_date"], conv["conversion_ref"]) == expected
