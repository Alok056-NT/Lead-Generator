"""User settings stored in `settings.yaml`. Unknown keys are preserved on save."""

from __future__ import annotations

import os
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from leadgen.core import paths
from leadgen.core.fsutil import atomic_write_text


class ConfigError(Exception):
    """settings.yaml is unreadable or invalid. The message never includes setting values."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="allow")


class Sender(_Section):
    name: str = ""
    company: str = ""
    role: str = ""
    email: str = ""
    website: str = ""


class Defaults(_Section):
    country: str = ""
    channels: list[str] = Field(default_factory=lambda: ["linkedin"])
    seniority_min: str = "manager"


class Budgets(_Section):
    search: int = 300
    hunter: int = 25
    verify: int = 200


class Sending(_Section):
    daily_cap: int = 20
    max_daily_cap: int = 50
    warmup_step: int = 5
    min_gap_s: int = 60
    max_gap_s: int = 180
    quiet_start: str = "20:00"
    quiet_end: str = "08:00"
    max_bounce_rate: float = 0.05


class Dashboard(_Section):
    host: str = "127.0.0.1"
    port: int = 8765


class McpHttp(_Section):
    host: str = "127.0.0.1"
    port: int = 8766
    secret: str = ""


class Settings(_Section):
    """All user settings with defaults (PLAN section 5)."""

    sender: Sender = Field(default_factory=Sender)
    postal_address: str = ""
    locale: str = "en-GB"
    defaults: Defaults = Field(default_factory=Defaults)
    budgets: Budgets = Field(default_factory=Budgets)
    confirm_threshold_credits: int = 20
    strict_review: bool = True
    allow_ddg_fallback: bool = False
    sending: Sending = Field(default_factory=Sending)
    dashboard: Dashboard = Field(default_factory=Dashboard)
    mcp_http: McpHttp = Field(default_factory=McpHttp)


def load_settings() -> Settings:
    """Load `settings.yaml` (defaults when the file is absent or empty).

    Raises ConfigError for broken YAML or invalid values.
    """
    path = paths.settings_path()
    if not path.exists():
        return Settings()
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"settings.yaml is not valid YAML ({type(exc).__name__})") from None
    if raw is None:
        return Settings()
    if not isinstance(raw, dict):
        raise ConfigError("settings.yaml must contain a mapping of settings")
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"settings.yaml has invalid values ({problems})") from None


def save_settings(settings: Settings) -> None:
    """Write settings to `settings.yaml` atomically (unknown keys included)."""
    data = settings.model_dump(mode="json")
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    atomic_write_text(paths.settings_path(), text, private=os.name == "posix")
