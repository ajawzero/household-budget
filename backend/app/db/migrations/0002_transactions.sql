-- Phase 4: transaction activity, merchant rules, and rules in v_transactions
-- (PLAN 3.4, 4.11 step 6, 5.6).
--
-- transaction_events: the activity timeline of one transaction. Edits, bulk
-- edits, rules and the importer's re-key and orphan steps add rows. Values are
-- category keys, true or false, a merchant name or tags. Note text is never
-- copied here. `prev_txn_key` is set on a 'rekeyed' row (the key it came from).
--
-- merchant_rules: "merchant equals" or "description contains" -> category.
-- Rules apply at read time in v_transactions and are never written into
-- transactions. Precedence: override > rule > imported value.

CREATE TABLE transaction_events (
    id           INTEGER PRIMARY KEY,
    txn_key      TEXT    NOT NULL,
    at           TEXT    NOT NULL,
    user_id      INTEGER REFERENCES users (id) ON DELETE SET NULL,
    kind         TEXT    NOT NULL,
    via          TEXT    NOT NULL CHECK (via IN ('edit', 'bulk', 'rule', 'import')),
    from_value   TEXT,
    to_value     TEXT,
    prev_txn_key TEXT,
    rule_id      INTEGER
) STRICT;
CREATE INDEX transaction_events_txn ON transaction_events (txn_key, id);

CREATE TABLE merchant_rules (
    id                   INTEGER PRIMARY KEY,
    match_kind           TEXT    NOT NULL
                         CHECK (match_kind IN ('merchant_equals', 'description_contains')),
    pattern              TEXT    NOT NULL CHECK (length(pattern) BETWEEN 1 AND 64),
    category_key         TEXT    NOT NULL REFERENCES categories (category_key),
    created_from_txn_key TEXT,
    created_by           INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at           TEXT    NOT NULL,
    updated_by           INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at           TEXT    NOT NULL,
    active               INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
) STRICT;
CREATE UNIQUE INDEX merchant_rules_match ON merchant_rules (match_kind, upper(pattern));

-- ---------------------------------------------------------------------------
-- Views. v_cashflow_rows reads v_transactions, so both are rebuilt.
-- ---------------------------------------------------------------------------

DROP VIEW v_cashflow_rows;
DROP VIEW v_transactions;

-- Live rows. A non-null override field wins over the imported value. The
-- category is: override, else the best matching active rule, else the
-- imported key (a retired key resolves through category_aliases).
-- Rule order: "merchant equals" before "description contains", then the
-- longer pattern, then the newest rule. Matching ignores ASCII case. Merchant
-- rules match the merchant shown (the override's, else the imported one).
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
    COALESCE(o.merchant, t.merchant)                               AS merchant,
    t.merchant                                                     AS imported_merchant,
    COALESCE(o.category_key, r.category_key, ca.new_key, t.category_key) AS category_key,
    t.category_key                                                 AS imported_category_key,
    CASE
        WHEN o.category_key IS NOT NULL THEN 'override'
        WHEN r.id IS NOT NULL THEN 'rule'
        ELSE 'imported'
    END                                                            AS category_source,
    CASE WHEN o.category_key IS NULL THEN r.id END                 AS rule_id,
    t.cardholder_person_key,
    t.txn_type,
    COALESCE(o.is_transfer, t.is_transfer)                         AS is_transfer,
    t.is_transfer                                                  AS imported_is_transfer,
    t.counterparty_account_key,
    COALESCE(o.is_reimbursable, t.is_reimbursable)                 AS is_reimbursable,
    t.is_reimbursable                                              AS imported_is_reimbursable,
    COALESCE(o.exclude_from_budget, t.exclude_from_budget)         AS exclude_from_budget,
    t.exclude_from_budget                                          AS imported_exclude_from_budget,
    COALESCE(o.hidden, 0)                                          AS hidden,
    t.plan_key,
    t.recurring_key,
    t.trip_key,
    t.hsa_eligible,
    t.needs_review,
    t.confidence_pct,
    t.notes                                                        AS import_notes,
    o.notes                                                        AS notes,
    o.tags_json,
    t.occurrence_n,
    t.pre_history,
    t.ocr,
    t.run_id,
    (o.txn_key IS NOT NULL)                                        AS has_override
FROM transactions AS t
LEFT JOIN transaction_overrides AS o ON o.txn_key = t.txn_key
LEFT JOIN category_aliases AS ca ON ca.old_key = t.category_key
LEFT JOIN merchant_rules AS r ON r.id = (
    SELECT mr.id FROM merchant_rules AS mr
    WHERE mr.active = 1
      AND (
          (mr.match_kind = 'merchant_equals'
           AND upper(COALESCE(o.merchant, t.merchant)) = upper(mr.pattern))
          OR (mr.match_kind = 'description_contains'
              AND instr(upper(t.description), upper(mr.pattern)) > 0)
      )
    ORDER BY mr.match_kind = 'merchant_equals' DESC, length(mr.pattern) DESC, mr.id DESC
    LIMIT 1
)
WHERE t.removed_at IS NULL;

-- Unchanged from 0001 (it reads the new v_transactions).
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
