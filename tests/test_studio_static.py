"""The Studio's look and feel and first screens: files, CSS, script rules, labels, routing.

Everything is offline; the script check uses node only when it is installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_dashboard_projects import banned_in, js_strings, visible

from leadgen.dashboard import app as dash

BASE = "http://127.0.0.1:8765"
STATIC = Path(dash.STATIC_DIR)
SCRIPTS = ("ui.js", "studio.js", "studio_project.js", "studio_app.js")


@pytest.fixture
def client():
    return TestClient(dash.create_app(), base_url=BASE)


def test_studio_files_are_served(client):
    for path in ("/static/studio.css", *(f"/static/{name}" for name in SCRIPTS)):
        assert client.get(path).status_code == 200, path
    assert "javascript" in client.get("/static/studio.js").headers["content-type"]
    page = client.get("/").text
    order = [page.index(f"/static/{name}") for name in (*SCRIPTS, "app.js")]
    assert order == sorted(order)
    assert page.index("/static/app.css") < page.index("/static/studio.css")
    assert 'id="ai-pill"' in page
    assert "http://" not in page and "https://" not in page


def test_studio_css_has_the_tokens_and_dark_mode():
    css = (STATIC / "studio.css").read_text(encoding="utf-8")
    for expected in (
        "--accent: #0071e3",
        "prefers-color-scheme: dark",
        "backdrop-filter",
        "prefers-reduced-motion",
        ".ui-card",
        ".card-body",
        ".segmented",
        ".orb",
        ".bars",
        ".shimmer",
        ".tl-step",
        "grid-template-rows",
    ):
        assert expected in css, expected


@pytest.mark.parametrize("name", SCRIPTS)
def test_studio_js_rules(name):
    source = (STATIC / name).read_text(encoding="utf-8")
    assert "innerHTML" not in source and "allow_guess" not in source
    assert "http://" not in source and "https://" not in source
    problems = [
        (pattern, text)
        for text in js_strings(source)
        if visible(text)
        for pattern in banned_in(text)
        if pattern != r"\bAPI keys?\b"  # the Connect screen says "API key" on purpose
    ]
    assert not problems, problems


def test_ui_js_remembers_choices_safely():
    js = (STATIC / "ui.js").read_text(encoding="utf-8")
    for expected in (
        "localStorage", "'lg.card.'", "'lg.layout.'", "aria-expanded", "inert", "try {",
    ):
        assert expected in js, expected
    assert "innerHTML" not in js


def test_the_labels_of_the_connect_and_home_screens():
    js = (STATIC / "studio.js").read_text(encoding="utf-8")
    for expected in (
        "Connect your AI", "Use my Claude plan", "Use an API key", "Sign in to Claude",
        "Show me how to install it", "Test the connection", "Skip for now", "Name your project",
        "Create and open", "Edit project", "Sign-off is in Settings", "Delete this project?",
        "Type the project name to confirm", "Keep it", "Your projects",
    ):  # fmt: skip
        assert expected in js, expected


def test_the_project_page_labels_and_pieces():
    js = (STATIC / "studio_project.js").read_text(encoding="utf-8")
    for expected in (
        "EventSource", "/chat/stream", "'Stop'", "'Attach a file'",
        "'Send by email...'", "'Send to lemlist...'", "'Connect lemlist'", "'Not ready yet:'",
        "'Progress and results'",
        "'What the AI is doing'", "UI.timeline", "'Searches and message style'",
        "'Message templates'", "'Your sign-off'", "'Your job title'", "'Your company'",
        "'Your product'", "\"Don't contact again\"", "'Contacts you already have'", "'Chat wide'",
        "'Side by side'", "'Results wide'", "'New template'",
        "'Copy into my templates'",
        "setRangeText", "/template'",
        "'Activity'", "'Settings'", "'Waves'", "'Launch a new wave'", "'Review messages'",
        "'Approve'", "'Skip'", "'Export all waves (Excel CSV)'", "/waves", "/api/messages/",
        "TAB_KEY", "'Show me'", "'You have unsaved changes: Save or Discard'",
    ):  # fmt: skip
        assert expected in js, expected
    # one home for each thing: the old project-page pieces are gone
    gone_words = ("'Ready contacts'", "'Export and send'", "waveTable", "'Back to all waves'")
    for gone in (*gone_words, "actionsCard"):
        assert gone not in js, gone


def test_every_send_asks_first_and_is_confirmed():
    js = (STATIC / "studio_project.js").read_text(encoding="utf-8")
    sends = re.findall(r"dry_run: false([^}]*)", js)
    assert sends and all("confirm: true" in tail for tail in sends), sends
    assert js.index("Studio.confirmDialog") < js.index("dry_run: false")


def test_send_dialog_words_every_missing_item():
    # the service lists missing items as ids; each id the dialog may get has a plain sentence
    js = (STATIC / "studio_project.js").read_text(encoding="utf-8")
    for item in ("mailbox_configured", "sender_identity", "postal_address", "approved_messages"):
        assert item + ":" in js, item
    assert "missingLine" in js


def test_the_connectors_page_labels():
    js = (STATIC / "studio_app.js").read_text(encoding="utf-8")
    for expected in ("'Connectors'", "Every key stays on this computer", "pageConnectors"):
        assert expected in js, expected


def test_app_js_routes_the_new_pages():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for expected in (
        "studio.pageHome", "studio.pageConnect", "studio.pageAdvanced", "studio.pageProject",
        "studio.pageConnectors", "NAV_GROUP", "['connect', 'Your AI']", "['keys', 'Connectors']",
        "'Costs: '", "'Connect your mailbox'",
    ):  # fmt: skip
        assert expected in js, expected
    assert "href: '#/mailbox'" in js  # pageHome keeps its mailbox link


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("name", (*SCRIPTS, "app.js"))
def test_js_syntax_with_node_when_available(name):
    result = subprocess.run(
        ["node", "--check", str(STATIC / name)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_v14_page_rules():
    """The drawer keys, the clickable steps and the home checklist are in place."""
    project = (STATIC / "studio_project.js").read_text(encoding="utf-8")
    for expected in (
        "Escape", "'j'", "'k'", "step-go", "nextStepLine", "reviewDrawer", "launchDialog",
    ):
        assert expected in project, expected
    home = (STATIC / "studio.js").read_text(encoding="utf-8")
    for expected in ("setupChecklist", "Get set up", "sign_off_set", "Set your sign-off"):
        assert expected in home, expected
    assert "ep-sender" not in home and "ep-title" not in home  # the sign-off has one home
    css = (STATIC / "studio.css").read_text(encoding="utf-8")
    for expected in (".drawer", ".next-step", ".right-tabs", ".step-go"):
        assert expected in css, expected


def test_launch_and_send_never_spend_without_a_yes():
    """The launch dialog only sends a chat message that asks the AI to wait for the person's yes."""
    js = (STATIC / "studio_project.js").read_text(encoding="utf-8")
    assert "wait for my yes before anything is spent" in js
    assert "confirm: true" not in js[js.index("function launchDialog") :].split("// ----")[0]


def test_v15_one_clear_path_and_popups_close_outside():
    """Waves open their own pages, Back keeps the Waves tab, pop-ups close on an outside click."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    for expected in (
        "Back to ", "UI.save('lg.tab.'", "'Export or send'", "reviewerFor", "pointerdown",
    ):  # fmt: skip
        assert expected in app, expected
    assert "'1 Companies'" not in app.split("const CAMPAIGN_NAV")[1].split("];")[0]
    project = (STATIC / "studio_project.js").read_text(encoding="utf-8")
    for expected in ("pointerdown", "=== 'results' ? 'waves'", "reviewDrawer, sendCard"):
        assert expected in project, expected
    studio = (STATIC / "studio.js").read_text(encoding="utf-8")
    for expected in (
        "getBoundingClientRect", "pointerdown", "Open all waves", "Open your mailbox", "'details'",
    ):  # fmt: skip
        assert expected in studio, expected
