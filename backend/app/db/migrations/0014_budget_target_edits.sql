-- Budget target edits (docs/plans/budget-targets.md section 3). An admin changes a
-- plan group target on the Budget page. One row per (kind, key, month): the owner's
-- number (target_cents) and the chat's number for that month and key when it was saved
-- it (base_cents, NULL when the chat had none). The app lays the rows on the
-- chat's plan when a page is read (app/services/budget_targets.py). The chat's
-- run data is never changed.
--
-- App-owned: no foreign key to imported tables, so imports, `cli reapply` and
-- purges never touch it. In the fingerprint, so a restore drill sees a loss.
-- updated_by and updated_at are bookkeeping and stay out of it. No free text.
-- kind leaves room for category targets later. The upper bound lives in Pydantic.
--
-- An older app ignores the table, so a rollback needs no restore.
CREATE TABLE budget_target_edits (
    kind         TEXT    NOT NULL CHECK (kind IN ('group')),
    target_key   TEXT    NOT NULL CHECK (target_key <> ''),
    month        TEXT    NOT NULL CHECK (month GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]'),
    target_cents INTEGER NOT NULL CHECK (target_cents >= 0),
    base_cents   INTEGER CHECK (base_cents IS NULL OR base_cents >= 0),
    updated_by   INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at   TEXT    NOT NULL,
    PRIMARY KEY (kind, target_key, month)
) STRICT;
