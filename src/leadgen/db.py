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
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from leadgen.core import paths

BASE_VERSION = 1
MIN_SQLITE = (3, 35, 0)  # needed for RETURNING

# Forward-only migrations applied after schema.sql, as (version, sql). Versions must be greater
# than BASE_VERSION and strictly increasing. Each script runs in one transaction, so it must not
# contain BEGIN/COMMIT. Empty for now.
MIGRATIONS: list[tuple[int, str]] = []

TABLES = frozenset(
    {
        "schema_version",
        "offers",
        "campaigns",
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
    for version, _sql in MIGRATIONS:
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
        for version, sql in MIGRATIONS:
            if version <= applied:
                continue
            try:
                conn.executescript("BEGIN;\n" + sql)
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
