"""Tests for the database layer (synthetic data only)."""

from __future__ import annotations

import sqlite3

import pytest

from leadgen import db
from leadgen.core import paths


@pytest.fixture
def conn():
    db.init_db()
    connection = db.connect()
    yield connection
    connection.close()


def make_project(conn, name="Northwind project") -> int:
    return db.insert(conn, "projects", name=name)


def make_campaign(conn, name="Northwind push") -> int:
    return db.insert(conn, "campaigns", name=name, project_id=make_project(conn, f"P {name}"))


def make_company(conn, campaign_id, name="Northwind Logistics", domain="northwind.example.com"):
    return db.insert(conn, "companies", campaign_id=campaign_id, name=name, domain=domain)


def make_person(conn, campaign_id, company_id, url="https://www.linkedin.com/in/aria-tan"):
    return db.insert(
        conn,
        "people",
        campaign_id=campaign_id,
        company_id=company_id,
        first_name="Aria",
        last_name="Tan",
        linkedin_url=url,
    )


def count(conn, table) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- connection and schema -------------------------------------------------------------------


def test_connect_pragmas(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.row_factory is sqlite3.Row


def test_init_db_creates_database_in_workspace_and_is_idempotent():
    assert db.init_db() == db.SCHEMA_VERSION == 4
    assert paths.db_path().exists()
    assert db.init_db() == db.SCHEMA_VERSION
    with db.session() as conn:
        assert count(conn, "schema_version") == 4
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert db.TABLES <= names


def test_init_db_keeps_existing_data(conn):
    make_campaign(conn)
    conn.commit()
    db.init_db()
    assert count(conn, "campaigns") == 1


def test_connect_explicit_path_creates_parent(tmp_path):
    target = tmp_path / "nested" / "x.db"
    db.init_db(target)
    assert target.exists()


def test_connect_rejects_old_sqlite(monkeypatch):
    monkeypatch.setattr(db.sqlite3, "sqlite_version_info", (3, 30, 0))
    with pytest.raises(RuntimeError):
        db.connect()


def test_every_foreign_key_has_an_index(conn):
    for table in sorted(db.TABLES):
        indexed_first_cols = set()
        for idx in conn.execute(f"PRAGMA index_list({table})").fetchall():
            if idx["partial"]:
                continue
            first = conn.execute(f"PRAGMA index_info({idx['name']})").fetchone()
            indexed_first_cols.add(first["name"])
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
            assert fk["from"] in indexed_first_cols, f"{table}.{fk['from']} has no index"


def test_required_indexes_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_people_campaign_status", "idx_messages_status", "idx_qa_issues_entity"} <= names


# --- migrations ------------------------------------------------------------------------------


def test_migrations_apply_once_and_in_order(monkeypatch):
    migrations = [
        (2, "ALTER TABLE offers ADD COLUMN note TEXT NOT NULL DEFAULT '';"),
        (3, "CREATE TABLE extra (id INTEGER PRIMARY KEY);"),
    ]
    monkeypatch.setattr(db, "MIGRATIONS", migrations)
    assert db.init_db() == 3
    assert db.init_db() == 3
    with db.session() as conn:
        assert db.current_version(conn) == 3
        assert count(conn, "schema_version") == 3
        assert "note" in {r[1] for r in conn.execute("PRAGMA table_info(offers)")}


def test_failed_migration_rolls_back(monkeypatch):
    db.init_db()
    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        [(5, "CREATE TABLE half_done (id INTEGER); INSERT INTO no_such_table VALUES (1);")],
    )
    with pytest.raises(sqlite3.Error):
        db.init_db()
    with db.session() as conn:
        assert db.current_version(conn) == db.SCHEMA_VERSION
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "half_done" not in tables


def test_bad_migration_numbering_rejected(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [(3, "SELECT 1;"), (2, "SELECT 1;")])
    with pytest.raises(ValueError):
        db.init_db()
    monkeypatch.setattr(db, "MIGRATIONS", [(1, "SELECT 1;")])
    with pytest.raises(ValueError):
        db.init_db()


# --- session ---------------------------------------------------------------------------------


def test_session_commits_and_rolls_back(conn):
    with db.session() as s:
        db.insert(s, "offers", name="Kept")
    with pytest.raises(RuntimeError):
        with db.session() as s:
            db.insert(s, "offers", name="Dropped")
            raise RuntimeError("boom")
    names = [r["name"] for r in db.query(conn, "SELECT name FROM offers")]
    assert names == ["Kept"]


# --- helpers ---------------------------------------------------------------------------------


def test_now_format():
    value = db.now()
    assert value.endswith("Z") and "T" in value and "+" not in value


def test_insert_get_and_timestamps(conn):
    cid = make_campaign(conn)
    row = db.get(conn, "campaigns", cid)
    assert row is not None
    assert row["name"] == "Northwind push"
    assert row["created_at"] == row["updated_at"]
    assert row["countries_json"] == []  # default decoded
    assert row["status"] == "draft"
    assert db.get(conn, "campaigns", 9999) is None


def test_tables_without_created_at_are_handled(conn):
    uid = db.insert(conn, "api_usage", provider="serper", day="2026-10-14", calls=1)
    usage = db.get(conn, "api_usage", uid)
    assert "created_at" not in usage and usage["updated_at"]
    sid = db.insert(
        conn,
        "search_cache",
        provider="serper",
        query_hash="h",
        fetched_at=db.now(),
        results_json=[],
    )
    assert db.get(conn, "search_cache", sid)["results_json"] == []


def test_json_round_trip(conn):
    cid = db.insert(
        conn,
        "campaigns",
        name="JSON",
        project_id=make_project(conn),
        countries_json=["Singapore", "Malaysia"],
        options_json={"invite_note": False, "search_mode": "auto", "note": "café – ✓"},
    )
    row = db.get(conn, "campaigns", cid)
    assert row["countries_json"] == ["Singapore", "Malaysia"]
    assert row["options_json"] == {"invite_note": False, "search_mode": "auto", "note": "café – ✓"}
    raw = conn.execute("SELECT options_json FROM campaigns WHERE id=?", (cid,)).fetchone()[0]
    assert isinstance(raw, str) and "café" in raw  # stored as text, not escaped
    assert db.query(conn, "SELECT countries_json FROM campaigns WHERE id=?", (cid,)) == [
        {"countries_json": ["Singapore", "Malaysia"]}
    ]
    db.update(conn, "campaigns", cid, countries_json=["Ireland"])
    assert db.get(conn, "campaigns", cid)["countries_json"] == ["Ireland"]


def test_json_string_and_none_and_invalid_text(conn):
    oid = db.insert(conn, "offers", name="O", profile_json="plain text")
    assert db.get(conn, "offers", oid)["profile_json"] == "plain text"
    assert conn.execute("SELECT profile_json FROM offers").fetchone()[0] == '"plain text"'
    nid = db.insert(conn, "offers", name="N")
    assert db.get(conn, "offers", nid)["profile_json"] is None
    conn.execute("UPDATE offers SET profile_json = 'not json {' WHERE id = ?", (nid,))
    assert db.get(conn, "offers", nid)["profile_json"] == "not json {"


def test_update_bumps_updated_at(conn, monkeypatch):
    monkeypatch.setattr(db, "now", lambda: "2026-01-01T00:00:00.000Z")
    cid = make_campaign(conn)
    monkeypatch.setattr(db, "now", lambda: "2026-02-02T00:00:00.000Z")
    assert db.update(conn, "campaigns", cid, status="active") is True
    row = db.get(conn, "campaigns", cid)
    assert row["status"] == "active"
    assert row["created_at"] == "2026-01-01T00:00:00.000Z"
    assert row["updated_at"] == "2026-02-02T00:00:00.000Z"
    assert db.update(conn, "campaigns", 9999, status="active") is False


def test_update_validation(conn):
    cid = make_campaign(conn)
    with pytest.raises(ValueError):
        db.update(conn, "campaigns", cid)


def test_table_and_column_whitelist(conn):
    with pytest.raises(ValueError):
        db.insert(conn, "sqlite_master", name="x")
    with pytest.raises(ValueError):
        db.get(conn, "campaigns; DROP TABLE campaigns", 1)
    with pytest.raises(ValueError):
        db.insert(conn, "campaigns", name="x", **{"bogus) VALUES (1); --": 1})
    with pytest.raises(ValueError):
        db.update(conn, "campaigns", 1, nope=1)
    with pytest.raises(ValueError):
        db.upsert(conn, "settings", ["key"], key="a", nope=1)
    assert count(conn, "campaigns") == 0


def test_query_with_params(conn):
    make_campaign(conn, "A")
    make_campaign(conn, "B")
    rows = db.query(conn, "SELECT name FROM campaigns WHERE name = ?", ("B",))
    assert rows == [{"name": "B"}]
    rows = db.query(conn, "SELECT name FROM campaigns WHERE name = :n", {"n": "A"})
    assert rows == [{"name": "A"}]


# --- upsert and unique constraints -----------------------------------------------------------


def test_upsert_inserts_then_updates(conn, monkeypatch):
    monkeypatch.setattr(db, "now", lambda: "2026-01-01T00:00:00.000Z")
    first = db.upsert(conn, "settings", ["key"], key="theme", value="light")
    monkeypatch.setattr(db, "now", lambda: "2026-03-03T00:00:00.000Z")
    second = db.upsert(conn, "settings", ["key"], key="theme", value="dark")
    assert first == second
    assert count(conn, "settings") == 1
    row = db.get(conn, "settings", first)
    assert row["value"] == "dark"
    assert row["created_at"] == "2026-01-01T00:00:00.000Z"
    assert row["updated_at"] == "2026-03-03T00:00:00.000Z"


def test_upsert_with_nothing_to_change_returns_id(conn):
    a = db.upsert(conn, "suppression", ["kind", "value"], kind="email", value="x@example.com")
    b = db.upsert(conn, "suppression", ["kind", "value"], kind="email", value="x@example.com")
    assert a == b


def test_upsert_composite_and_json(conn):
    keys = ["provider", "day"]
    a = db.upsert(conn, "api_usage", keys, provider="serper", day="d1", calls=1)
    b = db.upsert(conn, "api_usage", keys, provider="serper", day="d1", calls=7)
    c = db.upsert(conn, "api_usage", keys, provider="serper", day="d2", calls=1)
    assert a == b != c
    assert db.get(conn, "api_usage", a)["calls"] == 7
    cache_keys = ["provider", "query_hash"]
    s1 = db.upsert(
        conn,
        "search_cache",
        cache_keys,
        provider="serper",
        query_hash="h",
        fetched_at="t1",
        results_json=[{"url": "u"}],
    )
    s2 = db.upsert(
        conn,
        "search_cache",
        cache_keys,
        provider="serper",
        query_hash="h",
        fetched_at="t2",
        results_json=[],
    )
    assert s1 == s2
    assert db.get(conn, "search_cache", s1)["results_json"] == []


def test_upsert_argument_validation(conn):
    with pytest.raises(ValueError):
        db.upsert(conn, "settings", [], key="a")
    with pytest.raises(ValueError):
        db.upsert(conn, "settings", ["key"], value="a")


def test_upsert_company_and_person_partial_indexes(conn):
    cid = make_campaign(conn)
    ckeys = ["campaign_id", "domain"]
    a = db.upsert(conn, "companies", ckeys, campaign_id=cid, domain="a.example.com", name="A One")
    b = db.upsert(conn, "companies", ckeys, campaign_id=cid, domain="a.example.com", name="A Two")
    assert a == b and db.get(conn, "companies", a)["name"] == "A Two"
    # An empty domain never conflicts: a new row each time.
    e1 = db.upsert(conn, "companies", ckeys, campaign_id=cid, domain="", name="No Site 1")
    e2 = db.upsert(conn, "companies", ckeys, campaign_id=cid, domain="", name="No Site 2")
    assert e1 != e2
    pkeys = ["campaign_id", "linkedin_url"]
    url = "https://www.linkedin.com/in/x"
    p1 = db.upsert(
        conn, "people", pkeys, campaign_id=cid, company_id=a, linkedin_url=url, title="A"
    )
    p2 = db.upsert(
        conn, "people", pkeys, campaign_id=cid, company_id=a, linkedin_url=url, title="B"
    )
    assert p1 == p2 and db.get(conn, "people", p1)["title"] == "B"


def test_unknown_conflict_target_raises(conn):
    with pytest.raises(sqlite3.OperationalError):
        db.upsert(conn, "settings", ["value"], value="v")


def test_partial_unique_allows_many_empty_domains_but_not_duplicates(conn):
    cid = make_campaign(conn)
    make_company(conn, cid, "No Site 1", "")
    make_company(conn, cid, "No Site 2", "")
    assert count(conn, "companies") == 2
    make_company(conn, cid, "Northwind", "northwind.example.com")
    with pytest.raises(sqlite3.IntegrityError):
        make_company(conn, cid, "Northwind Again", "northwind.example.com")
    other = make_campaign(conn, "Other")
    make_company(conn, other, "Northwind", "northwind.example.com")  # different campaign is fine


def test_people_unique_linkedin_partial(conn):
    cid = make_campaign(conn)
    co = make_company(conn, cid)
    make_person(conn, cid, co, "")
    make_person(conn, cid, co, "")  # empty URLs may repeat
    make_person(conn, cid, co, "https://www.linkedin.com/in/aria-tan")
    with pytest.raises(sqlite3.IntegrityError):
        make_person(conn, cid, co, "https://www.linkedin.com/in/aria-tan")


@pytest.mark.parametrize(
    ("table", "values"),
    [
        ("suppression", {"kind": "email", "value": "a@example.com"}),
        ("api_usage", {"provider": "serper", "day": "2026-10-14"}),
        ("search_cache", {"provider": "serper", "query_hash": "h", "fetched_at": "t"}),
        ("settings", {"key": "k"}),
    ],
)
def test_simple_unique_constraints(conn, table, values):
    db.insert(conn, table, **values)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, table, **values)


def test_email_and_message_uniques(conn):
    cid = make_campaign(conn)
    pid = make_person(conn, cid, make_company(conn, cid))
    db.insert(conn, "emails", person_id=pid, email="aria.tan@northwind.example.com")
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, "emails", person_id=pid, email="aria.tan@northwind.example.com")
    db.insert(conn, "emails", person_id=pid, email="a.tan@northwind.example.com")
    db.insert(conn, "messages", person_id=pid, channel="email")
    db.insert(conn, "messages", person_id=pid, channel="linkedin_invite")
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, "messages", person_id=pid, channel="email")


# --- foreign keys ----------------------------------------------------------------------------


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, "companies", campaign_id=999, name="Orphan")


def test_campaign_delete_cascades_everywhere(conn):
    cid = make_campaign(conn)
    company = make_company(conn, cid)
    pid = make_person(conn, cid, company)
    db.insert(conn, "emails", person_id=pid, email="aria.tan@northwind.example.com")
    mid = db.insert(conn, "messages", person_id=pid, channel="email", body="Hi Aria")
    sid = db.insert(conn, "sends", message_id=mid, to_email="aria.tan@northwind.example.com")
    db.insert(conn, "events", send_id=sid, type="reply", at=db.now())
    db.insert(conn, "search_tasks", campaign_id=cid, kind="people", company_id=company, query="q")
    other = make_campaign(conn, "Keep me")
    make_company(conn, other, "Contoso", "contoso.example.com")

    conn.execute("DELETE FROM campaigns WHERE id = ?", (cid,))

    for table in ("people", "emails", "messages", "sends", "events", "search_tasks"):
        assert count(conn, table) == 0, table
    assert count(conn, "companies") == 1  # only the other campaign's company remains


def test_company_delete_cascades_to_people(conn):
    cid = make_campaign(conn)
    company = make_company(conn, cid)
    make_person(conn, cid, company)
    conn.execute("DELETE FROM companies WHERE id = ?", (company,))
    assert count(conn, "people") == 0


def test_offer_with_campaigns_cannot_be_deleted(conn):
    oid = db.insert(conn, "offers", name="Offer")
    db.insert(conn, "campaigns", name="C", offer_id=oid, project_id=make_project(conn))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM offers WHERE id = ?", (oid,))
