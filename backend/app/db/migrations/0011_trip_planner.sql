-- Trip planner (docs/plans/trip-planner.md section 8).
--
-- Saved plans belong to the household. Each plan or follow-up adds turns:
-- the request, the research notes with their sources, the plan, or a notice.
-- Turns are only added, never changed. A plan is kept until someone deletes
-- it; the privacy rescan deletes a matching plan whole. trip_key is plain
-- text, not a foreign key: trips are imported tables that cli reapply rebuilds.

CREATE TABLE trip_plans (
    plan_id      TEXT    PRIMARY KEY CHECK (length(plan_id) = 32),
    created_by   INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    -- From the request's place, after the question checks. Shown in the list.
    title        TEXT    NOT NULL,
    status       TEXT    NOT NULL CHECK (status IN ('running', 'ready', 'failed')),
    request_json TEXT    NOT NULL,
    trip_key     TEXT,
    model        TEXT    NOT NULL
) STRICT;
CREATE INDEX trip_plans_updated ON trip_plans (updated_at);

CREATE TABLE trip_plan_turns (
    plan_id      TEXT    NOT NULL REFERENCES trip_plans (plan_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    kind         TEXT    NOT NULL CHECK (kind IN ('request', 'research', 'plan', 'notice')),
    content_json TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    PRIMARY KEY (plan_id, seq)
) STRICT;
-- A request turn counts toward the daily question limit, like an Ask question.
CREATE INDEX trip_plan_turns_kind_at ON trip_plan_turns (kind, created_at);

-- ai_usage gets the planner's two feature names, the billed web search count,
-- the plan a call belongs to and the question flag. SQLite cannot change a
-- CHECK in place, so the table is copied. Every column is named: the new ones
-- take their defaults. Nothing references ai_usage, and its own foreign keys
-- stay valid.
CREATE TABLE ai_usage_new (
    id                          INTEGER PRIMARY KEY,
    at                          TEXT    NOT NULL,
    user_id                     INTEGER REFERENCES users (id) ON DELETE SET NULL,
    feature                     TEXT    NOT NULL
                                CHECK (feature IN ('ask', 'review', 'categorize', 'cache_check',
                                                   'plan_research', 'plan_write')),
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
    error                       TEXT,
    -- Billed web searches (`usage.server_tool_use.web_search_requests`).
    web_searches                INTEGER NOT NULL DEFAULT 0 CHECK (web_searches >= 0),
    plan_id                     TEXT    REFERENCES trip_plans (plan_id) ON DELETE SET NULL,
    -- 1 on the first call of a plan or follow-up. After the plan is deleted it
    -- still counts toward the daily question limit.
    question                    INTEGER NOT NULL DEFAULT 0 CHECK (question IN (0, 1))
) STRICT;
INSERT INTO ai_usage_new (id, at, user_id, feature, model, effort, input_tokens,
                          cache_creation_input_tokens, cache_read_input_tokens, output_tokens,
                          cost_micro_usd, stop_reason, request_id, latency_ms, thread_id, error)
    SELECT id, at, user_id, feature, model, effort, input_tokens,
           cache_creation_input_tokens, cache_read_input_tokens, output_tokens,
           cost_micro_usd, stop_reason, request_id, latency_ms, thread_id, error
    FROM ai_usage;
-- Stop the migration (it runs in one transaction) unless every row and its
-- cost came across.
CREATE TEMP TABLE ai_usage_copy_check (ok INTEGER NOT NULL CHECK (ok = 1)) STRICT;
INSERT INTO ai_usage_copy_check
    SELECT (SELECT count(*) FROM ai_usage) = (SELECT count(*) FROM ai_usage_new)
       AND (SELECT total(cost_micro_usd) FROM ai_usage)
         = (SELECT total(cost_micro_usd) FROM ai_usage_new);
DROP TABLE ai_usage_copy_check;
DROP TABLE ai_usage;
ALTER TABLE ai_usage_new RENAME TO ai_usage;
CREATE INDEX ai_usage_at ON ai_usage (at);
CREATE INDEX ai_usage_plan ON ai_usage (plan_id);
