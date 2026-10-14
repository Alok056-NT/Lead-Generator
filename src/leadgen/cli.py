"""Command line entry point: `leadgen doctor | version | paths`. Thin adapter, no business logic."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from leadgen import __version__
from leadgen.core import config, paths, secrets
from leadgen.core.logging import setup_logging

MIN_PYTHON = (3, 11)

# Components built by later tasks; doctor lists them so the owner can see what is coming.
_LATER_COMPONENTS = (
    "database",
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

    print("Later components:")
    for name in _LATER_COMPONENTS:
        print(f"  {name:<28} not installed yet")

    print("\nAll good." if healthy else "\nProblems found. See the lines marked above.")
    return 0 if healthy else 1


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(prog="leadgen", description="Lead Generator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check the installation").set_defaults(func=cmd_doctor)
    sub.add_parser("version", help="show the version").set_defaults(func=cmd_version)
    sub.add_parser("paths", help="show workspace locations").set_defaults(func=cmd_paths)
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
