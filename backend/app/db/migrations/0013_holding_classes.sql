-- Asset class picks (Settings > Prices, admin only). One row per holding: the
-- class an admin chose, in place of the one the statement gave (or left out, the
-- "No class" group on Allocation).
--
-- App-owned: no foreign key, so a pick survives `cli reapply` and new statements
-- and reattaches by key, like symbol_map. A pick changes how a holding is grouped
-- (Allocation, Holdings by class, the account drawer, Ask) and the cash rules that
-- follow the class. Never a value or a price mode: those keep the statement's class.
-- The app checks the key against its fixed list (app/market/classes.py).
--
-- An older app ignores the table, so a rollback needs no restore.
CREATE TABLE holding_classes (
    account_key TEXT    NOT NULL,
    holding_key TEXT    NOT NULL,
    asset_class TEXT    NOT NULL CHECK (asset_class <> ''),
    updated_by  INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (account_key, holding_key)
) STRICT;
