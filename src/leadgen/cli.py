"""Command line entry point: `leadgen doctor | version | paths`. Thin adapter, no business logic."""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from leadgen import __version__, db
from leadgen.core import config, paths, secrets
from leadgen.core.logging import setup_logging
from leadgen.providers.registry import ALL_PROVIDERS
from leadgen.services import status

MIN_PYTHON = (3, 11)

# Components built by later tasks; doctor lists them so the owner can see what is coming.
_LATER_COMPONENTS = (
    "dashboard",
)


def _workspace_writable(root: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=root):
            pass
    except OSError:
        return False
    return True


def cmd_version(_args: argparse.Namespace) -> int:
    """Print the package version."""
    print(__version__)
    return 0


def cmd_paths(_args: argparse.Namespace) -> int:
    """Print the workspace locations."""
    print(f"workspace : {paths.home()}")
    print(f"database  : {paths.db_path()}")
    print(f"secrets   : {paths.secrets_path()}")
    print(f"settings  : {paths.settings_path()}")
    print(f"exports   : {paths.home() / 'exports'}")
    print(f"logs      : {paths.logs_dir()}")
    print(f"cache     : {paths.cache_dir()}")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Check the installation and print a plain-English health report."""
    healthy = True

    py = sys.version_info
    py_ok = (py.major, py.minor) >= MIN_PYTHON
    healthy &= py_ok
    verdict = "OK" if py_ok else "TOO OLD (need 3.11+)"
    print(f"Python        : {py.major}.{py.minor}.{py.micro} {verdict}")
    print(f"Lead Generator: {__version__}")

    try:
        root = paths.home()
        writable = _workspace_writable(root)
        print(f"Workspace     : {root} (exists, {'writable' if writable else 'NOT writable'})")
        healthy &= writable
    except OSError as exc:
        print(f"Workspace     : cannot be created ({type(exc).__name__})")
        return 1

    print("API keys and mailbox settings:")
    for name, is_set in secrets.configured().items():
        shown = f"configured ({secrets.masked(name)})" if is_set else "not set"
        print(f"  {name:<20} {shown}")

    try:
        config.load_settings()
        print("Settings      : valid")
    except config.ConfigError as exc:
        healthy = False
        print(f"Settings      : PROBLEM - {exc}")

    try:
        version = db.init_db()
        print(f"Database      : ok (schema v{version})")
    except RuntimeError as exc:  # db.connect(): SQLite too old (message is plain English)
        healthy = False
        print(f"Database      : PROBLEM - {exc}")
    except (sqlite3.Error, OSError) as exc:
        healthy = False
        print(f"Database      : PROBLEM - {type(exc).__name__}")

    print("Later components:")
    for name in _LATER_COMPONENTS:
        print(f"  {name:<28} not installed yet")

    print("\nAll good." if healthy else "\nProblems found. See the lines marked above.")
    return 0 if healthy else 1


def cmd_keys_list(_args: argparse.Namespace) -> int:
    """Show every provider, whether its key is set, and this month's usage."""
    db.init_db()
    for item in status.list_providers():
        state = f"configured ({item['masked_key']})" if item["configured"] else "not set"
        if item["configured"] and not item["masked_key"]:
            state = "configured"
        if not item["implemented"]:
            state += ", not built yet"
        used = item["usage_month"]
        line = f"{item['key']:<11} {state:<34} used this month: {used['credits']:g} credits"
        if item["remaining_free"] is not None:
            line += f", about {item['remaining_free']:g} free left"
        print(line)
    return 0


def cmd_keys_set(args: argparse.Namespace) -> int:
    """Ask for a key without echoing it, save it and test it."""
    db.init_db()
    value = getpass.getpass(f"Paste the {args.provider} API key (hidden): ")
    result = status.set_key(args.provider, value)
    if result["ok"]:
        print(f"Saved {args.provider} key {result['masked_key']}. {result['message']}")
        return 0
    print(result["message"])
    return 1


def cmd_keys_test(args: argparse.Namespace) -> int:
    """Test a saved key (uses at most one credit)."""
    db.init_db()
    result = status.check_key(args.provider)
    print(result["message"])
    return 0 if result["ok"] else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run the MCP server: stdio by default, Streamable HTTP with --http."""
    from leadgen import mcp_server

    if args.http:
        mcp_server.run_http(args.host or "127.0.0.1", args.port, args.allowed_host or ())
    else:
        mcp_server.run_stdio()
    return 0


def cmd_tools(_args: argparse.Namespace) -> int:
    """Print the MCP tools (name and first sentence of the description)."""
    from leadgen import mcp_server

    for name, summary in mcp_server.tool_summaries():
        print(f"{name:<28} {summary}")
    return 0


_MAILBOX_KEYS = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "IMAP_HOST", "IMAP_PORT",
)  # fmt: skip
_PRESETS = {
    "gmail": ("smtp.gmail.com", "587"),
    "outlook": ("smtp.office365.com", "587"),
}


def cmd_mailbox_set(args: argparse.Namespace) -> int:
    """Ask for the mailbox settings (password hidden), save them and test the log-in."""
    from leadgen.send.smtp_imap import SmtpImapMailbox, default_imap_host

    host_default, port_default = _PRESETS.get(args.preset or "", ("", "587"))
    host = input(f"SMTP server [{host_default}]: ").strip() or host_default
    port = input(f"SMTP port [{port_default}]: ").strip() or port_default
    user = input("Mailbox address (user name): ").strip()
    password = getpass.getpass("Password or app password (hidden): ")
    imap_default = default_imap_host(host) if host else ""
    imap_host = input(f"IMAP server [{imap_default}]: ").strip() or imap_default
    if not (host and user and password):
        print("Server, address and password are all needed. Nothing was saved.")
        return 1
    previous = {name: secrets.stored(name) for name in _MAILBOX_KEYS}
    for name, value in (
        ("SMTP_HOST", host), ("SMTP_PORT", port), ("SMTP_USER", user),
        ("SMTP_PASSWORD", password), ("IMAP_HOST", imap_host),
    ):  # fmt: skip
        secrets.set(name, value)
    ok, message = SmtpImapMailbox.from_config().test()
    if not ok:
        for name, old in previous.items():
            if old:
                secrets.set(name, old)
            else:
                secrets.delete(name)
        print(f"The mailbox was not saved. {message}")
        return 1
    print(f"Saved. {message}")
    return 0


def cmd_mailbox_test(_args: argparse.Namespace) -> int:
    """Log in to the saved mailbox over SMTP and IMAP (nothing is sent)."""
    from leadgen.send.smtp_imap import SmtpImapMailbox

    ok, message = SmtpImapMailbox.from_config().test()
    print(message)
    return 0 if ok else 1


def cmd_send_pause(_args: argparse.Namespace) -> int:
    """Kill switch: stop sending (the worker stops before its next email)."""
    from leadgen.services import sending

    db.init_db()
    sending.pause()
    print("Sending is paused. Run `leadgen send resume` to switch it on again.")
    return 0


def cmd_send_resume(_args: argparse.Namespace) -> int:
    """Switch sending on again and forget earlier send errors."""
    from leadgen.services import sending

    db.init_db()
    sending.resume()
    print("Sending is on again. Queued emails go out when the worker runs (`leadgen send-worker`).")
    return 0


def cmd_send_status(_args: argparse.Namespace) -> int:
    """Show the send queue, the worker and today's cap."""
    from leadgen.services import sending

    db.init_db()
    queue = sending.queue_status()
    cap = sending.cap_status(config.load_settings(), datetime.now(UTC))
    worker = queue["worker"]
    last_stop = f" (last stop: {worker['reason']})" if worker["reason"] else ""
    paused = "yes - " + queue["pause_reason"] if queue["paused"] else "no"
    print(f"Paused       : {paused}")
    print(f"Worker       : {'running' if worker['running'] else 'not running'}{last_stop}")
    print(
        f"Queue        : {queue['queued']} queued, {queue['sent']} sent, {queue['failed']} "
        f"failed, {queue['refused']} refused, {queue['skipped']} skipped"
    )
    print(f"Today        : {cap['sent_today']} of {cap['cap_today']} allowed")
    return 0


def cmd_send_worker(_args: argparse.Namespace) -> int:
    """Send the queued emails one by one (runs until the queue is empty or a stop rule applies)."""
    from leadgen.services import sending

    db.init_db()
    result = sending.run_worker()
    print(f"Sent {result['sent']} email(s). Stopped: {result['reason']}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(prog="leadgen", description="Lead Generator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check the installation").set_defaults(func=cmd_doctor)
    sub.add_parser("version", help="show the version").set_defaults(func=cmd_version)
    sub.add_parser("paths", help="show workspace locations").set_defaults(func=cmd_paths)
    mcp = sub.add_parser("mcp", help="run the MCP server (stdio; --http for remote clients)")
    mcp.add_argument("--http", action="store_true", help="serve Streamable HTTP at /mcp/<secret>")
    mcp.add_argument("--host", default=None, help="address to bind (default 127.0.0.1 only)")
    mcp.add_argument("--port", type=int, default=None, help="port (default from settings, 8766)")
    mcp.add_argument(
        "--allowed-host",
        action="append",
        help="extra Host header to accept, e.g. a tunnel name (repeatable)",
    )
    mcp.set_defaults(func=cmd_mcp)
    sub.add_parser("tools", help="list the MCP tools").set_defaults(func=cmd_tools)
    mailbox = sub.add_parser("mailbox", help="set up and test the sending mailbox").add_subparsers(
        dest="mailbox_command", required=True
    )
    mb_set = mailbox.add_parser("set", help="save the SMTP/IMAP settings (password is prompted)")
    mb_set.add_argument("--preset", choices=sorted(_PRESETS), help="fill in a known provider")
    mb_set.set_defaults(func=cmd_mailbox_set)
    mailbox.add_parser("test", help="log in to the saved mailbox").set_defaults(
        func=cmd_mailbox_test
    )
    send = sub.add_parser("send", help="control email sending").add_subparsers(
        dest="send_command", required=True
    )
    send.add_parser("pause", help="stop sending (kill switch)").set_defaults(func=cmd_send_pause)
    send.add_parser("resume", help="switch sending on again").set_defaults(func=cmd_send_resume)
    send.add_parser("status", help="show the queue and today's cap").set_defaults(
        func=cmd_send_status
    )
    sub.add_parser("send-worker", help="send the queued emails (background worker)").set_defaults(
        func=cmd_send_worker
    )
    keys = sub.add_parser("keys", help="manage API keys").add_subparsers(
        dest="keys_command", required=True
    )
    providers = sorted(ALL_PROVIDERS)
    keys.add_parser("list", help="show providers and usage").set_defaults(func=cmd_keys_list)
    for name, func, text in (
        ("set", cmd_keys_set, "save a key (prompted, never shown)"),
        ("test", cmd_keys_test, "test a saved key (1 credit at most)"),
    ):
        sp = keys.add_parser(name, help=text)
        sp.add_argument("provider", choices=providers)
        sp.set_defaults(func=func)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the exit code."""
    args = build_parser().parse_args(argv)
    if args.command != "version":
        try:
            setup_logging()
        except OSError:
            # Broken workspace: carry on without file logging so `doctor` can explain the problem.
            pass
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
