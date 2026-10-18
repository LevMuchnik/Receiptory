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
    assert currency.rate_to_ils("USD", "2026-10-12") == (3.078, "2026-10-12", "boi")
    calls = len(sources.calls)
    # rows from one fetch serve every later lookup inside the window, without HTTP
    assert currency.rate_to_ils("USD", "2026-10-12") == (3.078, "2026-10-12", "boi")
    assert currency.rate_to_ils("USD", "2026-10-09") == (3.055, "2026-10-09", "boi")
    assert len(sources.calls) == calls


def test_weekend_takes_the_last_published_rate(sources):
    # 2026-10-10/11 have no publication: Friday's rate applies
    assert currency.rate_to_ils("USD", "2026-10-11") == (3.055, "2026-10-09", "boi")


def test_future_date_is_clamped_to_today(sources):
    # a misread year: the latest rate on or before today, not "no rate forever"
    assert currency.rate_to_ils("USD", "2027-10-12") == (3.078, "2026-10-12", "boi")
    assert all(call[3] <= "2026-10-18" for call in sources.calls)


def test_no_rate_before_the_window(sources):
    assert currency.rate_to_ils("USD", "2020-01-01") is None


def test_ecb_cross_rate_for_a_currency_the_boi_does_not_publish(sources):
    rate, rate_date, source = currency.rate_to_ils("PLN", "2026-10-12")
    assert rate == pytest.approx(3.48 / 4.35)
    assert (rate_date, source) == ("2026-10-12", "boi+ecb")
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
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0)
    currency.apply_conversion(doc)
    with get_connection() as conn:
        conn.execute("UPDATE documents SET currency = 'ILS' WHERE id = ?", (doc,))
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


def test_converted_amount_follows_an_amount_edit(sources):
    doc = _doc(currency="USD", receipt_date="2026-10-12", total_amount=10.0)
    currency.apply_conversion(doc)
    with get_connection() as conn:
        conn.execute("UPDATE documents SET total_amount = 20.0 WHERE id = ?", (doc,))
    currency.refresh_converted_amount(doc)
    assert _conv(doc)["converted_amount"] == 61.56
    assert len(sources.calls) == 1


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
    assert result == {"converted": 0, "no_rate": 0, "superseded": 0, "unavailable": 1}
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
