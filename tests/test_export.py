"""CSV export: formats, include modes, reports and spreadsheet safety.

All people and companies are invented. No network is used.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from qa_world import (
    COMPANY,
    add_company,
    add_email,
    add_message,
    add_person,
    email_message,
    make_campaign,
    message_row,
    suppress,
)

from leadgen import db
from leadgen.services import export
from leadgen.services.export import ExportError, export_campaign, safe_cell

NAMES = ("Aria", "Marcus", "Lena")
WORD_A = ("practical", "hands-on", "applied")
WORD_B = (
    "programmes for new managers",
    "workshops for first-time managers",
    "courses for team leads",
)
WORD_C = ("short supervisor courses", "brief supervisor courses", "compact supervisor courses")


def _li_body(name: str, i: int) -> str:
    body = message_row()["body"].replace("Aria", name).replace("practical", WORD_A[i])
    return body.replace("programmes for new managers", WORD_B[i])


def _em_body(name: str, i: int) -> str:
    body = email_message()["body"].replace("Aria", name)
    return body.replace("short supervisor courses", WORD_C[i])


def read(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def sql(statement: str, *params) -> None:
    with db.session() as conn:
        conn.execute(statement, params)


@pytest.fixture
def world(ready_db):
    """Three people with a verified email, an email message and a LinkedIn message, all with
    an AI `pass`."""
    c = make_campaign()
    cid = c["id"]
    co = add_company(cid)
    ids = []
    for i, name in enumerate(NAMES):
        pid = add_person(cid, co, name, "Tan")
        add_email(pid, "verified", f"{name.lower()}.tan@northwind.example")
        add_message(
            pid, "linkedin_message", body=_li_body(name, i), ai_review="pass", status="approved"
        )
        add_message(
            pid,
            "email",
            body=_em_body(name, i),
            subject=f"Training for {COMPANY}",
            ai_review="pass",
            status="approved",
        )
        ids.append(pid)
    return cid, co, ids


def test_lemlist_csv(world):
    cid, _, ids = world
    reply = export_campaign(cid, "lemlist")
    assert reply["ok"] and reply["exported"] == 3 and reply["messages_exported"] == 6
    raw = Path(reply["path"]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    header, rows = read(reply["path"])
    assert header == list(export.LEMLIST_COLUMNS)
    assert "evidenceOrigin" in header
    first = rows[0]
    assert first["email"] == "aria.tan@northwind.example"
    assert first["firstName"] == "Aria" and first["lastName"] == "Tan"
    assert first["companyName"] == COMPANY
    assert first["companyDomain"] == "northwind.example"
    assert first["linkedinUrl"].startswith("https://www.linkedin.com/in/")
    assert first["greetingName"] == "Aria"
    assert first["emailSubject"] == f"Training for {COMPANY}"
    assert first["emailBody"].startswith("Hi Aria")
    assert first["linkedinMessage"].startswith("Hi Aria")
    assert first["evidenceOrigin"] == "api" and first["emailStatus"] == "verified"
    assert Path(reply["path"]).parent.name.startswith("sg-logistics")
    assert Path(reply["path"]).name.startswith("lemlist-")
    assert any("Accepted invite" in step for step in reply["next_steps"])


def test_generic_and_instantly_csv(world):
    cid, _, _ = world
    reply = export_campaign(cid, "generic")
    header, rows = read(reply["path"])
    assert header == list(export.GENERIC_COLUMNS) and len(rows) == 3
    assert rows[0]["website"] == rows[0]["companyDomain"] == "northwind.example"
    assert rows[0]["evidenceOrigin"] == "api"
    reply = export_campaign(cid, "instantly")
    header, rows = read(reply["path"])
    assert header == list(export.INSTANTLY_COLUMNS)
    assert len(rows) == 3
    assert rows[0]["website"] == "northwind.example"
    assert rows[0]["first_name"] == "Aria" and rows[0]["company_name"] == COMPANY
    assert rows[0]["personalization"] == rows[0]["message_body"] != ""
    assert rows[0]["evidence_origin"] == "api"
    assert rows[0]["subject_line"] and rows[0]["follow_up_dm"] and rows[0]["address_status"]


def test_instantly_custom_columns_do_not_resemble_predefined_fields():
    predefined = {"email", "first_name", "last_name", "job_title", "company_name",
                  "personalization", "phone", "website", "location", "linkedin_url"}
    for column in export.INSTANTLY_COLUMNS:
        if column in predefined:
            continue
        assert not column.startswith(("email", "linkedin", "first", "last", "company", "job"))
        assert column == column.lower()


def test_same_minute_exports_get_distinct_names(world):
    cid, _, _ = world
    a = export_campaign(cid, "generic")["path"]
    b = export_campaign(cid, "generic")["path"]
    assert a != b and Path(a).exists() and Path(b).exists()


def test_instantly_skips_people_without_email(world):
    cid, _, ids = world
    sql("DELETE FROM emails WHERE person_id = ?", ids[2])
    # the third person's email message is now blocked (L17); their LinkedIn one is not
    reply = export_campaign(cid, "instantly", include="qa_pass")
    assert reply["skipped_no_email"] == 1 and reply["exported"] == 2
    _, qa = read(reply["qa_report_path"])
    by_id = {r["person_id"]: r for r in qa}
    assert by_id[str(ids[2])]["exported"] == "no (no email for this format)"
    assert any("no email" in w for w in reply["warnings"])
    # lemlist keeps the person, with an empty email, and warns
    reply = export_campaign(cid, "lemlist", include="qa_pass")
    _, rows = read(reply["path"])
    assert len(rows) == 3
    assert [r["email"] for r in rows][2] == ""
    assert any("no email" in w for w in reply["warnings"])


def test_include_modes(world):
    cid, _, ids = world
    sql("UPDATE messages SET ai_review = '', status = 'qa_pass' WHERE person_id = ?", ids[0])
    approved = export_campaign(cid, "generic", include="approved")
    assert approved["exported"] == 2 and approved["not_reviewed"] == 2
    assert approved["blocked_by_code"].get("M16") == 2
    qa_pass = export_campaign(cid, "generic", include="qa_pass")
    assert qa_pass["exported"] == 3 and qa_pass["not_reviewed"] == 2
    assert any("AI review" in w for w in qa_pass["warnings"])
    everyone = export_campaign(cid, "generic", include="all_confirmed")
    assert everyone["exported"] == 3


def test_all_confirmed_withholds_blocked_messages_and_lead_blocks(world):
    cid, _, ids = world
    sql(
        "UPDATE messages SET body = 'Hi Marcus, TODO write this' "
        "WHERE person_id = ? AND channel = 'linkedin_message'",
        ids[1],
    )
    sql(
        "UPDATE people SET evidence_snippet = 'Singapore', "
        "evidence_title = 'Lena Tan - Head of L&D | LinkedIn' WHERE id = ?",
        ids[2],
    )
    reply = export_campaign(cid, "generic", include="all_confirmed")
    _, rows = read(reply["path"])
    by = {r["personId"]: r for r in rows}
    assert str(ids[2]) not in by  # lead-level block (employer not confirmed)
    assert by[str(ids[1])]["linkedinMessage"] == ""  # withheld
    assert by[str(ids[1])]["emailBody"] != ""
    assert any("left out" in w for w in reply["warnings"])


def test_gate_blocks_and_counts(world):
    cid, _, ids = world
    sql(
        "UPDATE messages SET body = 'Hi Marcus, TODO' "
        "WHERE person_id = ? AND channel = 'linkedin_message'",
        ids[1],
    )
    reply = export_campaign(cid, "generic")
    _, rows = read(reply["path"])
    marcus = [r for r in rows if r["firstName"] == "Marcus"][0]
    assert marcus["linkedinMessage"] == "" and marcus["emailBody"] != ""
    assert reply["blocked_by_code"].get("M03") == 1
    assert reply["messages_blocked"] == 1 and reply["exported"] == 3


def test_nothing_exportable_writes_only_reports(world):
    cid, _, _ = world
    sql("UPDATE messages SET ai_review = ''")
    reply = export_campaign(cid, "lemlist")
    assert reply["exported"] == 0 and reply["path"] is None and "message" in reply
    assert Path(reply["qa_report_path"]).exists() and Path(reply["removed_path"]).exists()
    assert reply["blocked_by_code"]["M16"] == 6


def test_evidence_origin_and_guess_label(world):
    cid, _, ids = world
    sql(
        "UPDATE people SET evidence_origin = 'ai_web_search', spot_check = 'ok' WHERE id = ?",
        ids[0],
    )
    sql("UPDATE emails SET status = 'guess' WHERE person_id = ?", ids[1])
    reply = export_campaign(cid, "generic", include="qa_pass")
    _, rows = read(reply["path"])
    by = {r["personId"]: r for r in rows}
    assert by[str(ids[0])]["evidenceOrigin"] == "ai_web_search"
    assert reply["evidence_origin_counts"] == {"ai_web_search": 1, "api": 2}
    assert any("AI web search" in w for w in reply["warnings"])
    # a guess address is exported with its label (an export is not a send)
    assert by[str(ids[1])]["email"] == "marcus.tan@northwind.example"
    assert by[str(ids[1])]["emailStatus"] == "guess"
    assert any("guess" in w for w in reply["warnings"])
    header, qa = read(reply["qa_report_path"])
    assert "evidence_origin" in header
    assert {r["person_id"]: r["evidence_origin"] for r in qa}[str(ids[0])] == "ai_web_search"


def test_role_and_unusable_emails_are_not_exported(world):
    cid, _, ids = world
    sql("UPDATE emails SET email = 'info@northwind.example' WHERE person_id = ?", ids[0])
    sql("UPDATE emails SET status = 'invalid' WHERE person_id = ?", ids[1])
    reply = export_campaign(cid, "lemlist", include="qa_pass")
    _, rows = read(reply["path"])
    by = {r["firstName"]: r for r in rows}
    assert by["Aria"]["email"] == "" and by["Marcus"]["email"] == ""
    assert by["Lena"]["email"] != ""


def test_suppressed_person_is_never_exported(world):
    cid, _, _ = world
    suppress("email", "lena.tan@northwind.example")
    for include in export.INCLUDES:
        reply = export_campaign(cid, "generic", include=include)
        _, rows = read(reply["path"])
        assert "Lena" not in [r["firstName"] for r in rows], include


def test_qa_report_and_removed_files(world):
    cid, co, ids = world
    gone = add_person(
        cid,
        co,
        "Ravi",
        "Menon",
        status="removed",
        confirmed=0,
        reasons_json=["Removed by the user: left the company"],
    )
    review_one = add_person(
        cid, co, "Mei", "Lim", status="review", confirmed=0, reasons_json=["L04: no employer"]
    )
    add_message(
        gone, "linkedin_message", status="rejected", ai_review="reject", ai_review_notes="Wrong."
    )
    sql(
        "UPDATE messages SET ai_review = 'fix', ai_review_notes = 'Too salesy' "
        "WHERE person_id = ? AND channel = 'email'",
        ids[0],
    )
    reply = export_campaign(cid, "generic")
    header, qa = read(reply["qa_report_path"])
    assert header == list(export.QA_COLUMNS)
    assert len(qa) == 5  # every person, whatever the status
    by = {r["person_id"]: r for r in qa}
    assert by[str(ids[0])]["exported"] == "yes"  # the LinkedIn message still goes
    assert "AI review verdict is 'fix'" in by[str(ids[0])]["blocking_issues"]
    assert "Too salesy" in by[str(ids[0])]["ai_verdict_notes"]
    assert by[str(ids[1])]["exported"] == "yes" and by[str(ids[1])]["email_status"] == "verified"
    assert by[str(review_one)]["person_status"] == "review"
    assert "no employer" in by[str(review_one)]["review_reasons"]
    assert by[str(review_one)]["exported"] == "no"
    _, removed = read(reply["removed_path"])
    assert [r["person_id"] for r in removed] == [str(gone)]
    assert "left the company" in removed[0]["reasons"]
    assert "Wrong." in removed[0]["ai_verdict_notes"]
    assert reply["removed"] == 1


def test_csv_injection_is_neutralised(world):
    cid, _, ids = world
    assert safe_cell("=SUM(A1)") == "'=SUM(A1)"
    for start in "+-@\t\r":
        assert safe_cell(f"{start}x").startswith("'")
    assert safe_cell("plain") == "plain" and safe_cell(None) == "" and safe_cell(5) == "5"
    sql("UPDATE people SET title = '=HYPERLINK(1)' WHERE id = ?", ids[0])
    sql(
        "UPDATE messages SET subject = '-1 offer' WHERE person_id = ? AND channel = 'email'",
        ids[0],
    )
    reply = export_campaign(cid, "generic", include="qa_pass")
    _, rows = read(reply["path"])
    first = [r for r in rows if r["firstName"] == "Aria"][0]
    assert first["jobTitle"].startswith("'=HYPERLINK")
    assert first["emailSubject"].startswith("'-1")


def test_bad_arguments_and_locked_file(world, monkeypatch):
    cid, _, _ = world
    with pytest.raises(ExportError) as err:
        export_campaign(cid, "xml")
    assert err.value.code == "invalid_value" and err.value.as_response()["ok"] is False
    with pytest.raises(ExportError):
        export_campaign(cid, "generic", include="everything")
    original = Path.open

    def locked(self, *args, **kwargs):
        if self.name == "qa_report.csv":
            raise PermissionError
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", locked)
    with pytest.raises(ExportError) as err:
        export_campaign(cid, "generic")
    assert err.value.code == "file_locked"


def test_unicode_survives(world):
    cid, _, ids = world
    sql("UPDATE people SET first_name = 'Zoë' WHERE id = ?", ids[0])
    reply = export_campaign(cid, "generic", include="qa_pass")
    _, rows = read(reply["path"])
    assert "Zoë" in [r["firstName"] for r in rows]


def test_linkedin_only_campaign(ready_db):
    c = make_campaign(channels=("linkedin",))
    co = add_company(c["id"])
    pid = add_person(c["id"], co, "Aria", "Tan")
    add_message(pid, "linkedin_message", ai_review="pass", status="approved")
    reply = export_campaign(c["id"], "lemlist")
    _, rows = read(reply["path"])
    assert reply["exported"] == 1 and rows[0]["email"] == "" and rows[0]["emailBody"] == ""
    assert rows[0]["linkedinMessage"].startswith("Hi Aria")


def test_incomplete_rows_warn_in_every_include_mode(world):
    cid, _, ids = world
    # Marcus's email message has a QA block (M03): he has an address but no exportable email
    sql(
        "UPDATE messages SET body = 'Hi Marcus, TODO' WHERE person_id = ? AND channel = 'email'",
        ids[1],
    )
    for include in ("approved", "qa_pass", "all_confirmed"):
        reply = export_campaign(cid, "lemlist", include=include)
        assert reply["incomplete_rows"] == 1, include
        assert any("without a message for every channel" in w for w in reply["warnings"]), include
    # a complete export has no such warning
    sql(
        "UPDATE messages SET body = ? WHERE person_id = ? AND channel = 'email'",
        _em_body("Marcus", 1),
        ids[1],
    )
    reply = export_campaign(cid, "lemlist")
    assert reply["incomplete_rows"] == 0
    assert not any("every channel" in w for w in reply["warnings"])


def test_missing_linkedin_message_counts_as_incomplete(world):
    cid, _, ids = world
    sql("DELETE FROM messages WHERE person_id = ? AND channel = 'linkedin_message'", ids[0])
    assert export_campaign(cid, "generic")["incomplete_rows"] == 1
