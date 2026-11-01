-- 0001_init: Phase 1 schema (PLAN 3.1 to 3.4).
-- Money in integer cents, quantities and prices in micro-units, rates in
-- milli-percent. Dates are YYYY-MM-DD, timestamps are UTC ISO strings.
-- Booleans are 0 or 1. NULL in an imported boolean means "unknown".

-- ---------------------------------------------------------------------------
-- System and import (3.1)
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE,
    display_name  TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('admin', 'member')),
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at    TEXT    NOT NULL
) STRICT;

CREATE TABLE sessions (
    token_hash   TEXT    PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   TEXT    NOT NULL,
    last_seen_at TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL
) STRICT;
CREATE INDEX sessions_user ON sessions (user_id);

CREATE TABLE settings (
    key        TEXT    PRIMARY KEY,
    value_json TEXT    NOT NULL,
    updated_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at TEXT    NOT NULL
) STRICT;

-- detail_json never holds amounts or typed text.
CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    at          TEXT    NOT NULL,
    user_id     INTEGER REFERENCES users (id) ON DELETE SET NULL,
    kind        TEXT    NOT NULL,
    entity      TEXT,
    entity_key  TEXT,
    detail_json TEXT
) STRICT;
CREATE INDEX events_kind_at ON events (kind, at);

-- error_json and warnings_json hold [{"rule": ..., "where": ...}] only. Never values.
CREATE TABLE import_runs (
    run_id          TEXT    PRIMARY KEY,
    run_date        TEXT    NOT NULL,
    run_seq         INTEGER NOT NULL,
    mode            TEXT    CHECK (mode IN ('monthly', 'backfill', 'correction')),
    schema_version  TEXT,
    manifest_sha256 TEXT,
    status          TEXT    NOT NULL
                    CHECK (status IN ('waiting', 'importing', 'imported', 'failed', 'purged')),
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    counts_json     TEXT,
    warnings_json   TEXT,
    error_json      TEXT
) STRICT;

-- Every validated section of every imported run. Statement rows are stored as
-- section 'rows:<statement_key>'. Used by `cli reapply`.
CREATE TABLE run_sections (
    run_id  TEXT NOT NULL REFERENCES import_runs (run_id) ON DELETE CASCADE,
    section TEXT NOT NULL,
    sha256  TEXT NOT NULL,
    json    TEXT NOT NULL,
    PRIMARY KEY (run_id, section)
) STRICT;

CREATE TABLE job_runs (
    id           INTEGER PRIMARY KEY,
    job          TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,
    summary_json TEXT
) STRICT;
CREATE INDEX job_runs_job ON job_runs (job, started_at);

-- ---------------------------------------------------------------------------
-- Registry (3.2)
-- ---------------------------------------------------------------------------

CREATE TABLE people (
    person_key   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    aliases_json TEXT,
    role         TEXT NOT NULL CHECK (role IN ('adult', 'child', 'trust', 'joint')),
    first_run_id TEXT NOT NULL,
    run_id       TEXT NOT NULL
) STRICT;

CREATE TABLE accounts (
    account_key            TEXT    PRIMARY KEY,
    display_name           TEXT    NOT NULL,
    institution            TEXT    NOT NULL,
    custodian              TEXT,
    advisor                TEXT,
    type                   TEXT    NOT NULL CHECK (type IN (
                               'checking', 'savings', 'credit_card', 'store_card', 'loan',
                               'mortgage', 'heloc', 'brokerage', 'retirement', '529', 'hsa',
                               'custodial', 'nqdc', 'property', 'vehicle', 'other')),
    subtype                TEXT,
    owner_person_key       TEXT    NOT NULL REFERENCES people (person_key),
    ownership              TEXT    NOT NULL
                           CHECK (ownership IN ('individual', 'joint', 'trust', 'custodial')),
    account_group          TEXT    NOT NULL CHECK (account_group IN (
                               'cash', 'credit_cards', 'investments', 'retirement',
                               'real_estate', 'vehicles', 'loans', 'other')),
    nw_group               TEXT,
    nw_group_label         TEXT,
    is_asset               INTEGER NOT NULL CHECK (is_asset IN (0, 1)),
    include_in_net_worth   INTEGER NOT NULL CHECK (include_in_net_worth IN (0, 1)),
    in_cashflow            INTEGER NOT NULL CHECK (in_cashflow IN (0, 1)),
    tracked_by             TEXT    NOT NULL DEFAULT 'statements'
                           CHECK (tracked_by IN ('statements', 'payments')),
    statement_cadence      TEXT    NOT NULL
                           CHECK (statement_cadence IN ('monthly', 'quarterly', 'report', 'none')),
    stale_after_days       INTEGER,
    last4                  TEXT    CHECK (last4 IS NULL
                                          OR last4 GLOB '[0-9][0-9][0-9]'
                                          OR last4 GLOB '[0-9][0-9][0-9][0-9]'),
    opened_on              TEXT,
    closed_on              TEXT,
    active                 INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    dormant                INTEGER NOT NULL DEFAULT 0 CHECK (dormant IN (0, 1)),
    display_order          INTEGER,
    apr_milli_pct          INTEGER,
    rate_type              TEXT    CHECK (rate_type IN ('fixed', 'variable', 'promo')),
    credit_limit_cents     INTEGER,
    min_payment_cents      INTEGER,
    autopay                TEXT    CHECK (autopay IN ('minimum', 'statement', 'full', 'none')),
    cycle_close_day        INTEGER,
    servicer               TEXT,
    payoff_date            TEXT,
    priority               INTEGER,
    policy_note            TEXT,
    secured_by_account_key TEXT,
    annual_fee_cents       INTEGER,
    fee_renews_on          TEXT,
    notes                  TEXT,
    first_run_id           TEXT    NOT NULL,
    run_id                 TEXT    NOT NULL
) STRICT;

CREATE TABLE category_groups (
    group_key          TEXT    PRIMARY KEY,
    name               TEXT    NOT NULL,
    sort               INTEGER NOT NULL,
    counts_in_spending INTEGER NOT NULL CHECK (counts_in_spending IN (0, 1))
) STRICT;

CREATE TABLE categories (
    category_key TEXT    PRIMARY KEY,
    name         TEXT    NOT NULL,
    group_key    TEXT    NOT NULL REFERENCES category_groups (group_key),
    emoji        TEXT,
    kind         TEXT    NOT NULL CHECK (kind IN (
                     'spending', 'income', 'transfer', 'reimbursement', 'uncategorized')),
    sort         INTEGER NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
) STRICT;

CREATE TABLE category_aliases (
    old_key TEXT PRIMARY KEY,
    new_key TEXT NOT NULL REFERENCES categories (category_key)
) STRICT;

-- ---------------------------------------------------------------------------
-- History (3.3)
-- ---------------------------------------------------------------------------

-- Every version of every statement. One version per statement_key is current (4.9).
CREATE TABLE statements (
    statement_key       TEXT    NOT NULL,
    run_id              TEXT    NOT NULL REFERENCES import_runs (run_id),
    is_current          INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
    account_key         TEXT    NOT NULL REFERENCES accounts (account_key),
    period_start        TEXT    NOT NULL,
    period_end          TEXT    NOT NULL,
    statement_date      TEXT,
    due_date            TEXT,
    opening_cents       INTEGER,
    closing_cents       INTEGER NOT NULL,
    balance_cents       INTEGER,
    credits_cents       INTEGER,
    debits_cents        INTEGER,
    fees_cents          INTEGER,
    interest_cents      INTEGER,
    min_due_cents       INTEGER,
    credit_limit_cents  INTEGER,
    net_flows_cents     INTEGER,
    market_change_cents INTEGER,
    txn_count           INTEGER NOT NULL,
    txn_sum_cents       INTEGER,
    reconciled          INTEGER NOT NULL CHECK (reconciled IN (0, 1)),
    reconcile_note      TEXT,
    ocr                 INTEGER NOT NULL DEFAULT 0 CHECK (ocr IN (0, 1)),
    has_holdings        INTEGER NOT NULL DEFAULT 0 CHECK (has_holdings IN (0, 1)),
    source_sha256       TEXT,
    flags_text          TEXT,
    content_sha256      TEXT    NOT NULL,  -- statement plus rows, to count drift
    PRIMARY KEY (statement_key, run_id)
) STRICT;
CREATE UNIQUE INDEX statements_one_current ON statements (statement_key) WHERE is_current = 1;
CREATE INDEX statements_account ON statements (account_key, period_end);

CREATE TABLE transactions (
    txn_key                  TEXT    PRIMARY KEY,
    statement_key            TEXT    NOT NULL,
    account_key              TEXT    NOT NULL REFERENCES accounts (account_key),
    txn_date                 TEXT    NOT NULL,
    post_date                TEXT,
    amount_cents             INTEGER NOT NULL,
    description              TEXT    NOT NULL,
    desc_norm                TEXT    NOT NULL,
    merchant                 TEXT,
    category_key             TEXT    NOT NULL,
    cardholder_person_key    TEXT,
    txn_type                 TEXT    NOT NULL,
    is_transfer              INTEGER CHECK (is_transfer IN (0, 1)),
    counterparty_account_key TEXT,
    is_reimbursable          INTEGER CHECK (is_reimbursable IN (0, 1)),
    exclude_from_budget      INTEGER CHECK (exclude_from_budget IN (0, 1)),
    plan_key                 TEXT,
    recurring_key            TEXT,
    trip_key                 TEXT,
    hsa_eligible             INTEGER CHECK (hsa_eligible IN (0, 1)),
    needs_review             INTEGER CHECK (needs_review IN (0, 1)),
    confidence_pct           INTEGER CHECK (confidence_pct BETWEEN 0 AND 100),
    notes                    TEXT,
    occurrence_n             INTEGER NOT NULL,
    pre_history              INTEGER NOT NULL DEFAULT 0 CHECK (pre_history IN (0, 1)),
    ocr                      INTEGER NOT NULL DEFAULT 0 CHECK (ocr IN (0, 1)),
    run_id                   TEXT    NOT NULL,
    removed_at               TEXT
) STRICT;
CREATE INDEX transactions_statement ON transactions (statement_key);
CREATE INDEX transactions_account_date ON transactions (account_key, txn_date);
CREATE INDEX transactions_date ON transactions (txn_date);

-- Signed from the net worth view: assets positive, money owed negative.
CREATE TABLE balances (
    account_key   TEXT    NOT NULL REFERENCES accounts (account_key),
    as_of         TEXT    NOT NULL,
    source        TEXT    NOT NULL CHECK (source IN ('statement', 'valuation', 'manual', 'estimate')),
    balance_cents INTEGER NOT NULL,
    statement_key TEXT,
    method        TEXT    CHECK (method IN ('comps', 'kbb', 'report', 'manual')),
    source_note   TEXT,
    run_id        TEXT    NOT NULL,
    PRIMARY KEY (account_key, as_of, source)
) STRICT;

-- The full position list of one statement. A missing holding is zero.
CREATE TABLE holdings (
    statement_key    TEXT    NOT NULL,
    account_key      TEXT    NOT NULL REFERENCES accounts (account_key),
    as_of            TEXT    NOT NULL,
    holding_key      TEXT    NOT NULL,
    holding_name     TEXT    NOT NULL,
    symbol           TEXT,
    cusip            TEXT,
    quantity_micro   INTEGER,
    price_micro      INTEGER,
    value_cents      INTEGER NOT NULL,
    cost_basis_cents INTEGER,
    asset_class      TEXT,
    run_id           TEXT    NOT NULL,
    PRIMARY KEY (statement_key, holding_key)
) STRICT;
CREATE INDEX holdings_account ON holdings (account_key, as_of);

-- ---------------------------------------------------------------------------
-- App-owned edits (3.4). Empty in Phase 1. Keyed by txn_key with no foreign
-- key, so overrides survive `cli reapply` and reattach by key.
-- ---------------------------------------------------------------------------

CREATE TABLE transaction_overrides (
    txn_key             TEXT    PRIMARY KEY,
    category_key        TEXT,
    merchant            TEXT,
    is_transfer         INTEGER CHECK (is_transfer IN (0, 1)),
    is_reimbursable     INTEGER CHECK (is_reimbursable IN (0, 1)),
    exclude_from_budget INTEGER CHECK (exclude_from_budget IN (0, 1)),
    hidden              INTEGER CHECK (hidden IN (0, 1)),
    notes               TEXT,
    tags_json           TEXT,
    guard_json          TEXT,
    updated_by          INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at          TEXT    NOT NULL
) STRICT;

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------

-- Live rows. A non-null override field wins over the imported value. A retired
-- category key resolves through category_aliases. (Merchant rules join in P4.)
CREATE VIEW v_transactions AS
SELECT
    t.txn_key,
    t.statement_key,
    t.account_key,
    t.txn_date,
    t.post_date,
    t.amount_cents,
    t.description,
    t.desc_norm,
    COALESCE(o.merchant, t.merchant)                           AS merchant,
    COALESCE(o.category_key, ca.new_key, t.category_key)       AS category_key,
    t.category_key                                             AS imported_category_key,
    t.cardholder_person_key,
    t.txn_type,
    COALESCE(o.is_transfer, t.is_transfer)                     AS is_transfer,
    t.counterparty_account_key,
    COALESCE(o.is_reimbursable, t.is_reimbursable)             AS is_reimbursable,
    COALESCE(o.exclude_from_budget, t.exclude_from_budget)     AS exclude_from_budget,
    COALESCE(o.hidden, 0)                                      AS hidden,
    t.plan_key,
    t.recurring_key,
    t.trip_key,
    t.hsa_eligible,
    t.needs_review,
    t.confidence_pct,
    t.notes                                                    AS import_notes,
    o.notes                                                    AS notes,
    o.tags_json,
    t.occurrence_n,
    t.pre_history,
    t.ocr,
    t.run_id,
    (o.txn_key IS NOT NULL)                                    AS has_override
FROM transactions AS t
LEFT JOIN transaction_overrides AS o ON o.txn_key = t.txn_key
LEFT JOIN category_aliases AS ca ON ca.old_key = t.category_key
WHERE t.removed_at IS NULL;

-- The one cash flow rule set (3.9). The first matching rule wins. `flow` is one of:
--   spending      counts as spending in its category (refunds net against it).
--                 `payment_basis` = 1 marks a payment to a card tracked by
--                 payments (is_transfer not true): shown as "payment basis".
--   card_no_stmt  a payment to a statement-tracked card with no statement for
--                 that date: spending, shown as "Card spending (no statement)"
--   income        income category
--   saved         saved and invested (a move into another household asset)
--   transfer      ignored (card payments, moves between cash flow accounts)
--   reimbursable  left out, waiting on reimbursement
--   reimbursement neither income nor spending
-- `out_cents` is the amount as spending (positive = money spent).
CREATE VIEW v_cashflow_rows AS
WITH base AS (
    SELECT
        v.*,
        a.type             AS account_type,
        c.kind             AS category_kind,
        c.group_key        AS category_group,
        cp.type            AS counterparty_type,
        cp.tracked_by      AS counterparty_tracked_by,
        cp.in_cashflow     AS counterparty_in_cashflow,
        cp.is_asset        AS counterparty_is_asset
    FROM v_transactions AS v
    JOIN accounts AS a ON a.account_key = v.account_key
    LEFT JOIN categories AS c ON c.category_key = v.category_key
    LEFT JOIN accounts AS cp ON cp.account_key = v.counterparty_account_key
    WHERE a.in_cashflow = 1
      AND v.hidden = 0
      AND COALESCE(v.exclude_from_budget, 0) = 0
      AND v.pre_history = 0
)
SELECT
    base.*,
    substr(base.txn_date, 1, 7) AS month,
    CASE
        WHEN base.counterparty_tracked_by = 'payments' AND COALESCE(base.is_transfer, 0) = 0
            THEN 'spending'
        WHEN base.counterparty_type IN ('credit_card', 'store_card')
             AND base.counterparty_tracked_by = 'statements'
             AND base.account_type NOT IN ('credit_card', 'store_card')
             AND NOT EXISTS (
                 SELECT 1 FROM statements AS s
                 WHERE s.is_current = 1
                   AND s.account_key = base.counterparty_account_key
                   AND base.txn_date BETWEEN s.period_start AND s.period_end)
            THEN 'card_no_stmt'
        WHEN base.txn_type = 'payment' AND base.account_type IN ('credit_card', 'store_card')
            THEN 'transfer'
        WHEN base.counterparty_in_cashflow = 1
            THEN 'transfer'
        WHEN base.counterparty_type IN ('loan', 'mortgage', 'heloc')
            THEN 'spending'
        WHEN (base.is_transfer = 1 OR base.category_kind = 'transfer')
             AND base.counterparty_is_asset = 1
            THEN 'saved'
        WHEN base.category_kind = 'income'
            THEN 'income'
        WHEN base.is_reimbursable = 1
            THEN 'reimbursable'
        WHEN base.category_kind = 'reimbursement'
            THEN 'reimbursement'
        WHEN base.is_transfer = 1 OR base.category_kind = 'transfer'
            THEN 'transfer'
        ELSE 'spending'
    END AS flow,
    COALESCE(base.counterparty_tracked_by = 'payments' AND COALESCE(base.is_transfer, 0) = 0, 0)
        AS payment_basis,
    -base.amount_cents AS out_cents
FROM base;
