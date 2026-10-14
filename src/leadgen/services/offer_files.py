"""File sources for offer intake: PDF, DOCX, TXT and MD to plain text.

Highlighted DOCX text is written as `[HIGHLIGHT: ...]` so the AI (and the "to confirm" detector)
can see what the author marked as a placeholder.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from leadgen.core.text import normalize_whitespace

logger = logging.getLogger("leadgen.offer_files")

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 300
SUPPORTED_SUFFIXES = (".pdf", ".docx", ".txt", ".md")

_BLANKS_RE = re.compile(r"\n{3,}")


class FileSourceError(Exception):
    """A file could not be read. `code` is stable text for the AI; `message` is plain English."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def tidy(text: str) -> str:
    """Normalise newlines, drop NUL/BOM and trailing spaces, and collapse long blank runs."""
    text = text.replace("\x00", "").replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANKS_RE.sub("\n\n", text).strip()


def read_file_text(path: str | Path) -> str:
    """Return the text of a `.pdf`, `.docx`, `.txt` or `.md` file (at most 20 MB).

    Raises `FileSourceError` with a plain message for a missing, too large, unsupported,
    unreadable, password-protected or text-less file.
    """
    file = Path(path).expanduser()
    suffix = file.suffix.lower()
    if not file.is_file():
        raise FileSourceError("file_not_found", "That file does not exist.")
    if suffix == ".doc":
        raise FileSourceError(
            "unsupported_type", "Old .doc files are not supported. Save it as .docx and try again."
        )
    if suffix not in SUPPORTED_SUFFIXES:
        raise FileSourceError(
            "unsupported_type",
            f"Files of type '{suffix or 'none'}' are not supported. "
            "Use .pdf, .docx, .txt or .md, or paste the text.",
        )
    if file.stat().st_size > MAX_FILE_BYTES:
        raise FileSourceError("file_too_large", "That file is larger than 20 MB.")
    if suffix == ".pdf":
        text = _read_pdf(file)
    elif suffix == ".docx":
        text = _read_docx(file)
    else:
        text = _read_plain(file)
    text = tidy(text)
    if not text:
        raise FileSourceError(
            "no_text",
            "No readable text was found in that file (a scanned PDF has no text layer).",
        )
    return text


def _read_plain(file: Path) -> str:
    data = file.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _read_pdf(file: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(file)
        if reader.is_encrypted and not reader.decrypt(""):
            raise FileSourceError("password_protected", "That PDF is password protected.")
        parts: list[str] = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # one bad page must not lose the rest
                logger.debug("pdf page could not be read")
    except FileSourceError:
        raise
    except Exception:
        raise FileSourceError("unreadable", "That PDF could not be read.") from None
    return "\n\n".join(parts)


def _read_docx(file: Path) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(str(file))
        blocks: list[str] = []
        for item in document.iter_inner_content():
            if isinstance(item, Paragraph):
                blocks.append(_paragraph_text(item, styled=True))
            elif isinstance(item, Table):
                blocks.append(_table_text(item))
    except Exception:
        raise FileSourceError("unreadable", "That DOCX file could not be read.") from None
    return "\n".join(blocks)


def _is_highlighted(run) -> bool:
    """True if the run has a highlight colour.

    `<w:highlight w:val="none"/>` is valid ("no highlight") but python-docx raises ValueError
    for it, so a ValueError counts as not highlighted.
    """
    try:
        return run.font.highlight_color is not None
    except ValueError:
        return False


def _paragraph_text(paragraph, styled: bool) -> str:
    """Text of one paragraph; highlighted runs are wrapped in `[HIGHLIGHT: ...]`."""
    segments: list[tuple[str, bool]] = []
    for part in paragraph.iter_inner_content():
        runs = part.runs if hasattr(part, "runs") else [part]
        for run in runs:
            text = run.text
            if not text:
                continue
            highlighted = _is_highlighted(run)
            if segments and segments[-1][1] == highlighted:
                segments[-1] = (segments[-1][0] + text, highlighted)
            else:
                segments.append((text, highlighted))
    out = ""
    for text, highlighted in segments:
        out += f"[HIGHLIGHT: {text.strip()}]" if highlighted and text.strip() else text
    out = normalize_whitespace(out)
    if not out or not styled:
        return out
    style = ""
    try:
        style = (paragraph.style.name or "") if paragraph.style is not None else ""
    except Exception:
        pass
    if style == "Title" or style.startswith("Heading"):
        return f"## {out}"
    if style.startswith("List"):
        return f"- {out}"
    return out


def _table_text(table) -> str:
    """One line per row, cells joined by ` | `; merged cells are written once."""
    lines: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            marker = getattr(cell, "_tc", None)
            if previous is not None and marker is previous:
                continue  # horizontally merged cell appears once per grid column
            previous = marker
            text = " ".join(
                part for part in (_paragraph_text(p, styled=False) for p in cell.paragraphs) if part
            )
            cells.append(text)
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)
