"""Account-based people finder: named target companies → confirmed contacts.

Discovery (``harvey discover``) enumerates local businesses by category, which
suits trades and clinics but not "the L&D head at a large employer". This
collector starts from a list of companies you already want, searches Google
(via Serper) for public LinkedIn profiles of people at each one, and keeps a
person only when the search result itself confirms all of:

  - the profile is a real linkedin.com/in/ URL,
  - the person is at that company (named in their headline or experience),
  - they are in the target country,
  - the role falls into one of the export designation buckets, and
  - they are manager level or above.

Confirmed people are stored as prospects (source ``linkedin_confirmed``) so
they show up in the dashboard and in ``harvey export``. Everything else goes to
a review CSV with the reason it failed, so nothing is silently dropped.

No LinkedIn login is used. Results come from Google's public index, so a
profile can be stale (someone may have moved on since Google last crawled it).
"""

import asyncio
import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from harvey.export import DESIGNATION_OTHER, classify_designation
from harvey.models.company import Company
from harvey.models.prospect import Prospect

logger = logging.getLogger("harvey.accounts")

SERPER_URL = "https://google.serper.dev/search"
CONFIRMED_SOURCE = "linkedin_confirmed"
LARGE_COMPANY_SIZE = "1,000+ employees"

# One search per role group per company. Google supports OR inside quotes.
ROLE_QUERIES = {
    "people": '("learning" OR "training" OR "L&D" OR "talent development" '
              'OR "HR" OR "human resources" OR "people")',
    "operations": '("safety" OR "EHS" OR "WSH" OR "HSE" OR "operations" '
                  'OR "facilities")',
    # HR titles crowd out L&D in the "people" search, so L&D gets its own.
    "learning": '("learning and development" OR "L&D" OR "head of training" '
                'OR "training manager" OR "organisation development" '
                'OR "talent development" OR "learning")',
}

# Manager level and above. Big organisations buy through managers, heads and
# directors; executives and coordinators go to review instead.
_SENIOR_RE = re.compile(
    r"\b(manager|head|director|lead|chief|vp|avp|svp|evp|vice president|principal|partner"
    r"|general manager|managing director|founder|owner|president|chro|ceo|coo)\b",
    re.I,
)

OWNER_DESIGNATION = "Business owners / MDs"

# Who actually runs the organisation (or its Singapore arm).
_TOP_LEADER_RE = re.compile(
    r"\b(ceo|chief executive|deputy ceo|founder|co-?founder|owner|chairman"
    r"|managing partner|country (manager|head)|general manager|president)\b",
    re.I,
)
_VICE_PRESIDENT_RE = re.compile(r"\bvice[\s-]+president\b", re.I)

_FORMER_RE = re.compile(r"\b(former|formerly|ex-|retired|previously)\b", re.I)

_TRAILING_NOISE_RE = re.compile(r"\s*(\|\s*LinkedIn.*|\.\.\.|…)\s*$", re.I)


@dataclass
class Account:
    company: str
    aliases: list[str]
    domain: str
    industry: str
    notes: str = ""


@dataclass
class Candidate:
    account: Account
    first_name: str
    last_name: str
    headline: str
    linkedin_url: str
    snippet: str
    designation: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return not self.reasons


def load_accounts(path: str | Path) -> list[Account]:
    """Read the target-account CSV (company, aliases, domain, industry, notes)."""
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("company") or "").strip()
            if not name:
                continue
            aliases = [a.strip() for a in (row.get("aliases") or "").split(";") if a.strip()]
            if name not in aliases:
                aliases.insert(0, name)
            out.append(Account(
                company=name,
                aliases=aliases,
                domain=(row.get("domain") or "").strip().lower(),
                industry=(row.get("industry") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            ))
    return out


def _alias_in(alias: str, text: str) -> bool:
    """Short all-caps aliases (NHG, DBS, SATS) must match case-sensitively as
    whole words, so "Shell" or "SIT" can't match inside ordinary words."""
    if len(alias) <= 5 and alias.isupper():
        return re.search(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", text) is not None
    return re.search(rf"(?<![A-Za-z]){re.escape(alias.lower())}(?![A-Za-z])", text.lower()) is not None


def _clean(s: str) -> str:
    return _TRAILING_NOISE_RE.sub("", (s or "").strip()).strip(" -–|.")


def parse_result(account: Account, item: dict, country: str) -> Candidate | None:
    """Turn one Google result into a Candidate, recording why it isn't confirmed."""
    url = (item.get("link") or "").split("?")[0].rstrip("/")
    title = item.get("title") or ""
    snippet = (item.get("snippet") or "").replace("·", "·")
    if not re.search(r"(^|\.)linkedin\.com/in/[^/]+$", url.split("://", 1)[-1]):
        return None

    parts = [p.strip() for p in title.split(" - ")]
    name = _clean(parts[0]) if parts else ""
    headline = _clean(" - ".join(parts[1:])) if len(parts) > 1 else ""
    # Google truncates long titles; the snippet often carries the full headline
    # as "Name. Headline. Company ..." or "Headline at Company · Experience: ...".
    if name and snippet.startswith(name + "."):
        lead = snippet[len(name) + 1:].strip().split(". ")[0].strip()
        if lead and len(lead) > len(headline):
            headline = _clean(lead)
    if not headline:
        headline = _clean(snippet.split("·")[0])

    name_parts = name.split(" ", 1)
    cand = Candidate(
        account=account,
        first_name=name_parts[0] if name_parts else "",
        last_name=name_parts[1] if len(name_parts) > 1 else "",
        headline=headline,
        linkedin_url=url,
        snippet=snippet,
    )

    if not (cand.first_name and cand.last_name):
        cand.reasons.append("no full name")

    # Company: named in the headline, or as current experience in the snippet.
    # Google renders LinkedIn snippets a few ways: "Experience: X",
    # "Experience · X", "... at X", "... @ X", "Jul 2011 - Present", or
    # "Name. Headline. CurrentCompany School. Location."
    after_headline = ""
    if name and snippet.startswith(name + "."):
        rest = snippet[len(name) + 1:].strip()
        after_headline = rest.split(". ", 1)[1] if ". " in rest else ""
    head_hit = any(_alias_in(a, headline) for a in account.aliases)
    exp_hit = any(
        _alias_in(a, snippet) and (
            re.search(rf"Experience\W{{0,4}}{re.escape(a)}", snippet, re.I)
            or re.search(rf"(\bat|@)\s*{re.escape(a)}", snippet, re.I)
            or after_headline.lower().startswith(a.lower())
            or re.search(rf"{re.escape(a)}.{{0,60}}\bPresent\b", snippet, re.I)
        )
        for a in account.aliases
    )
    # A headline like "Director at SEED the Art Space" names the current
    # employer outright; if that isn't this account, the snippet's mention of
    # the account is history, not a current job.
    at_match = re.search(r"(?:\bat\s+|@\s*)([^|,·]+)", headline)
    if at_match:
        employer = re.sub(r"^the\s+", "", at_match.group(1).strip(), flags=re.I).lower()
        if employer and not any(
            employer.startswith(a.split()[0].lower())
            # Google truncates titles: "... at Rolls" for Rolls-Royce.
            or (len(employer) >= 4 and a.lower().startswith(employer))
            or _alias_in(a, at_match.group(1))
            for a in account.aliases
        ):
            cand.reasons.append(f"headline names a different employer ({at_match.group(1).strip()[:40]})")
            head_hit = exp_hit = True  # reason already recorded; skip the generic one

    if not (head_hit or exp_hit):
        if any(_alias_in(a, snippet + " " + title) for a in account.aliases):
            cand.reasons.append("company mentioned but not confirmed as current employer")
        else:
            cand.reasons.append("company not found in profile")

    host = url.split("://", 1)[-1].split(".linkedin.com")[0]
    if country and not (country.lower() in snippet.lower() or
                        (country.lower() == "singapore" and host == "sg")):
        cand.reasons.append(f"not shown as based in {country}")

    if _FORMER_RE.search(headline):
        cand.reasons.append("headline describes a former role")

    cand.designation = classify_designation(headline)
    if cand.designation in ("", DESIGNATION_OTHER):
        cand.reasons.append("role not in target designations")
    elif not _SENIOR_RE.search(headline):
        cand.reasons.append("below manager level")
    elif cand.designation == OWNER_DESIGNATION and not _TOP_LEADER_RE.search(
            _VICE_PRESIDENT_RE.sub("vp", headline)):
        # At a large organisation "Managing Director" or "Principal" is a
        # rank (senior banker, principal engineer), not the person who runs
        # the company, so it doesn't earn the owner/MD label on its own.
        cand.reasons.append("MD/principal is a rank here, not the company head")

    return cand


async def search_serper(client: httpx.AsyncClient, api_key: str, query: str,
                        gl: str = "sg", num: int = 10) -> list[dict]:
    resp = await client.post(
        SERPER_URL,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "gl": gl, "num": num},
        timeout=30,
    )
    resp.raise_for_status()
    return [i for i in (resp.json().get("organic") or []) if isinstance(i, dict)]


async def find_people(state, accounts: list[Account], api_key: str,
                      country: str = "Singapore", gl: str = "sg",
                      review_path: str | Path | None = None,
                      delay_s: float = 0.4,
                      groups: list[str] | None = None) -> dict:
    """Search every account, store confirmed people, write the rest to review.

    ``groups`` limits which ROLE_QUERIES run (default: all). Returns counts:
    queries, confirmed, review, duplicates.
    """
    role_queries = {g: q for g, q in ROLE_QUERIES.items() if not groups or g in groups}
    stats = {"queries": 0, "confirmed": 0, "review": 0, "duplicates": 0}
    review_rows: list[dict] = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient() as client:
        for account in accounts:
            company_id = await state.add_company(Company(
                name=account.company,
                domain=account.domain,
                website=f"https://{account.domain}" if account.domain else "",
                industry=account.industry,
                company_size=LARGE_COMPANY_SIZE,
                location=country,
                source="target_accounts",
                notes=account.notes,
            ))

            for group, roles in role_queries.items():
                query = f'site:linkedin.com/in {roles} "{account.company}" {country}'
                try:
                    items = await search_serper(client, api_key, query, gl=gl)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (401, 403):
                        raise RuntimeError("Serper rejected the API key (check SERPER_API_KEY).") from e
                    logger.warning(f"Serper error for {account.company}/{group}: {e}")
                    continue
                except httpx.HTTPError as e:
                    logger.warning(f"Serper request failed for {account.company}/{group}: {e}")
                    continue
                stats["queries"] += 1

                for item in items:
                    cand = parse_result(account, item, country)
                    if cand is None or cand.linkedin_url in seen_urls:
                        continue
                    seen_urls.add(cand.linkedin_url)

                    if not cand.confirmed:
                        stats["review"] += 1
                        review_rows.append({
                            "company": account.company,
                            "name": f"{cand.first_name} {cand.last_name}".strip(),
                            "headline": cand.headline,
                            "designation": cand.designation,
                            "linkedin_url": cand.linkedin_url,
                            "why_not_confirmed": "; ".join(cand.reasons),
                            "snippet": cand.snippet,
                        })
                        continue

                    if await state.prospect_exists(
                        linkedin_url=cand.linkedin_url,
                        first_name=cand.first_name, last_name=cand.last_name,
                        company=account.company,
                    ):
                        stats["duplicates"] += 1
                        continue

                    await state.add_prospect(Prospect(
                        company_id=company_id,
                        first_name=cand.first_name,
                        last_name=cand.last_name,
                        linkedin_url=cand.linkedin_url,
                        title=cand.headline,
                        source=CONFIRMED_SOURCE,
                        source_url=cand.linkedin_url,
                        personalization_notes=cand.snippet[:500],
                        company=account.company,
                        industry=account.industry,
                        company_size=LARGE_COMPANY_SIZE,
                    ))
                    stats["confirmed"] += 1

                await asyncio.sleep(delay_s)

            logger.info(f"Accounts: {account.company} done "
                        f"({stats['confirmed']} confirmed so far)")

    if review_path:
        # Append, so a follow-up run (e.g. one extra role group) doesn't wipe
        # the review list from the previous one.
        path = Path(review_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        cols = ["company", "name", "headline", "designation",
                "linkedin_url", "why_not_confirmed", "snippet"]
        existing = set()
        if path.exists():
            with open(path, encoding="utf-8-sig", newline="") as f:
                existing = {r.get("linkedin_url", "") for r in csv.DictReader(f)}
        new_file = not path.exists()
        with open(path, "a", encoding="utf-8-sig" if new_file else "utf-8",
                  newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            if new_file:
                w.writeheader()
            w.writerows(r for r in review_rows if r["linkedin_url"] not in existing)
    return stats
