"""Lead quality checks L01-L25 (PLAN section 14; L24 and L25 are the project checks of T23).

`check_person` re-runs the confirmation rules (`confirm.parse_result`) over a person's stored
evidence and turns each failed rule into an `Issue`, then adds the checks that need more than one
search result: duplicates across the campaign (L02), name and title quality (L11, L12), the
profile language/location hint (L20), AI-search evidence (L21, L22) and the email checks
(L17-L19, L23). Nothing is fetched; the only I/O is `load_leads`, which reads the database once
for a whole campaign.

Severity comes from `LEAD_SEVERITY`. Some blocks apply to one channel only: `EMAIL_ONLY_CODES`
block the email channel but not a LinkedIn message, and `SEND_ONLY_BLOCKS` (L23, an unverified
guess) block sending but not a labelled export. `review.export_gate` applies those rules.

T21 can add checks by appending a function to `EXTRA_LEAD_CHECKS`; it is called with
`(person, company, campaign, email_row, context)` and returns a list of `Issue`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from leadgen import db
from leadgen.core import countries as countries_mod
from leadgen.providers.search import SearchResult
from leadgen.services import confirm as rules
from leadgen.services import drafting
from leadgen.services.emails import clean_domain, is_role_address
from leadgen.services.spot_check import spot_state

# --- codes --------------------------------------------------------------------------------------

LEAD_SEVERITY: dict[str, str] = {
    "L01": "block", "L02": "block", "L03": "block", "L04": "block", "L05": "block",
    "L06": "block", "L07": "block", "L08": "block", "L09": "block", "L10": "block",
    "L11": "warn", "L12": "warn", "L13": "warn", "L14": "block", "L15": "warn",
    "L16": "block", "L17": "block", "L18": "warn", "L19": "block", "L20": "warn",
    "L21": "warn", "L22": "warn", "L23": "warn", "L24": "block", "L25": "block",
}  # fmt: skip

LEAD_TITLES: dict[str, str] = {
    "L01": "LinkedIn URL is not a personal profile",
    "L02": "Duplicate person",
    "L03": "Company not approved, or excluded / competitor",
    "L04": "Current employer not confirmed",
    "L05": "Country not confirmed",
    "L06": "Designation not in the campaign groups",
    "L07": "Seniority below the minimum",
    "L08": "Former role",
    "L09": "Headline names a different employer",
    "L10": "Owner group but rank-only title",
    "L11": "Name quality",
    "L12": "Title garbled or truncated",
    "L13": "Weak-fit function for the group",
    "L14": "Junior keyword in title",
    "L15": "Job-seeking hint",
    "L16": "On the suppression list",
    "L17": "No usable email for the email channel",
    "L18": "Email domain differs from the company domain",
    "L19": "Role-based email address",
    "L20": "Profile language or location hint",
    "L21": "Evidence came from AI web search",
    "L22": "LinkedIn URL does not match the name",
    "L23": "Email is an unverified guess",
    "L24": "Already found or contacted in this project",
    "L25": "Already in another campaign of this project",
}  # fmt: skip

# Blocks that stop the email channel only (a LinkedIn message to the same person is still fine).
EMAIL_ONLY_CODES = frozenset({"L17", "L18", "L19", "L23"})
# Blocks that stop sending but not a CSV export that labels the address as a guess.
SEND_ONLY_BLOCKS = frozenset({"L23"})
# Blocks a hand promotion (`MANUAL:` entry) overrides: they become warnings. The owner looked
# at the profile and accepted the evidence. L01-L03, L16 and the email blocks are never
# overridden.
_OVERRIDABLE = frozenset({"L04", "L05", "L06", "L07", "L08", "L09", "L10", "L14"})

# L20: words that mark a non-English profile (Spanish, Portuguese, French, German, Italian).
# "director" and "manager" are English too, so they are not listed.
_FOREIGN_WORD_RE = re.compile(
    r"(?<![A-Za-z])(?:gerente|jefe|jefa|responsable|responsables|diretor|diretora|directeur|"
    r"directrice|leiter|leiterin|geschäftsführer|geschäftsführerin|"
    r"direttore|direttrice|responsabile|dirigente|recursos humanos|ressources humaines|"
    r"personalabteilung|formación|formação|capacitación|gestión|"
    r"gestão|dirección|operações|operaciones|ventes|vertrieb|"
    r"director de|directora de|diretor de)(?![A-Za-z])",
    re.IGNORECASE,
)
_LOCATION_VALUE_RE = re.compile(r"(?<![A-Za-z])location\s*[:·]\s*([^·|]+)", re.IGNORECASE)

# L11: a name that needs a greeting_name (initial only, brackets, credentials, ALL CAPS).
_NAME_BRACKET_RE = re.compile(r"[(\[]")
_NAME_CAPS_RE = re.compile(r"(?<![A-Za-z])[A-Z]{3,}(?![A-Za-z])")
_NAME_INITIAL_RE = re.compile(r"^[A-Za-z]\.?$")

_STATUS_RANK = {"verified": 0, "risky": 1, "guess": 2, "unknown": 3, "invalid": 4}


@dataclass(frozen=True)
class Issue:
    """One quality finding. `severity` is `block` or `warn`."""

    code: str
    severity: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        """The `{code, severity, detail}` shape stored in `qa_json` and shown to the AI."""
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


@dataclass
class LeadContext:
    """Campaign-wide facts the per-person checks need (see `load_leads`)."""

    campaign_ctx: rules.CampaignCtx
    duplicates: dict[int, str] = field(default_factory=dict)  # person id -> why (L02)
    suppressed_emails: frozenset[str] = frozenset()
    suppressed_domains: frozenset[str] = frozenset()
    ai_spot_check: str = ""  # "" (no AI-search leads), "ok", "incomplete" or "failed"
    extras: dict[str, Any] = field(default_factory=dict)  # caches of the EXTRA_LEAD_CHECKS

    @classmethod
    def basic(cls, campaign: Mapping[str, Any]) -> LeadContext:
        """A context with no database facts (no suppression list, no duplicates)."""
        return cls(campaign_ctx=rules.CampaignCtx.from_campaign(campaign))


ExtraCheck = Callable[
    [
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any] | None,
        LeadContext,
    ],
    list[Issue],
]
EXTRA_LEAD_CHECKS: list[ExtraCheck] = []  # more checks register here (T23: contact_checks)


def _issue(code: str, detail: str, severity: str | None = None) -> Issue:
    return Issue(code, severity or LEAD_SEVERITY[code], detail)


# --- small helpers ------------------------------------------------------------------------------


def _company_ctx(company: Mapping[str, Any]) -> rules.CompanyCtx:
    """The company as `confirm.parse_result` wants it (a known company country narrows the
    campaign's countries, as in `people`)."""
    aliases = company.get("aliases")
    if aliases is None:
        aliases = company.get("aliases_json") or []
    countries: tuple[str, ...] = ()
    if company.get("country"):
        try:
            countries = (countries_mod.get(str(company["country"])).iso2,)
        except countries_mod.UnknownCountryError:
            countries = ()
    return rules.CompanyCtx(
        name=str(company.get("name") or ""),
        aliases=tuple(str(a) for a in aliases),
        domain=str(company.get("domain") or ""),
        status=str(company.get("status") or "approved"),
        countries=countries,
        id=company.get("id"),
    )


def _evidence_name(person: Mapping[str, Any]) -> str:
    """The name as Google showed it (before the first dash of the result title)."""
    title = str(person.get("evidence_title") or "")
    head = re.split(r"\s+[-–—|]\s+", title, maxsplit=1)[0]
    return head.strip()


def pick_email(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The best email row of a person: verified, then risky, guess, unknown, invalid; newest
    first among equals."""
    if not rows:
        return None
    return sorted(
        rows, key=lambda r: (_STATUS_RANK.get(str(r.get("status")), 9), -int(r.get("id") or 0))
    )[0]


def _is_manual(person: Mapping[str, Any]) -> str:
    """The note of a hand promotion (`MANUAL:` entry in the stored reasons), or ""."""
    for reason in person.get("reasons_json") or person.get("reasons") or []:
        if str(reason).startswith("MANUAL:"):
            return str(reason)[len("MANUAL:") :].strip()
    return ""


# --- individual checks --------------------------------------------------------------------------


def _confirmation_issues(
    person: Mapping[str, Any],
    company: Mapping[str, Any],
    campaign: Mapping[str, Any],
    context: LeadContext,
) -> list[Issue]:
    """L01, L03-L16 from `confirm.parse_result` run again on the stored evidence."""
    result = SearchResult(
        title=str(person.get("evidence_title") or ""),
        link=str(person.get("linkedin_url") or ""),
        snippet=str(person.get("evidence_snippet") or ""),
        position=0,
    )
    cand = rules.parse_result(result, _company_ctx(company), context.campaign_ctx)
    if cand is None:
        return [_issue("L01", "The LinkedIn link is not a personal profile (linkedin.com/in/...).")]
    manual = _is_manual(person)
    out: list[Issue] = []
    for reason in cand.reasons:
        code, _, text = reason.partition(":")
        severity = LEAD_SEVERITY.get(code)
        if severity is None or code in {"L01", "L02"}:
            continue
        detail = text.strip()
        if manual and severity == "block" and code in _OVERRIDABLE:
            severity = "warn"
            detail = f"{detail} (accepted by hand: {manual})"
        out.append(Issue(code, severity, detail))
    return out


def _duplicate_issue(person: Mapping[str, Any], context: LeadContext) -> list[Issue]:
    why = context.duplicates.get(int(person.get("id") or 0))
    return [_issue("L02", why)] if why else []


def _name_quality_issue(person: Mapping[str, Any]) -> list[Issue]:
    """L11 (beyond 'no first and last name', which the confirmation rules report)."""
    shown = _evidence_name(person)
    first = str(person.get("first_name") or "").strip()
    greeting = str(person.get("greeting_name") or "").strip()
    problems: list[str] = []
    if _NAME_BRACKET_RE.search(shown):
        problems.append("brackets in the name")
    if "," in shown:
        problems.append("credentials after the name")
    if _NAME_CAPS_RE.search(shown) and not shown.isupper():
        problems.append("an ALL CAPS part")
    elif shown.isupper() and len(shown) > 3:
        problems.append("the name is in ALL CAPS")
    if _NAME_INITIAL_RE.match(first):
        problems.append("the first name is only an initial")
    if not greeting:
        return [_issue("L11", "greeting_name is empty, so no greeting can be checked.")]
    if problems and _NAME_INITIAL_RE.match(greeting):
        return [
            _issue("L11", f"{'; '.join(problems)}, and greeting_name '{greeting}' is an initial.")
        ]
    return []


def _title_issue(person: Mapping[str, Any]) -> list[Issue]:
    """L12 for a title that is empty or cut off (the headline check is in `parse_result`)."""
    title = str(person.get("title") or "").strip()
    if not title:
        return [_issue("L12", "The stored title is empty.")]
    if title.endswith(("…", "...")) or re.search(r"(?:\s|^)(?:at|@)\s*$", title, re.I):
        return [_issue("L12", f"The title looks cut off: '{title[-40:]}'.")]
    return []


def _language_issue(
    person: Mapping[str, Any], company: Mapping[str, Any], campaign: Mapping[str, Any]
) -> list[Issue]:
    """L20: a non-English profile, or text that places the person in another country while the
    campaign country was confirmed from the link only."""
    text = f"{person.get('evidence_title') or ''} {person.get('evidence_snippet') or ''}"
    hint = _FOREIGN_WORD_RE.search(text)
    if hint:
        return [
            _issue("L20", f"The profile looks non-English ('{hint.group(0)}'); check the language.")
        ]
    targets = []
    for value in campaign.get("countries") or []:
        try:
            targets.append(countries_mod.get(str(value)))
        except countries_mod.UnknownCountryError:
            continue
    if not targets:
        return []
    visible = text
    for loc in _LOCATION_VALUE_RE.finditer(text):
        visible += " " + loc.group(1)
    if any(countries_mod.matches_location(c, "", visible) for c in targets):
        return []
    for other in countries_mod.all_countries():
        if other not in targets and countries_mod.matches_location(other, "", visible):
            return [
                _issue(
                    "L20",
                    f"The profile text places this person in {other.name}, but the campaign "
                    "country was only confirmed from the link.",
                )
            ]
    return []


def _slug(url: str) -> str:
    key = rules.url_key(url)
    return unquote(key.split("/", 1)[1]) if key else ""


def _slug_issue(person: Mapping[str, Any], ai_search: bool) -> list[Issue]:
    """L22: some part of the person's name should appear in the LinkedIn slug
    (`confirm.slug_matches_name`; the greeting name and the name as shown count too)."""
    url = str(person.get("linkedin_url") or "")
    slug = _slug(url)
    if not slug:
        return []
    first, last = str(person.get("first_name") or ""), str(person.get("last_name") or "")
    verdicts = {
        rules.slug_matches_name(url, first, last),
        rules.slug_matches_name(url, str(person.get("greeting_name") or ""), ""),
        rules.slug_matches_name(url, str(person.get("raw_name") or ""), ""),
    }
    if "match" in verdicts:
        return []
    if verdicts == {"no_tokens"} and not rules.looks_like_id(slug):
        return []  # nothing to compare (for example a name in Chinese characters)
    if "mismatch" not in verdicts:
        return [_issue("L22", f"The LinkedIn address ('{slug[:30]}') is an ID, so the name "
                              "cannot be matched; check the link by hand.", "warn")]  # fmt: skip
    return [
        _issue(
            "L22",
            f"The LinkedIn address '{slug[:40]}' does not contain any part of the name "
            f"'{person.get('raw_name') or ''}'.",
            "block" if ai_search else "warn",
        )
    ]


def _ai_search_issue(person: Mapping[str, Any], context: LeadContext) -> list[Issue]:
    """L21: the evidence came from the host AI's own web search."""
    if person.get("evidence_origin") != "ai_web_search":
        return []
    if person.get("spot_check") == "wrong":
        return [_issue("L21", "AI-search lead: the spot check found this profile wrong.", "block")]
    if context.ai_spot_check != "ok":
        state = "has not been completed" if context.ai_spot_check == "incomplete" else "failed"
        return [
            _issue(
                "L21",
                f"AI-search lead (lower confidence) and the campaign's spot check {state}. "
                "Run the spot check and record the answers.",
                "block",
            )
        ]
    return [_issue("L21", "AI-search lead (lower confidence); the spot check passed.")]


def _domain_matches(email_domain: str, company_domain: str) -> bool:
    return (
        email_domain == company_domain
        or email_domain.endswith("." + company_domain)
        or company_domain.endswith("." + email_domain)
    )


def _domain_suppressed(domain: str, context: LeadContext) -> bool:
    """Whether `domain` or a parent domain is on the `domain` suppression list."""
    return bool(domain) and any(
        domain == d or domain.endswith("." + d) for d in context.suppressed_domains
    )


def _suppression_issues(
    company: Mapping[str, Any], email_row: Mapping[str, Any] | None, context: LeadContext
) -> list[Issue]:
    """L16 for the email address, its domain and the company's domain; every channel."""
    out: list[Issue] = []
    address = str((email_row or {}).get("email") or "").strip().lower()
    if address and (
        address in context.suppressed_emails
        or _domain_suppressed(clean_domain(address), context)
    ):
        out.append(_issue("L16", f"The email address {address} (or its domain) is suppressed."))
    company_domain = clean_domain(str(company.get("domain") or ""))
    if _domain_suppressed(company_domain, context):
        out.append(
            _issue("L16", f"The company's domain {company_domain} is on the suppression list.")
        )
    return out


def _email_issues(
    person: Mapping[str, Any],
    company: Mapping[str, Any],
    campaign: Mapping[str, Any],
    email_row: Mapping[str, Any] | None,
    context: LeadContext,
) -> list[Issue]:
    """L16 (email, email domain or company domain suppressed; any channel) and L17-L19, L23
    when the campaign uses email."""
    out: list[Issue] = _suppression_issues(company, email_row, context)
    if "email" not in (campaign.get("channels") or []):
        return out
    if email_row is None or not str(email_row.get("email") or "").strip():
        return [*out, _issue("L17", "No email address has been found for this person.")]
    address = str(email_row["email"]).strip().lower()
    status = str(email_row.get("status") or "")
    domain = clean_domain(address)
    sendable = drafting.sendable_email_statuses(campaign)
    if status == "guess":
        if "guess" in sendable:
            out.append(
                _issue("L23", f"{address} is an unverified guess; allowed for this campaign.")
            )
        else:
            out.append(
                _issue(
                    "L23",
                    f"{address} is an unverified guess. It can be exported with a 'guess' label "
                    "but is not sent unless the campaign allows guessed emails.",
                    "block",
                )
            )
    elif status not in sendable:
        why = "not allowed for this campaign" if status == "risky" else "not usable"
        out.append(_issue("L17", f"The email status is '{status or 'unknown'}' ({why})."))
    if is_role_address(address):
        out.append(_issue("L19", f"{address} is a role address (info@, hr@ ...), not a person."))
    company_domain = clean_domain(str(company.get("domain") or ""))
    if company_domain and domain:
        known = {company_domain}
        for alias in company.get("aliases") or company.get("aliases_json") or []:
            alias = str(alias).strip().lower()
            if "." in alias and " " not in alias:
                known.add(clean_domain(alias))
        if not any(_domain_matches(domain, d) for d in known):
            out.append(
                _issue(
                    "L18",
                    f"The email domain {domain} is not the company's domain "
                    f"({company_domain}) or a known alias.",
                )
            )
    return out


# --- public check -------------------------------------------------------------------------------


def check_person(
    person: Mapping[str, Any],
    company: Mapping[str, Any],
    campaign: Mapping[str, Any],
    email_row: Mapping[str, Any] | None = None,
    context: LeadContext | None = None,
) -> list[Issue]:
    """All lead checks L01-L23 for one person, one `Issue` per code (the first finding wins).

    `person` and `company` are database rows (decoded dicts), `campaign` is the dict from
    `campaign.get_campaign`, `email_row` the person's best email row (see `pick_email`) or None.
    Without a `context` no suppression list or duplicate information is used (see `load_leads`
    for the full context of a campaign). The confirmation rules are not copied here: they run
    again through `confirm.parse_result` on the stored evidence, so rule changes apply at once.
    A person promoted by hand (`MANUAL:` entry) keeps their blocks as warnings, except L01-L03
    and L16.
    """
    context = context or LeadContext.basic(campaign)
    issues: list[Issue] = []
    issues += _confirmation_issues(person, company, campaign, context)
    issues += _duplicate_issue(person, context)
    issues += _name_quality_issue(person)
    issues += _title_issue(person)
    issues += _language_issue(person, company, campaign)
    ai_search = person.get("evidence_origin") == "ai_web_search"
    issues += _ai_search_issue(person, context)
    issues += _slug_issue(person, ai_search)
    issues += _email_issues(person, company, campaign, email_row, context)
    for extra in EXTRA_LEAD_CHECKS:
        issues += extra(person, company, campaign, email_row, context)
    seen: dict[str, Issue] = {}
    for item in issues:
        previous = seen.get(item.code)
        if previous is None or (previous.severity == "warn" and item.severity == "block"):
            seen[item.code] = item
    return sorted(seen.values(), key=lambda i: i.code)


# --- loading a whole campaign -------------------------------------------------------------------


@dataclass
class LeadData:
    """Everything `check_person` needs for the confirmed people of one campaign."""

    campaign: dict[str, Any]
    people: list[dict[str, Any]]
    companies: dict[int, dict[str, Any]]
    emails: dict[int, dict[str, Any] | None]  # person id -> best email row
    context: LeadContext


def _duplicates(
    people: Sequence[Mapping[str, Any]], emails: Mapping[int, list[dict[str, Any]]]
) -> dict[int, str]:
    """For every person after the first with the same URL, email address or name+company, why
    (L02). The earliest confirmed person is the one kept."""
    seen: dict[tuple[str, str], tuple[int, str]] = {}
    out: dict[int, str] = {}
    for person in sorted(people, key=lambda p: p["id"]):
        keys: list[tuple[str, str]] = []
        url = rules.url_key(str(person.get("linkedin_url") or ""))
        if url:
            keys.append(("LinkedIn address", url))
        for row in emails.get(person["id"], []):
            if row.get("status") != "invalid" and row.get("email"):
                keys.append(("email address", str(row["email"]).strip().lower()))
        name = rules.name_key(str(person.get("raw_name") or ""))
        if name:
            keys.append(("name and company", f"{person['company_id']}:{name}"))
        for what, value in keys:
            hit = seen.get((what, value))
            if hit is not None and hit[0] != person["id"]:
                out.setdefault(
                    person["id"],
                    f"Same {what} as {hit[1]} (person {hit[0]}), who is kept.",
                )
        for what, value in keys:
            seen.setdefault((what, value), (person["id"], str(person.get("raw_name") or "")))
    return out


def _ai_spot_state(people: Sequence[Mapping[str, Any]], campaign: Mapping[str, Any]) -> str:
    """"" / "ok" / "incomplete" / "failed"; the rule lives in `spot_check.spot_state`."""
    ai = [p for p in people if p.get("evidence_origin") == "ai_web_search"]
    return spot_state(ai, campaign.get("options") or {})


def load_leads(campaign: Mapping[str, Any]) -> LeadData:
    """Read the confirmed people of a campaign with their companies, best email and the
    campaign-wide context (suppression list, duplicates, AI-search spot check)."""
    cid = campaign["id"]
    with db.session() as conn:
        people = db.query(
            conn,
            "SELECT * FROM people WHERE campaign_id = ? AND status = 'confirmed' ORDER BY id",
            (cid,),
        )
        companies = {
            r["id"]: {**r, "aliases": r["aliases_json"] or []}
            for r in db.query(conn, "SELECT * FROM companies WHERE campaign_id = ?", (cid,))
        }
        email_rows = db.query(
            conn,
            "SELECT e.* FROM emails e JOIN people p ON p.id = e.person_id "
            "WHERE p.campaign_id = ? AND p.status = 'confirmed' ORDER BY e.id",
            (cid,),
        )
        suppressed = db.query(conn, "SELECT value, kind FROM suppression")
    by_person: dict[int, list[dict[str, Any]]] = {}
    for row in email_rows:
        by_person.setdefault(row["person_id"], []).append(row)
    linkedin = [r["value"] for r in suppressed if r["kind"] == "linkedin"]
    context = LeadContext(
        campaign_ctx=rules.CampaignCtx.from_campaign(campaign, linkedin),
        duplicates=_duplicates(people, by_person),
        suppressed_emails=frozenset(
            str(r["value"]).strip().lower() for r in suppressed if r["kind"] == "email"
        ),
        suppressed_domains=frozenset(
            d
            for d in (clean_domain(str(r["value"])) for r in suppressed if r["kind"] == "domain")
            if d
        ),
        ai_spot_check=_ai_spot_state(people, campaign),
    )
    return LeadData(
        campaign=dict(campaign),
        people=people,
        companies=companies,
        emails={p["id"]: pick_email(by_person.get(p["id"], [])) for p in people},
        context=context,
    )


def check_all(data: LeadData) -> dict[int, list[Issue]]:
    """`check_person` for every person in `data`: person id -> issues."""
    out: dict[int, list[Issue]] = {}
    for person in data.people:
        company = data.companies.get(person["company_id"])
        if company is None:
            continue
        out[person["id"]] = check_person(
            person, company, data.campaign, data.emails.get(person["id"]), data.context
        )
    return out


from leadgen.services import contact_checks  # noqa: E402,F401  (registers L24 and L25)
