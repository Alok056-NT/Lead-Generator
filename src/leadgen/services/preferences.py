"""The few settings the dashboard can change: sender identity, postal address, locale, credit
budgets and strict review. Everything else in `settings.yaml` is left alone."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from leadgen import db
from leadgen.core import config

SENDER_FIELDS = ("name", "company", "role", "email", "website")
AI_MODES = ("claude_plan", "api_key")
AI_MODE_KEY = "ai_mode"
BUDGET_FIELDS = ("search", "hunter", "verify", "finder")
MAX_LINE = 200
MAX_ADDRESS = 500
MAX_BUDGET = 1_000_000
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PreferencesError(Exception):
    """A setting was not valid. The message names the field, never the value."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, ...}` shape used everywhere."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


def get_ai_mode() -> str:
    """The stored AI mode: "claude_plan", "api_key", or "" when not chosen yet."""
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (AI_MODE_KEY,))
    value = str(rows[0]["value"]) if rows else ""
    return value if value in AI_MODES else ""


def set_ai_mode(mode: str) -> str:
    """Store the choice (upsert on `key`); anything else raises PreferencesError("invalid_value",
    "Choose Claude plan or API key.")."""
    if mode not in AI_MODES:
        raise PreferencesError("invalid_value", "Choose Claude plan or API key.")
    with db.session() as conn:
        db.upsert(conn, "settings", ["key"], key=AI_MODE_KEY, value=mode)
    return mode


def get_preferences() -> dict[str, Any]:
    """The editable settings. Raises `ConfigError` when settings.yaml is broken."""
    settings = config.load_settings()
    return {
        "ok": True,
        "sender": {f: getattr(settings.sender, f) for f in SENDER_FIELDS},
        "postal_address": settings.postal_address,
        "locale": settings.locale,
        "budgets": {f: getattr(settings.budgets, f) for f in BUDGET_FIELDS},
        "strict_review": settings.strict_review,
    }


def _line(value: Any, label: str, limit: int = MAX_LINE) -> str:
    if not isinstance(value, str):
        raise PreferencesError("invalid_value", f"{label} must be text.")
    text = value.strip()
    if "\n" in text or "\r" in text:
        raise PreferencesError("invalid_value", f"{label} must be a single line.")
    if len(text) > limit:
        raise PreferencesError(
            "invalid_value", f"{label} is too long (at most {limit} characters)."
        )
    return text


def _budget(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise PreferencesError("invalid_value", f"The {label} budget must be a whole number.")
    number = int(value)
    if not 0 <= number <= MAX_BUDGET:
        raise PreferencesError("invalid_value", f"The {label} budget must be 0 or more.")
    return number


def update_preferences(changes: dict[str, Any]) -> dict[str, Any]:
    """Change the editable settings; only the keys given change. Returns `get_preferences()`.

    Raises `PreferencesError` for an unknown or invalid field and `ConfigError` for a broken file.
    """
    allowed = {"sender", "postal_address", "locale", "budgets", "strict_review"}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise PreferencesError(
            "invalid_value", f"These settings cannot be changed here: {unknown}."
        )
    settings = config.load_settings()
    data = settings.model_dump(mode="json")
    if "sender" in changes:
        sender = changes["sender"]
        if not isinstance(sender, dict) or set(sender) - set(SENDER_FIELDS):
            raise PreferencesError(
                "invalid_value", "sender must hold name, company, role, email, website."
            )
        for field, value in sender.items():
            text = _line(value, f"Sender {field}")
            if field == "email" and text and not _EMAIL_RE.match(text):
                raise PreferencesError("invalid_value", "The sender email does not look right.")
            data["sender"][field] = text
    if "postal_address" in changes:
        value = changes["postal_address"]
        if not isinstance(value, str):
            raise PreferencesError("invalid_value", "The postal address must be text.")
        if len(value.strip()) > MAX_ADDRESS:
            raise PreferencesError("invalid_value", "The postal address is too long.")
        data["postal_address"] = value.strip()
    if "locale" in changes:
        locale = _line(changes["locale"], "Locale", 12)
        if not _LOCALE_RE.match(locale):
            raise PreferencesError(
                "invalid_value", "Locale looks like en-GB, en-US or en-AU.", "Use language-REGION."
            )
        data["locale"] = locale
    if "budgets" in changes:
        budgets = changes["budgets"]
        if not isinstance(budgets, dict) or set(budgets) - set(BUDGET_FIELDS):
            raise PreferencesError(
                "invalid_value", "budgets must hold search, hunter, verify, finder."
            )
        for field, value in budgets.items():
            data["budgets"][field] = _budget(value, field)
    if "strict_review" in changes:
        if not isinstance(changes["strict_review"], bool):
            raise PreferencesError("invalid_value", "strict_review must be true or false.")
        data["strict_review"] = changes["strict_review"]
    try:
        config.save_settings(config.Settings.model_validate(data))
    except ValidationError:
        raise PreferencesError("invalid_value", "Those settings are not valid.") from None
    return get_preferences()
