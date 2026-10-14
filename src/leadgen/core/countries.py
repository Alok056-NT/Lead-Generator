"""Country table: names, search codes, LinkedIn subdomains, cities, locale and time zone.

`get()` resolves a code, name or alias to a `Country`; `matches_location()` decides whether a
LinkedIn URL / search snippet places a person in a country (PLAN section 11, rule 4).
Matching is accent- and case-insensitive and uses whole words only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from leadgen.core.countries_data import COUNTRY_ROWS

# Letters that Unicode decomposition (NFKD) does not turn into plain ASCII.
_SPECIAL = str.maketrans(
    {"ß": "ss", "æ": "ae", "Æ": "ae", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d", "ł": "l", "Ł": "l"}
)
# "Location: <value>" up to the next middle dot, bullet, pipe, full stop or line end.
_LOCATION_RE = re.compile(r"(?<![a-z])location\s*:\s*([^·•|.\n]*)", re.IGNORECASE)


class UnknownCountryError(ValueError):
    """Raised by `get()` when a code, name or alias is not in the table."""


def _fold(s: str) -> str:
    """Lower-case, strip accents and collapse whitespace ("Kraków" -> "krakow")."""
    decomposed = unicodedata.normalize("NFKD", s.translate(_SPECIAL))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


@dataclass(frozen=True)
class Country:
    """One country: search and matching data plus the IANA zone used for quiet hours."""

    name: str
    iso2: str
    gl: str
    linkedin_subdomains: tuple[str, ...]
    cities: tuple[str, ...]
    aliases: tuple[str, ...]
    locale: str
    timezone: str

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(
                f"{self.iso2}: unknown time zone {self.timezone!r} "
                "(on Windows the 'tzdata' package must be installed)"
            ) from exc


def _build() -> dict[str, Country]:
    countries: dict[str, Country] = {}
    for row in COUNTRY_ROWS:
        country = Country(
            name=row["name"],
            iso2=row["iso2"],
            gl=row["gl"],
            linkedin_subdomains=tuple(row["linkedin_subdomains"]),
            cities=tuple(row["cities"]),
            aliases=tuple(row["aliases"]),
            locale=row["locale"],
            timezone=row["timezone"],
        )
        countries[country.iso2] = country
    return countries


COUNTRIES: dict[str, Country] = _build()


def _build_indexes() -> tuple[dict[str, str], dict[str, tuple[str, str]], re.Pattern[str]]:
    """Return (lookup for get(), term -> (iso2, kind) for matching, compiled matcher)."""
    lookup: dict[str, str] = {}
    terms: dict[str, tuple[str, str]] = {}

    def claim(table: dict, key: str, value, label: str) -> None:
        if key in table and table[key] != value:
            raise ValueError(f"Country table clash on {label!r}: {table[key]} vs {value}")
        table[key] = value

    for c in COUNTRIES.values():
        claim(lookup, _fold(c.iso2), c.iso2, c.iso2)
        for name in (c.name, *c.aliases):
            claim(lookup, _fold(name), c.iso2, name)
            claim(terms, _fold(name), (c.iso2, "name"), name)
    for c in COUNTRIES.values():
        for city in c.cities:
            key = _fold(city)
            if key in terms and terms[key][0] == c.iso2:
                continue  # already a country name/alias (e.g. Dubai); the name wins
            claim(terms, key, (c.iso2, "city"), city)
    ordered = sorted(terms, key=lambda t: (-len(t), t))
    pattern = re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in ordered) + r")(?![a-z0-9])"
    )
    return lookup, terms, pattern


_LOOKUP, _TERMS, _MATCHER = _build_indexes()


def all_countries() -> list[Country]:
    """Every country in the table, in table order."""
    return list(COUNTRIES.values())


def get(code_or_name: str) -> Country:
    """Resolve an ISO code, country name or alias (case- and accent-insensitive)."""
    if isinstance(code_or_name, str):
        iso2 = _LOOKUP.get(_fold(code_or_name))
        if iso2:
            return COUNTRIES[iso2]
    raise UnknownCountryError(
        f"Unknown country {code_or_name!r}. Use a two-letter code (e.g. SG), "
        "a country name or a common alias (e.g. UK)."
    )


def _scan(text: str) -> list[tuple[str, str]]:
    """Return (iso2, kind) for every country/alias/city found as whole words in `text`.

    Longer names win over shorter ones that overlap them, so "New South Wales" counts for
    Australia and not for the "Wales" inside it.
    """
    folded = _fold(text)
    return [_TERMS[m.group(0)] for m in _MATCHER.finditer(folded)]


def _linkedin_subdomain(url: str) -> str:
    """Country label of a linkedin.com URL ("sg" for sg.linkedin.com), else ''."""
    if not url:
        return ""
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "//" + candidate
    try:
        host = (urlsplit(candidate).hostname or "").lower()
    except ValueError:
        return ""
    suffix = ".linkedin.com"
    if not host.endswith(suffix):
        return ""
    return host[: -len(suffix)].split(".")[-1]


def matches_location(country: Country | str, url: str | None, text: str | None) -> bool:
    """Does the URL / snippet place the person in `country`?

    Rules in order: (1) an explicit "Location: <value>" naming any known country or city
    decides alone (a country or alias named there outranks a city); (2) a LinkedIn country
    subdomain of this country; (3) the country name, alias or a city as a whole word
    anywhere in the text; otherwise False.
    """
    c = country if isinstance(country, Country) else get(country)
    body = text or ""

    for m in _LOCATION_RE.finditer(body):
        hits = _scan(m.group(1))
        if hits:
            named = {iso for iso, kind in hits if kind == "name"}
            return c.iso2 in (named or {iso for iso, _ in hits})

    sub = _linkedin_subdomain(url or "")
    if sub and sub != "www" and sub in c.linkedin_subdomains:
        return True

    return any(iso == c.iso2 for iso, _ in _scan(body))
