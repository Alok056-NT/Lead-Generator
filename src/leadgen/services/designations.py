# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/export.py (classify_designation)
"""Designation (job-role group) matching: group model, templates and `classify`.

A group has a label, keywords matched on whole words in the role part of a title, and search
terms used in search queries. Order matters: the first matching group wins.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from leadgen.services.designation_templates import TEMPLATE_DATA

DESIGNATION_OTHER = "Other"

# Phrases that contain a rule word but mean something else entirely ("Machine Learning
# Engineer" is not L&D, "IT Operations" is not Operations). Removed before matching.
_FALSE_FRIENDS = re.compile(
    r"\b(machine|deep|reinforcement|statistical)\s+learning\b"
    r"|\bmodel\s+training\b|\bquality\s+of\s+life\b"
    r"|\b(it|network|cloud|security|cyber|data|it infrastructure)\s+operations\b"
)
_L_AND_D = re.compile(r"\bl\s*(?:&|and)\s*d\b")
_VICE_PRESIDENT = re.compile(r"\bvice[\s-]+president\b")
_ROLE_SPLIT = re.compile(r"\s+at\s+|\s*@\s*")


class DesignationError(ValueError):
    """A designation group or template key is not valid. The message says what to fix."""


def role_part(title: str) -> str:
    """The role of a title: the text before ` at ` or `@` (the employer comes after)."""
    return _ROLE_SPLIT.split(title or "", maxsplit=1)[0]


def title_words(title: str) -> list[str]:
    """Lower-case words of a title; punctuation splits words, but "L&D" / "L & D" / "L and D"
    are kept whole as the token "l&d", and "vice president" becomes "vp"."""
    t = _FALSE_FRIENDS.sub(" ", (title or "").lower())
    t = _VICE_PRESIDENT.sub("vp", t)
    t = _L_AND_D.sub("l&d", t)
    t = re.sub(r"[^a-z0-9&]+", " ", t)
    words: list[str] = []
    for w in t.split():
        if w == "l&d":
            words.append(w)
        else:
            words.extend(x for x in w.split("&") if x)
    return words


def _clean_str_list(name: str, values: Any) -> list[str]:
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise DesignationError(f"{name} must be a list of text values")
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise DesignationError(f"{name} must contain only text values")
        value = " ".join(value.split())
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out


@dataclass
class DesignationGroup:
    """One job-role group: `keywords` classify a title, `search_terms` go into search queries.

    Keywords are lower-case whole words or phrases; a trailing `*` means "starts with".
    """

    label: str
    keywords: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not " ".join(self.label.split()):
            raise DesignationError("a designation group needs a label")
        self.label = " ".join(self.label.split())
        self.keywords = [k.lower() for k in _clean_str_list("keywords", self.keywords)]
        self.search_terms = _clean_str_list("search_terms", self.search_terms)
        if not self.keywords:
            raise DesignationError(f"group '{self.label}' needs at least one keyword")
        if not self.search_terms:
            raise DesignationError(f"group '{self.label}' needs at least one search term")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DesignationGroup:
        """Build a group from `{"label", "keywords", "search_terms"}`; unknown keys are rejected."""
        if not isinstance(data, Mapping):
            raise DesignationError("a designation group must be an object with label, keywords")
        unknown = set(data) - {"label", "keywords", "search_terms"}
        if unknown:
            raise DesignationError(
                f"unknown designation field(s): {', '.join(sorted(map(str, unknown)))}"
            )
        return cls(
            label=data.get("label", ""),
            keywords=data.get("keywords", []),
            search_terms=data.get("search_terms", []),
        )

    def to_dict(self) -> dict[str, Any]:
        """Plain dict (as stored in `campaigns.designations_json`)."""
        return {
            "label": self.label,
            "keywords": list(self.keywords),
            "search_terms": list(self.search_terms),
        }


# --- templates ------------------------------------------------------------------------------

TEMPLATES: dict[str, DesignationGroup] = {
    key: DesignationGroup(**data) for key, data in TEMPLATE_DATA.items()
}


def list_templates() -> list[dict[str, Any]]:
    """All built-in templates as `{"key", "label", "keywords", "search_terms"}` dicts."""
    return [{"key": key, **group.to_dict()} for key, group in TEMPLATES.items()]


def resolve_groups(
    items: Sequence[str | Mapping[str, Any] | DesignationGroup],
) -> list[DesignationGroup]:
    """Turn template keys, group dicts or groups into `DesignationGroup`s (order kept).

    Raises `DesignationError` for an unknown template key or an invalid group.
    """
    groups: list[DesignationGroup] = []
    for item in items:
        if isinstance(item, DesignationGroup):
            groups.append(item)
        elif isinstance(item, str):
            key = item.strip().lower()
            if key not in TEMPLATES:
                raise DesignationError(
                    f"unknown designation template '{item}'. Valid keys: {', '.join(TEMPLATES)}"
                )
            groups.append(DesignationGroup.from_dict(TEMPLATES[key].to_dict()))
        else:
            groups.append(DesignationGroup.from_dict(item))
    return groups


# --- matching -------------------------------------------------------------------------------


@lru_cache(maxsize=2048)
def _compile(keyword: str) -> tuple[tuple[str, bool], ...]:
    """A keyword as (word, is_prefix) pairs, tokenised like title words."""
    low = _L_AND_D.sub("l&d", keyword.lower().replace("-", " "))
    parts: list[tuple[str, bool]] = []
    for raw in low.split():
        prefix = raw.endswith("*")
        raw = raw.rstrip("*")
        if raw == "l&d":
            parts.append((raw, prefix))
            continue
        pieces = [p for p in re.sub(r"[^a-z0-9&]+", " ", raw).replace("&", " ").split() if p]
        for i, piece in enumerate(pieces):
            parts.append((piece, prefix and i == len(pieces) - 1))
    return tuple(parts)


def _rule_matches(keyword: str, words: list[str]) -> bool:
    rule = _compile(keyword)
    n = len(rule)
    if n == 0:
        return False
    for i in range(len(words) - n + 1):
        if all(
            w.startswith(rw) if prefix else w == rw
            for (rw, prefix), w in zip(rule, words[i : i + n], strict=True)
        ):
            return True
    return False


def _coerce(group: DesignationGroup | Mapping[str, Any]) -> DesignationGroup:
    return group if isinstance(group, DesignationGroup) else DesignationGroup.from_dict(group)


def _role_words(title: str) -> list[str]:
    return title_words(role_part(title)) or title_words(title)


def matches(title: str, group: DesignationGroup | Mapping[str, Any]) -> bool:
    """True if the role part of `title` matches one of the group's keywords."""
    words = _role_words(title)
    return any(_rule_matches(k, words) for k in _coerce(group).keywords)


def classify(title: str, groups: Sequence[DesignationGroup | Mapping[str, Any]]) -> str:
    """Label of the first group whose keywords match the role part of `title`.

    Returns "Other" when no group matches and "" for an empty title (no letters or digits).
    Only the role part (the text before ` at ` or `@`) is used, so "CEO at Example Facility
    Services" is not Operations.
    """
    if not re.search(r"[A-Za-z0-9]", title or ""):
        return ""
    words = _role_words(title)
    for group in groups:
        group = _coerce(group)
        if any(_rule_matches(k, words) for k in group.keywords):
            return group.label
    return DESIGNATION_OTHER
