"""Workspace locations. The workspace lives outside the repo (default ~/LeadGenerator)."""

from __future__ import annotations

import os
from pathlib import Path

from leadgen.core.text import slugify

ENV_VAR = "LEADGEN_HOME"
SUBFOLDERS = ("exports", "logs", "cache")


def _resolve_home() -> Path:
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "LeadGenerator"


def ensure_workspace() -> Path:
    """Create the workspace and its subfolders if missing; return the workspace path."""
    root = _resolve_home()
    root.mkdir(parents=True, exist_ok=True)
    for name in SUBFOLDERS:
        (root / name).mkdir(exist_ok=True)
    return root


def home() -> Path:
    """The workspace folder (`LEADGEN_HOME` or ~/LeadGenerator), created on first use."""
    return ensure_workspace()


def db_path() -> Path:
    """Path of the SQLite database."""
    return home() / "leadgen.db"


def secrets_path() -> Path:
    """Path of `secrets.env` (API keys)."""
    return home() / "secrets.env"


def settings_path() -> Path:
    """Path of `settings.yaml`."""
    return home() / "settings.yaml"


def exports_dir(campaign_slug: str) -> Path:
    """Export folder for one campaign (created). The slug is sanitised to stay inside exports/."""
    slug = slugify(campaign_slug)
    if not slug:
        raise ValueError("campaign slug must contain at least one letter or digit")
    path = home() / "exports" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """Folder for log files."""
    return home() / "logs"


def cache_dir() -> Path:
    """Folder for the HTTP/search cache."""
    return home() / "cache"
