"""Command line entry point: `leadgen doctor | version | paths`. Thin adapter, no business logic."""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from leadgen import __version__, db
from leadgen.core import config, paths, secrets
from leadgen.core.logging import setup_logging
from leadgen.providers.registry import ALL_PROVIDERS
from leadgen.services import status

MIN_PYTHON = (3, 11)

# Components built by later tasks; doctor lists them so the owner can see what is coming.
_LATER_COMPONENTS = (
    "search and email providers",
    "MCP server",
    "dashboard",
    "email sending",
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


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(prog="leadgen", description="Lead Generator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check the installation").set_defaults(func=cmd_doctor)
    sub.add_parser("version", help="show the version").set_defaults(func=cmd_version)
    sub.add_parser("paths", help="show workspace locations").set_defaults(func=cmd_paths)
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
