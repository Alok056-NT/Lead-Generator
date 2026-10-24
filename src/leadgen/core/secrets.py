"""API keys and passwords: read from `secrets.env`, then the process environment.

Values are never logged and never placed in exceptions; only names appear in messages.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from leadgen.core import paths
from leadgen.core.fsutil import atomic_write_text

KNOWN_KEYS: tuple[str, ...] = (
    "SERPER_API_KEY",
    "BRAVE_API_KEY",
    "HUNTER_API_KEY",
    "REOON_API_KEY",
    "ZEROBOUNCE_API_KEY",
    "LEMLIST_API_KEY",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "IMAP_HOST",
    "IMAP_PORT",
    "IMAP_USER",
    "IMAP_PASSWORD",
    "ANTHROPIC_API_KEY",
)

_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_QUOTES = ("'", '"')


def _check_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid secret name {name!r}: use UPPER_CASE letters, digits, _")


def _parse_line(line: str) -> tuple[str, str] | None:
    """Return (name, value) for a `KEY=value` line, or None for blanks/comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    name, _, value = stripped.partition("=")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        value = value[1:-1]
    return name.strip(), value


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8-sig").splitlines()


def _file_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read_lines(paths.secrets_path()):
        parsed = _parse_line(line)
        if parsed:
            values[parsed[0]] = parsed[1].strip()
    return values


def get(name: str) -> str:
    """Value from `secrets.env`, else the environment, else "". Whitespace-stripped."""
    value = _file_values().get(name, "").strip()
    if value:
        return value
    return os.environ.get(name, "").strip()


def stored(name: str) -> str:
    """Value saved in `secrets.env` only (ignores the environment), or ""."""
    return _file_values().get(name, "").strip()


def _format(name: str, value: str) -> str:
    if value[:1] in _QUOTES:
        value = f'"{value}"'
    return f"{name}={value}"


def _write(lines: list[str]) -> None:
    text = "\n".join(lines)
    if lines:
        text += "\n"
    atomic_write_text(paths.secrets_path(), text, private=True)


def set(name: str, value: str) -> None:  # noqa: A001 - public name required by the plan
    """Store `value` under `name`, keeping other keys and comments. Rewrites atomically."""
    _check_name(name)
    value = value.strip()
    if not value:
        raise ValueError(f"empty value for {name}; use delete() to remove a key")
    if "\n" in value or "\r" in value:
        raise ValueError(f"value for {name} must be a single line")
    lines = _read_lines(paths.secrets_path())
    new_line = _format(name, value)
    out: list[str] = []
    replaced = False
    for line in lines:
        parsed = _parse_line(line)
        if parsed and parsed[0] == name:
            if not replaced:
                out.append(new_line)
                replaced = True
            continue  # drop duplicates
        out.append(line)
    if not replaced:
        out.append(new_line)
    _write(out)


def delete(name: str) -> bool:
    """Remove `name` from `secrets.env`. Returns True if it was there."""
    _check_name(name)
    lines = _read_lines(paths.secrets_path())
    out = [ln for ln in lines if not ((p := _parse_line(ln)) and p[0] == name)]
    if len(out) == len(lines):
        return False
    _write(out)
    return True


def masked(name: str) -> str:
    """Safe display form: "…" plus the last 4 characters, or "" when not configured.

    Values shorter than 8 characters show only "…" so a short secret is never fully revealed.
    """
    value = get(name)
    if not value:
        return ""
    if len(value) < 8:
        return "…"
    return "…" + value[-4:]


def configured() -> dict[str, bool]:
    """Which known keys have a value (file or environment)."""
    return {name: bool(get(name)) for name in KNOWN_KEYS}
