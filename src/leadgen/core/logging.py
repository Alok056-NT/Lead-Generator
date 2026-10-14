"""Logging to a rotating file and stderr. Stdout is never used (MCP stdio owns it)."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from leadgen.core import paths

LOGGER_NAME = "leadgen"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_leadgen_handler"


def shutdown_logging() -> None:
    """Remove and close the handlers installed by `setup_logging`."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, _MARK, False):
            logger.removeHandler(handler)
            handler.close()


def setup_logging(level: str | int = "INFO") -> logging.Logger:
    """Configure the `leadgen` logger: logs/leadgen.log (1 MB x 5 files) plus stderr."""
    shutdown_logging()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        paths.logs_dir() / "leadgen.log",
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    stderr_handler = logging.StreamHandler(sys.stderr)
    for handler in (file_handler, stderr_handler):
        handler.setFormatter(formatter)
        setattr(handler, _MARK, True)
        logger.addHandler(handler)
    return logger
