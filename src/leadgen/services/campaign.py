"""Campaigns: create, update, read, list and archive (PLAN section 6, workflow step 2).

A campaign belongs to a project (`services/projects.py`) and ties the project's offer to its
targeting: countries, sectors, company size, channels, designation groups, minimum seniority,
exclusions and options (including the size limits `max_people`, `max_companies`,
`max_per_company`). All validation lives here;
errors are raised as `CampaignError` so adapters can return the structured reply.
Callers must run `db.init_db()` first.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from leadgen import db
from leadgen.core import countries as countries_mod
from leadgen.core.config import ConfigError, load_settings
from leadgen.core.text import normalize_whitespace, slugify
from leadgen.services.designations import DesignationError, resolve_groups
from leadgen.services.seniority import LEVELS

CHANNELS = ("email", "linkedin")
SEARCH_MODES = ("api", "ai", "auto")
STATUSES = ("draft", "active", "archived")
MIN_GROUPS = 1
MAX_GROUPS = 8
DEFAULT_OPTIONS: dict[str, Any] = {
    "invite_note": False,
    "allow_risky_emails": False,
    "allow_guess_emails": False,
    "search_mode": "auto",
    "ai_search_needs_recheck": False,  # set by the AI-search spot check (services/spot_check.py)
    "max_people": None,  # n: confirmed people the campaign keeps (None = no limit)
    "max_companies": None,  # c: companies searched (the first c approved ones)
    "max_per_company": None,  # p: confirmed people per company; more go to `reserve`
}
LIMIT_OPTIONS = ("max_people", "max_companies", "max_per_company")
MAX_LIMIT = 10_000
EXCLUSION_KEYS = ("companies", "domains", "people", "competitors")

_STATUS_VALUES = {
    "companies": ("candidate", "approved", "excluded"),
    "people": ("confirmed", "review", "rejected", "removed", "reserve"),
    "emails": ("verified", "risky", "guess", "invalid", "unknown"),
    "messages": ("draft", "qa_fail", "qa_pass", "approved", "sent"),
}
_LOCALE_RE = re.compile(r"([A-Za-z]{2,3})(?:[-_]([A-Za-z]{2}))?")
_FIELDS = (
    "name", "offer_id", "countries", "sectors", "company_size", "channels", "designations",
    "seniority_min", "exclusions", "locale", "budget", "options", "status",
)


class CampaignError(Exception):
    """A campaign request failed. `code` is stable text; `hint` tells the AI what to do."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, "error": {...}}` shape used by every tool."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


# --- validation -----------------------------------------------------------------------------


def _text_list(name: str, values: Any) -> list[str]:
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise CampaignError("invalid_value", f"{name} must be a list of text values.")
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise CampaignError("invalid_value", f"{name} must contain only text values.")
        value = normalize_whitespace(value)
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out


def _name(value: Any) -> str:
    if not isinstance(value, str) or not normalize_whitespace(value):
        raise CampaignError("invalid_name", "The campaign needs a name.")
    name = normalize_whitespace(value)
    if not slugify(name):
        raise CampaignError(
            "invalid_name", "The campaign name needs at least one letter or digit.",
            "Export folders are named after the campaign.",
        )
    return name


def _check_unique_name(conn, name: str, own_id: int | None) -> None:
    slug = slugify(name)
    for row in db.query(conn, "SELECT id, name FROM campaigns"):
        if row["id"] != own_id and slugify(row["name"]) == slug:
            raise CampaignError(
                "duplicate_name",
                f"Campaign '{row['name']}' (id {row['id']}) already uses a name that gives the "
                "same export folder.",
                "Pick a different name.",
            )


def _countries(values: Any) -> list[str]:
    names = _text_list("countries", values)
    if not names:
        raise CampaignError("invalid_country", "Give at least one country.")
    out: list[str] = []
    for value in names:
        try:
            iso2 = countries_mod.get(value).iso2
        except countries_mod.UnknownCountryError as exc:
            raise CampaignError(
                "invalid_country", str(exc), "Use a two-letter code such as SG, GB or US."
            ) from None
        if iso2 not in out:
            out.append(iso2)
    return out


def _channels(values: Any) -> list[str]:
    names = [v.lower() for v in _text_list("channels", values)]
    if not names:
        raise CampaignError("invalid_channel", "Give at least one channel (email, linkedin).")
    bad = [v for v in names if v not in CHANNELS]
    if bad:
        raise CampaignError(
            "invalid_channel",
            f"Unknown channel(s): {', '.join(bad)}. Valid channels: {', '.join(CHANNELS)}.",
        )
    return list(dict.fromkeys(names))


def _designations(values: Any) -> list[dict[str, Any]]:
    if isinstance(values, (str, Mapping)) or not isinstance(values, (list, tuple)):
        raise CampaignError(
            "invalid_designations", "designations must be a list of template keys or group objects."
        )
    if not MIN_GROUPS <= len(values) <= MAX_GROUPS:
        raise CampaignError(
            "invalid_designations",
            f"A campaign needs {MIN_GROUPS} to {MAX_GROUPS} designation groups, got {len(values)}.",
            "Use list_designation_templates to see the built-in groups.",
        )
    try:
        groups = resolve_groups(values)
    except DesignationError as exc:
        raise CampaignError("invalid_designations", str(exc)) from None
    labels = [g.label.casefold() for g in groups]
    if len(set(labels)) != len(labels):
        raise CampaignError("invalid_designations", "Two designation groups share a label.")
    return [g.to_dict() for g in groups]


def _seniority_min(value: Any) -> str:
    if value not in LEVELS:
        raise CampaignError(
            "invalid_seniority",
            f"Unknown minimum seniority {value!r}. Valid levels: {', '.join(LEVELS)}.",
        )
    return value


def _domain(value: str) -> str:
    value = value.strip().lower()
    host = urlparse(value if "://" in value else f"//{value}").hostname or value
    return host.removeprefix("www.")


def _exclusions(value: Any, base: Mapping[str, list[str]] | None = None) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise CampaignError(
            "invalid_value", "exclusions must be an object with companies, domains, people, "
            "competitors.",
        )
    unknown = set(value) - set(EXCLUSION_KEYS)
    if unknown:
        raise CampaignError(
            "invalid_value",
            f"Unknown exclusion key(s): {', '.join(sorted(map(str, unknown)))}. "
            f"Valid keys: {', '.join(EXCLUSION_KEYS)}.",
        )
    out = {key: list((base or {}).get(key, [])) for key in EXCLUSION_KEYS}
    for key, items in value.items():
        cleaned = _text_list(f"exclusions.{key}", items)
        if key == "domains":
            cleaned = list(dict.fromkeys(d for d in map(_domain, cleaned) if d))
        out[key] = cleaned
    return out


def _options(value: Any, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignError("invalid_value", "options must be an object.")
    unknown = set(value) - set(DEFAULT_OPTIONS)
    if unknown:
        raise CampaignError(
            "invalid_value",
            f"Unknown option(s): {', '.join(sorted(map(str, unknown)))}. "
            f"Valid options: {', '.join(DEFAULT_OPTIONS)}.",
        )
    out = {**DEFAULT_OPTIONS, **(base or {}), **value}
    for key in (
        "invite_note", "allow_risky_emails", "allow_guess_emails", "ai_search_needs_recheck"
    ):  # fmt: skip
        if not isinstance(out[key], bool):
            raise CampaignError("invalid_value", f"option {key} must be true or false.")
    for key in LIMIT_OPTIONS:
        limit = out[key]
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT
        ):
            raise CampaignError(
                "invalid_value",
                f"option {key} must be a whole number from 1 to {MAX_LIMIT} "
                "(or empty for no limit).",
            )
    if out["search_mode"] not in SEARCH_MODES:
        raise CampaignError(
            "invalid_value",
            f"search_mode must be one of: {', '.join(SEARCH_MODES)}.",
        )
    return out


def _budget(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CampaignError("invalid_value", "budget must be an object of names to numbers.")
    out: dict[str, float] = {}
    for key, amount in value.items():
        if (
            not isinstance(key, str)
            or isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or amount < 0
        ):
            raise CampaignError(
                "invalid_value", "budget values must be numbers that are not negative."
            )
        out[key] = amount
    return out


def _locale(value: Any) -> str:
    match = _LOCALE_RE.fullmatch(value.strip()) if isinstance(value, str) else None
    if not match:
        raise CampaignError("invalid_value", "locale must look like 'en' or 'en-GB'.")
    lang, region = match.group(1).lower(), match.group(2)
    return f"{lang}-{region.upper()}" if region else lang


def _company_size(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CampaignError("invalid_value", "company_size must be text.")
    return normalize_whitespace(value)


def _default_locale(conn, offer_id: int) -> str:
    """Locale from the offer profile's language, else the settings locale."""
    row = db.get(conn, "offers", offer_id)
    profile = row["profile_json"] if row else None
    if isinstance(profile, Mapping) and profile.get("language"):
        try:
            return _locale(profile["language"])
        except CampaignError:
            pass
    try:
        return _locale(load_settings().locale)
    except (ConfigError, CampaignError):
        return "en-GB"


def _check_offer(conn, offer_id: Any) -> int:
    if isinstance(offer_id, bool) or not isinstance(offer_id, int):
        raise CampaignError("invalid_offer", "offer_id must be the number of an existing offer.")
    if db.get(conn, "offers", offer_id) is None:
        raise CampaignError(
            "invalid_offer",
            f"There is no offer with id {offer_id}.",
            "Use list_offers to see them.",
        )
    return offer_id


# --- reading --------------------------------------------------------------------------------


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "offer_id": row["offer_id"],
        "project_id": row["project_id"],
        "countries": row["countries_json"],
        "sectors": row["sectors_json"],
        "company_size": row["company_size"],
        "channels": row["channels_json"],
        "designations": row["designations_json"],
        "seniority_min": row["seniority_min"],
        "exclusions": _exclusions(row["exclusions_json"])
        if isinstance(row["exclusions_json"], dict)
        else _exclusions({}),
        "budget": row["budget_json"],
        "options": {**DEFAULT_OPTIONS, **(row["options_json"] or {})},
        "locale": row["locale"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row(conn, campaign_id: Any) -> dict[str, Any]:
    row = db.get(conn, "campaigns", campaign_id) if isinstance(campaign_id, int) else None
    if row is None:
        raise CampaignError(
            "not_found", f"There is no campaign with id {campaign_id}.",
            "Use list_campaigns to see them.",
        )
    return row


def _counts(conn, campaign_id: int) -> dict[str, dict[str, int]]:
    queries = {
        "companies": "SELECT status, COUNT(*) AS n FROM companies WHERE campaign_id = ? "
        "GROUP BY status",
        "people": "SELECT status, COUNT(*) AS n FROM people WHERE campaign_id = ? "
        "GROUP BY status",
        "emails": "SELECT e.status AS status, COUNT(*) AS n FROM emails e "
        "JOIN people p ON p.id = e.person_id WHERE p.campaign_id = ? GROUP BY e.status",
        "messages": "SELECT m.status AS status, COUNT(*) AS n FROM messages m "
        "JOIN people p ON p.id = m.person_id WHERE p.campaign_id = ? GROUP BY m.status",
    }
    out: dict[str, dict[str, int]] = {}
    for kind, sql in queries.items():
        found = {r["status"]: r["n"] for r in db.query(conn, sql, (campaign_id,))}
        out[kind] = {**dict.fromkeys(_STATUS_VALUES[kind], 0), **found}
    return out


def get_campaign(campaign_id: int) -> dict[str, Any]:
    """One campaign with its settings and `counts`: companies, people, emails and messages
    by status. Raises `CampaignError` (`not_found`)."""
    with db.session() as conn:
        row = _row(conn, campaign_id)
        counts = _counts(conn, row["id"])
    return {**_shape(row), "counts": counts}


def list_campaigns(
    include_archived: bool = True, project_id: int | None = None
) -> list[dict[str, Any]]:
    """Short summaries of the campaigns, newest first; `project_id` limits them to one project."""
    with db.session() as conn:
        if project_id is None:
            rows = db.query(conn, "SELECT * FROM campaigns ORDER BY id DESC")
        else:
            rows = db.query(
                conn, "SELECT * FROM campaigns WHERE project_id = ? ORDER BY id DESC", (project_id,)
            )
    out = []
    for row in rows:
        if row["status"] == "archived" and not include_archived:
            continue
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "offer_id": row["offer_id"],
                "project_id": row["project_id"],
                "status": row["status"],
                "countries": row["countries_json"],
                "channels": row["channels_json"],
                "designations": [g["label"] for g in row["designations_json"]],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return out


# --- writing --------------------------------------------------------------------------------


def _project_offer(conn, project_id: Any, offer_id: Any) -> tuple[int, int]:
    """`(project id, offer id)` for a new campaign: the offer defaults to the project's offer; a
    project without an offer adopts the one given. Raises `CampaignError`."""
    if project_id is None:
        raise CampaignError(
            "project_required",
            "A campaign belongs to a project, so project_id is needed.",
            "Use list_projects to pick one, or create_project, then pass project_id.",
        )
    project = (
        db.get(conn, "projects", project_id)
        if isinstance(project_id, int) and not isinstance(project_id, bool)
        else None
    )
    if project is None:
        raise CampaignError(
            "invalid_project",
            f"There is no project with id {project_id}.",
            "Use list_projects to see them, or create_project.",
        )
    if project["status"] == "archived":
        raise CampaignError(
            "project_archived",
            f"Project '{project['name']}' is archived.",
            "Use update_project(status='active') to reopen it, or pick another project.",
        )
    if offer_id is None:
        if project["offer_id"] is None:
            raise CampaignError(
                "project_has_no_offer",
                f"Project '{project['name']}' has no offer yet.",
                "Read the offer (ingest_offer_source), then set_project_offer, then create the "
                "campaign.",
            )
        return project["id"], project["offer_id"]
    offer_id = _check_offer(conn, offer_id)
    if project["offer_id"] is None:
        db.update(conn, "projects", project["id"], offer_id=offer_id)
    elif project["offer_id"] != offer_id:
        raise CampaignError(
            "offer_mismatch",
            f"Project '{project['name']}' uses offer {project['offer_id']}, not {offer_id}.",
            "Leave offer_id out to use the project's offer, or change it with set_project_offer.",
        )
    return project["id"], offer_id


def create_campaign(
    name: str,
    offer_id: int | None = None,
    countries: list[str] | None = None,
    sectors: list[str] | None = None,
    company_size: str | None = "",
    channels: list[str] | None = None,
    designations: list[dict] | list[str] | None = None,
    seniority_min: str = "manager",
    exclusions: dict | None = None,
    locale: str | None = None,
    budget: dict | None = None,
    options: dict | None = None,
    project_id: int | None = None,
) -> dict[str, Any]:
    """Create a draft campaign inside a project and return it (see `get_campaign`).

    `project_id` is required. `offer_id` defaults to the project's offer (an error if the project
    has none yet; a project without an offer adopts the one given). `countries` accepts codes,
    names or aliases and stores ISO codes. `designations` is a list of 1-8 template keys (see
    `designations.TEMPLATES`) or full group objects. `options` is merged over the defaults and
    may hold the size limits `max_people`, `max_companies` and `max_per_company`. `locale`
    defaults to the offer profile's language, then the settings.
    Raises `CampaignError` with a stable `code` for each invalid input.
    """
    values = {
        "name": _name(name),
        "countries_json": _countries(countries),
        "sectors_json": _text_list("sectors", [] if sectors is None else sectors),
        "company_size": _company_size(company_size),
        "channels_json": _channels(channels),
        "designations_json": _designations(designations),
        "seniority_min": _seniority_min(seniority_min),
        "exclusions_json": _exclusions(exclusions or {}),
        "budget_json": _budget(budget),
        "options_json": _options(options or {}),
    }
    with db.session() as conn:
        values["project_id"], offer_id = _project_offer(conn, project_id, offer_id)
        values["offer_id"] = offer_id
        _check_unique_name(conn, values["name"], None)
        values["locale"] = _locale(locale) if locale else _default_locale(conn, offer_id)
        values["status"] = "draft"
        new_id = db.insert(conn, "campaigns", **values)
    return get_campaign(new_id)


def update_campaign(campaign_id: int, **changes: Any) -> dict[str, Any]:
    """Change fields of a campaign and return it. Accepts any create field plus `status`.

    `options` and `exclusions` are merged into the stored values (only the given keys change).
    The options (`search_mode`, `invite_note`, `allow_risky_emails`, `allow_guess_emails`,
    `max_people`, `max_companies`, `max_per_company`) can also be passed directly, e.g.
    `update_campaign(3, search_mode="ai")`; a limit set to None is removed.
    Raises `CampaignError`.
    """
    direct = {k: changes.pop(k) for k in list(changes) if k in DEFAULT_OPTIONS}
    unknown = set(changes) - set(_FIELDS)
    if unknown:
        raise CampaignError(
            "unknown_field",
            f"Cannot change: {', '.join(sorted(unknown))}. "
            f"Changeable: {', '.join(_FIELDS)}, {', '.join(DEFAULT_OPTIONS)}.",
        )
    with db.session() as conn:
        row = _row(conn, campaign_id)
        values: dict[str, Any] = {}
        if "name" in changes:
            values["name"] = _name(changes["name"])
            _check_unique_name(conn, values["name"], row["id"])
        if "offer_id" in changes:
            values["offer_id"] = _check_offer(conn, changes["offer_id"])
        if "countries" in changes:
            values["countries_json"] = _countries(changes["countries"])
        if "sectors" in changes:
            values["sectors_json"] = _text_list("sectors", changes["sectors"])
        if "company_size" in changes:
            values["company_size"] = _company_size(changes["company_size"])
        if "channels" in changes:
            values["channels_json"] = _channels(changes["channels"])
        if "designations" in changes:
            values["designations_json"] = _designations(changes["designations"])
        if "seniority_min" in changes:
            values["seniority_min"] = _seniority_min(changes["seniority_min"])
        if "exclusions" in changes:
            base = row["exclusions_json"] if isinstance(row["exclusions_json"], dict) else {}
            values["exclusions_json"] = _exclusions(changes["exclusions"], base)
        if "locale" in changes:
            values["locale"] = _locale(changes["locale"])
        if "budget" in changes:
            values["budget_json"] = _budget(changes["budget"])
        if "options" in changes or direct:
            given = changes.get("options")
            if given is not None and not isinstance(given, Mapping):
                raise CampaignError("invalid_value", "options must be an object.")
            merged = {**(given or {}), **direct}
            values["options_json"] = _options(merged, row["options_json"] or {})
        if "status" in changes:
            if changes["status"] not in STATUSES:
                raise CampaignError(
                    "invalid_value", f"status must be one of: {', '.join(STATUSES)}."
                )
            values["status"] = changes["status"]
        if values:
            db.update(conn, "campaigns", row["id"], **values)
    return get_campaign(campaign_id)


def archive_campaign(campaign_id: int) -> dict[str, Any]:
    """Mark a campaign archived (its data is kept) and return it."""
    return update_campaign(campaign_id, status="archived")
