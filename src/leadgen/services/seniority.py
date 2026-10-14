"""Seniority of a job title (whole-word rules, PLAN section 6) and the owner/top-leader check.

Levels: individual < manager < director < vp < c_suite. The role part of a title (before
` at ` or `@`) is read; the highest level found wins. A title with no rule is `individual`.
"""

from __future__ import annotations

import re

from leadgen.services.designations import role_part

LEVELS: tuple[str, ...] = ("individual", "manager", "director", "vp", "c_suite")
_RANK = {name: i for i, name in enumerate(LEVELS)}

# Words that look like a rank but are not ("Product Owner" is not a company owner).
_FALSE_FRIENDS = re.compile(
    r"\b(product|process|data|budget|service|system|solution|risk|case|content)\s+owners?\b"
    r"|\blead\s+(generation|gen|qualification|nurturing|scoring)\b"
    r"|\b(assistant|pa|secretary)\s+to\b.*"
    r"|\bhead[\s-]+(?:hunt\w*|office)\b"  # "Head-Hunter", "Head Office Administrator"
)
# "&", ",", "/" and ";" separate two titles ("VP & General Manager"), so a qualifier such as "vp"
# before the separator does not qualify the title after it. They are read as the word "and".
_SEPARATORS = re.compile(r"[&,/;]")
_VICE_PRESIDENT = re.compile(r"\bvice[\s-]+president\b")
# "Head of X", "Head, X", "Head - X", "Head | X" and "Head: X" are one marker; so is a trailing
# "X Head" (see _words).
_HEAD_OF = re.compile(r"\bhead\s+of\b|\bhead\s*[,\-\u2013\u2014|:]")
# "Country Head" is a top leader wherever it appears ("Country Head of HR" is only a vp).
_COUNTRY_HEAD = re.compile(r"\bcountry\s+head\b(?!\s+of\b)")

_C_SUITE_WORDS = frozenset(
    "ceo coo cfo cto chro cmo cio ciso cpo chief founder cofounder owner proprietor chairman "
    "chairwoman chairperson president".split()
)
_VP_WORDS = frozenset({"vp", "avp", "svp", "evp", "headof", "countryhead", "md"})
_C_SUITE_PHRASES = (("managing", "partner"), ("senior", "partner"), ("equity", "partner"))
_VP_PHRASES = (("managing", "director"),)
_DIRECTOR_WORDS = frozenset({"director"})
_MANAGER_WORDS = frozenset({"manager", "lead"})
_MANAGER_PHRASES = (("hr", "business", "partner"),)
_JUNIOR_WORDS = frozenset({"junior", "jr", "intern", "trainee"})

# Heads of a company (PLAN section 11.7). "Managing Director", "MD", "Principal" and
# "Executive Director" are ranks at large organisations, so they are not on this list.
_TOP_WORDS = frozenset(
    "ceo founder cofounder owner proprietor chairman chairwoman chairperson president "
    "countryhead".split()
)
_TOP_PHRASES = (
    ("chief", "executive"),
    ("managing", "partner"),
    ("country", "manager"),
    ("general", "manager"),
)
_NOT_TOP_BEFORE = frozenset({"assistant", "deputy", "associate", "vice", "vp"})


def _words(title: str) -> list[str]:
    t = _FALSE_FRIENDS.sub(" ", (role_part(title) or title or "").lower())
    t = _VICE_PRESIDENT.sub("vp", t)
    t = _COUNTRY_HEAD.sub(" countryhead ", t)
    t = _HEAD_OF.sub(" headof ", t)
    t = _SEPARATORS.sub(" and ", t)
    words = re.sub(r"[^a-z0-9]+", " ", t).split()
    if words and words[-1] == "head":  # "HR Head", "Regional Head"
        words[-1] = "headof"
    return words


def _phrase_at(words: list[str], phrases: tuple[tuple[str, ...], ...]) -> list[int]:
    hits = []
    for phrase in phrases:
        n = len(phrase)
        for i in range(len(words) - n + 1):
            if tuple(words[i : i + n]) == phrase:
                hits.append(i)
    return hits


def _levels(words: list[str]) -> list[tuple[int, str]]:
    """Every rank marker found, as (position, level)."""
    found: list[tuple[int, str]] = []
    for i, w in enumerate(words):
        if w in _C_SUITE_WORDS:
            found.append((i, "c_suite"))
        elif w in _VP_WORDS:
            found.append((i, "vp"))
        elif w in _DIRECTOR_WORDS:
            found.append((i, "director"))
        elif w in _MANAGER_WORDS:
            found.append((i, "manager"))
    for phrases, level in (
        (_C_SUITE_PHRASES, "c_suite"),
        (_VP_PHRASES, "vp"),
        (_MANAGER_PHRASES, "manager"),
    ):
        found.extend((i, level) for i in _phrase_at(words, phrases))
    return found


def seniority(title: str) -> str:
    """Seniority level of a job title: individual, manager, director, vp or c_suite.

    The highest rank word wins ("Managing Director" is vp, "Group CEO and Managing Director"
    is c_suite). "Junior", "Intern" and "Trainee" before the first rank word make the title
    individual ("Junior Manager"). Words that are not ranks (Associate, Specialist,
    Coordinator, Executive, Officer, Partner) never raise a title above individual.
    """
    words = _words(title)
    found = _levels(words)
    if not found:
        return "individual"
    first = min(pos for pos, _ in found)
    if any(w in _JUNIOR_WORDS for w in words[:first]):
        return "individual"
    return max((level for _, level in found), key=_RANK.__getitem__)


def meets(title: str, minimum: str) -> bool:
    """True if the title's seniority is at least `minimum`. Raises ValueError for a bad level."""
    if minimum not in _RANK:
        raise ValueError(f"unknown seniority level '{minimum}' (use one of: {', '.join(LEVELS)})")
    return _RANK[seniority(title)] >= _RANK[minimum]


def is_top_leader(title: str) -> bool:
    """True for a head-of-company term: CEO, founder, owner, chairman, managing partner,
    country manager/head, general manager, president (not vice), proprietor, chief executive.

    "Assistant"/"Deputy"/"Associate" versions ("Deputy General Manager") do not count.
    """
    words = _words(title)
    for i, w in enumerate(words):
        if w in _TOP_WORDS and not _qualified(words, i):
            return True
    for i in _phrase_at(words, _TOP_PHRASES):
        if not _qualified(words, i):
            return True
    return False


def _qualified(words: list[str], i: int) -> bool:
    return i > 0 and words[i - 1] in _NOT_TOP_BEFORE
