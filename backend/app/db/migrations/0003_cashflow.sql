-- Phase 4: cash flow (PLAN 3.9).
--
-- v_cashflow_rows is rebuilt with one change to the "card with no statement"
-- rule. Before, a card payment counted as spending whenever no statement of
-- the card covered the payment date. That double counted the normal case: a
-- payment on Sep 25 pays the cycle that closed Sep 10, whose rows are already
-- imported, while the cycle that holds Sep 25 is not in yet. Now the payment
-- is a transfer when a current statement of the card covers its date or
-- closed in the 45 days before it. Only payments (money out) count.
-- Everything else is the same as 0001 and 0002. Rows in `uncategorized`
-- still fall to the last rule and count as spending until someone files them.

DROP VIEW v_cashflow_rows;

-- The one cash flow rule set (3.9). The first matching rule wins. `flow` is one of:
--   spending      counts as spending in its category (refunds net against it).
--                 `payment_basis` = 1 marks a payment to a card tracked by
--                 payments (is_transfer not true): shown as "payment basis".
--   card_no_stmt  a payment from a cash account to a statement-tracked card
--                 with no current statement that covers the payment date or
--                 closed in the 45 days before it (the cycle it pays):
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
             AND base.amount_cents < 0
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
