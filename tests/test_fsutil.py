"""Tests for atomic writes."""

from __future__ import annotations

import os

import pytest

from leadgen.core.fsutil import atomic_write_text


def test_writes_lf_utf8_and_replaces(tmp_path):
    target = tmp_path / "a" / "f.txt"
    atomic_write_text(target, "one\ntwo é\n")
    assert target.read_bytes() == "one\ntwo é\n".encode()
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in target.parent.iterdir()] == ["f.txt"]


def test_failure_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "f.txt"
    target.write_text("keep", encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "lost")
    assert target.read_text(encoding="utf-8") == "keep"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]
