"""Tests for logging setup."""

from __future__ import annotations

import logging

from leadgen.core import paths
from leadgen.core.logging import setup_logging, shutdown_logging


def test_logs_to_file_and_stderr_not_stdout(capsys):
    logger = setup_logging("INFO")
    logging.getLogger("leadgen.test").info("hello log")
    for h in logger.handlers:
        h.flush()
    out, err = capsys.readouterr()
    assert out == ""
    assert "hello log" in err
    assert "hello log" in (paths.logs_dir() / "leadgen.log").read_text(encoding="utf-8")


def test_setup_is_idempotent():
    def ours(lg):
        return [h for h in lg.handlers if getattr(h, "_leadgen_handler", False)]

    setup_logging()
    logger = setup_logging()
    assert len(ours(logger)) == 2
    shutdown_logging()
    assert ours(logger) == []
