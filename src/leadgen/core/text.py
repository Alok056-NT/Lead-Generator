"""Small text utilities: slugs, whitespace, HTML stripping, truncation and similarity."""

from __future__ import annotations

import difflib
import re
import unicodedata

from bs4 import BeautifulSoup

_WS_RE = re.compile(r"\s+")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Letters that Unicode decomposition (NFKD) does not turn into plain ASCII.
_SPECIAL = str.maketrans(
    {"ß": "ss", "æ": "ae", "Æ": "ae", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d", "ł": "l", "Ł": "l"}
)


def normalize_whitespace(s: str) -> str:
    """Collapse every run of whitespace (including newlines) to one space and trim."""
    return _WS_RE.sub(" ", s).strip()


def slugify(s: str) -> str:
    """Lowercase ASCII slug with hyphens, e.g. "Zoë's Café" -> "zoe-s-cafe". May return ""."""
    folded = unicodedata.normalize("NFKD", s.translate(_SPECIAL))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    return _NON_SLUG_RE.sub("-", ascii_only).strip("-")


def strip_html(s: str) -> str:
    """Return the visible text of an HTML fragment (scripts and styles dropped)."""
    soup = BeautifulSoup(s, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return normalize_whitespace(soup.get_text(" "))


def truncate(s: str, n: int) -> str:
    """Cut `s` to at most `n` characters, ending with an ellipsis when something was removed."""
    if n < 0:
        raise ValueError("n must be >= 0")
    if len(s) <= n:
        return s
    if n == 0:
        return ""
    return s[: n - 1].rstrip() + "…"


def similarity(a: str, b: str) -> float:
    """Similarity from 0.0 to 1.0 (difflib ratio on case- and whitespace-normalised text)."""
    na = normalize_whitespace(a).casefold()
    nb = normalize_whitespace(b).casefold()
    return difflib.SequenceMatcher(None, na, nb).ratio()
