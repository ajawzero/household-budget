-- Phase 6: Claude API (PLAN 3.6, 5.7, 7).
--
-- Tables
--   ai_threads, ai_messages  Ask threads. Append-only: every message is stored
--                            exactly as sent or returned (thinking blocks too)
--                            and never edited. Deleted after the retention
--                            setting (90 days).
--   ai_usage                 One row per model call: tokens, cost, stop reason.
--                            Metadata only, never prompt or answer text.
--   ai_reviews               The monthly review, one per monthly run. Stored
--                            after the output filter.
--   category_suggestions     Categories the model suggests for uncategorized
--                            rows. Nothing changes until a person approves.
--   ai_facts, ai_facts_state Numbers the app's own services computed (balances
--                            with estimates, net worth, cash flow, budgets,
--                            plans, recurring, holdings), whitelisted field by
--                            field and rebuilt when the data changes. The AI
--                            reads them through ai_facts_safe.
--
-- Views
--   Every byte sent to Anthropic is read through a *_safe view on the
--   read-only AI connection (app/ai/safe.py). Its authorizer denies any
--   top-level read that is not one of these views. The views show account
--   display names (never last4), the cleaned merchant, and no notes, tags,
--   raw descriptions or hidden rows.
--
-- transaction_events is rebuilt so `via` can be 'suggestion' (an approved
-- category suggestion).

-- ---------------------------------------------------------------------------
-- transaction_events: add via = 'suggestion'
-- ---------------------------------------------------------------------------

CREATE TABLE transaction_events_new (
    id           INTEGER PRIMARY KEY,
    txn_key      TEXT    NOT NULL,
    at           TEXT    NOT NULL,
    user_id      INTEGER REFERENCES users (id) ON DELETE SET NULL,
    kind         TEXT    NOT NULL,
    via          TEXT    NOT NULL CHECK (via IN ('edit', 'bulk', 'rule', 'import', 'suggestion')),
    from_value   TEXT,
    to_value     TEXT,
    prev_txn_key TEXT,
    rule_id      INTEGER
) STRICT;
INSERT INTO transaction_events_new SELECT * FROM transaction_events;
DROP TABLE transaction_events;
ALTER TABLE transaction_events_new RENAME TO transaction_events;
CREATE INDEX transaction_events_txn ON transaction_events (txn_key, id);

-- ---------------------------------------------------------------------------
-- AI tables
-- ---------------------------------------------------------------------------

CREATE TABLE ai_threads (
    thread_id     TEXT    PRIMARY KEY,
    user_id       INTEGER REFERENCES users (id) ON DELETE CASCADE,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    effort        TEXT    NOT NULL,
    -- The first question, after the question checks. Shown in the thread list.
    title         TEXT    NOT NULL,
    turn_count    INTEGER NOT NULL DEFAULT 0,
    -- Input tokens of the newest call (all three kinds). A new thread starts near 80k.
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    closed        INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1))
) STRICT;
CREATE INDEX ai_threads_user ON ai_threads (user_id, updated_at);

-- kind: question (a person's question), assistant (a model response, stored as
-- returned), tool_results (the tool results sent back), notice (an app message
-- that ends a turn without a model answer, never sent to the model).
CREATE TABLE ai_messages (
    thread_id    TEXT    NOT NULL REFERENCES ai_threads (thread_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    role         TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    kind         TEXT    NOT NULL
                 CHECK (kind IN ('question', 'assistant', 'tool_results', 'notice')),
    content_json TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    PRIMARY KEY (thread_id, seq)
) STRICT;
CREATE INDEX ai_messages_kind_at ON ai_messages (kind, created_at);

CREATE TABLE ai_usage (
    id                          INTEGER PRIMARY KEY,
    at                          TEXT    NOT NULL,
    user_id                     INTEGER REFERENCES users (id) ON DELETE SET NULL,
    feature                     TEXT    NOT NULL
                                CHECK (feature IN ('ask', 'review', 'categorize', 'cache_check')),
    model                       TEXT    NOT NULL,
    effort                      TEXT,
    input_tokens                INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cost_micro_usd              INTEGER NOT NULL DEFAULT 0,
    stop_reason                 TEXT,
    request_id                  TEXT,
    latency_ms                  INTEGER,
    thread_id                   TEXT    REFERENCES ai_threads (thread_id) ON DELETE SET NULL,
    -- The exception class of a failed call. Never its message.
    error                       TEXT
) STRICT;
CREATE INDEX ai_usage_at ON ai_usage (at);

-- One review per complete month, written after the import that completed it
-- (run_id). Text is stored after the output filter.
CREATE TABLE ai_reviews (
    review_id      INTEGER PRIMARY KEY,
    run_id         TEXT    NOT NULL,
    month          TEXT    NOT NULL UNIQUE,
    model          TEXT    NOT NULL,
    summary        TEXT    NOT NULL,
    changes_json   TEXT    NOT NULL,
    anomalies_json TEXT    NOT NULL,
    actions_json   TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('ready', 'failed'))
) STRICT;

-- One row per transaction ever suggested. A rejected row is never suggested again.
CREATE TABLE category_suggestions (
    id                     INTEGER PRIMARY KEY,
    txn_key                TEXT    NOT NULL UNIQUE,
    suggested_category_key TEXT    NOT NULL REFERENCES categories (category_key),
    confidence             INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    reason                 TEXT,
    model                  TEXT    NOT NULL,
    status                 TEXT    NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at             TEXT    NOT NULL,
    decided_by             INTEGER REFERENCES users (id) ON DELETE SET NULL,
    decided_at             TEXT
) STRICT;
CREATE INDEX category_suggestions_status ON category_suggestions (status, created_at);

-- kind + key -> one JSON object of whitelisted fields (app/ai/facts.py).
CREATE TABLE ai_facts (
    kind TEXT NOT NULL,
    key  TEXT NOT NULL,
    json TEXT NOT NULL,
    PRIMARY KEY (kind, key)
) STRICT;

CREATE TABLE ai_facts_state (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    signature TEXT    NOT NULL,
    built_at  TEXT    NOT NULL
) STRICT;

-- ---------------------------------------------------------------------------
-- *_safe views (PLAN 5.7). Only these are readable on the AI connection.
-- ---------------------------------------------------------------------------

CREATE VIEW people_safe AS
SELECT person_key, display_name, role
FROM people;

CREATE VIEW accounts_safe AS
SELECT
    account_key,
    display_name,
    institution,
    type,
    account_group,
    owner_person_key,
    is_asset,
    include_in_net_worth,
    in_cashflow,
    tracked_by,
    active,
    closed_on
FROM accounts;

CREATE VIEW categories_safe AS
SELECT
    c.category_key,
    c.name,
    c.kind,
    c.group_key,
    g.name AS group_name,
    c.active
FROM categories AS c
JOIN category_groups AS g ON g.group_key = c.group_key;

-- Live, visible rows with edits and rules applied. The merchant is the cleaned
-- one (an edit's, else the import's). No description, notes or tags.
CREATE VIEW transactions_safe AS
SELECT
    v.txn_key,
    v.txn_date,
    substr(v.txn_date, 1, 7)  AS month,
    v.account_key,
    a.display_name            AS account_name,
    v.amount_cents,
    v.merchant,
    v.category_key,
    c.name                    AS category_name,
    c.group_key               AS group_key,
    c.kind                    AS category_kind,
    v.txn_type,
    COALESCE(v.is_transfer, 0)         AS is_transfer,
    COALESCE(v.is_reimbursable, 0)     AS is_reimbursable,
    COALESCE(v.exclude_from_budget, 0) AS exclude_from_budget,
    p.display_name            AS cardholder,
    v.counterparty_account_key,
    cp.display_name           AS counterparty_name,
    v.plan_key,
    v.recurring_key,
    v.pre_history
FROM v_transactions AS v
JOIN accounts AS a ON a.account_key = v.account_key
LEFT JOIN categories AS c ON c.category_key = v.category_key
LEFT JOIN people AS p ON p.person_key = v.cardholder_person_key
LEFT JOIN accounts AS cp ON cp.account_key = v.counterparty_account_key
WHERE v.hidden = 0;

-- The cash flow rows (PLAN 3.9), with the label and the reporting category the
-- Cash Flow page uses (app/services/cashflow.py merchant_label, report_category).
CREATE VIEW cashflow_rows_safe AS
SELECT
    r.txn_key,
    r.txn_date,
    r.month,
    r.account_key,
    a.display_name AS account_name,
    r.flow,
    r.amount_cents,
    r.out_cents,
    r.payment_basis,
    r.txn_type,
    CASE WHEN r.flow = 'card_no_stmt' THEN 'card-no-statement' ELSE r.category_key END
        AS category_key,
    CASE WHEN r.flow = 'card_no_stmt' THEN 'Card spending (no statement)' ELSE c.name END
        AS category_name,
    CASE WHEN r.flow = 'card_no_stmt' THEN 'card-no-statement'
         ELSE COALESCE(r.category_group, 'not-spending') END
        AS group_key,
    CASE
        WHEN r.flow = 'card_no_stmt' OR r.payment_basis THEN COALESCE(cp.display_name, r.merchant)
        WHEN r.merchant IS NOT NULL THEN r.merchant
        ELSE cp.display_name
    END AS merchant_label
FROM v_cashflow_rows AS r
JOIN accounts AS a ON a.account_key = r.account_key
LEFT JOIN categories AS c ON c.category_key = r.category_key
LEFT JOIN accounts AS cp ON cp.account_key = r.counterparty_account_key;

-- Uncategorized, visible spending-side rows for category suggestions. The
-- normalized description is added only when the merchant is missing or too
-- short to say anything (a weak merchant).
CREATE VIEW txn_categorize_safe AS
SELECT
    v.txn_key,
    v.txn_date,
    v.amount_cents,
    v.txn_type,
    a.display_name AS account_name,
    v.merchant,
    CASE WHEN v.merchant IS NULL OR length(trim(v.merchant)) < 4 THEN v.desc_norm END
        AS desc_norm
FROM v_transactions AS v
JOIN accounts AS a ON a.account_key = v.account_key
JOIN categories AS c ON c.category_key = v.category_key
WHERE v.hidden = 0
  AND c.kind = 'uncategorized'
  AND v.pre_history = 0
  AND COALESCE(v.is_transfer, 0) = 0;

CREATE VIEW ai_facts_safe AS
SELECT kind, key, json FROM ai_facts;
