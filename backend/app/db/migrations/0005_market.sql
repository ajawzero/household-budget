-- Phase 5 market data (PLAN 3.5, 6.2).
--
-- prices: daily closes per ticker, upserted on (symbol, date). Re-runs change
-- nothing. Closes are raw (not split adjusted). split_factor_micro is the
-- split that took effect on that date, in micro-units (one million = no split,
-- two million = 2 for 1).
-- Rows are never deleted, so the last close is kept when a provider fails.
CREATE TABLE prices (
    symbol             TEXT    NOT NULL,
    date               TEXT    NOT NULL,
    close_micro        INTEGER NOT NULL CHECK (close_micro > 0),
    adj_close_micro    INTEGER,
    split_factor_micro INTEGER NOT NULL CHECK (split_factor_micro > 0),
    div_cash_micro     INTEGER NOT NULL DEFAULT 0,
    source             TEXT    NOT NULL CHECK (source IN ('tiingo', 'yahoo', 'constant')),
    fetched_at         TEXT    NOT NULL,
    PRIMARY KEY (symbol, date)
) STRICT;

-- symbol_map: overrides only, keyed by account and holding. Defaults come
-- from the rules in app/market/modes.py. Names never match across accounts.
-- App-owned: no foreign key, so overrides survive `cli reapply`.
-- note is typed text: it passes detect_sensitive before it is stored.
CREATE TABLE symbol_map (
    account_key TEXT    NOT NULL,
    holding_key TEXT    NOT NULL,
    price_mode  TEXT    NOT NULL CHECK (price_mode IN ('api', 'constant', 'statement')),
    symbol      TEXT,
    note        TEXT,
    updated_by  INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (account_key, holding_key),
    CHECK (price_mode = 'api' OR symbol IS NULL)
) STRICT;
