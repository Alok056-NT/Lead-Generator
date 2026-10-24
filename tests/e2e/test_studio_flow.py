"""End-to-end test (V12 C7): the Studio flow through the HTTP routes, with the fake claude.

Everything is invented (project "Aria Training", a contacts file of three made-up people, the
seeded campaign of `dashboard_world`). The AI is the fake `claude` program and the key check makes
no request, because the fakes are switched on. Nothing is sent.
"""

from __future__ import annotations

import pytest
from dashboard_world import seed
from fastapi.testclient import TestClient
from test_chat import parse

from leadgen import db
from leadgen.core import paths
from leadgen.dashboard import app as dash
from leadgen.services import campaign as campaign_svc
from leadgen.services import chat, projects

BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}
KEY = "sk-ant-api03-" + "z" * 40
CONTACTS = (
    "first_name,last_name,email,company,linkedin_url\n"
    "Mira,Lind,mira.lind@kestrel.example.com,Kestrel Works,https://www.linkedin.com/in/mira-x\n"
    "Oskar,Dahl,oskar.dahl@kestrel.example.com,Kestrel Works,https://www.linkedin.com/in/oskar-x\n"
    "Nia,Reyes,nia.reyes@harbour.example.com,Harbour Lab,https://www.linkedin.com/in/nia-x\n"
)


@pytest.fixture
def client(ready_db, fake_claude, monkeypatch):
    monkeypatch.setenv("LEADGEN_FAKE_PROVIDERS", "1")
    monkeypatch.setenv("LEADGEN_ALLOW_FAKES", "1")
    monkeypatch.setenv("FAKE_CLAUDE_REAL_STATUS", "1")
    chat._TURNS.clear()  # project ids repeat in every test database
    yield TestClient(dash.create_app(), base_url=BASE)
    chat._TURNS.clear()


def count(query: str, params: tuple = ()) -> int:
    with db.session() as conn:
        return db.query(conn, query, params)[0]["n"]


def test_studio_flow_end_to_end(client, fake_claude, tmp_path):
    # 1. connect the AI with an API key
    assert client.get("/api/ai").json()["ready"] is False
    assert client.post("/api/ai/mode", json={"mode": "api_key"}, headers=CSRF).status_code == 200
    assert client.post("/api/ai/api-key", json={"key": KEY}, headers=CSRF).json()["ok"] is True
    assert client.post("/api/ai/test", headers=CSRF).json()["message"] == "Connected"
    ai = client.get("/api/ai")
    assert ai.json()["ready"] is True and KEY not in ai.text

    # 2. a project
    made = client.post("/api/projects", json={"name": "Aria Training"}, headers=CSRF)
    pid = made.json()["project"]["id"]

    # 3. an upload, then a turn that uses it
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(CONTACTS, encoding="utf-8")
    upload = {"file": ("contacts.csv", contacts.read_bytes(), "text/csv")}
    uploaded = client.post(f"/api/projects/{pid}/chat/upload", headers=CSRF, files=upload)
    assert uploaded.json()["file"]["kind"] == "contacts"
    body = {"message": "Here are my old contacts", "attachments": ["contacts.csv"]}
    sent = client.post(f"/api/projects/{pid}/chat", json=body, headers=CSRF)
    assert sent.status_code == 200
    assert chat.wait(pid, 30)
    turn = sent.json()["turn_id"]
    events = parse(client.get(f"/api/projects/{pid}/chat/stream?turn={turn}").text)
    names = [name for name, _ in events]
    assert "text" in names and "refresh" in names
    assert [d.get("state") for name, d in events if name == "tool"] == ["started", "finished"]
    assert "Looks good" in next(d for name, d in events if name == "chips")["options"]
    assert events[-1] == ("done", {"ok": True, "cancelled": False})

    # 4. the history and what the AI was given
    rows = client.get(f"/api/projects/{pid}/chat").json()["messages"]
    assert [row["role"] for row in rows] == ["user", "assistant", "tool", "assistant"]
    first_call = fake_claude.calls()[0]
    assert "contacts.csv" in first_call["prompt"] and f"project_id: {pid}" in first_call["prompt"]
    assert first_call["api_key"] == KEY

    # 5. the sign-off name, and the progress of the new project
    patched = client.patch(f"/api/projects/{pid}", json={"sender_name": "Sam"}, headers=CSRF)
    assert patched.status_code == 200
    studio = client.get(f"/api/projects/{pid}/studio").json()
    assert len(studio["steps"]) == 8
    assert studio["steps"][0]["key"] == "product" and studio["steps"][0]["state"] == "active"
    assert studio["project"]["sender_name"] == "Sam"
    assert studio["ai"]["ready"] is True

    # 6. a seeded project: ready contacts, export, and send only as a preview until confirmed
    data = seed()
    campaign_id = data["campaign"]
    seeded_pid = campaign_svc.get_campaign(campaign_id)["project_id"]
    results = client.get(f"/api/projects/{seeded_pid}/results").json()
    aria = next(lead for lead in results["leads"] if lead["name"] == "Aria Tan")
    assert aria["email"] == "aria.tan@northwind.example" and aria["linkedin_message"]
    export = {"format": "generic", "include": "approved"}
    exported = client.post(f"/api/campaigns/{campaign_id}/exports", json=export, headers=CSRF)
    assert exported.json()["ok"] is True and exported.json()["exported"] >= 1
    send_url = f"/api/campaigns/{campaign_id}/send"
    dry = {"limit": 50, "dry_run": True, "confirm": False}
    preview = client.post(send_url, json=dry, headers=CSRF)
    assert preview.json()["ok"] is True and preview.json()["dry_run"] is True
    live = {"limit": 50, "dry_run": False, "confirm": False}
    queued = "SELECT COUNT(*) AS n FROM sends WHERE status = 'queued'"
    before = count(queued)
    real = client.post(send_url, json=live, headers=CSRF)
    assert real.json()["needs_confirmation"] is True
    assert count(queued) == before == 0

    # 7. a second turn resumes the same Claude session
    session = projects.get_chat_session(pid)
    again = client.post(f"/api/projects/{pid}/chat", json={"message": "Looks good"}, headers=CSRF)
    assert again.status_code == 200
    assert chat.wait(pid, 30)
    argv = fake_claude.calls()[1]["argv"]
    assert argv[argv.index("--resume") + 1] == session

    # 8. delete the project: a wrong name is refused, the right one removes everything of it
    url = f"/api/projects/{pid}"
    wrong = client.request("DELETE", url, json={"confirm_name": "Aria"}, headers=CSRF)
    assert wrong.status_code == 400
    gone = client.request("DELETE", url, json={"confirm_name": "Aria Training"}, headers=CSRF)
    assert gone.status_code == 200
    listed = [p["id"] for p in client.get("/api/projects").json()["projects"]]
    assert pid not in listed and seeded_pid in listed
    assert count("SELECT COUNT(*) AS n FROM chat_messages WHERE project_id = ?", (pid,)) == 0
    assert not (paths.home() / "uploads" / str(pid)).exists()
