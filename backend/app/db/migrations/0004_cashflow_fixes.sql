-- Phase 4 review fixes to the cash flow rules (PLAN 3.9).
--
-- v_cashflow_rows is rebuilt from 0003 with three changes:
-- 1. Money in from a loan, mortgage or HELOC (a draw or a payout) is a
--    transfer. Only money out to a loan counts as spending.
-- 2. When someone marks a row as a transfer (the override sets is_transfer
--    and the import did not), the row is a transfer, or saved and invested
--    when the other side is a household asset. Before, the income and
--    reimbursable rules came first and the mark did nothing on those rows.
-- 3. A payment to a card that was closed on or before the payment date, or
--    that is no longer active, is a transfer, never "Card spending (no
--    statement)". Its charges were counted from its last statements.
-- Two columns are added for rule 3: counterparty_closed_on and
-- counterparty_active.

DROP VIEW v_cashflow_rows;

-- The one cash flow rule set (3.9). The first matching rule wins. `flow` is one of:
--   spending      counts as spending in its category (refunds net against it).
--                 `payment_basis` = 1 marks a payment to a card tracked by
--                 payments (is_transfer not true): shown as "payment basis".
--   card_no_stmt  a payment from a cash account to an open, active,
--                 statement-tracked card with no current statement that
--                 covers the payment date or closed in the 45 days before it:
--                 spending, shown as "Card spending (no statement)"
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
        cp.is_asset        AS counterparty_is_asset,
        cp.closed_on       AS counterparty_closed_on,
        cp.active          AS counterparty_active
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
             AND base.amount_cents < 0
             AND COALESCE(base.counterparty_active, 1) = 1
             AND (base.counterparty_closed_on IS NULL
                  OR base.counterparty_closed_on > base.txn_date)
             AND NOT EXISTS (
                 SELECT 1 FROM statements AS s
                 WHERE s.is_current = 1
                   AND s.account_key = base.counterparty_account_key
                   AND s.period_start <= base.txn_date
                   AND s.period_end >= date(base.txn_date, '-45 days'))
            THEN 'card_no_stmt'
        WHEN base.txn_type = 'payment' AND base.account_type IN ('credit_card', 'store_card')
            THEN 'transfer'
        WHEN base.counterparty_in_cashflow = 1
            THEN 'transfer'
        WHEN base.counterparty_type IN ('loan', 'mortgage', 'heloc')
             AND base.amount_cents < 0
            THEN 'spending'
        WHEN base.counterparty_type IN ('loan', 'mortgage', 'heloc')
            THEN 'transfer'
        WHEN (base.is_transfer = 1 OR base.category_kind = 'transfer')
             AND base.counterparty_is_asset = 1
            THEN 'saved'
        WHEN base.is_transfer = 1 AND COALESCE(base.imported_is_transfer, 0) = 0
            THEN 'transfer'
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
