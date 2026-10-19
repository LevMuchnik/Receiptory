-- 010_currency_conversion.sql
-- Issue #72: convert document amounts into reference_currency.
--
-- exchange_rates caches published rates, never "no rate": a published rate
-- does not change, so rows never expire. `value` is per one unit of
-- `currency` (UNIT_MULT already applied), in the source's own terms:
--   source 'boi': ILS per unit (Bank of Israel representative rate)
--   source 'ecb': units of `currency` per EUR (European Central Bank), used
--                 only for currencies the BoI does not publish, crossed with
--                 the BoI's EUR rate.
CREATE TABLE IF NOT EXISTS exchange_rates (
    source TEXT NOT NULL,
    currency TEXT NOT NULL,
    rate_date TEXT NOT NULL,
    value REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, currency, rate_date)
);

-- The windows fetched into exchange_rates, one row per successful fetch. A
-- date inside one is answered from the cache: every rate published in the
-- window is there. Cached rows on both sides of a date prove nothing (two
-- fetches that do not overlap leave unfetched days between them).
CREATE TABLE IF NOT EXISTS exchange_rate_fetches (
    source TEXT NOT NULL,
    currency TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, currency, start_date, end_date)
);

-- conversion_rate (migration 001) is per unit of the document's currency into
-- conversion_ref; conversion_rate_date is the publication date of the rate
-- used (a Saturday receipt carries Friday's). A rate whose conversion_ref is
-- not the current reference_currency is stale and is not used.
ALTER TABLE documents ADD COLUMN conversion_rate_date TEXT DEFAULT NULL;
ALTER TABLE documents ADD COLUMN conversion_ref TEXT DEFAULT NULL;
