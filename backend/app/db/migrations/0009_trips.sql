-- Format 1.3: trips (docs/plans/phase-7.md sections 2.2 and 3). Rewards has no
-- table: it stays as run JSON in run_sections, like the 1.1 state sections.
--
-- Imported tables: the importer's apply step fills them (newest run in run
-- order wins per trip key, the whole trip with its people, items and IOUs;
-- tombstones delete), and `cli reapply` rebuilds them from stored runs.
-- Money in cents, points in micro-units like utility usage. No confirmation
-- codes, record locators, flight numbers, addresses or names of people outside
-- the household: IOUs name a role, guests are a count.
CREATE TABLE trips (
    trip_key            TEXT    PRIMARY KEY,
    name                TEXT    NOT NULL,
    status              TEXT    NOT NULL CHECK (status IN ('planning', 'booked', 'in_progress',
                                    'complete', 'final')),
    start_date          TEXT,
    end_date            TEXT,
    month               TEXT,
    guests              INTEGER CHECK (guests IS NULL OR guests BETWEEN 0 AND 20),
    vehicle             TEXT,
    estimate_low_cents  INTEGER CHECK (estimate_low_cents IS NULL OR estimate_low_cents >= 0),
    estimate_high_cents INTEGER CHECK (estimate_high_cents IS NULL OR estimate_high_cents >= 0),
    note                TEXT,
    run_id              TEXT    NOT NULL,
    CHECK ((start_date IS NULL) = (end_date IS NULL)),
    CHECK ((start_date IS NULL) <> (month IS NULL)),
    CHECK (start_date IS NULL OR start_date <= end_date),
    CHECK ((estimate_low_cents IS NULL) = (estimate_high_cents IS NULL)),
    CHECK (estimate_low_cents IS NULL OR estimate_low_cents <= estimate_high_cents)
) STRICT;

-- Household members on the trip (person keys). Guests are a count on trips.
CREATE TABLE trip_people (
    trip_key   TEXT NOT NULL REFERENCES trips (trip_key) ON DELETE CASCADE,
    person_key TEXT NOT NULL,
    PRIMARY KEY (trip_key, person_key)
) STRICT;

-- One cost of a trip. amount_cents is cash for the whole household: for points
-- and free nights the taxes and fees, for points_credit the size of the credit.
CREATE TABLE trip_items (
    trip_key            TEXT    NOT NULL REFERENCES trips (trip_key) ON DELETE CASCADE,
    item_key            TEXT    NOT NULL,
    kind                TEXT    NOT NULL CHECK (kind IN ('flight', 'lodging', 'rental_car', 'gas',
                                    'food', 'activities', 'tickets', 'shopping', 'insurance',
                                    'parking', 'transit', 'cash', 'supplies', 'fees', 'other')),
    label               TEXT    NOT NULL,
    status              TEXT    NOT NULL CHECK (status IN ('posted', 'booked', 'estimate', 'tbd',
                                    'excluded')),
    tender              TEXT    NOT NULL CHECK (tender IN ('cash', 'points', 'free_night',
                                    'points_credit', 'covered')),
    amount_cents        INTEGER CHECK (amount_cents IS NULL OR amount_cents >= 0),
    estimate_low_cents  INTEGER CHECK (estimate_low_cents IS NULL OR estimate_low_cents >= 0),
    estimate_high_cents INTEGER CHECK (estimate_high_cents IS NULL OR estimate_high_cents >= 0),
    points_micro        INTEGER CHECK (points_micro IS NULL OR points_micro >= 0),
    program_key         TEXT,
    account_key         TEXT,
    person_key          TEXT,
    note                TEXT,
    PRIMARY KEY (trip_key, item_key)
) STRICT;

-- Money owed between the household and someone else on the trip.
CREATE TABLE trip_ious (
    trip_key     TEXT    NOT NULL REFERENCES trips (trip_key) ON DELETE CASCADE,
    iou_key      TEXT    NOT NULL,
    direction    TEXT    NOT NULL CHECK (direction IN ('owed_to_us', 'we_owe')),
    counterparty TEXT    NOT NULL,  -- a role, like relative or friend couple
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    status       TEXT    NOT NULL CHECK (status IN ('open', 'settled', 'waived')),
    settled_on   TEXT,
    item_key     TEXT,
    note         TEXT,
    PRIMARY KEY (trip_key, iou_key),
    CHECK ((status = 'settled') = (settled_on IS NOT NULL))
) STRICT;

-- Rows tagged with a trip, for the Travel page and the trip filter (Phase 7).
CREATE INDEX transactions_trip_key ON transactions (trip_key);
