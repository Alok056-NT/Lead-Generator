"""Studio chat engine: one turn = one `claude -p` child process, streamed to the page as events."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from leadgen import db
from leadgen.core import paths
from leadgen.core.fsutil import atomic_write_text
from leadgen.services import ai_setup, connect, guide, projects

log = logging.getLogger("leadgen.chat")
SERVER_NAME = connect.SERVER_NAME
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
MAX_MESSAGE = 8000
MAX_TURNS = 80
IDLE_TIMEOUT_S = 300.0
TURN_TIMEOUT_S = 1800.0
WATCH_INTERVAL_S = 0.5
HEARTBEAT_S = 15.0
HISTORY_LIMIT = 200
UPLOAD_SUFFIXES = (".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx")
_TURNS: dict[int, Turn] = {}
_LOCK = threading.Lock()


class ChatError(Exception):
    """A chat request failed. `code` is stable text; `hint` says what to do."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_response(self) -> dict[str, Any]:
        return {
            "ok": False, "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


@dataclass
class Turn:
    project_id: int
    turn_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    cancelled: bool = False
    timed_out: bool = False
    proc: subprocess.Popen[bytes] | None = None
    thread: threading.Thread | None = None
    cond: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        with self.cond:
            self.events.append({"id": len(self.events) + 1, "event": event, "data": data})
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()


def studio_dir() -> Path:
    folder = paths.home() / "studio"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def uploads_dir(project_id: int) -> Path:
    folder = paths.home() / "uploads" / str(int(project_id))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def allowed_tools_arg() -> str:
    return ",".join(TOOL_PREFIX + name for name in STUDIO_TOOLS)


def write_runtime_files() -> tuple[Path, Path]:
    """Write the MCP config and the system prompt the program is started with (every turn)."""
    folder = studio_dir()
    command, args = connect.leadgen_launcher()
    config = {"mcpServers": {SERVER_NAME: {
        "command": command, "args": args, "env": {"LEADGEN_HOME": str(paths.home())},
    }}}
    mcp_config = folder / "mcp.json"
    atomic_write_text(mcp_config, json.dumps(config, indent=2) + "\n")
    prompt_file = folder / "system.md"
    atomic_write_text(prompt_file, guide.studio_text())
    return mcp_config, prompt_file


def resolve_attachments(project_id: int, names: Sequence[str]) -> list[Path]:
    folder = uploads_dir(project_id)
    out: list[Path] = []
    for name in names:
        path = folder / name
        if (Path(name).name != name or Path(name).suffix.lower() not in UPLOAD_SUFFIXES
                or not path.is_file()):
            raise ChatError(
                "bad_attachment", "One of the attached files is missing.", "Attach it again."
            )
        out.append(path)
    return out


def _save_rows(project_id: int, rows: Sequence[Mapping[str, Any]]) -> None:
    """Store chat rows (`role`, `text`, `meta`); never raises (a problem is logged by name)."""
    try:
        with db.session() as conn:
            for row in rows:
                if not str(row.get("text") or "").strip():
                    continue
                db.insert(conn, "chat_messages", project_id=project_id, role=row["role"],
                          text=row["text"], meta_json=row.get("meta") or {}, at=db.now())
    except Exception as exc:
        log.error("chat history not saved: %s", type(exc).__name__)


def history(project_id: int, limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT * FROM (SELECT * FROM chat_messages WHERE project_id = ? ORDER BY id DESC "
            "LIMIT ?) ORDER BY id", (project_id, int(limit)),
        )
    out = []
    for row in rows:
        meta = row["meta_json"] if isinstance(row["meta_json"], dict) else {}
        out.append({
            "id": row["id"], "role": row["role"], "text": row["text"], "at": row["at"],
            "chips": meta.get("chips") or [], "buttons": meta.get("buttons") or [],
            "attachments": meta.get("attachments") or [], "tool": meta.get("tool") or "",
            "ok": meta.get("ok"),
        })
    return out


def turn_state(project_id: int) -> dict[str, Any]:
    with _LOCK:
        turn = _TURNS.get(project_id)
    running = turn is not None and not turn.done
    return {"running": running, "turn_id": turn.turn_id if running and turn else None}


def cancel_turn(project_id: int) -> dict[str, Any]:
    with _LOCK:
        turn = _TURNS.get(project_id)
    if turn is None or turn.done:
        return {"ok": True, "stopped": False}
    turn.cancelled = True
    _kill(turn.proc)
    return {"ok": True, "stopped": True}


def wait(project_id: int, timeout: float = 30.0) -> bool:
    with _LOCK:
        turn = _TURNS.get(project_id)
    if turn is None or turn.done:
        return True
    if turn.thread is not None:
        turn.thread.join(timeout)
    return turn.done


def start_turn(project_id: int, message: str, attachments: Sequence[str] = ()) -> dict[str, Any]:
    text = (message or "").strip()
    if not text and not attachments:
        raise ChatError("empty_message", "Type a message first.")
    if len(text) > MAX_MESSAGE:
        raise ChatError("message_too_long", "That message is too long.",
                        "Attach a file instead of pasting a lot of text.")
    project = projects.get_project(project_id)
    status = ai_setup.get_status()
    if not status["ready"]:
        raise ChatError(
            "ai_not_ready", "Connect your AI first.", "Open Your AI and finish the steps."
        )
    files = resolve_attachments(project_id, attachments)
    text = text or "I attached a file."
    with _LOCK:
        old = _TURNS.get(project_id)
        if old is not None and not old.done:
            raise ChatError("turn_running", "The AI is still working on your last message.",
                            "Wait for it to finish, or press Stop.")
        turn = Turn(project_id, uuid.uuid4().hex)
        _TURNS[project_id] = turn
    _save_rows(project_id, [{"role": "user", "text": text,
                             "meta": {"attachments": [p.name for p in files]}}])
    turn.thread = threading.Thread(
        target=_run_turn, args=(turn, project, text, files, status["mode"]), daemon=True
    )
    turn.thread.start()
    return {"ok": True, "turn_id": turn.turn_id}


_RULES = (
    ("session_lost", re.compile(r"no conversation found|session.*not found", re.I)),
    ("no_credit", re.compile(r"credit balance|billing", re.I)),
    ("auth", re.compile(
        r"not logged in|please run /login|invalid api key|authentication|unauthorized|"
        r"oauth token|\b401\b", re.I)),
    ("usage_limit", re.compile(
        r"usage limit|limit reached|rate limit|too many requests|\b429\b|resets at", re.I)),
    ("network", re.compile(
        r"enotfound|econnrefused|econnreset|etimedout|fetch failed|network|overloaded|\b529\b|"
        r"\b503\b|socket hang up|api_error|server_error|connection", re.I)),
)


def classify_failure(*, text: str, stderr: str, assistant_error: str, limited: bool,
                     subtype: str, mode: str) -> str:
    if subtype == "error_max_turns":
        return "too_long"
    blob = f"{text} {stderr} {assistant_error}"
    hit = {name for name, rule in _RULES if rule.search(blob)}
    if "session_lost" in hit:
        return "session_lost"
    if assistant_error == "billing_error" or "no_credit" in hit:
        return "no_credit"
    if assistant_error == "authentication_failed" or "auth" in hit:
        return "bad_api_key" if mode == "api_key" else "not_signed_in"
    if limited or assistant_error == "rate_limit" or "usage_limit" in hit:
        return "usage_limit"
    if "network" in hit:
        return "network"
    return "ai_failed"


def failure_event(code: str, mode: str) -> dict[str, str]:
    message, hint = FAILURES.get((code, mode)) or FAILURES[(code, "")]
    return {"code": code, "message": message, "hint": hint}


class Mapper:
    """Turns the program's stream-json lines into events and the rows saved when the turn ends."""

    def __init__(self, turn: Turn, project_id: int, mode: str, new_session: str | None) -> None:
        self.turn, self.project_id, self.mode = turn, project_id, mode
        self.new_session = new_session
        self.filter = ChipFilter()
        self.text = ""
        self.rows: list[dict[str, Any]] = []
        self.started: dict[str, int] = {}
        self.saw_delta = False
        self.limited = False
        self.assistant_error = ""
        self.result: dict[str, Any] | None = None
        self.failure: dict[str, str] | None = None
        self.ended = False

    def feed(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "system":
            if msg.get("subtype") == "init":
                self._init(msg)
        elif kind == "stream_event":
            self._stream(msg.get("event") or {})
        elif kind == "assistant":
            self._assistant(msg.get("message") or {}, msg.get("error"))
        elif kind == "user":
            self._tool_results(msg.get("message") or {})
        elif kind == "rate_limit_event":
            if (msg.get("rate_limit_info") or {}).get("status") == "rejected":
                self.limited = True
        elif kind == "result":
            self.result = msg

    def _init(self, msg: dict[str, Any]) -> None:
        sid = msg.get("session_id")
        if self.new_session and isinstance(sid, str) and sid:
            projects.set_chat_session(self.project_id, sid)
            self.new_session = None
        for server in msg.get("mcp_servers") or []:
            if (isinstance(server, dict) and server.get("name") == SERVER_NAME
                    and server.get("status") in ("failed", "needs-auth")):
                self.failure = failure_event("tools_unavailable", self.mode)
                _kill(self.turn.proc)

    def _stream(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "message_start":
            self.saw_delta = False
        elif kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                self._tool_started(block.get("id"), block.get("name"))
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                self.saw_delta = True
                self._text(delta["text"])

    def _assistant(self, message: dict[str, Any], error: Any) -> None:
        if error:
            self.assistant_error = str(error)
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and not self.saw_delta:
                self._text(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                self._tool_started(block.get("id"), block.get("name"))
        self.saw_delta = False

    def _text(self, raw: str) -> None:
        clean = self.filter.feed(raw)
        if clean:
            self.text += clean
            self.turn.emit("text", {"delta": clean})

    def _tool_started(self, tool_id: Any, name: Any) -> None:
        if not isinstance(tool_id, str) or tool_id in self.started:
            return
        self._flush_text()
        short = str(name or "").removeprefix(TOOL_PREFIX)
        label = TOOL_LABELS.get(short, "Working...")
        self.started[tool_id] = len(self.rows)
        self.rows.append({"role": "tool", "text": label, "meta": {"tool": short, "ok": None}})
        self.turn.emit("tool", {"id": tool_id, "name": short, "label": label, "state": "started"})

    def _tool_results(self, message: dict[str, Any]) -> None:
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_id = block.get("tool_use_id")
            if tool_id in self.started:
                ok = not block.get("is_error")
                self.rows[self.started[tool_id]]["meta"]["ok"] = ok
                self.turn.emit("tool", {"id": tool_id, "state": "finished", "ok": ok})
                self.turn.emit("refresh", {})

    def _flush_text(self) -> None:
        tail = self.filter.finish()
        if tail:
            self.text += tail
            self.turn.emit("text", {"delta": tail})
        if self.text.strip():
            self.rows.append({"role": "assistant", "text": self.text.strip(), "meta": {}})
        self.text = ""

    def abort(self, code: str) -> None:
        if self.ended:
            return
        self._flush_text()
        self.failure = failure_event(code, self.mode)
        self.turn.emit("error", self.failure)
        self.turn.emit("done", {"ok": False, "cancelled": False})
        self.ended = True

    def finish(self, code: int, stderr: str) -> None:
        if self.ended:
            return
        self._flush_text()
        last = next((r for r in reversed(self.rows) if r["role"] == "assistant"), None)
        if last is not None and (self.filter.chips or self.filter.buttons):
            last["meta"] = {"chips": self.filter.chips, "buttons": self.filter.buttons}
        if self.turn.cancelled:
            self.turn.emit("done", {"ok": False, "cancelled": True})
            self.ended = True
            return
        if self.failure is None:
            if self.turn.timed_out:
                self.failure = failure_event("timeout", self.mode)
            else:
                result = self.result
                failed = (result is None and code != 0) or (
                    result is not None
                    and (result.get("is_error") or result.get("subtype") not in (None, "success"))
                )
                if failed:
                    result = result or {}
                    self.failure = failure_event(classify_failure(
                        text=str(result.get("result") or ""), stderr=stderr,
                        assistant_error=self.assistant_error, limited=self.limited,
                        subtype=str(result.get("subtype") or ""), mode=self.mode), self.mode)
        if self.failure is not None:
            self.turn.emit("error", self.failure)
            if self.failure["code"] == "session_lost":
                projects.set_chat_session(self.project_id, "")
        elif self.filter.chips or self.filter.buttons:
            self.turn.emit("chips", {"options": self.filter.chips, "buttons": self.filter.buttons})
        self.turn.emit("done", {"ok": self.failure is None, "cancelled": False})
        self.ended = True


# --- which tools the AI gets (5.2) ---------------------------------------------------------------

STUDIO_TOOLS = (
    "get_status", "get_workflow_guide", "list_providers", "ingest_offer_source",
    "save_offer_profile", "get_offer", "list_projects", "get_project", "update_project",
    "move_reserve_people", "list_designation_templates", "create_campaign", "update_campaign",
    "get_campaign", "list_campaigns", "search_web", "add_companies", "list_companies",
    "set_company_status", "approve_companies", "estimate_people_search", "find_people",
    "list_people", "set_person_status", "recheck_people", "estimate_email_search", "find_emails",
    "get_drafting_batch", "save_drafts", "run_qa", "list_qa_issues", "get_review_batch",
    "record_review", "export_gate", "export_campaign",
    "list_lemlist_campaigns", "check_send_readiness", "acknowledge_compliance", "sync_mailbox",
    "get_analytics", "add_suppression", "get_contacts_template", "import_contacts",
)
WITHHELD_TOOLS = frozenset({
    "save_api_key", "create_project", "open_dashboard", "send_emails", "push_to_lemlist",
    "get_search_tasks", "submit_search_results", "skip_task", "get_spot_check", "record_spot_check",
})


# --- the command line and the message (5.3, 5.4) -------------------------------------------------


def build_command(binary: str, *, session_id: str, resume: bool, mcp_config: Path,
                  prompt_file: Path) -> list[str]:
    cmd = [
        *ai_setup.launcher(binary), "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--mcp-config", str(mcp_config), "--strict-mcp-config",
        "--tools", "",
        "--allowedTools", allowed_tools_arg(),
        "--permission-mode", "dontAsk",
        "--append-system-prompt-file", str(prompt_file),
        "--disable-slash-commands",
        "--max-turns", str(MAX_TURNS),
    ]
    cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    return cmd


def build_prompt(project: Mapping[str, Any], text: str, files: Sequence[Path]) -> str:
    lines = [
        "[Studio context. Never repeat this block to the person.",
        f"project_id: {project['id']}",
        f"project_name: {project['name']}",
        f"sender_name: {project['sender_name'] or '(not set yet)'}",
        f"sender_title: {project.get('sender_title') or '(not set)'}",
        f"sender_company: {project.get('sender_company') or '(not set)'}",
        f"product_attached: {'yes' if project['offer_id'] else 'no'}",
    ]
    if files:
        lines.append("attached_files:")
        lines += [f"- {p.name}: {p}" for p in files]
    lines.append("]")
    return "\n".join(lines) + "\n\n" + text


# --- running the program (5.5) -------------------------------------------------------------------


def _stream_process(turn: Turn, cmd: list[str], env: dict[str, str], prompt: str,
                    mapper: Mapper) -> tuple[int, str]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "cwd": str(studio_dir()), "env": env, "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)  # no shell; the caller handles FileNotFoundError
    turn.proc = proc
    if turn.cancelled:                      # Stop was pressed before the program started
        _kill(proc)
    stderr_text = bytearray()
    drain = threading.Thread(target=_drain, args=(proc.stderr, stderr_text), daemon=True)
    drain.start()
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
    except OSError:
        pass
    started = time.monotonic()
    last = {"at": started}

    def watchdog() -> None:                       # kills a silent or endless program
        while proc.poll() is None:
            now = time.monotonic()
            if now - last["at"] > IDLE_TIMEOUT_S or now - started > TURN_TIMEOUT_S:
                turn.timed_out = True
                _kill(proc)
                return
            time.sleep(WATCH_INTERVAL_S)

    threading.Thread(target=watchdog, daemon=True).start()
    assert proc.stdout is not None
    try:
        for raw in iter(proc.stdout.readline, b""):
            last["at"] = time.monotonic()
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue                          # a stray line is ignored
            if isinstance(msg, dict):
                mapper.feed(msg)
    except BaseException:  # a bug in the mapping must not leave the program running
        _kill(proc)
        raise
    code = proc.wait()
    drain.join(5.0)  # stderr may still be in the pipe when the program has ended
    return code, bytes(stderr_text).decode("utf-8", "replace")


def _drain(stream: IO[bytes], sink: bytearray) -> None:
    """Read stderr to the end (so the program never blocks) but keep only the first 8000 bytes."""
    for chunk in iter(lambda: stream.read(4096), b""):
        if len(sink) < 8000:
            sink.extend(chunk[: 8000 - len(sink)])


def _kill(proc: subprocess.Popen[bytes] | None) -> None:
    """Stop the program and what it started (taskkill /T on Windows, the process group elsewhere).
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _run_turn(turn: Turn, project: dict[str, Any], text: str, files: list[Path], mode: str) -> None:
    session = projects.get_chat_session(project["id"])
    mapper = Mapper(turn, project["id"], mode, new_session=None if session else str(uuid.uuid4()))
    turn.emit("turn", {"turn_id": turn.turn_id, "project_id": project["id"]})
    try:
        binary = ai_setup.find_claude()
        if binary is None:
            mapper.abort("missing_cli")
            return
        mcp_config, prompt_file = write_runtime_files()
        cmd = build_command(binary, session_id=session or mapper.new_session, resume=bool(session),
                            mcp_config=mcp_config, prompt_file=prompt_file)
        code, stderr = _stream_process(turn, cmd, ai_setup.child_env(mode),
                                       build_prompt(project, text, files), mapper)
        mapper.finish(code, stderr)
    except FileNotFoundError:
        mapper.abort("missing_cli")
    except Exception as exc:                      # a thread must never die silently
        log.error("chat turn failed: %s", type(exc).__name__)
        mapper.abort("ai_failed")
    finally:
        _save_rows(project["id"], mapper.rows)
        turn.finish()


# --- the chips and buttons (5.6) -----------------------------------------------------------------

GO_TARGETS = {  # marker target -> (button label, page address); anything else is dropped
    "keys": ("Open Connectors", "#/keys"),
    "mailbox": ("Connect your email", "#/mailbox"),
    "settings": ("Open Settings", "#/settings"),
    "ai": ("Open Your AI", "#/connect"),
}
_MARKER = re.compile(r"\s*(chips|go)\s*:(.*)", re.S | re.I)


class ChipFilter:
    """Takes the AI's text in pieces; returns what is safe to show now and collects markers."""

    def __init__(self) -> None:
        self.hold = ""
        self.chips: list[str] = []
        self.buttons: list[dict[str, str]] = []

    def feed(self, piece: str) -> str:
        text, self.hold, out = self.hold + piece, "", ""
        while text:
            start = text.find("[[")
            if start == -1:
                if text.endswith("["):                       # maybe the first half of "[["
                    out, self.hold = out + text[:-1], "["
                else:
                    out += text
                break
            out += text[:start]
            end = text.find("]]", start)
            if end == -1:                                    # marker not finished yet: hold it back
                self.hold = text[start:]
                break
            body = text[start + 2 : end]
            if not self._take(body):
                out += text[start : end + 2]                 # not one of ours: it is plain text
            text = text[end + 2 :]
        return out

    def finish(self) -> str:
        """Text still held back when the message ends (an unfinished marker is shown as text)."""
        tail, self.hold = self.hold, ""
        return tail

    def _take(self, body: str) -> bool:
        match = _MARKER.match(body)
        if not match:
            return False
        kind, value = match.group(1).lower(), match.group(2)
        if kind == "chips":
            options = [o.strip()[:30] for o in value.split("|") if o.strip()]
            self.chips = options[:4]
        else:
            target = GO_TARGETS.get(value.strip().lower())
            if target and all(b["href"] != target[1] for b in self.buttons):
                self.buttons.append({"label": target[0], "href": target[1]})
        return True


TOOL_LABELS = {
    "get_status": "Checking where things stand...",
    "get_workflow_guide": "Reading my instructions...",
    "list_providers": "Checking which services are connected...",
    "ingest_offer_source": "Reading your product...",
    "save_offer_profile": "Saving what your product is about...",
    "get_offer": "Looking at your product notes...",
    "list_projects": "Checking your projects...",
    "get_project": "Checking your project...",
    "update_project": "Saving your project details...",
    "move_reserve_people": "Moving saved people into this search...",
    "list_designation_templates": "Looking up job roles...",
    "create_campaign": "Setting up the search...",
    "update_campaign": "Updating the search...",
    "get_campaign": "Checking the search...",
    "list_campaigns": "Checking your searches...",
    "search_web": "Searching the web...",
    "add_companies": "Adding companies...",
    "list_companies": "Looking at the companies...",
    "set_company_status": "Updating the company list...",
    "approve_companies": "Approving the companies...",
    "estimate_people_search": "Working out the cost...",
    "estimate_email_search": "Working out the cost...",
    "find_people": "Finding people...",
    "list_people": "Looking at the people found...",
    "set_person_status": "Updating the people list...",
    "recheck_people": "Double-checking the people...",
    "find_emails": "Finding and checking email addresses...",
    "get_drafting_batch": "Getting ready to write messages...",
    "save_drafts": "Saving the messages...",
    "run_qa": "Checking the messages...",
    "list_qa_issues": "Reading the check results...",
    "get_review_batch": "Reviewing the messages...",
    "record_review": "Saving the review...",
    "export_gate": "Checking everything is ready...",
    "export_campaign": "Preparing your file...",
    "list_lemlist_campaigns": "Looking at your lemlist campaigns...",
    "check_send_readiness": "Checking what is needed to send...",
    "acknowledge_compliance": "Noting your email-rules answer...",
    "sync_mailbox": "Reading replies...",
    "get_analytics": "Adding up the results...",
    "add_suppression": "Adding to the do-not-contact list...",
    "get_contacts_template": "Getting the contacts template...",
    "import_contacts": "Reading your contacts file...",
}


# --- errors the person sees (5.7) ----------------------------------------------------------------

FAILURES = {  # (code, mode) or (code, "") -> (message, hint)
    ("not_signed_in", ""): (
        "Your AI is not signed in.", "Open Your AI and sign in to Claude again.",
    ),
    ("bad_api_key", ""): (
        "Anthropic did not accept your API key.", "Open Your AI and paste a working key.",
    ),
    ("usage_limit", "claude_plan"): ("You have reached your Claude plan's usage limit for now.",
        "It resets after a few hours. Try again later, or switch to an API key in Your AI."),
    ("usage_limit", "api_key"): (
        "Your Anthropic account is being rate limited.", "Wait a minute and try again.",
    ),
    ("no_credit", ""): ("Your Anthropic account has no credit left.",
        "Add credit at console.anthropic.com, or switch to your Claude plan in Your AI."),
    ("missing_cli", ""): (
        "Claude Code is not installed on this computer.",
        "Open Your AI and follow the install step.",
    ),
    ("network", ""): (
        "I could not reach Anthropic.", "Check your internet connection and try again.",
    ),
    ("tools_unavailable", ""): (
        "The Lead Generator tools did not start.",
        "Close and reopen Lead Generator, then try again.",
    ),
    ("session_lost", ""): (
        "I lost the thread of our conversation.",
        "Send your message again and I will start fresh.",
    ),
    ("too_long", ""): ("I stopped after a lot of work.", 'Say "continue" and I will carry on.'),
    ("timeout", ""): ("The AI stopped answering.", "Try again in a moment."),
    ("ai_failed", ""): (
        "The AI stopped with a problem.",
        "Try again. If it keeps happening, look at logs/leadgen.log.",
    ),
}


# --- the stream to the page (4.7, 5.8) -----------------------------------------------------------


def stream_events(project_id: int, turn_id: str, after: int = 0,
                  heartbeat_s: float = HEARTBEAT_S) -> Iterator[str]:
    with _LOCK:
        turn = _TURNS.get(project_id)
    if turn is None or turn.turn_id != turn_id:
        raise ChatError("not_found", "That conversation step is not available any more.",
                        "Reload the page.")

    def generate() -> Iterator[str]:
        index = max(0, int(after))
        while True:
            with turn.cond:
                if index >= len(turn.events) and not turn.done:
                    turn.cond.wait(timeout=heartbeat_s)          # wakes on every new event
                batch = turn.events[index:]
                finished = turn.done and index + len(batch) >= len(turn.events)
            if not batch:
                if finished:
                    return
                yield ": keep-alive\n\n"  # silence: also notices a closed page
                continue
            for event in batch:
                data = json.dumps(event["data"], ensure_ascii=False)
                yield f"id: {event['id']}\nevent: {event['event']}\ndata: {data}\n\n"
            index += len(batch)
            if finished:
                return

    return generate()
