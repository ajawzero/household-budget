-- Phase 5: paychecks, contributions and limits (PLAN 3.3), and the inputs for
-- home, vehicle and loan estimates (PLAN 3.9, 6.3).
--
-- paychecks and contributions are imported tables: the importer's apply step
-- fills them, and `cli reapply` rebuilds them from stored runs. No employer
-- name and no deposit account.
CREATE TABLE paychecks (
    paycheck_key           TEXT    PRIMARY KEY,
    person_key             TEXT    NOT NULL,
    pay_date               TEXT    NOT NULL,
    period_start           TEXT,
    period_end             TEXT,
    kind                   TEXT    NOT NULL CHECK (kind IN ('regular', 'bonus', 'draw')),
    seq                    INTEGER NOT NULL CHECK (seq >= 1),
    gross_cents            INTEGER NOT NULL,
    net_cents              INTEGER NOT NULL,
    k401_cents             INTEGER,
    k401_milli_pct         INTEGER,
    hsa_employee_cents     INTEGER,
    hsa_employer_cents     INTEGER,
    pretax_health_cents    INTEGER,
    ytd_k401_cents         INTEGER,
    ytd_hsa_employee_cents INTEGER,
    ytd_hsa_employer_cents INTEGER,
    run_id                 TEXT    NOT NULL,
    UNIQUE (person_key, pay_date, kind, seq)
) STRICT;
CREATE INDEX paychecks_person ON paychecks (person_key, pay_date);

CREATE TABLE contributions (
    contribution_key TEXT    PRIMARY KEY,
    person_key       TEXT    NOT NULL,
    account_key      TEXT    NOT NULL,
    kind             TEXT    NOT NULL CHECK (kind IN ('401k_employee', '401k_employer',
                                 'hsa_employee', 'hsa_employer', 'ira', '529')),
    source           TEXT    NOT NULL CHECK (source IN ('current_employer', 'prior_employer',
                                 'personal', 'gift')),
    date             TEXT    NOT NULL,
    amount_cents     INTEGER NOT NULL,
    paycheck_key     TEXT,
    run_id           TEXT    NOT NULL,
    UNIQUE (person_key, account_key, kind, source, date)
) STRICT;
CREATE INDEX contributions_person ON contributions (person_key, date);

-- App-owned (kept by `cli reapply`). One row per year and kind. Seeded from
-- IRS figures. confirmed_at stays null until an admin confirms in Settings.
CREATE TABLE contribution_limits (
    year              INTEGER NOT NULL,
    kind              TEXT    NOT NULL CHECK (kind IN ('k401_deferral', 'k401_catchup_50',
                                  'k401_catchup_60_63', 'k415c_total', 'hsa_self',
                                  'hsa_family', 'hsa_catchup_55')),
    limit_cents       INTEGER NOT NULL CHECK (limit_cents >= 0),
    includes_employer INTEGER NOT NULL CHECK (includes_employer IN (0, 1)),
    source_url        TEXT,
    confirmed_at      TEXT,
    confirmed_by      INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at        TEXT    NOT NULL,
    PRIMARY KEY (year, kind)
) STRICT;

INSERT INTO contribution_limits (year, kind, limit_cents, includes_employer, source_url, updated_at)
VALUES
    (2026, 'k401_deferral', 2450000, 0,
     'https://www.irs.gov/retirement-plans/cola-increases-for-dollar-limitations-on-benefits-and-contributions',
     '2026-10-21T00:00:00+00:00'),
    (2026, 'k401_catchup_50', 800000, 0,
     'https://www.irs.gov/retirement-plans/cola-increases-for-dollar-limitations-on-benefits-and-contributions',
     '2026-10-21T00:00:00+00:00'),
    (2026, 'k401_catchup_60_63', 1125000, 0,
     'https://www.irs.gov/retirement-plans/cola-increases-for-dollar-limitations-on-benefits-and-contributions',
     '2026-10-21T00:00:00+00:00'),
    (2026, 'k415c_total', 7200000, 1,
     'https://www.irs.gov/retirement-plans/cola-increases-for-dollar-limitations-on-benefits-and-contributions',
     '2026-10-21T00:00:00+00:00'),
    (2026, 'hsa_self', 440000, 1, 'https://www.irs.gov/pub/irs-drop/rp-25-19.pdf',
     '2026-10-21T00:00:00+00:00'),
    (2026, 'hsa_family', 875000, 1, 'https://www.irs.gov/pub/irs-drop/rp-25-19.pdf',
     '2026-10-21T00:00:00+00:00'),
    (2026, 'hsa_catchup_55', 100000, 0, 'https://www.irs.gov/publications/p969',
     '2026-10-21T00:00:00+00:00');

-- App-owned. Per person: which catch-up applies this year (no birth years are
-- stored, PLAN 3.8), HSA coverage, and how many pay periods ahead an election
-- change must be made.
CREATE TABLE contribution_people (
    person_key           TEXT    PRIMARY KEY,
    k401_catch_up        TEXT    NOT NULL DEFAULT 'none'
                                 CHECK (k401_catch_up IN ('none', '50', '60-63')),
    hsa_coverage         TEXT    CHECK (hsa_coverage IN ('none', 'self', 'family')),
    hsa_catch_up         INTEGER NOT NULL DEFAULT 0 CHECK (hsa_catch_up IN (0, 1)),
    election_lead_checks INTEGER NOT NULL DEFAULT 1
                                 CHECK (election_lead_checks BETWEEN 0 AND 6),
    updated_by           INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at           TEXT    NOT NULL
) STRICT;

-- Public house price index series (FHFA). Nothing about the household is sent:
-- the app downloads FHFA's public file and keeps these series.
CREATE TABLE index_series (
    series_id     TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    last_fetch_at TEXT,
    last_period   TEXT
) STRICT;

INSERT INTO index_series (series_id, label, source_url) VALUES
    ('fhfa:exp:austin:sa', 'FHFA HPI, Austin, TX, expanded data, seasonally adjusted',
     'https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv'),
    ('fhfa:at:austin:nsa', 'FHFA HPI, Austin, TX, all transactions',
     'https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv'),
    ('fhfa:po:tx:sa', 'FHFA HPI, Texas, purchase only, seasonally adjusted',
     'https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv'),
    ('fhfa:po:wsc:sa:m', 'FHFA HPI, West South Central, purchase only, monthly',
     'https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv');

-- value_micro: the index level in micro-units (527.20 times one million).
-- period_start: the first day of the quarter or month. Upserted, never deleted,
-- so a failed fetch keeps the last values.
CREATE TABLE index_values (
    series_id    TEXT    NOT NULL REFERENCES index_series (series_id),
    period_start TEXT    NOT NULL,
    freq         TEXT    NOT NULL CHECK (freq IN ('q', 'm')),
    value_micro  INTEGER NOT NULL CHECK (value_micro > 0),
    fetched_at   TEXT    NOT NULL,
    PRIMARY KEY (series_id, period_start)
) STRICT;

-- App-owned vehicle estimate settings. A null rate or mileage field means the
-- default (app/valuations/vehicles.py). The chat's valuation is the total,
-- conversion included. conversion_* models a camper conversion that loses
-- value at its own rate.
CREATE TABLE vehicle_settings (
    account_key           TEXT    PRIMARY KEY,
    enabled               INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    model_year            INTEGER CHECK (model_year IS NULL OR model_year BETWEEN 1950 AND 2100),
    depreciation_milli_pct INTEGER CHECK (depreciation_milli_pct BETWEEN 0 AND 50000),
    expected_miles_year   INTEGER CHECK (expected_miles_year BETWEEN 0 AND 100000),
    mileage_milli_pct     INTEGER CHECK (mileage_milli_pct BETWEEN 0 AND 5000),
    conversion_value_cents INTEGER CHECK (conversion_value_cents >= 0),
    conversion_value_date TEXT,
    conversion_milli_pct  INTEGER CHECK (conversion_milli_pct BETWEEN 0 AND 50000),
    updated_by            INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at            TEXT    NOT NULL,
    CHECK ((conversion_value_cents IS NULL) = (conversion_value_date IS NULL))
) STRICT;

-- App-owned odometer readings ("Update mileage"). One per vehicle and day.
CREATE TABLE vehicle_readings (
    id           INTEGER PRIMARY KEY,
    account_key  TEXT    NOT NULL,
    reading_date TEXT    NOT NULL,
    miles        INTEGER NOT NULL CHECK (miles BETWEEN 0 AND 2000000),
    entered_by   INTEGER REFERENCES users (id) ON DELETE SET NULL,
    entered_at   TEXT    NOT NULL,
    UNIQUE (account_key, reading_date)
) STRICT;
