"""Tests for workspace paths."""

from __future__ import annotations

import pytest

from leadgen.core import paths


def test_workspace_created_with_subfolders(workspace):
    assert not workspace.exists()
    assert paths.home() == workspace
    for name in ("exports", "logs", "cache"):
        assert (workspace / name).is_dir()


def test_env_override_changes_location(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("LEADGEN_HOME", str(other))
    assert paths.home() == other
    assert paths.db_path() == other / "leadgen.db"


def test_default_home_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEADGEN_HOME", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.home() == tmp_path / "LeadGenerator"


def test_file_paths(workspace):
    assert paths.secrets_path() == workspace / "secrets.env"
    assert paths.settings_path() == workspace / "settings.yaml"
    assert paths.logs_dir() == workspace / "logs"
    assert paths.cache_dir() == workspace / "cache"


def test_exports_dir_created_and_sanitised(workspace):
    d = paths.exports_dir("Acme Q4")
    assert d == workspace / "exports" / "acme-q4"
    assert d.is_dir()
    assert paths.exports_dir("../../etc").parent == workspace / "exports"


def test_exports_dir_rejects_empty_slug():
    with pytest.raises(ValueError):
        paths.exports_dir("///")
