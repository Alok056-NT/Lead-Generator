# T02: Database layer and schema

Depends on: T01. PLAN sections: 6.

## Goal
A small, well-tested SQLite layer holding every table in PLAN section 6, with forward-only migrations and typed helper functions the services will use.

## Deliverables
- `src/leadgen/schema.sql`: `CREATE TABLE IF NOT EXISTS` for every table in PLAN section 6, with:
  - `id INTEGER PRIMARY KEY`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL` on every table except `schema_version`, `search_cache`, `api_usage` (they get their own timestamps).
  - Foreign keys with `ON DELETE CASCADE` from companies→campaigns, people→campaigns/companies, emails→people, messages→people, sends→messages, events→sends.
  - Unique constraints: `companies(campaign_id, domain)` (domain normalised lower-case, may be empty: use a partial unique index `WHERE domain <> ''`), `people(campaign_id, linkedin_url)` (partial unique where not empty), `emails(person_id, email)`, `messages(person_id, channel)` (one current message per channel per person), `suppression(kind, value)`, `api_usage(provider, day)`, `search_cache(provider, query_hash)`.
  - Indexes on every foreign key and on `people(campaign_id, status)`, `messages(status)`, `qa_issues(entity_type, entity_id)`.
- `src/leadgen/db.py`:
  - `connect(path=None) -> sqlite3.Connection`: `row_factory = sqlite3.Row`, `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`. One connection per call site; provide a context manager `session()` that commits or rolls back.
  - `init_db()`: applies `schema.sql`, then migrations from a list `MIGRATIONS: list[tuple[int, str]]` (empty for now) tracked in `schema_version`.
  - `now() -> str` ISO-8601 UTC with `Z`.
  - Generic helpers: `insert(table, **values) -> int` (sets created/updated), `update(table, id, **values)`, `get(table, id) -> dict | None`, `query(sql, params) -> list[dict]`, `upsert(table, unique_cols, **values) -> int` (insert or update on conflict; returns id).
  - JSON helpers: columns ending `_json` are auto-encoded on write and decoded on read by the helpers (document this).
  - Whitelist table names in helpers (no SQL built from untrusted input).
- No business logic here: services own that.

## Tests
- `init_db` idempotent (run twice).
- Each unique constraint enforced (duplicate insert raises / upsert updates).
- Cascade deletes: deleting a campaign removes its companies, people, emails, messages.
- JSON round-trip for `_json` columns.
- `update` bumps `updated_at`.
- Partial unique index allows multiple empty domains.

## Acceptance
`pytest -q` passes; `leadgen doctor` now reports "database: ok (schema vN)" and creates the db on first run.

## Out of scope
Service functions (T05+).
