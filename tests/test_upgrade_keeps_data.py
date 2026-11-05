"""Upgrade keeps data (v1.3.2): every row and value of an old workspace survives the migrations.

The v1.0 workspace is the one `tests/e2e/test_upgrade_v10.py` builds (schema 1, filled from the
invented universe). Here the folder is copied and opened with the normal `db.init_db()` path, then
every value of every old table is compared with what was there before, column by column.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# The v1.0 builder and the invented universe live next to the e2e tests.
sys.path.insert(0, str(Path(__file__).parent / "e2e"))
from test_upgrade_v10 import OLD_TABLES, build_v10_workspace  # noqa: E402

from leadgen import db  # noqa: E402
from leadgen.core import paths  # noqa: E402

NEW_TABLES = ("known_contacts", "chat_messages", "message_templates", "phones", "ai_usage")


def read_old_tables(path, columns=None) -> dict[str, tuple[list[str], list[tuple]]]:
    """Every row of each old table, in rowid order. `columns` limits the read to those names."""
    out = {}
    with db.session(path) as conn:
        for table in OLD_TABLES:
            names = (columns or {}).get(table) or [
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            ]
            rows = conn.execute(f"SELECT {', '.join(names)} FROM {table} ORDER BY rowid").fetchall()
            out[table] = (names, [tuple(row) for row in rows])
    return out


def test_upgrading_a_v10_workspace_keeps_every_row_and_value(tmp_path, monkeypatch):
    info = build_v10_workspace()  # the autouse fixture keeps this inside a temp LEADGEN_HOME
    old_db = paths.db_path()
    before = read_old_tables(old_db)
    columns = {table: names for table, (names, _) in before.items()}
    assert len(before["people"][1]) > 100 and len(before["companies"][1]) == 12

    # back up the whole folder, as an owner would, and open the copy with the current code
    copy_home = tmp_path / "LeadGenerator_copy"
    shutil.copytree(paths.home(), copy_home)
    monkeypatch.setenv("LEADGEN_HOME", str(copy_home))
    copy_db = paths.db_path()
    assert copy_db == copy_home / "leadgen.db"

    assert db.init_db(copy_db) == db.SCHEMA_VERSION
    after = read_old_tables(copy_db, columns)
    for table in OLD_TABLES:
        assert len(after[table][1]) == len(before[table][1]), f"{table}: row count changed"
        assert after[table] == before[table], f"{table}: a value changed"

    # the old database is untouched: still schema 1, still the same rows
    with db.session(old_db) as conn:
        assert db.current_version(conn) == 1
    assert read_old_tables(old_db, columns) == before

    with db.session(copy_db) as conn:
        assert db.current_version(conn) == db.SCHEMA_VERSION
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
        assert sorted(versions) == list(range(1, db.SCHEMA_VERSION + 1))
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # the old campaign now sits in a project named after its offer
        orphans = conn.execute("SELECT COUNT(*) FROM campaigns WHERE project_id IS NULL")
        assert orphans.fetchone()[0] == 0
        project = db.query(conn, "SELECT * FROM projects")[0]
        assert project["name"] == "Lanternfish VR"
        assert project["cooldown_days"] == 90 and project["email_policy"] == "verified_only"

        # contact memory: every found person and the one emailed address
        found = conn.execute("SELECT COUNT(*) FROM contact_log WHERE channel = 'found'").fetchone()
        sent = conn.execute("SELECT COUNT(*) FROM contact_log WHERE channel = 'email_sent'")
        assert found[0] == info["people"] and sent.fetchone()[0] == 1

        # the new columns and tables start empty, with their defaults
        choices = conn.execute("SELECT COUNT(*) FROM messages WHERE owner_choice <> ''")
        assert choices.fetchone()[0] == 0
        for table in NEW_TABLES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table

    # opening again changes nothing
    assert db.init_db(copy_db) == db.SCHEMA_VERSION
    assert read_old_tables(copy_db, columns) == before
