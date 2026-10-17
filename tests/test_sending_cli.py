"""`leadgen send ...`, `leadgen send-worker`, `leadgen mailbox ...` and the two MCP tools."""

from __future__ import annotations

import json

import anyio
import pytest
from mcp.client import Client
from qa_world import add_company, make_campaign

from leadgen import cli, db, mcp_server
from leadgen.core import secrets
from leadgen.core.config import load_settings
from leadgen.send.smtp_imap import SmtpImapMailbox
from leadgen.services import sending


def test_pause_resume_and_status(ready_db, capsys):
    assert cli.main(["send", "pause"]) == 0
    assert load_settings().sending.paused is True
    assert cli.main(["send", "status"]) == 0
    out = capsys.readouterr().out
    assert "Paused       : yes" in out and "Worker       : not running" in out
    assert cli.main(["send", "resume"]) == 0
    assert load_settings().sending.paused is False
    cli.main(["send", "status"])
    assert "Paused       : no" in capsys.readouterr().out


def test_send_worker_runs_the_worker(ready_db, monkeypatch, capsys):
    monkeypatch.setattr(sending, "run_worker", lambda: {"sent": 2, "reason": "queue_empty"})
    assert cli.main(["send-worker"]) == 0
    assert "Sent 2 email(s). Stopped: queue_empty." in capsys.readouterr().out


def test_mailbox_set_saves_after_a_good_test(monkeypatch, capsys):
    answers = iter(["smtp.mail.example", "587", "priya@bh.example", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "app-password")
    monkeypatch.setattr(SmtpImapMailbox, "test", lambda self: (True, "SMTP and IMAP both work."))
    assert cli.main(["mailbox", "set"]) == 0
    assert secrets.get("SMTP_HOST") == "smtp.mail.example"
    assert secrets.get("SMTP_USER") == "priya@bh.example"
    assert secrets.get("SMTP_PASSWORD") == "app-password"
    assert secrets.get("IMAP_HOST") == "imap.mail.example"
    out = capsys.readouterr().out
    assert "Saved." in out and "app-password" not in out


def test_mailbox_set_keeps_nothing_after_a_failed_test(monkeypatch, capsys):
    secrets.set("SMTP_HOST", "old.example")
    answers = iter(["smtp.mail.example", "587", "priya@bh.example", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "wrong-password")
    monkeypatch.setattr(SmtpImapMailbox, "test", lambda self: (False, "SMTP login failed."))
    assert cli.main(["mailbox", "set"]) == 1
    assert secrets.get("SMTP_HOST") == "old.example"
    assert secrets.get("SMTP_PASSWORD") == ""
    assert "was not saved" in capsys.readouterr().out


def test_mailbox_set_with_a_preset_needs_all_values(monkeypatch, capsys):
    answers = iter(["", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "")
    assert cli.main(["mailbox", "set", "--preset", "gmail"]) == 1
    assert secrets.get("SMTP_HOST") == ""


def test_mailbox_test_command(monkeypatch, capsys):
    monkeypatch.setattr(SmtpImapMailbox, "test", lambda self: (False, "SMTP login failed."))
    assert cli.main(["mailbox", "test"]) == 1
    assert "SMTP login failed." in capsys.readouterr().out


def test_doctor_no_longer_lists_email_sending_as_missing(capsys):
    cli.main(["doctor"])
    assert "email sending" not in capsys.readouterr().out


# --- MCP tools ---------------------------------------------------------------------------------


@pytest.fixture
def mcp():
    server = mcp_server.build_server()
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(server)) as client:

            def call(tool, /, **args):
                result = portal.call(client.call_tool, tool, args)
                assert not result.is_error, result.content
                return json.loads(result.content[0].text)

            call.client = client
            call.portal = portal
            yield call


def test_check_send_readiness_tool_lists_the_checklist(mcp):
    campaign = make_campaign(channels=("email",))
    add_company(campaign["id"])
    reply = mcp("check_send_readiness", campaign_id=campaign["id"])
    assert reply["ok"] is True and reply["ready"] is False
    assert "mailbox_configured" in reply["missing"] and "compliance:SG" in reply["missing"]
    assert reply["rules"][0]["country"] == "SG"


def test_send_emails_tool_defaults_to_a_dry_run_and_needs_confirm(mcp):
    campaign = make_campaign(channels=("email",))
    reply = mcp("send_emails", campaign_id=campaign["id"])
    assert reply["dry_run"] is True and reply["count"] == 0
    confirm = mcp("send_emails", campaign_id=campaign["id"], dry_run=False)
    assert confirm["needs_confirmation"] is True
    blocked = mcp("send_emails", campaign_id=campaign["id"], dry_run=False, confirm=True)
    assert blocked["ok"] is False and blocked["error"]["code"] == "not_ready"
    assert blocked["error"]["hint"]


def test_send_emails_tool_validates_the_limit(mcp):
    campaign = make_campaign(channels=("email",))
    reply = mcp("send_emails", campaign_id=campaign["id"], limit=500)
    assert reply["ok"] is False and reply["error"]["code"] == "invalid_value"


def test_acknowledge_compliance_tool(mcp):
    campaign = make_campaign(channels=("email",))
    reply = mcp("acknowledge_compliance", campaign_id=campaign["id"], country="SG")
    assert reply["ok"] is True and reply["counts_for_sending"] is True
    bad = mcp("acknowledge_compliance", campaign_id=campaign["id"], country="DE")
    assert bad["error"]["code"] == "not_a_campaign_country"
    with db.session() as conn:
        keys = [r["key"] for r in db.query(conn, "SELECT key FROM settings")]
    assert f"compliance_ack:{campaign['id']}:SG" in keys


def test_the_workflow_guide_describes_the_real_sending_flow():
    from leadgen.services import guide

    text = guide.workflow_text()
    assert "acknowledge_compliance" in text and "dry run" in text.lower()
    assert "Send emails from the user's mailbox" in text
