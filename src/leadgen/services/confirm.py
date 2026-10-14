# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/collectors/accounts.py
"""Confirmation engine: turn one Google result into a `Candidate` (PLAN section 11, pure functions).

A person is `confirmed` only when the search result itself proves every rule; otherwise the
candidate carries `reasons` (strings such as "L04:company mentioned but not confirmed as
current employer", code before the first colon) and goes to review. No I/O happens here.

Codes follow the PLAN section 14 lead checks: L03 excluded/competitor company, L04 current
employer not confirmed, L05 country, L06 designation, L07 seniority, L08 former role, L09
headline names a different employer, L10 owner group but rank-only title, L11 name is not a
first and last name, L12 headline garbled/truncated, L13 weak-fit function, L14 junior
keyword, L15 job-seeking hint, L16 suppressed.

Name heuristic and its limits (`parse_name`): credentials are stripped after a comma or in
brackets (and trailing unambiguous ones such as "MBA"); a single bracketed capitalised word is
a nickname and becomes the greeting; an initial-only first name uses the last name; ALL CAPS
is title-cased; East Asian name order is guessed from two built-in word lists (about 170
romanised Chinese, Korean and Vietnamese surnames, about 330 Western given names), see
`services/name_data.py`. The lists are not exhaustive and some words are in more than one
role, so the guess is wrong for names such as "Lee Min Ho" (both ends are surnames) and for
two-word names with a surname first and a non-Western given name ("Tan Wei" reads as "Tan").
A wrong greeting is caught by the message QA, and the AI reviews every message.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from leadgen.core import countries as countries_mod
from leadgen.providers.search import SearchResult
from leadgen.services import seniority as seniority_mod
from leadgen.services.designation_templates import TEMPLATE_DATA
from leadgen.services.designations import (
    DESIGNATION_OTHER,
    DesignationGroup,
    classify,
    role_part,
)
from leadgen.services.name_data import COMMON_SURNAMES, CREDENTIALS, WESTERN_GIVEN_NAMES

OWNERS_LABEL = str(TEMPLATE_DATA["owners"]["label"])

_PROFILE_RE = re.compile(r"^https://(?:[a-z]{2,3}\.|www\.)?linkedin\.com/in/[^/?#]+/?$")
_TRAILING_NOISE_RE = re.compile(r"\s*(?:[|·\-–—]\s*LinkedIn.*|\.\.\.|…)\s*$", re.I)
_TITLE_SPLIT_RE = re.compile(r"\s+[-–—]\s+")
_SEPARATORS_RE = re.compile(r"[·•�]")
_DASH_SEPARATOR_RE = re.compile(r"\s+[-–—]\s+")
_FORMER_RE = re.compile(
    r"(?<![A-Za-z])(?:former(?:ly)?|retired|previously)(?![A-Za-z])|(?<![A-Za-z])ex-", re.I
)
# Words that make an `at <alias>` match in the snippet a past job when they come earlier in the
# same segment or sentence ("Previously Training Manager at X", "Formerly HR lead at X").
_FORMER_BEFORE_RE = re.compile(
    r"(?<![A-Za-z])(?:former(?:ly)?|retired|previously|past|until|earlier)(?![A-Za-z])"
    r"|(?<![A-Za-z])ex-",
    re.I,
)
_EXPERIENCE_VALUE_RE = re.compile(r"(?<![A-Za-z])experience\s*[:·]\s*([^·]*)", re.I)
_JOB_SEEKER_RE = re.compile(
    r"looking\s+for|open\s+to\s+work|#?opentowork|seeking|job\s+seeker", re.I
)
_GARBLED_RE = re.compile(r"mutual\s+connection|can\s+introduce\s+you|(?:\s|^)(?:at|@)\s*$", re.I)
_AT_EMPLOYER_RE = re.compile(r"(?:(?<![A-Za-z])at\s+|@\s*)([^|,·]+)", re.I)
_DEPUTY_CEO_RE = re.compile(r"\bdeputy\s+(?:ceo|chief\s+executive(?:\s+officer)?)\b", re.I)
_SEGMENT_RE = re.compile(r"\s+[-–—|·]\s+")

_JUNIOR_HARD = re.compile(
    r"(?<![A-Za-z])(?:junior|jr|intern|internship|trainee|apprentice)(?![A-Za-z])", re.I
)
# These only mark a junior title when no rank word is present ("Associate Director" and
# "Executive Director" are fine; "Senior Executive, Training" is not).
_JUNIOR_SOFT = re.compile(
    r"(?<![A-Za-z])(?:associate|executive|specialist|coordinator)(?![A-Za-z])", re.I
)

_WEAK_FIT_RE = re.compile(
    r"(?<![A-Za-z])(?:it|information\s+technology|technology|tech|digital|data|finance|financial"
    r"|treasury|sales|commercial|investments?)(?![A-Za-z])",
    re.I,
)
_SAFE_FIT_RE = re.compile(
    r"(?<![A-Za-z])(?:safety|ehs|hse|wsh|health|facilit[a-z]*)(?![A-Za-z])", re.I
)
_NOT_A_HEADLINE_RE = re.compile(r"(?:experience|education|location)\b|\d+\+?\s+connections", re.I)
_OPERATIONS_RE = re.compile(r"(?<![A-Za-z])operations?(?![A-Za-z])", re.I)

_HONORIFICS = frozenset("dr prof professor mr mrs ms miss mdm madam ir ar er sir ts".split())
# Credentials that are also ordinary words or surnames are only stripped after a comma or in
# brackets; the rest may also trail the name without a comma ("Jerome Tan MBA").
_SAFE_TRAILING = frozenset(
    c
    for c in CREDENTIALS
    if len(c) >= 3 and c not in COMMON_SURNAMES and c not in WESTERN_GIVEN_NAMES
)
_INITIAL_RE = re.compile(r"[A-Za-z]|(?:[A-Za-z]\.){1,3}[A-Za-z]?")


# --- context ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyCtx:
    """The company a search was for. `countries` narrows the campaign's countries (optional)."""

    name: str
    aliases: tuple[str, ...] = ()
    domain: str = ""
    status: str = "approved"
    countries: tuple[str, ...] = ()
    id: int | None = None

    def all_aliases(self) -> tuple[str, ...]:
        """The name first, then the aliases, without repeats."""
        seen: dict[str, None] = {}
        for alias in (self.name, *self.aliases):
            alias = " ".join((alias or "").split())
            if alias:
                seen.setdefault(alias, None)
        return tuple(seen)


@dataclass(frozen=True)
class CampaignCtx:
    """The campaign rules a result is checked against."""

    countries: tuple[str, ...]
    groups: tuple[DesignationGroup, ...]
    seniority_min: str = "manager"
    exclusions: Mapping[str, Sequence[str]] = field(default_factory=dict)
    suppressed: frozenset[str] = frozenset()  # url keys (see `url_key`) and normalised names

    @classmethod
    def from_campaign(cls, camp: Mapping[str, Any], suppressed: Iterable[str] = ()) -> CampaignCtx:
        """Build from `campaign.get_campaign()` output plus linkedin suppression values."""
        groups = tuple(DesignationGroup.from_dict(g) for g in camp.get("designations") or [])
        exclusions = camp.get("exclusions") or {}
        blocked: set[str] = set()
        for value in (*suppressed, *(exclusions.get("people") or [])):
            if "linkedin.com/" in str(value).lower():
                blocked.add(url_key(str(value)))
            else:
                blocked.add(name_key(str(value)))
        blocked.discard("")
        return cls(
            countries=tuple(camp.get("countries") or ()),
            groups=groups,
            seniority_min=camp.get("seniority_min") or "manager",
            exclusions=exclusions,
            suppressed=frozenset(blocked),
        )


@dataclass
class Candidate:
    """One person found in a search result, with the reasons (if any) they are not confirmed."""

    first_name: str
    last_name: str
    greeting_name: str
    raw_name: str
    headline: str
    designation: str
    seniority: str
    linkedin_url: str
    evidence_title: str
    evidence_snippet: str
    country: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        """True when no rule failed."""
        return not self.reasons

    @property
    def reason_codes(self) -> list[str]:
        """The code (text before the first colon) of every reason."""
        return [r.split(":", 1)[0] for r in self.reasons]


# --- urls -------------------------------------------------------------------------------------


def canonical_url(link: str) -> str:
    """Lower-case `https://<host>/in/<slug>` without query, fragment or trailing slash, or ""
    when the link is not a personal LinkedIn profile (PLAN 11.1)."""
    try:
        parts = urlsplit((link or "").strip())
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    candidate = f"{parts.scheme.lower()}://{host}{parts.path}".rstrip("/").lower()
    return candidate if _PROFILE_RE.match(candidate) else ""


def url_key(link: str) -> str:
    """The profile slug (`in/<slug>`, lower case) that identifies a person whatever the
    country subdomain; "" when the link is not a personal profile."""
    canon = canonical_url(link)
    return canon.split("linkedin.com/", 1)[1] if canon else ""


def name_key(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", html.unescape(name or "").casefold()).split())


# --- text helpers -----------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Unescape HTML, unify quotes, treat middle dots, bullets, U+FFFD and ` - ` as separators."""
    t = html.unescape(text or "").replace(" ", " ").replace("’", "'").replace("‘", "'")
    t = _SEPARATORS_RE.sub(" · ", t)
    t = _DASH_SEPARATOR_RE.sub(" · ", t)
    return " ".join(t.split())


def _clean(s: str) -> str:
    return _TRAILING_NOISE_RE.sub("", (s or "").strip()).strip(" -–|.")


def _is_short_caps(alias: str) -> bool:
    return len(alias) <= 5 and alias.isupper()


def _alias_source(alias: str) -> str:
    """Regex source for an alias: whitespace flexible, `&` and `and` interchangeable."""
    a = " ".join(html.unescape(alias).replace("’", "'").split())
    if not _is_short_caps(alias):
        a = re.sub(r"\s*&\s*", " and ", a)
    return r"\s+".join(re.escape(w) for w in a.split())


@lru_cache(maxsize=4096)
def _compiled(alias: str, template: str) -> re.Pattern[str]:
    """`template` with `{A}` replaced by the alias (boundaries added); short all-caps aliases
    are case-sensitive, the literal words in the template stay case-insensitive."""
    flags = 0 if _is_short_caps(alias) else re.IGNORECASE
    source = r"(?<![A-Za-z0-9])" + _alias_source(alias) + r"(?![A-Za-z0-9])"
    return re.compile(template.replace("{A}", source), flags)


def _prep(text: str, alias: str) -> str:
    """Text as the alias regex sees it (`&` read as `and` unless the alias is short all-caps)."""
    return text if _is_short_caps(alias) else re.sub(r"\s*&\s*", " and ", text)


def _alias_in(alias: str, text: str) -> bool:
    return _compiled(alias, "{A}").search(_prep(text, alias)) is not None


def _any_alias_in(aliases: Iterable[str], text: str) -> bool:
    return any(_alias_in(a, text) for a in aliases)


def _mask_aliases(text: str, aliases: Iterable[str]) -> str:
    for alias in aliases:
        text = _compiled(alias, "{A}").sub(" ", _prep(text, alias))
    return text


# --- names ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NameParts:
    """A parsed person name: `first`/`last` (given name(s) and surname), the `greeting`, and the
    cleaned `display` name. `full` is True when both a first and a last name were found."""

    first: str
    last: str
    greeting: str
    display: str

    @property
    def full(self) -> bool:
        return bool(self.first and self.last) and len(self.last.replace(".", "")) > 1


def _title_token(token: str) -> str:
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), token.lower())


def _is_nickname(word: str) -> bool:
    word = word.strip().strip("\"'“”")
    return bool(
        re.fullmatch(r"[A-Z][A-Za-z]{1,9}", word)
        and not word.isupper()
        and word.lower() not in CREDENTIALS
    )


def parse_name(raw: str) -> NameParts:
    """Split a profile name into first/last/greeting (see the module docstring for the limits)."""
    text = html.unescape(raw or "").replace("’", "'")
    nickname = ""
    for m in re.finditer(r"[(\[]([^)\]]*)[)\]]", text):
        if _is_nickname(m.group(1)):
            nickname = m.group(1).strip().strip("\"'“”")
            break
    text = re.sub(r"[(\[][^)\]]*[)\]]", " ", text)
    text = text.split(",")[0]
    text = re.sub(r"[^\w\s'.\-]|\d|_", " ", text)
    tokens = text.split()
    while len(tokens) > 2 and tokens[0].lower().strip(".") in _HONORIFICS:
        tokens = tokens[1:]
    if len(tokens) > 1 and tokens[0].lower().strip(".") in _HONORIFICS:
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1].lower().strip(".") in _SAFE_TRAILING:
        tokens = tokens[:-1]
    pairs = [(t.strip("."), bool(_INITIAL_RE.fullmatch(t))) for t in tokens]
    pairs = [(t, i) for t, i in pairs if t]
    if not pairs:
        return NameParts("", "", nickname, "")
    tokens = [_title_token(t) if len(t) > 1 and t.isupper() else t for t, _ in pairs]
    initials = [i for _, i in pairs]
    display = " ".join(tokens)

    first, last = tokens[0], " ".join(tokens[1:])
    greeting = tokens[0]
    if len(tokens) > 1 and initials[0] and not initials[-1]:
        greeting = tokens[-1]  # "R Kumarasamy" -> "Kumarasamy"
    elif not any(initials):
        low = [t.lower() for t in tokens]
        given: list[str] = []
        surname = ""
        if len(tokens) == 3:
            if low[2] in COMMON_SURNAMES and low[0] not in WESTERN_GIVEN_NAMES:
                given, surname = tokens[:2], tokens[2]
            elif low[0] in COMMON_SURNAMES and low[2] not in COMMON_SURNAMES:
                given, surname = tokens[1:], tokens[0]
        elif len(tokens) == 2 and low[0] in COMMON_SURNAMES and low[1] in WESTERN_GIVEN_NAMES:
            given, surname = [tokens[1]], tokens[0]
        if given:
            first, last, greeting = " ".join(given), surname, " ".join(given)
    if nickname:
        greeting = nickname
    return NameParts(first, last, greeting, display)


# --- the rules --------------------------------------------------------------------------------


def _split_title(title: str) -> tuple[str, str, bool]:
    """(name, headline, truncated) from a result title; Google cuts long titles with an ellipsis."""
    t = html.unescape(title or "").replace("’", "'")
    truncated = bool(re.search(r"(?:…|\.\.\.)\s*(?:[|·\-–—]\s*LinkedIn.*)?$", t.strip(), re.I))
    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(_TRAILING_NOISE_RE.sub("", t.strip()), 1)]
    name = _clean(parts[0]) if parts else ""
    headline = _clean(parts[1]) if len(parts) > 1 else ""
    return name, headline, truncated


def _snippet_layouts(name: str, snippet: str) -> tuple[str, str, str]:
    """(layout-A headline, text after it, first segment). Layout A is `Name. Headline. Rest`;
    the first segment is what comes before the first separator (`Headline at Company · ...`)."""
    lead, after = "", ""
    for candidate in dict.fromkeys((name, name.split(",")[0].strip())):
        if candidate and snippet.casefold().startswith(candidate.casefold() + "."):
            rest = snippet[len(candidate) + 1 :].strip()
            ends = _sentence_ends(rest, company_suffix_ends=True)
            lead, after = (rest[: ends[0] - 2], rest[ends[0] :]) if ends else (rest, "")
            break
    segment = snippet.split(" · ")[0].strip()
    return _clean(lead), after.strip(), segment


def _better_headline(headline: str, truncated: bool, lead: str, segment: str) -> str:
    """The title headline, or a longer one from the snippet when Google truncated the title."""
    if headline and not truncated:
        return headline
    stem = headline.rstrip(" .…").casefold()
    options = [c for c in (lead, _clean(segment)) if c]
    if headline:
        for option in options:
            if option.casefold().startswith(stem) and len(option) > len(headline):
                return option
        return headline
    if lead:
        return lead
    segment = _clean(segment)
    if segment and not _NOT_A_HEADLINE_RE.match(segment):
        return segment
    return ""


_EXPERIENCE = r"(?i:Experience)\s*[:·]\s*{A}"
_AT_ALIAS = r"(?:(?<![A-Za-z])(?i:at)\s+|@\s*){A}"
_PRESENT = r"{A}\s*[.·,]?\s*(?:[A-Za-z]{3,9}\.?\s+)?\d{4}\s*[·\-–]?\s*(?i:Present)(?![A-Za-z])"


_COMPANY_SUFFIXES = frozenset("co corp ltd inc pte bhd sdn intl".split())
_ABBREVIATIONS = _COMPANY_SUFFIXES | frozenset(
    "sr snr jr asst dy dep mgr exec assoc dir gen st dept "
    "eng admin coord supt supv rep prof dr mr mrs ms".split()
)
_DOT_SPACE_RE = re.compile(r"([A-Za-z]*)\. (?=([A-Za-z]*))")


def _sentence_ends(text: str, company_suffix_ends: bool = False) -> list[int]:
    """Index just after each `. ` in `text` that ends a sentence. A dot after a common title or
    company abbreviation (`Sr.`, `Asst.`, `Mgr.`, `Corp.`) or a single-letter initial does not.
    With `company_suffix_ends` (the layout-A headline split) a company-suffix dot does end the
    sentence, unless the next word is also a suffix (`Pte. Ltd.`, `Sdn. Bhd.`, `Co. Ltd.`)."""
    ends: list[int] = []
    for m in _DOT_SPACE_RE.finditer(text):
        word = m.group(1).lower()
        if len(word) == 1:
            continue
        if word in _COMPANY_SUFFIXES and company_suffix_ends:
            if m.group(2).lower() in _COMPANY_SUFFIXES:
                continue
        elif word in _ABBREVIATIONS:
            continue
        ends.append(m.end())
    return ends


def _at_alias_is_current(alias: str, text: str) -> bool:
    """True when some `at <alias>` / `@ <alias>` match is not preceded, in its own segment or
    sentence, by a former-role word (PLAN 11.5: when unsure, review)."""
    for m in _compiled(alias, _AT_ALIAS).finditer(text):
        before = text[: m.start()]
        ends = _sentence_ends(before)
        start = max(before.rfind(" · ") + 1, ends[-1] if ends else 0)
        if not _FORMER_BEFORE_RE.search(before[start:]):
            return True
    return False


def _experience_names_none(aliases: Sequence[str], snippet: str) -> bool:
    """True when the snippet has an `Experience:` field whose value names none of the aliases."""
    values = [m.group(1) for m in _EXPERIENCE_VALUE_RE.finditer(snippet)]
    return bool(values) and not any(_any_alias_in(aliases, v) for v in values)


def _current_in_snippet(alias: str, snippet: str, after: str, loose_ok: bool = True) -> bool:
    """Does the snippet show `alias` as the current employer? Google's shapes: `Experience ·
    X`, `at X`, `@ X`, `Name. Headline. X School.` (X starts the text after the headline) and
    `X. Jul 2011 - Present`. A loose `at X` / `@ X` is no evidence when a former-role word
    comes before it in its segment, or when `loose_ok` is False (the Experience field names
    another company)."""
    text = _prep(snippet, alias)
    return (
        _compiled(alias, _EXPERIENCE).search(text) is not None
        or (loose_ok and _at_alias_is_current(alias, text))
        or _compiled(alias, "^{A}").match(_prep(after, alias)) is not None
        or _compiled(alias, _PRESENT).search(text) is not None
    )


def _current_employer(
    aliases: Sequence[str], headline: str, snippet: str, after: str, truncated: bool
) -> tuple[bool, str]:
    """(confirmed, failure reason). Implements PLAN 11.3 and the L09 'different employer' rule."""
    head_hit = _any_alias_in(aliases, headline)
    loose_ok = not _experience_names_none(aliases, snippet)
    snippet_hit = any(_current_in_snippet(a, snippet, after, loose_ok) for a in aliases)
    at_match = _AT_EMPLOYER_RE.search(headline)
    if at_match:
        named = at_match.group(1).strip()
        employer = re.sub(r"^the\s+", "", named, flags=re.I).strip(" .…").lower()
        same = _any_alias_in(aliases, at_match.group(1))
        if not same and truncated and len(employer) >= 4:
            same = any(a.lower().startswith(employer) for a in aliases)
        if employer and not same:
            return False, f"L09:headline names a different employer ({named[:40]})"
        if same:
            head_hit = True
    if head_hit or snippet_hit:
        return True, ""
    if _any_alias_in(aliases, snippet + " " + headline):
        return False, "L04:company mentioned but not confirmed as current employer"
    return False, "L04:company not found in profile"


def _strip_company_segments(headline: str, aliases: Sequence[str]) -> str:
    """The role part of a headline: drops a trailing ` - Company` / ` | Company` segment."""
    segments = _SEGMENT_RE.split(headline)
    kept: list[str] = []
    for i, seg in enumerate(segments):
        if i > 0 and _any_alias_in(aliases, seg):
            break
        kept.append(seg)
    return " - ".join(kept)


def _is_ops_group(group: DesignationGroup) -> bool:
    return any(k.lower().startswith("operation") for k in group.keywords)


def _is_owners_group(group: DesignationGroup) -> bool:
    return group.label.lower() == OWNERS_LABEL.lower() or "managing director" in group.keywords


def _weak_fit(role: str) -> bool:
    """PLAN 11.6: an IT/finance/sales/... operations title is not a safety/operations role."""
    return bool(
        _OPERATIONS_RE.search(role) and _WEAK_FIT_RE.search(role) and not _SAFE_FIT_RE.search(role)
    )


def _designation(role: str, groups: Sequence[DesignationGroup]) -> tuple[str, bool]:
    """(designation label, weak_fit). A weak-fit operations title does not count for an
    Operations group; it may still match another group of the campaign."""
    label = classify(role, groups)
    ops = [g for g in groups if _is_ops_group(g)]
    in_ops = label == DESIGNATION_OTHER or label in {g.label for g in ops}
    if ops and in_ops and _weak_fit(role_part(role)):
        others = [g for g in groups if not _is_ops_group(g)]
        other = classify(role, others) if others else DESIGNATION_OTHER
        return (other, False) if other not in ("", DESIGNATION_OTHER) else (DESIGNATION_OTHER, True)
    return label, False


def _excluded(company: CompanyCtx, campaign: CampaignCtx) -> str:
    """Why this company must not be contacted (L03), or ""."""
    if company.status != "approved":
        return f"company '{company.name}' is {company.status}, not approved"
    exclusions = campaign.exclusions
    listed = {
        name_key(entry): label
        for label, key in (("excluded company", "companies"), ("competitor", "competitors"))
        for entry in exclusions.get(key, ())
    }
    for alias in company.all_aliases():
        label = listed.get(name_key(alias))
        if label:
            return f"{label} '{alias}'"
    blocked = [str(d).lower().removeprefix("www.") for d in exclusions.get("domains", ())]
    for entry in (*exclusions.get("companies", ()), *exclusions.get("competitors", ())):
        if "." in entry and " " not in entry:
            blocked.append(entry.lower().removeprefix("www."))
    domain = (company.domain or "").lower().removeprefix("www.")
    if domain and any(domain == b or domain.endswith("." + b) for b in blocked):
        return f"excluded domain '{domain}'"
    return ""


def _country_names(company: CompanyCtx, campaign: CampaignCtx) -> list[countries_mod.Country]:
    found = []
    for value in company.countries or campaign.countries:
        try:
            found.append(countries_mod.get(value))
        except countries_mod.UnknownCountryError:
            continue
    return found


def parse_result(
    result: SearchResult, company: CompanyCtx, campaign: CampaignCtx
) -> Candidate | None:
    """Turn one search result into a `Candidate`, recording why it is not confirmed.

    Returns None when the link is not a personal LinkedIn profile (PLAN 11.1).
    """
    url = canonical_url(result.link)
    if not url:
        return None
    aliases = company.all_aliases()
    name_raw, title_headline, truncated = _split_title(result.title)
    if title_headline and any(
        _compiled(a, "^{A}$").match(_prep(title_headline, a)) for a in aliases
    ):
        title_headline = ""  # "Name - Company | LinkedIn" carries no role: use the snippet
    snippet = _normalize(result.snippet)
    lead, after, segment = _snippet_layouts(name_raw, snippet)
    headline = _better_headline(title_headline, truncated, lead, segment)
    parts = parse_name(name_raw)
    reasons: list[str] = []

    # 2. name
    if not parts.full:
        reasons.append("L11:name is not a first and last name")
    # 3. current employer
    ok, why = _current_employer(aliases, headline, snippet, after, truncated)
    if not ok:
        reasons.append(why)
    # 4. country
    targets = _country_names(company, campaign)
    shown = ""
    if targets:
        evidence = _mask_aliases(snippet, aliases)
        for country in targets:
            if countries_mod.matches_location(country, url, evidence):
                shown = country.name
                break
        if not shown:
            names = " or ".join(c.name for c in targets)
            reasons.append(f"L05:not shown as based in {names}")
    # 5. former role
    if _FORMER_RE.search(headline) or _FORMER_RE.search(lead or segment):
        reasons.append("L08:headline describes a former role")
    if headline and _GARBLED_RE.search(headline):
        reasons.append("L12:headline looks garbled or cut off")

    # 6. designation (weak-fit rule PLAN 11.6)
    role = _strip_company_segments(headline, aliases)
    designation, weak = _designation(role, campaign.groups)
    if designation in ("", DESIGNATION_OTHER):
        reasons.append("L06:role not in the campaign's designations")
        if weak:
            reasons.append("L13:weak-fit function for an operations group (not safety/facilities)")
    owners = any(g.label == designation and _is_owners_group(g) for g in campaign.groups)
    # 7. seniority
    level = seniority_mod.seniority(role)
    top_leader = seniority_mod.is_top_leader(_DEPUTY_CEO_RE.sub("CEO", role))
    if not (owners and top_leader):
        try:
            if not seniority_mod.meets(role, campaign.seniority_min):
                reasons.append(f"L07:below {campaign.seniority_min} level")
        except ValueError:
            reasons.append(f"L07:unknown minimum seniority {campaign.seniority_min}")
    if _JUNIOR_HARD.search(role_part(role)) or (
        level == "individual" and _JUNIOR_SOFT.search(role_part(role))
    ):
        reasons.append("L14:junior keyword in the title")
    if owners and not top_leader:
        # At a large organisation "Managing Director" or "Principal" is a rank, not the head.
        reasons.append("L10:MD/principal is a rank here, not the company head")
    # 8. excluded company, suppression
    excluded = _excluded(company, campaign)
    if excluded:
        reasons.append(f"L03:{excluded}")
    if url_key(url) in campaign.suppressed or name_key(parts.display) in campaign.suppressed:
        reasons.append("L16:on the suppression list")
    # 9. job seeker
    if _JOB_SEEKER_RE.search(_normalize(result.title) + " " + snippet):
        reasons.append("L15:job-seeking hint")

    return Candidate(
        first_name=parts.first,
        last_name=parts.last,
        greeting_name=parts.greeting,
        raw_name=parts.display,
        headline=headline,
        designation=designation,
        seniority=level,
        linkedin_url=url,
        evidence_title=result.title,
        evidence_snippet=result.snippet,
        country=shown,
        reasons=reasons,
    )
