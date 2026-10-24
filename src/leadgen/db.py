"""SQLite access: connections, schema and migrations, and small typed CRUD helpers.

No business logic lives here; services own that.

Conventions
- One connection per call site. Use `session()` (commits or rolls back, then closes) or `connect()`.
- Every helper takes the connection first: `insert(conn, "people", ...)`.
- Table names are checked against a fixed whitelist and column names against the real table
  columns, so no SQL is ever built from unvalidated names. Values are always bound parameters.
- Columns whose name ends in `_json` are encoded with `json.dumps` on write (any JSON-serialisable
  value, including a plain string, is stored as JSON) and decoded with `json.loads` on read, by
  `get` and `query`. `None` is stored as NULL. Text that is not valid
  JSON is returned unchanged rather than raising.
- Timestamps are ISO-8601 UTC text ending in `Z` (see `now`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from leadgen.core import paths

BASE_VERSION = 1
MIN_SQLITE = (3, 35, 0)  # needed for RETURNING

_V2_SQL = """
CREATE TABLE projects (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    offer_id      INTEGER REFERENCES offers(id) ON DELETE RESTRICT,
    cooldown_days INTEGER NOT NULL DEFAULT 90,
    status        TEXT NOT NULL DEFAULT 'active'     -- active / archived
);
CREATE UNIQUE INDEX uq_projects_name ON projects(name COLLATE NOCASE);
CREATE INDEX idx_projects_offer ON projects(offer_id);

ALTER TABLE campaigns ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE RESTRICT;
CREATE INDEX idx_campaigns_project ON campaigns(project_id);

CREATE TABLE contact_log (
    id               INTEGER PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    campaign_id      INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    person_id        INTEGER REFERENCES people(id) ON DELETE SET NULL,
    key_linkedin     TEXT NOT NULL DEFAULT '',
    key_email        TEXT NOT NULL DEFAULT '',
    key_name_company TEXT NOT NULL DEFAULT '',
    channel          TEXT NOT NULL,   -- found / exported_csv / lemlist / email_sent / imported
    at               TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_contact_log_linkedin ON contact_log(project_id, key_linkedin);
CREATE INDEX idx_contact_log_email ON contact_log(project_id, key_email);
CREATE INDEX idx_contact_log_name ON contact_log(project_id, key_name_company);
CREATE INDEX idx_contact_log_campaign ON contact_log(campaign_id);
CREATE INDEX idx_contact_log_person ON contact_log(person_id);

CREATE TRIGGER trg_campaigns_project_required BEFORE INSERT ON campaigns
WHEN NEW.project_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'campaigns.project_id is required');
END;
CREATE TRIGGER trg_campaigns_project_kept BEFORE UPDATE OF project_id ON campaigns
WHEN NEW.project_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'campaigns.project_id cannot be cleared');
END;
"""


def _unique_project_name(name: str, used: set[str]) -> str:
    base = " ".join(name.split())[:120].strip() or "Project"
    candidate, n = base, 1
    while candidate.casefold() in used:
        n += 1
        candidate = f"{base} ({n})"
    used.add(candidate.casefold())
    return candidate


def _v2_assign_projects(conn: sqlite3.Connection) -> None:
    """Give every offer a project named after it and put each campaign in its offer's project.

    Campaigns without an offer (allowed in v1) go to one extra project. Nothing is deleted.
    """
    stamp = now()
    used: set[str] = set()
    for offer in conn.execute("SELECT id, name FROM offers ORDER BY id").fetchall():
        name = _unique_project_name(offer[1] or f"Offer {offer[0]}", used)
        cur = conn.execute(
            "INSERT INTO projects (created_at, updated_at, name, offer_id) VALUES (?, ?, ?, ?)",
            (stamp, stamp, name, offer[0]),
        )
        conn.execute(
            "UPDATE campaigns SET project_id = ? WHERE offer_id = ?", (cur.lastrowid, offer[0])
        )
    if conn.execute("SELECT 1 FROM campaigns WHERE project_id IS NULL LIMIT 1").fetchone():
        name = _unique_project_name("Campaigns without an offer", used)
        cur = conn.execute(
            "INSERT INTO projects (created_at, updated_at, name) VALUES (?, ?, ?)",
            (stamp, stamp, name),
        )
        conn.execute(
            "UPDATE campaigns SET project_id = ? WHERE project_id IS NULL", (cur.lastrowid,)
        )
    from leadgen.services import projects  # late import: services import this module

    projects.backfill_v1_contacts(conn)


# Forward-only migrations applied after schema.sql, as (version, sql) or (version, sql, after).
# Versions must be greater than BASE_VERSION and strictly increasing. Each runs in one
# transaction, so the SQL must not contain BEGIN/COMMIT; `after(conn)` (optional) runs in the
# same transaction once the SQL has run, for data changes that plain SQL cannot express.
# v2 (T23): projects, contact memory, campaigns.project_id.
Migration = tuple[int, str] | tuple[int, str, Callable[[sqlite3.Connection], None]]
MIGRATIONS: list[Migration] = [(2, _V2_SQL, _v2_assign_projects)]
SCHEMA_VERSION = max([BASE_VERSION, *(m[0] for m in MIGRATIONS)])

TABLES = frozenset(
    {
        "schema_version",
        "offers",
        "projects",
        "campaigns",
        "contact_log",
        "companies",
        "people",
        "search_tasks",
        "emails",
        "messages",
        "qa_issues",
        "suppression",
        "sends",
        "events",
        "api_usage",
        "search_cache",
        "settings",
    }
)

# Conflict targets of partial unique indexes need the index's WHERE clause repeated.
_PARTIAL_CONFLICT: dict[tuple[str, frozenset[str]], str] = {
    ("companies", frozenset({"campaign_id", "domain"})): "domain <> ''",
    ("people", frozenset({"campaign_id", "linkedin_url"})): "linkedin_url <> ''",
}

_JSON_SUFFIX = "_json"


def now() -> str:
    """Current UTC time as ISO-8601 text with a trailing Z (millisecond precision)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection (default: the workspace database) with foreign keys, WAL and a timeout."""
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is too old; 3.35 or newer is needed")
    if path is None:
        path = paths.db_path()
    elif str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


@contextmanager
def session(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on any error, always close."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def current_version(conn: sqlite3.Connection) -> int:
    """Highest applied schema version (0 if the schema has not been created)."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0)


def _schema_sql() -> str:
    return resources.files("leadgen").joinpath("schema.sql").read_text(encoding="utf-8")


def _check_migrations() -> None:
    last = BASE_VERSION
    for migration in MIGRATIONS:
        version = migration[0]
        if version <= last:
            raise ValueError(f"migration versions must be increasing and above {BASE_VERSION}")
        last = version


def init_db(path: str | Path | None = None) -> int:
    """Create or update the database (idempotent) and return the schema version now in place."""
    _check_migrations()
    conn = connect(path)
    try:
        conn.executescript(_schema_sql())
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (BASE_VERSION, now()),
        )
        conn.commit()
        applied = current_version(conn)
        for migration in MIGRATIONS:
            version, sql = migration[0], migration[1]
            if version <= applied:
                continue
            try:
                conn.executescript("BEGIN;\n" + sql)
                if len(migration) > 2:
                    migration[2](conn)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (version, now()),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            applied = version
        return applied
    finally:
        conn.close()


# --- helpers -------------------------------------------------------------------------------


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if table not in TABLES:
        raise ValueError(f"unknown table: {table!r}")
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _check_names(conn: sqlite3.Connection, table: str, names: Sequence[str]) -> set[str]:
    columns = _columns(conn, table)
    unknown = [n for n in names if n not in columns]
    if unknown:
        raise ValueError(f"unknown column(s) for {table}: {', '.join(sorted(unknown))}")
    return columns


def _encode(values: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key.endswith(_JSON_SUFFIX) and value is not None:
            value = json.dumps(value, ensure_ascii=False)
        out[key] = value
    return out


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key.endswith(_JSON_SUFFIX) and isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        out[key] = value
    return out


def insert(conn: sqlite3.Connection, table: str, **values: Any) -> int:
    """Insert one row and return its id. Sets created_at/updated_at when the table has them."""
    columns = _check_names(conn, table, list(values))
    stamp = now()
    data = _encode(values)
    for col in ("created_at", "updated_at"):
        if col in columns:
            data.setdefault(col, stamp)
    names = list(data)
    sql = (
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})"
        if names
        else f"INSERT INTO {table} DEFAULT VALUES"
    )
    return int(conn.execute(sql, [data[n] for n in names]).lastrowid or 0)


def update(conn: sqlite3.Connection, table: str, id: int, **values: Any) -> bool:
    """Update the given columns of one row and bump updated_at. True if a row was changed."""
    if not values:
        raise ValueError("update needs at least one column to change")
    columns = _check_names(conn, table, list(values))
    data = _encode(values)
    if "updated_at" in columns:
        data.setdefault("updated_at", now())
    sets = ", ".join(f"{n} = ?" for n in data)
    cur = conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*data.values(), id])
    return cur.rowcount > 0


def get(conn: sqlite3.Connection, table: str, id: int) -> dict[str, Any] | None:
    """Fetch one row by id as a dict (JSON columns decoded), or None."""
    _columns(conn, table)  # validates the table name
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id,)).fetchone()
    return _decode(row) if row is not None else None


def query(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
) -> list[dict[str, Any]]:
    """Run a SELECT with bound parameters; return dicts with JSON columns decoded.

    `sql` must be written by the calling code (never built from user input); pass user values
    only through `params`.
    """
    return [_decode(row) for row in conn.execute(sql, params).fetchall()]


def upsert(
    conn: sqlite3.Connection, table: str, unique_cols: Sequence[str], **values: Any
) -> int:
    """Insert, or update the existing row with the same `unique_cols`; return its id.

    `unique_cols` must match a UNIQUE constraint or unique index of the table and be present in
    `values`. For the partial unique indexes (companies domain, people linkedin_url) an empty
    value never conflicts, so it always inserts a new row. created_at is kept on update.
    """
    if not unique_cols:
        raise ValueError("unique_cols must not be empty")
    missing = [c for c in unique_cols if c not in values]
    if missing:
        raise ValueError(f"values must include the unique column(s): {', '.join(missing)}")
    columns = _check_names(conn, table, list(values))
    stamp = now()
    data = _encode(values)
    for col in ("created_at", "updated_at"):
        if col in columns:
            data.setdefault(col, stamp)
    names = list(data)
    changing = [n for n in names if n not in unique_cols and n not in ("id", "created_at")]
    if not changing:
        # Nothing to change on conflict; a no-op assignment still lets RETURNING give the id.
        changing = [unique_cols[0]]
    assignments = ", ".join(f"{n} = excluded.{n}" for n in changing)
    where = _PARTIAL_CONFLICT.get((table, frozenset(unique_cols)))
    target = f"({', '.join(unique_cols)})" + (f" WHERE {where}" if where else "")
    sql = (
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)}) "
        f"ON CONFLICT {target} DO UPDATE SET {assignments} RETURNING id"
    )
    rows = conn.execute(sql, [data[n] for n in names]).fetchall()
    return int(rows[0][0])
