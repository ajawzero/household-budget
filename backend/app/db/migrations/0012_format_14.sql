-- Format 1.4 (docs/plans/format-14.md section 4): four added columns, no table
-- rebuild. Every other 1.4 field stays run JSON in run_sections.
--
-- accounts: original_cents is the loan amount when it was made (loan and
-- mortgage only, rule debt-terms). rate_changes_json is the rate schedule as
-- canonical JSON in the run's own shape, oldest first. A run that sends [] clears
-- it: [] is stored as NULL. A field the run leaves out keeps its value.
-- trip_items: due_on is the day a booked amount is charged or due. maybe is 1 on
-- an estimate or tbd cost not booked yet, else NULL (never 0).
--
-- Imported columns: the importer fills them and cli reapply rebuilds them. Rows
-- from earlier runs keep NULL. An older app names its columns on insert, so it
-- leaves these empty. The tables stay STRICT.
ALTER TABLE accounts ADD COLUMN original_cents INTEGER
    CHECK (original_cents IS NULL OR original_cents > 0);
ALTER TABLE accounts ADD COLUMN rate_changes_json TEXT
    CHECK (rate_changes_json IS NULL OR json_valid(rate_changes_json));
ALTER TABLE trip_items ADD COLUMN due_on TEXT
    CHECK (due_on IS NULL OR status = 'booked');
ALTER TABLE trip_items ADD COLUMN maybe INTEGER
    CHECK (maybe IS NULL OR (maybe = 1 AND status IN ('estimate', 'tbd')));
