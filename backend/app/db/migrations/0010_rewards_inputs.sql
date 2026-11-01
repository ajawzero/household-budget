-- R1.1: rewards inputs (docs/plans/rewards-inputs.md section 2).
--
-- App-owned rewards inputs, laid over the chat's rewards (run_sections) on read.
-- No foreign key to imported tables, so cli reapply and replay keep every row.
-- Keys are the chat's keys. Keys the app makes start with "app-". Points in
-- micro-units, money in cents. Typed text is scanned before it is stored.
--
-- Not imported tables: imports never write them. They are in the fingerprint,
-- like transaction_overrides, so a restore drill sees a loss. updated_by and
-- updated_at are bookkeeping and stay out of it. Upper bounds live in Pydantic.

-- A dated balance for a program. The newest date wins over the chat's. unit is
-- what it was typed in: whole (points and miles) or cash (cash back). Only
-- entries in the program's unit now count. AUTOINCREMENT: a deleted id is never
-- given again, so a stale delete by id cannot take another program's or another
-- day's entry. A same-day entry replaces the value under the same id.
CREATE TABLE reward_balances (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    program_key   TEXT    NOT NULL CHECK (length(program_key) BETWEEN 1 AND 40),
    as_of         TEXT    NOT NULL CHECK (as_of GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    balance_micro INTEGER NOT NULL CHECK (balance_micro >= 0),
    unit          TEXT    NOT NULL CHECK (unit IN ('whole', 'cash')),
    updated_by    INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at    TEXT    NOT NULL,
    UNIQUE (program_key, as_of)
) STRICT;

-- A program the chat never sends (airline and hotel programs).
CREATE TABLE reward_app_programs (
    program_key       TEXT    PRIMARY KEY CHECK (program_key GLOB 'app-?*' AND length(program_key) <= 40),
    name              TEXT    NOT NULL,
    kind              TEXT    NOT NULL CHECK (kind IN ('points', 'miles', 'cash_back')),
    program_group     TEXT    NOT NULL DEFAULT 'secondary' CHECK (program_group IN ('primary', 'secondary')),
    owner_key         TEXT,
    cpp_low_micro     INTEGER,
    cpp_high_micro    INTEGER,
    expires_on        TEXT,
    note              TEXT,
    dup_dismissed_key TEXT,
    updated_by        INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at        TEXT    NOT NULL,
    CHECK ((cpp_low_micro IS NULL) = (cpp_high_micro IS NULL)),
    CHECK (cpp_low_micro IS NULL OR (kind <> 'cash_back' AND cpp_low_micro BETWEEN 0 AND cpp_high_micro))
) STRICT;

-- One row per benefit key: edits on a chat benefit (origin chat) or a benefit added in the app (origin app).
CREATE TABLE reward_benefit_edits (
    benefit_key       TEXT    PRIMARY KEY CHECK (length(benefit_key) BETWEEN 1 AND 40),
    origin            TEXT    NOT NULL CHECK (origin IN ('chat', 'app')),
    account_key       TEXT    NOT NULL,
    fields_json       TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(fields_json) AND json_type(fields_json) = 'object'),
    base_json         TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(base_json) AND json_type(base_json) = 'object'),
    amount_from       TEXT,
    amount_before     TEXT,
    hidden            INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    match_off         INTEGER NOT NULL DEFAULT 0 CHECK (match_off IN (0, 1)),
    dup_dismissed_key TEXT,
    updated_by        INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at        TEXT    NOT NULL,
    CHECK (origin = 'chat' OR benefit_key GLOB 'app-?*')
) STRICT;
CREATE INDEX reward_benefit_edits_account ON reward_benefit_edits (account_key);

-- Match words for a benefit's statement credits. Their own table, so "Use the
-- chat's for every field" never deletes them.
CREATE TABLE reward_match_terms (
    id          INTEGER PRIMARY KEY,
    benefit_key TEXT    NOT NULL,
    term        TEXT    NOT NULL CHECK (length(term) BETWEEN 3 AND 40),
    updated_by  INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at  TEXT    NOT NULL
) STRICT;
CREATE UNIQUE INDEX reward_match_terms_one ON reward_match_terms (benefit_key, upper(term));

-- the owner's choice on one credit row. guard_json re-finds the row after a statement correction.
CREATE TABLE reward_credit_decisions (
    txn_key     TEXT    PRIMARY KEY CHECK (length(txn_key) = 64),
    decision    TEXT    NOT NULL CHECK (decision IN ('link', 'not_benefit')),
    reason      TEXT    CHECK (reason IN ('return', 'points', 'other')),
    benefit_key TEXT,
    cycle_start TEXT,
    guard_json  TEXT    NOT NULL CHECK (json_valid(guard_json) AND json_type(guard_json) = 'object'),
    updated_by  INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at  TEXT    NOT NULL,
    CHECK ((decision = 'link') = (benefit_key IS NOT NULL)),
    CHECK ((decision = 'not_benefit') = (reason IS NOT NULL)),
    CHECK (cycle_start IS NULL OR decision = 'link')
) STRICT;
CREATE INDEX reward_credit_decisions_benefit ON reward_credit_decisions (benefit_key);

-- "Mark used" or "Mark not used" on one cycle. used_cents 0 means not used.
CREATE TABLE reward_cycle_marks (
    benefit_key TEXT    NOT NULL,
    cycle_start TEXT    NOT NULL,
    used_cents  INTEGER NOT NULL CHECK (used_cents >= 0),
    used_on     TEXT,
    updated_by  INTEGER REFERENCES users (id) ON DELETE SET NULL,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (benefit_key, cycle_start)
) STRICT;
