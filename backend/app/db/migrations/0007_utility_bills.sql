-- Format 1.2: utility bills (PLAN 4.12). City Power gas and electric
-- first, quarterly water later.
--
-- Imported tables: the importer's apply step fills them (newest run in run
-- order wins per utility_key and period_end, tombstones delete), and
-- `cli reapply` rebuilds them from stored runs. No account number, meter
-- number, service address or customer name.
CREATE TABLE utility_bills (
    bill_key              TEXT    PRIMARY KEY,  -- '<utility_key>:<period_end>'
    utility_key           TEXT    NOT NULL,
    provider              TEXT    NOT NULL,
    bill_date             TEXT    NOT NULL,
    period_start          TEXT    NOT NULL,
    period_end            TEXT    NOT NULL,
    days                  INTEGER NOT NULL CHECK (days >= 1),
    total_cents           INTEGER NOT NULL CHECK (total_cents >= 0),
    due_date              TEXT,
    paid_from_account_key TEXT,
    heating_degree_days   INTEGER CHECK (heating_degree_days IS NULL OR heating_degree_days >= 0),
    estimated_read        INTEGER CHECK (estimated_read IS NULL OR estimated_read IN (0, 1)),
    note                  TEXT,
    run_id                TEXT    NOT NULL,
    UNIQUE (utility_key, period_end),
    CHECK (period_start < period_end)
) STRICT;
CREATE INDEX utility_bills_bill_date ON utility_bills (utility_key, bill_date);

-- One line per service per bill. usage in micro-units of `unit`.
CREATE TABLE utility_bill_lines (
    bill_key            TEXT    NOT NULL REFERENCES utility_bills (bill_key) ON DELETE CASCADE,
    service             TEXT    NOT NULL CHECK (service IN ('electric', 'gas', 'water', 'sewer',
                                    'stormwater', 'other')),
    usage_micro         INTEGER NOT NULL CHECK (usage_micro >= 0),
    unit                TEXT    NOT NULL CHECK (unit IN ('kwh', 'therms', 'gallons', 'ccf', 'none')),
    charges_cents       INTEGER NOT NULL CHECK (charges_cents >= 0),
    fixed_charges_cents INTEGER CHECK (fixed_charges_cents IS NULL
                                    OR fixed_charges_cents BETWEEN 0 AND charges_cents),
    PRIMARY KEY (bill_key, service)
) STRICT;

-- Credits on a bill, in the export's order (label, then amount). Always negative.
CREATE TABLE utility_bill_credits (
    bill_key     TEXT    NOT NULL REFERENCES utility_bills (bill_key) ON DELETE CASCADE,
    seq          INTEGER NOT NULL CHECK (seq >= 1),
    label        TEXT    NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents < 0),
    PRIMARY KEY (bill_key, seq)
) STRICT;
