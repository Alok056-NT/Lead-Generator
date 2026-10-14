-- Lead Generator base schema (schema version 1). Idempotent: safe to run on every start.
-- Later changes go in db.MIGRATIONS, never in edits to existing tables here.
-- Timestamps are ISO-8601 UTC text set by the helpers in db.py.
-- JSON columns (names ending in _json) hold JSON text; db.py encodes and decodes them.
-- Status and kind values are checked by the services, not by CHECK constraints, so a
-- forward-only migration is never needed just to add a new value.

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offers (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    source_type  TEXT NOT NULL DEFAULT 'text',      -- url / text / file
    source_ref   TEXT NOT NULL DEFAULT '',
    raw_text     TEXT NOT NULL DEFAULT '',
    profile_json TEXT                                -- OfferProfile; NULL until saved
);

CREATE TABLE IF NOT EXISTS campaigns (
    id                  INTEGER PRIMARY KEY,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    name                TEXT NOT NULL,
    offer_id            INTEGER REFERENCES offers(id) ON DELETE RESTRICT,
    countries_json      TEXT NOT NULL DEFAULT '[]',
    sectors_json        TEXT NOT NULL DEFAULT '[]',
    company_size        TEXT NOT NULL DEFAULT '',
    channels_json       TEXT NOT NULL DEFAULT '[]',
    designations_json   TEXT NOT NULL DEFAULT '[]',
    seniority_min       TEXT NOT NULL DEFAULT 'manager',
    exclusions_json     TEXT NOT NULL DEFAULT '[]',
    budget_json         TEXT NOT NULL DEFAULT '{}',
    options_json        TEXT NOT NULL DEFAULT '{}',
    locale              TEXT NOT NULL DEFAULT 'en-GB',
    status              TEXT NOT NULL DEFAULT 'draft'  -- draft / active / archived
);
CREATE INDEX IF NOT EXISTS idx_campaigns_offer ON campaigns(offer_id);

CREATE TABLE IF NOT EXISTS companies (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    domain       TEXT NOT NULL DEFAULT '',          -- lower-case; may be empty
    country      TEXT NOT NULL DEFAULT '',
    sector       TEXT NOT NULL DEFAULT '',
    size_note    TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    domain_ok    INTEGER,                           -- NULL unknown, 0 failed, 1 verified
    status       TEXT NOT NULL DEFAULT 'candidate', -- candidate / approved / excluded
    notes        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_companies_campaign ON companies(campaign_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_campaign_domain
    ON companies(campaign_id, domain) WHERE domain <> '';

CREATE TABLE IF NOT EXISTS people (
    id               INTEGER PRIMARY KEY,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    campaign_id      INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    company_id       INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    first_name       TEXT NOT NULL DEFAULT '',
    last_name        TEXT NOT NULL DEFAULT '',
    greeting_name    TEXT NOT NULL DEFAULT '',
    raw_name         TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    designation      TEXT NOT NULL DEFAULT '',
    seniority        TEXT NOT NULL DEFAULT '',
    country          TEXT NOT NULL DEFAULT '',
    linkedin_url     TEXT NOT NULL DEFAULT '',
    evidence_title   TEXT NOT NULL DEFAULT '',
    evidence_snippet TEXT NOT NULL DEFAULT '',
    source_query     TEXT NOT NULL DEFAULT '',
    evidence_origin  TEXT NOT NULL DEFAULT 'api',   -- api / ai_web_search / user
    spot_check       TEXT,                          -- NULL / pending / ok / wrong
    confirmed        INTEGER NOT NULL DEFAULT 0,
    reasons_json     TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'review' -- confirmed / review / rejected / removed
);
CREATE INDEX IF NOT EXISTS idx_people_campaign ON people(campaign_id);
CREATE INDEX IF NOT EXISTS idx_people_company ON people(company_id);
CREATE INDEX IF NOT EXISTS idx_people_campaign_status ON people(campaign_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_people_campaign_linkedin
    ON people(campaign_id, linkedin_url) WHERE linkedin_url <> '';

CREATE TABLE IF NOT EXISTS search_tasks (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                     -- people / email / company
    company_id   INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    group_label  TEXT NOT NULL DEFAULT '',
    query        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',      -- open / submitted / skipped
    submitted_at TEXT,
    result_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_search_tasks_campaign ON search_tasks(campaign_id);
CREATE INDEX IF NOT EXISTS idx_search_tasks_company ON search_tasks(company_id);

CREATE TABLE IF NOT EXISTS emails (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    method     TEXT NOT NULL DEFAULT '',
    pattern    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'unknown',     -- verified / risky / guess / invalid / unknown
    verifier   TEXT NOT NULL DEFAULT '',
    checked_at TEXT,
    UNIQUE (person_id, email)
);
CREATE INDEX IF NOT EXISTS idx_emails_person ON emails(person_id);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    person_id       INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    channel         TEXT NOT NULL,                  -- email / linkedin_invite / linkedin_message
    subject         TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',  -- draft / qa_fail / qa_pass / approved / sent
    qa_json         TEXT NOT NULL DEFAULT '{}',
    ai_review       TEXT NOT NULL DEFAULT '',       -- '' / pass / fix / reject
    ai_review_notes TEXT NOT NULL DEFAULT '',
    UNIQUE (person_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_messages_person ON messages(person_id);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

CREATE TABLE IF NOT EXISTS qa_issues (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    entity_type TEXT NOT NULL,                      -- person / message / ...
    entity_id   INTEGER NOT NULL,
    code        TEXT NOT NULL,
    severity    TEXT NOT NULL,                      -- block / warn
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_qa_issues_entity ON qa_issues(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS suppression (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    value      TEXT NOT NULL,
    kind       TEXT NOT NULL,                       -- email / domain / linkedin
    reason     TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, value)
);

CREATE TABLE IF NOT EXISTS sends (
    id                  INTEGER PRIMARY KEY,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    message_id          INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL DEFAULT '',
    to_email            TEXT NOT NULL DEFAULT '',
    provider_message_id TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'queued',
    sent_at             TEXT,
    error               TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sends_message ON sends(message_id);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    send_id    INTEGER NOT NULL REFERENCES sends(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,                       -- reply / bounce / unsubscribe / open / click
    at         TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_send ON events(send_id);

CREATE TABLE IF NOT EXISTS settings (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    key        TEXT NOT NULL UNIQUE,
    value      TEXT NOT NULL DEFAULT ''
);

-- These two tables keep their own timestamps instead of created_at/updated_at.
CREATE TABLE IF NOT EXISTS api_usage (
    id         INTEGER PRIMARY KEY,
    provider   TEXT NOT NULL,
    day        TEXT NOT NULL,                       -- YYYY-MM-DD (UTC)
    calls      INTEGER NOT NULL DEFAULT 0,
    credits    REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, day)
);

CREATE TABLE IF NOT EXISTS search_cache (
    id           INTEGER PRIMARY KEY,
    provider     TEXT NOT NULL,
    query_hash   TEXT NOT NULL,
    query        TEXT NOT NULL DEFAULT '',
    results_json TEXT NOT NULL DEFAULT '[]',
    fetched_at   TEXT NOT NULL,                     -- 30-day TTL is applied by the reader
    UNIQUE (provider, query_hash)
);
