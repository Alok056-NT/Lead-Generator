"""MCP server: every service as a tool, the workflow guide as a prompt and a tool, two transports.

This is a thin adapter (PLAN section 3). Each tool calls one service function, turns the
service's errors into the reply shape of PLAN section 10 (`{"ok": false, "error": {...}}`),
keeps long strings short and never returns a secret. All business logic lives in `services/`.

No `from __future__ import annotations` here: the SDK reads the tool signatures at run time.
"""

import functools
import json
import logging
import re
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from leadgen import __version__, db
from leadgen.core import config, paths
from leadgen.providers import fake
from leadgen.providers.base import ProviderError
from leadgen.services import (
    ai_search,
    analytics,
    dashboard_ctl,
    drafting,
    emails,
    export,
    guide,
    lemlist,
    mailbox,
    offer,
    people,
    review,
    sending,
    spot_check,
    suppression,
)
from leadgen.services import campaign as campaign_svc
from leadgen.services import companies as companies_svc
from leadgen.services import designations as designations_svc
from leadgen.services import projects as projects_svc
from leadgen.services import status as status_svc

log = logging.getLogger("leadgen.mcp")

SERVER_NAME = "lead-generator"
LONG_STRING = 2000  # strings longer than this are shortened, except the fields a tool lists
MAX_REPLY_CHARS = 90_000  # claude.ai/Desktop: ~150k characters; Claude Code: 25k tokens
DEFAULT_HTTP_PORT = 8766
LOOPBACK = ("127.0.0.1", "localhost", "::1")
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{16,}")
_QUIET_LOGGERS = ("httpx", "httpcore", "httpx2", "httpcore2", "uvicorn.access")

INSTRUCTIONS = (
    "Lead Generator finds target companies and the right people at them, checks every lead, "
    "writes personalised messages and exports them. Work is organised in projects (one per "
    "product, service, content piece or website), each with one offer and several campaigns. "
    "Call get_status first in every "
    "conversation and follow its next_step. Call get_workflow_guide for the full step-by-step "
    "script (some clients do not show prompts). Always show the user an estimate and get a "
    "clear yes before any tool that spends credits or sends anything. Never invent companies, "
    "people, addresses or claims."
)

# --- reply handling -----------------------------------------------------------------------------


def _error(code: str, message: str, hint: str = "") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def _error_reply(tool: str, exc: Exception) -> dict[str, Any]:
    """The reply for an exception: services give their own, anything else a safe summary."""
    as_response = getattr(exc, "as_response", None)
    if callable(as_response):
        return as_response()
    if isinstance(exc, ProviderError):
        return _error(
            f"provider_{exc.code}",
            exc.message,
            "Tell the user what happened. If a key or credits are the problem, offer to add a "
            "key or to use AI search mode.",
        )
    if isinstance(exc, config.ConfigError):
        return _error("settings_problem", str(exc), "Ask the user to fix settings.yaml.")
    if isinstance(exc, ValidationError):
        fields = sorted({".".join(str(p) for p in err["loc"]) or "value" for err in exc.errors()})
        return _error(
            "invalid_value",
            f"These fields are not valid: {', '.join(fields)}.",
            "Check the tool's parameter descriptions and call it again.",
        )
    # Log the kind and place only: exception text can hold URLs with keys or message bodies.
    where = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
    log.error(
        "tool %s failed: %s at %s:%s",
        tool, type(exc).__name__,
        Path(where.filename).name if where else "?", where.lineno if where else "?",
    )  # fmt: skip
    return _error(
        "internal_error",
        "Something went wrong inside the tool.",
        "Call get_status and continue from its next_step; if it happens again, tell the user "
        "and check the log file in the Lead Generator folder (logs/leadgen.log).",
    )


def _clip(value: Any, keep: frozenset[str]) -> Any:
    """Shorten strings over LONG_STRING characters, except under the keys in `keep`."""
    if isinstance(value, str):
        if len(value) > LONG_STRING:
            return value[:LONG_STRING] + f"... [shortened, {len(value)} characters in all]"
        return value
    if isinstance(value, dict):
        return {k: (v if k in keep else _clip(v, keep)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v, keep) for v in value]
    return value


def _finish(result: Any, keep: frozenset[str]) -> dict[str, Any]:
    """Make a service result a JSON object with `ok`, short strings and a bounded size."""
    if isinstance(result, dict):
        data = result if "ok" in result else {"ok": True, **result}
    else:
        data = {"ok": True, "items": result}
    data = json.loads(json.dumps(_clip(data, keep), default=str))
    if len(json.dumps(data, ensure_ascii=False)) > MAX_REPLY_CHARS:
        return _error(
            "result_too_large",
            "The result is too large to return in one call.",
            "Ask for a smaller page (page_size or batch_size) or use a filter.",
        )
    return data


def _guarded(fn: Callable[..., Any], keep: frozenset[str]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # the one place that turns every failure into a reply
            return _finish(_error_reply(fn.__name__, exc), keep)
        return _finish(result, keep)

    return wrapper


# --- parameter types ----------------------------------------------------------------------------

CampaignId = Annotated[
    int, Field(description="Campaign id from create_campaign or list_campaigns.")
]
Confirm = Annotated[
    bool,
    Field(
        description="Set true ONLY after you showed the user the estimate and they clearly said "
        "yes. Never set it on your own."
    ),
]
Cursor = Annotated[
    str | int | None,
    Field(description="The next_cursor from the previous reply; leave out on the first call."),
]
Page = Annotated[int, Field(description="Page number, starting at 1.")]
PageSize = Annotated[int, Field(description="Rows per page, at most 50.")]
Ids = Annotated[list[int], Field(description="Row ids (from the matching list tool).")]
ProjectId = Annotated[int, Field(description="Project id from create_project or list_projects.")]


def _ann(
    title: str,
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


READ = {"read_only": True, "idempotent": True}
WRITE = {"idempotent": False}
SPEND = {"open_world": True}  # uses provider credits
OUTWARD = {"open_world": True, "destructive": True}  # leaves the computer, cannot be undone


# --- the server ---------------------------------------------------------------------------------


def build_server(*, remote: bool = False, init_db: bool = True) -> MCPServer:
    """Create the MCP server with every tool, the prompt and the instructions.

    `remote` is True for the HTTP transport: files can then only be read from inside the
    workspace folder. `init_db` creates or updates the database first (adapters must do this).
    """
    if init_db:
        db.init_db()
    if fake.fakes_enabled():
        log.warning("Fake providers are ON: search answers are canned data, for tests only.")
    server = MCPServer(
        SERVER_NAME,
        title="Lead Generator",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
    )
    for name in _QUIET_LOGGERS:  # request URLs of some providers carry the API key
        logging.getLogger(name).setLevel(logging.WARNING)
    file_root = paths.home() if remote else None

    def tool(
        title: str,
        description: str,
        flags: dict[str, bool],
        keep: Sequence[str] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            server.add_tool(
                _guarded(fn, frozenset(keep)),
                name=fn.__name__,
                title=title,
                description=" ".join(description.split()),
                annotations=_ann(title, **flags),
                structured_output=False,
            )
            return fn

        return register

    @server.prompt(
        name="lead_generation",
        title="Lead generation workflow",
        description="The step-by-step script for finding and contacting leads with Lead Generator.",
    )
    def lead_generation() -> str:
        return guide.workflow_text()

    # ---- 1-4: status, guide, providers, keys -------------------------------------------------

    @tool(
        "Status and next step",
        """Shows the workspace, which API keys are set (masked), the offers and campaigns with
        counts, and the suggested next step. Call this first in every conversation and whenever
        you are unsure where you are. Free, changes nothing.""",
        READ,
    )
    def get_status() -> dict[str, Any]:
        return status_svc.get_status()

    @tool(
        "Workflow guide",
        """Returns the written instructions for the job. section = workflow (default; the same
        text as the lead_generation prompt), ai_search (running searches with your own web
        search tool), messaging_style (how to write messages), offer_profile (how to write the
        offer profile) or all. Call it at the start for workflow, and read ai_search before using
        AI search mode. Free, changes nothing.""",
        READ,
        keep=("text",),
    )
    def get_workflow_guide(
        section: Annotated[
            str,
            Field(description="workflow, ai_search, messaging_style, offer_profile or all."),
        ] = "workflow",
    ) -> dict[str, Any]:
        return guide.get_guide(section)

    @tool(
        "List providers",
        """Lists the data providers (search, email finding, verification, lemlist, mailbox):
        what each is for, the free allowance, whether its key is set (last 4 characters only),
        usage this month and the sign-up link. Call it before a step that needs a key. Free,
        changes nothing.""",
        READ,
    )
    def list_providers() -> dict[str, Any]:
        return {"providers": status_svc.list_providers()}

    @tool(
        "Save an API key",
        """Stores a provider API key on this computer and tests it with one cheap call (the key
        is kept only if the test passes). Only call this when the user insists on pasting a key
        into the chat: keys pasted here stay in the chat history, so tell them that first and
        recommend the dashboard Keys page or `leadgen keys set <provider>` in a terminal. Never
        repeat the key back. Returns only the masked key.""",
        {"destructive": True, "open_world": True},
    )
    def save_api_key(
        provider: Annotated[
            str, Field(description="Provider key from list_providers, e.g. serper.")
        ],
        key: Annotated[str, Field(description="The API key exactly as the provider shows it.")],
    ) -> dict[str, Any]:
        return status_svc.save_key(provider, key)

    # ---- 5-8: offer ---------------------------------------------------------------------------

    @tool(
        "Read the offer source",
        """Learns the product or service. Give exactly ONE of url (a website or a public Google
        Doc; reads up to 15 pages, about a second each), text (pasted text) or file_path (a
        .pdf, .docx, .txt or .md file on this computer). Returns offer_id, the first chunk of
        the text (up to 40,000 characters) and next_cursor; to read the rest call it again with
        offer_id and cursor until next_cursor is empty. Also lists lines marked as unconfirmed
        (to confirm, TBC, placeholders). Free. Ask the user for the source first. If the page
        is blocked or empty the error says what to ask for instead.""",
        WRITE | {"open_world": True},
        keep=("text",),
    )
    def ingest_offer_source(
        url: Annotated[
            str | None, Field(description="Website address or public Google Doc link.")
        ] = None,
        text: Annotated[str | None, Field(description="Pasted description of the offer.")] = None,
        file_path: Annotated[str | None, Field(description="Path of a local document.")] = None,
        name: Annotated[str | None, Field(description="Optional name for pasted text.")] = None,
        offer_id: Annotated[
            int | None, Field(description="To read more of an offer already stored, with cursor.")
        ] = None,
        cursor: Cursor = None,
    ) -> dict[str, Any]:
        return offer.ingest_source(
            url=url, text=text, file_path=file_path, name=name, offer_id=offer_id,
            cursor=cursor, file_root=file_root,
        )  # fmt: skip

    @tool(
        "Save the offer profile",
        """Validates and stores the offer profile you wrote (see get_workflow_guide with
        section=offer_profile). profile is an object with: name, one_liner, description,
        services [{name, what, best_for[]}], audiences[], confirmed_claims[] (facts the messages
        may state), forbidden_claims[] (things they must not state), proof_points
        [{claim, source}], ctas[], sender {name, company, role, email?, website?}, tone,
        language (for example en-GB). Show the user the confirmed and forbidden claims and get
        their agreement before saving. Free.""",
        WRITE | {"idempotent": True},
    )
    def save_offer_profile(
        offer_id: Annotated[int, Field(description="Offer id from ingest_offer_source.")],
        profile: Annotated[dict[str, Any], Field(description="The OfferProfile object.")],
    ) -> dict[str, Any]:
        saved = offer.save_profile(offer_id, profile)
        return {"offer_id": offer_id, "profile": saved.model_dump(mode="json")}

    @tool(
        "Get an offer",
        """Returns an offer's details and its saved profile (profile is null until saved), without
        the full text. Free, changes nothing.""",
        READ,
    )
    def get_offer(offer_id: Annotated[int, Field(description="Offer id.")]) -> dict[str, Any]:
        return offer.get_offer(offer_id)

    # ---- projects -----------------------------------------------------------------------------

    @tool(
        "Create a project",
        """Creates a project: one product, service, content piece or website. A project has ONE
        offer and many campaigns, and it remembers every person found or contacted in it so
        nobody is offered twice until cooldown_days have passed (default 90; 0 = never block
        repeats; at most 3650). Ask the user what the project is and what to call it. Set the
        offer now (offer_id from ingest_offer_source) or later with update_project. Free.""",
        WRITE,
    )
    def create_project(
        name: Annotated[str, Field(description="Short unique name, e.g. the product name.")],
        description: Annotated[str, Field(description="Optional one-line description.")] = "",
        cooldown_days: Annotated[
            int, Field(description="Days before someone can be offered again (0 to 3650).")
        ] = 90,
        offer_id: Annotated[int | None, Field(description="Offer id, if already read.")] = None,
    ) -> dict[str, Any]:
        return projects_svc.create_project(name, description, cooldown_days, offer_id)

    @tool(
        "List projects",
        """Lists the projects (newest first) with their offer, cooldown, number of campaigns and
        confirmed people. Free, changes nothing.""",
        READ,
    )
    def list_projects(
        include_archived: Annotated[
            bool, Field(description="Also list archived projects.")
        ] = False,
    ) -> dict[str, Any]:
        return {"projects": projects_svc.list_projects(include_archived)}

    @tool(
        "Get a project",
        """Returns one project with its campaigns by status, its people by status (confirmed,
        reserve, review, rejected, removed) and how many people are still inside the cooldown.
        Free, changes nothing.""",
        READ,
    )
    def get_project(project_id: ProjectId) -> dict[str, Any]:
        result = projects_svc.get_project(project_id)
        result["campaign_list"] = campaign_svc.list_campaigns(True, project_id)
        return result

    @tool(
        "Update a project",
        """Changes a project; only the fields you pass change: name, description,
        cooldown_days (0 to 3650), offer_id (the project's offer; campaigns already made keep
        theirs) or status (active or archived). Free.""",
        WRITE | {"idempotent": True},
    )
    def update_project(
        project_id: ProjectId,
        name: Annotated[str | None, Field(description="New name.")] = None,
        description: Annotated[str | None, Field(description="New description.")] = None,
        cooldown_days: Annotated[
            int | None, Field(description="Days before someone can be offered again.")
        ] = None,
        offer_id: Annotated[int | None, Field(description="The project's offer id.")] = None,
        status: Annotated[
            Literal["active", "archived"] | None, Field(description="Status.")
        ] = None,
    ) -> dict[str, Any]:
        given = {
            "name": name, "description": description, "cooldown_days": cooldown_days,
            "offer_id": offer_id, "status": status,
        }  # fmt: skip
        return projects_svc.update_project(
            project_id, **{k: v for k, v in given.items() if v is not None}
        )

    @tool(
        "Move reserve people",
        """Moves people kept in reserve (confirmed, but beyond a campaign's size limits) from one
        campaign into another campaign of the SAME project, as confirmed, while the target's
        limits (max_people, max_companies, max_per_company) hold. Their evidence is judged
        again by the target's rules and anyone already contacted or in a third campaign stays
        in reserve. max_per_company / max_people can tighten the limits for this move. Free.""",
        WRITE,
    )
    def move_reserve_people(
        from_campaign_id: Annotated[int, Field(description="Campaign holding the reserve.")],
        to_campaign_id: Annotated[int, Field(description="Campaign that receives them.")],
        max_per_company: Annotated[
            int | None, Field(description="At most this many per company in the target.")
        ] = None,
        max_people: Annotated[
            int | None, Field(description="At most this many confirmed people in the target.")
        ] = None,
    ) -> dict[str, Any]:
        return people.move_reserve(from_campaign_id, to_campaign_id, max_per_company, max_people)

    # ---- 8-12: designations and campaigns -----------------------------------------------------

    @tool(
        "Designation templates",
        """Lists the built-in job-function groups (L&D, HR, Safety/Operations, Owners/MDs,
        Marketing, Sales, IT, Finance, Procurement, Facilities, Customer service) with their
        keywords and search terms. Use the keys in create_campaign, or write custom groups.
        Free, changes nothing.""",
        READ,
    )
    def list_designation_templates() -> dict[str, Any]:
        return {"templates": designations_svc.list_templates()}

    @tool(
        "Create a campaign",
        """Creates a draft campaign inside a project (project_id is required; create the project
        first). A campaign is the whole plan for finding and writing to people by email and
        LinkedIn; sending stays optional. The offer is the project's offer. Ask the user for
        countries, sectors, company size and channels first, and have them confirm the
        designation groups. Size: max_people (n people in all), max_companies (c companies
        searched) and max_per_company (p people per company; extra confirmed people are kept
        in reserve for a later campaign); leave out for no limit.
        designations is 1 to 8 template keys (see list_designation_templates) or custom groups
        {label, keywords[], search_terms[]}; order matters (first match wins). exclusions is
        {companies[], domains[], people[], competitors[]}. budget is {search, hunter, verify}
        numbers (defaults 300, 25, 200). search_mode: api, ai (use your own web search) or auto
        (default). Free.""",
        WRITE,
    )
    def create_campaign(
        name: Annotated[
            str, Field(description="Campaign name, unique, with some letters or digits.")
        ],
        project_id: ProjectId,
        countries: Annotated[list[str], Field(description="Country names or ISO codes.")],
        sectors: Annotated[list[str], Field(description="Sectors, e.g. logistics.")],
        company_size: Annotated[str, Field(description="Company size, e.g. 200-1000 employees.")],
        channels: Annotated[list[str], Field(description="email, linkedin or both.")],
        designations: Annotated[
            list[str | dict[str, Any]], Field(description="Template keys or custom groups.")
        ],
        offer_id: Annotated[
            int | None, Field(description="Leave out: the project's offer is used.")
        ] = None,
        seniority_min: Annotated[
            str, Field(description="individual, manager, director, vp or c_suite.")
        ] = "manager",
        exclusions: Annotated[
            dict[str, Any] | None, Field(description="What to leave out.")
        ] = None,
        locale: Annotated[str | None, Field(description="Spelling locale, e.g. en-GB.")] = None,
        budget: Annotated[dict[str, float] | None, Field(description="Credit budgets.")] = None,
        search_mode: Annotated[
            Literal["api", "ai", "auto"] | None, Field(description="Search mode.")
        ] = None,
        invite_note: Annotated[
            bool | None, Field(description="Draft LinkedIn invite notes.")
        ] = None,
        allow_risky_emails: Annotated[
            bool | None, Field(description="Allow risky addresses.")
        ] = None,
        allow_guess_emails: Annotated[
            bool | None, Field(description="Allow unverified guesses.")
        ] = None,
        max_people: Annotated[
            int | None, Field(description="n: confirmed people to keep (1 to 10000).")
        ] = None,
        max_companies: Annotated[
            int | None, Field(description="c: companies to search (1 to 10000).")
        ] = None,
        max_per_company: Annotated[
            int | None, Field(description="p: confirmed people per company (1 to 10000).")
        ] = None,
    ) -> dict[str, Any]:
        options = {
            key: value
            for key, value in (
                ("search_mode", search_mode), ("invite_note", invite_note),
                ("allow_risky_emails", allow_risky_emails),
                ("allow_guess_emails", allow_guess_emails), ("max_people", max_people),
                ("max_companies", max_companies), ("max_per_company", max_per_company),
            )
            if value is not None
        }  # fmt: skip
        return campaign_svc.create_campaign(
            name=name, offer_id=offer_id, countries=countries, sectors=sectors,
            company_size=company_size, channels=channels, designations=designations,
            seniority_min=seniority_min, exclusions=exclusions, locale=locale,
            budget=budget, options=options, project_id=project_id,
        )  # fmt: skip

    @tool(
        "Update a campaign",
        """Changes a campaign; only the fields you pass change (exclusions and options are
        merged). Use it to set search_mode ("ai" for AI search mode), to turn on
        allow_guess_emails or allow_risky_emails (only after warning the user that some
        addresses will bounce) or to change the targeting. After changing countries,
        designations or seniority, call recheck_people so people already found are judged by
        the new rules. status: draft, active or archived. Free.""",
        WRITE | {"idempotent": True},
    )
    def update_campaign(
        campaign_id: CampaignId,
        name: Annotated[str | None, Field(description="New name.")] = None,
        offer_id: Annotated[int | None, Field(description="Another offer id.")] = None,
        countries: Annotated[list[str] | None, Field(description="Countries.")] = None,
        sectors: Annotated[list[str] | None, Field(description="Sectors.")] = None,
        company_size: Annotated[str | None, Field(description="Company size.")] = None,
        channels: Annotated[list[str] | None, Field(description="email, linkedin.")] = None,
        designations: Annotated[
            list[str | dict[str, Any]] | None, Field(description="Template keys or groups.")
        ] = None,
        seniority_min: Annotated[str | None, Field(description="Minimum seniority.")] = None,
        exclusions: Annotated[
            dict[str, Any] | None, Field(description="Exclusions to merge.")
        ] = None,
        locale: Annotated[str | None, Field(description="Locale, e.g. en-GB.")] = None,
        budget: Annotated[dict[str, float] | None, Field(description="Credit budgets.")] = None,
        status: Annotated[
            Literal["draft", "active", "archived"] | None, Field(description="Status.")
        ] = None,
        search_mode: Annotated[
            Literal["api", "ai", "auto"] | None, Field(description="Search mode.")
        ] = None,
        invite_note: Annotated[
            bool | None, Field(description="Draft LinkedIn invite notes.")
        ] = None,
        allow_risky_emails: Annotated[
            bool | None, Field(description="Allow risky addresses.")
        ] = None,
        allow_guess_emails: Annotated[
            bool | None, Field(description="Allow unverified guesses.")
        ] = None,
        max_people: Annotated[
            int | None, Field(description="n: confirmed people to keep; 0 removes the limit.")
        ] = None,
        max_companies: Annotated[
            int | None, Field(description="c: companies to search; 0 removes the limit.")
        ] = None,
        max_per_company: Annotated[
            int | None, Field(description="p: people per company; 0 removes the limit.")
        ] = None,
    ) -> dict[str, Any]:
        limits = {
            key: (None if value == 0 else value)
            for key, value in (
                ("max_people", max_people), ("max_companies", max_companies),
                ("max_per_company", max_per_company),
            )
            if value is not None
        }  # fmt: skip
        given = {
            "name": name, "offer_id": offer_id, "countries": countries, "sectors": sectors,
            "company_size": company_size, "channels": channels, "designations": designations,
            "seniority_min": seniority_min, "exclusions": exclusions, "locale": locale,
            "budget": budget, "status": status, "search_mode": search_mode,
            "invite_note": invite_note, "allow_risky_emails": allow_risky_emails,
            "allow_guess_emails": allow_guess_emails,
        }  # fmt: skip
        return campaign_svc.update_campaign(
            campaign_id, **{k: v for k, v in given.items() if v is not None}, **limits
        )

    @tool(
        "Get a campaign",
        """Returns one campaign with its settings and counts of companies, people, emails and
        messages by status. Free, changes nothing.""",
        READ,
    )
    def get_campaign(campaign_id: CampaignId) -> dict[str, Any]:
        return campaign_svc.get_campaign(campaign_id)

    @tool(
        "List campaigns",
        """Lists campaigns, newest first, with name, project, status, countries, channels and
        designation labels; pass project_id to list one project's campaigns. Free, changes
        nothing.""",
        READ,
    )
    def list_campaigns(
        include_archived: Annotated[
            bool, Field(description="Also list archived campaigns.")
        ] = False,
        project_id: Annotated[
            int | None, Field(description="Only this project's campaigns.")
        ] = None,
    ) -> dict[str, Any]:
        return {"campaigns": campaign_svc.list_campaigns(include_archived, project_id)}

    # ---- 13-16: companies ---------------------------------------------------------------------

    @tool(
        "Search the web",
        """Runs a Google-style search (title, link, snippet) for research, for example "largest
        logistics companies in Singapore". Results are cached for 30 days; a new search costs
        one search credit, so tell the user first. Pass campaign_id so it counts against the
        campaign's search budget; when the budget is reached you get budget_reached unless
        confirm is true. Without a search key you get needs_key (offer a free Serper key or AI
        search mode).""",
        SPEND,
    )
    def search_web(
        query: Annotated[str, Field(description="The search query.")],
        country: Annotated[str | None, Field(description="Country name or ISO code.")] = None,
        num: Annotated[int, Field(description="Results wanted, 1 to 10.")] = 10,
        campaign_id: Annotated[
            int | None, Field(description="Count against this campaign.")
        ] = None,
        confirm: Confirm = False,
    ) -> dict[str, Any]:
        return {
            "results": companies_svc.search_web(
                query, country, num, campaign_id=campaign_id, confirm=confirm
            )
        }

    @tool(
        "Add companies",
        """Adds up to 200 candidate companies to a campaign: cleans names and domains, removes
        duplicates, marks excluded or competitor companies, and checks each domain (DNS and
        homepage). Each company: name (required), aliases (other spellings, as on LinkedIn),
        domain, sector, size_note, source (ai_knowledge, search or user), notes, country.
        Only add companies you are sure exist; never invent one. Read domain_failures and
        alias_warnings in the reply and tell the user. Free.""",
        WRITE | {"open_world": True},
    )
    def add_companies(
        campaign_id: CampaignId,
        companies: Annotated[list[dict[str, Any]], Field(description="The companies to add.")],
    ) -> dict[str, Any]:
        return companies_svc.add_companies(campaign_id, companies)

    @tool(
        "List companies",
        """Lists a campaign's companies a page at a time with their status (candidate,
        approved, excluded), domain check and notes, plus counts. Free, changes nothing.""",
        READ,
    )
    def list_companies(
        campaign_id: CampaignId,
        status: Annotated[
            Literal["candidate", "approved", "excluded"] | None,
            Field(description="Only companies with this status."),
        ] = None,
        page: Page = 1,
        page_size: PageSize = 50,
    ) -> dict[str, Any]:
        return companies_svc.list_companies(campaign_id, status, page, page_size)

    @tool(
        "Set company status",
        """Approves, excludes or resets companies by id. Only approved companies are searched
        for people. reason is added to the company notes. The reply lists ids not found and
        approved companies whose domain check failed. Free.""",
        WRITE | {"idempotent": True},
    )
    def set_company_status(
        campaign_id: CampaignId,
        ids: Ids,
        status: Annotated[
            Literal["candidate", "approved", "excluded"], Field(description="The new status.")
        ],
        reason: Annotated[str, Field(description="Why, in a few words.")] = "",
    ) -> dict[str, Any]:
        return companies_svc.set_company_status(campaign_id, ids, status, reason)

    @tool(
        "Approve clean companies",
        """Approves every candidate company whose domain is verified (or that has no domain)
        and that has no warning. Others are skipped and listed with the reason. Show the user
        the company list before or after, and let them remove any. Free.""",
        WRITE | {"idempotent": True},
    )
    def approve_companies(campaign_id: CampaignId) -> dict[str, Any]:
        return companies_svc.approve_all(campaign_id)

    # ---- 17-20: people ------------------------------------------------------------------------

    @tool(
        "Estimate the people search",
        """Says how many searches the people search needs (approved companies x designation
        groups), how many are already cached (free), the credits needed and the campaign budget
        left. Call it before find_people and tell the user the cost in plain words. Free,
        changes nothing.""",
        READ,
    )
    def estimate_people_search(
        campaign_id: CampaignId,
        company_ids: Annotated[list[int] | None, Field(description="Only these companies.")] = None,
    ) -> dict[str, Any]:
        return people.estimate_people_search(campaign_id, company_ids)

    @tool(
        "Find people",
        """Searches Google (through the user's search key) for people at the approved companies
        and stores confirmed leads and a review list with reasons. Spends one search credit per
        uncached search: call estimate_people_search first, tell the user the cost and pass
        confirm=true only after a clear yes. Works in chunks of about 30 searches: while the
        reply says done is false, call again with next_cursor. Replies needs_key,
        quota_exhausted and budget_reached explain what to do (add a key, raise the budget, or
        use AI search mode). In AI search mode it replies use_ai_search; use get_search_tasks
        instead.""",
        SPEND,
    )
    def find_people(
        campaign_id: CampaignId,
        company_ids: Annotated[list[int] | None, Field(description="Only these companies.")] = None,
        confirm: Confirm = False,
        cursor: Cursor = None,
    ) -> dict[str, Any]:
        return people.find_people(campaign_id, company_ids, confirm, cursor)

    @tool(
        "List people",
        """Lists a campaign's people a page at a time with title, designation, company,
        LinkedIn link, evidence origin, status and reasons, plus counts by status. Filters:
        status (confirmed, review, rejected, removed), designation label, company_id,
        has_email. Show at most 20 rows to the user. Free, changes nothing.""",
        READ,
    )
    def list_people(
        campaign_id: CampaignId,
        status: Annotated[
            Literal["confirmed", "review", "rejected", "removed"] | None,
            Field(description="Only people with this status."),
        ] = None,
        designation: Annotated[str | None, Field(description="Designation group label.")] = None,
        company_id: Annotated[int | None, Field(description="Only this company.")] = None,
        has_email: Annotated[bool | None, Field(description="Only with/without an email.")] = None,
        page: Page = 1,
        page_size: PageSize = 50,
    ) -> dict[str, Any]:
        return people.list_people(
            campaign_id, status, designation, company_id, has_email, page, page_size
        )

    @tool(
        "Set person status",
        """Moves people between confirmed, review, rejected and removed. Promoting someone from
        review to confirmed needs a reason, and only when the stored evidence really shows they
        fit; people who fail the excluded-company or do-not-contact rules cannot be promoted.
        Free.""",
        WRITE | {"idempotent": True},
    )
    def set_person_status(
        campaign_id: CampaignId,
        ids: Ids,
        status: Annotated[
            Literal["confirmed", "review", "rejected", "removed"],
            Field(description="The new status."),
        ],
        reason: Annotated[str, Field(description="Why (required to confirm by hand).")] = "",
    ) -> dict[str, Any]:
        return people.set_person_status(campaign_id, ids, status, reason)

    @tool(
        "Re-check people",
        """Runs the current confirmation rules again over the stored evidence of every
        confirmed or review person. Call it after update_campaign changed countries,
        designations or seniority. People changed by hand are left alone. Free (no searches).""",
        WRITE | {"idempotent": True},
    )
    def recheck_people(campaign_id: CampaignId) -> dict[str, Any]:
        return people.recheck_people(campaign_id)

    # ---- 21-22: emails ------------------------------------------------------------------------

    @tool(
        "Estimate the email search",
        """Says how many people and domains an email search covers and the credits it would use
        (Hunter pattern lookups, one verifier check per person) against the campaign budget.
        Call it before find_emails and tell the user the cost. Free, changes nothing.""",
        READ,
    )
    def estimate_email_search(
        campaign_id: CampaignId,
        person_ids: Annotated[list[int] | None, Field(description="Only these people.")] = None,
    ) -> dict[str, Any]:
        return emails.estimate_email_search(campaign_id, person_ids)

    @tool(
        "Find emails",
        """Finds and verifies one work email per confirmed person (company pattern, then a
        verifier such as Reoon, ZeroBounce or Hunter). Spends verification credits: call
        estimate_email_search first, tell the user the cost and pass confirm=true only after a
        clear yes. Works in chunks of 25 people: while done is false call again with
        next_cursor. Statuses: verified, risky, guess (not verified, never sent unless the user
        allows it), invalid. Relay needs_confirmation, budget_reached, provider_issues and
        verifier_needs_key replies to the user.""",
        SPEND,
    )
    def find_emails(
        campaign_id: CampaignId,
        person_ids: Annotated[list[int] | None, Field(description="Only these people.")] = None,
        confirm: Confirm = False,
        cursor: Cursor = None,
    ) -> dict[str, Any]:
        return emails.find_emails(campaign_id, person_ids, confirm, cursor)

    # ---- 23-24: drafting ----------------------------------------------------------------------

    @tool(
        "Get a drafting batch",
        """Returns people who need a message on one channel (email, linkedin_invite or
        linkedin_message), each with cleaned evidence, flags and (when redrafting) the earlier
        draft and its issues, plus the offer profile, the channel limits and, on the first call,
        the writing guide. Write one message per person using only that data, then call
        save_drafts. Loop with next_cursor until done. Free, changes nothing.""",
        READ,
        keep=("guide",),
    )
    def get_drafting_batch(
        campaign_id: CampaignId,
        channel: Annotated[str, Field(description="email, linkedin_invite or linkedin_message.")],
        batch_size: Annotated[int, Field(description="People per batch.")] = drafting.DEFAULT_BATCH,
        cursor: Cursor = None,
    ) -> dict[str, Any]:
        return drafting.get_drafting_batch(campaign_id, channel, batch_size, cursor)

    @tool(
        "Save drafts",
        """Stores up to 50 drafts: [{person_id, channel, body, subject (email only)}]. One
        message exists per person per channel; saving again replaces the text. Each draft is
        checked at once (greeting, length, placeholders, forbidden claims, numbers, subject).
        The reply lists qa_pass, qa_fail (with the issues to fix: rewrite and save only those
        again) and not_saved (cannot be fixed by rewriting; skip those people). Free.""",
        WRITE,
    )
    def save_drafts(
        campaign_id: CampaignId,
        drafts: Annotated[list[dict[str, Any]], Field(description="The drafts to store.")],
    ) -> dict[str, Any]:
        return drafting.save_drafts(campaign_id, drafts)

    # ---- 25-28: QA and review -----------------------------------------------------------------

    @tool(
        "Run the quality check",
        """Re-checks every lead (L01-L25) and every message (M01-M16) of a campaign and stores
        the issues. Returns counts per code, the top 20 issues and how much is export-ready.
        Blocking issues keep a lead or message out of exports and sending. Run it after saving
        drafts and again after fixes. Free.""",
        WRITE | {"idempotent": True},
    )
    def run_qa(campaign_id: CampaignId) -> dict[str, Any]:
        return review.run_qa(campaign_id)

    @tool(
        "List QA issues",
        """Lists the issues stored by the last run_qa a page at a time, blocks first. Filter by
        severity (block or warn) and code (for example L04, M06). Free, changes nothing.""",
        READ,
    )
    def list_qa_issues(
        campaign_id: CampaignId,
        severity: Annotated[Literal["block", "warn"] | None, Field(description="Severity.")] = None,
        code: Annotated[str | None, Field(description="Issue code, e.g. M06.")] = None,
        page: Page = 1,
        page_size: PageSize = review.PAGE_SIZE,
    ) -> dict[str, Any]:
        return review.list_qa_issues(campaign_id, severity, code, page, page_size)

    @tool(
        "Get a review batch",
        """Returns messages waiting for YOUR review with the full lead evidence, the message,
        its warnings, the offer's claim lists and a checklist. Read every message against its
        evidence, then call record_review. Loop with next_cursor until done, then start a fresh
        pass without a cursor because fixed messages return to the queue. Free, changes
        nothing.""",
        READ,
        keep=("instructions",),
    )
    def get_review_batch(
        campaign_id: CampaignId,
        batch_size: Annotated[int, Field(description="Messages per batch.")] = 15,
        cursor: Cursor = None,
    ) -> dict[str, Any]:
        return review.get_review_batch(campaign_id, batch_size, cursor)

    @tool(
        "Record review verdicts",
        """Records your verdict per message: [{message_id, verdict: pass|fix|reject, notes,
        corrected_body (fix), corrected_subject (fix, email)}]. pass only after you read the
        message and found it accurate, relevant, allowed and respectful; fix needs the
        corrected text; reject needs notes and removes the person. A bad entry is reported on
        its own and the others are still recorded. Free.""",
        WRITE,
    )
    def record_review(
        campaign_id: CampaignId,
        results: Annotated[list[dict[str, Any]], Field(description="One verdict per message.")],
    ) -> dict[str, Any]:
        return review.record_review(campaign_id, results)

    @tool(
        "Check what may leave",
        """Shows what may be exported (purpose=export) or sent (purpose=send): counts, why the
        rest is held back (count per QA code) and up to 50 blocked messages with reasons. Free,
        changes nothing.""",
        READ,
    )
    def export_gate(
        campaign_id: CampaignId,
        purpose: Annotated[
            Literal["export", "send"], Field(description="export or send.")
        ] = "export",
    ) -> dict[str, Any]:
        return review.export_gate(campaign_id, purpose)

    # ---- 29-30: delivery ----------------------------------------------------------------------

    @tool(
        "Export to CSV",
        """Writes CSV files into the campaign's export folder: the leads file (format generic,
        lemlist or instantly), qa_report.csv and removed.csv. include: approved (default; only
        messages you passed), qa_pass or all_confirmed. Returns the file paths, counts,
        blocked_by_code, evidence_origin_counts, warnings (held-back messages, guess addresses,
        incomplete rows; tell the user) and next_steps text to read to them. Free.""",
        WRITE,
    )
    def export_campaign(
        campaign_id: CampaignId,
        format: Annotated[
            Literal["generic", "lemlist", "instantly"], Field(description="CSV layout.")
        ] = "generic",
        include: Annotated[
            Literal["approved", "qa_pass", "all_confirmed"], Field(description="Who to include.")
        ] = "approved",
    ) -> dict[str, Any]:
        return export.export_campaign(campaign_id, format, include)

    @tool(
        "List lemlist campaigns",
        """Lists the user's lemlist campaigns (id, name, status) so one can be chosen for
        push_to_lemlist. Needs the lemlist key (needs_key otherwise). Free.""",
        READ | {"open_world": True},
    )
    def list_lemlist_campaigns() -> dict[str, Any]:
        return {"campaigns": lemlist.list_lemlist_campaigns()}

    @tool(
        "Push leads to lemlist",
        """Creates the campaign's exportable leads in an EXISTING lemlist campaign with custom
        variables (messages, greeting, designation, ...). Always run it first as a dry run
        (dry_run=true, the default): it makes no call and shows counts and a preview. Only after
        the user clearly agrees call it with dry_run=false and confirm=true; it works in chunks
        of 25 (call again with next_cursor until done). Leads without an email are skipped
        (relay no_email_hint). Leads with an address but a missing message are held back
        (relay messages_incomplete_hint); allow_incomplete=true pushes them anyway, only if the
        user asks. Relay guess_emails and warnings. This cannot be undone from here.""",
        OUTWARD,
    )
    def push_to_lemlist(
        campaign_id: CampaignId,
        lemlist_campaign_id: Annotated[
            str, Field(description="lemlist campaign id from list_lemlist_campaigns (cam_...).")
        ],
        dry_run: Annotated[bool, Field(description="True = preview only (default).")] = True,
        confirm: Confirm = False,
        cursor: Cursor = None,
        allow_incomplete: Annotated[
            bool, Field(description="Also push leads with a missing message (only if asked).")
        ] = False,
    ) -> dict[str, Any]:
        return lemlist.push_to_lemlist(
            campaign_id, lemlist_campaign_id, dry_run, confirm, cursor,
            allow_incomplete=allow_incomplete,
        )  # fmt: skip

    # ---- 31-36: sending, mailbox, analytics, suppression, dashboard ----------------

    @tool(
        "Check send readiness",
        """Checks whether the campaign can send email from the user's own mailbox. Returns a
        checklist where every item is ok or missing with how to fix it: mailbox set up and the
        SMTP/IMAP log-in works, sender name and company, postal address, locale, a recorded
        compliance acknowledgement for EVERY target country (rules lists each country's summary,
        policy and required elements), approved emails that pass the send check, do-not-contact
        list, kill switch, bounce rate and today's cap (with warm-up). Also shows the send queue
        and whether the background worker is running. Logs in to the mailbox but sends
        nothing. Free.""",
        READ | {"open_world": True},
    )
    def check_send_readiness(campaign_id: CampaignId) -> dict[str, Any]:
        return sending.check_send_readiness(campaign_id)

    @tool(
        "Record compliance acknowledgement",
        """Records that the user has read a country's cold-email rule and accepts it for this
        campaign. Show the user that country's rules entry from check_send_readiness first
        (summary, policy, required elements; it is not legal advice) and call this only after
        they agree. For a country whose policy is block_without_consent, consent_basis is
        required: the user's own statement of the basis on which these people may be emailed
        (guessed or scraped addresses do not qualify). Without it the acknowledgement does not
        count. Never invent a consent_basis. Free.""",
        WRITE,
    )
    def acknowledge_compliance(
        campaign_id: CampaignId,
        country: Annotated[str, Field(description="Two-letter country code, e.g. SG.")],
        consent_basis: Annotated[
            str, Field(description="The user's statement of their basis; only if they gave one.")
        ] = "",
    ) -> dict[str, Any]:
        return sending.acknowledge_compliance(campaign_id, country, consent_basis)

    @tool(
        "Send emails",
        """Sends the campaign's approved emails from the user's own mailbox. ALWAYS run a dry run
        first (dry_run=true, the default): it makes no connection and shows each email exactly as
        it would be sent (subject label, footer with postal address and opt-out line included),
        what was skipped and why, and the cap. Show the user the emails. Only after a clear yes
        call with dry_run=false and confirm=true: the emails are QUEUED and a background worker
        sends them one at a time with a gap of 1-3 minutes, within the daily cap and outside the
        recipient's quiet hours, so the reply comes at once and most emails go out later. It
        stops by itself on a high bounce rate or repeated errors. Needs a ready
        check_send_readiness. Cannot be undone once an email is sent.""",
        OUTWARD,
    )
    def send_emails(
        campaign_id: CampaignId,
        limit: Annotated[int, Field(description="Most emails to queue in this call (1-50).")] = 10,
        dry_run: Annotated[bool, Field(description="True = preview only (default).")] = True,
        confirm: Confirm = False,
    ) -> dict[str, Any]:
        return sending.send_emails(campaign_id, limit, dry_run, confirm)

    @tool(
        "Sync the mailbox",
        """Reads the user's mailbox (read-only: nothing is deleted, moved or marked read) for
        replies, bounces and unsubscribes to the emails this tool sent, and records them. A
        bounce marks the address invalid and adds it to the do-not-contact list; an unsubscribe
        request adds the sender to the list and cancels their queued emails; automatic
        out-of-office answers are kept apart and do not count as replies. Nothing is answered
        automatically. Handles up to 60 messages per call: while done is false call it again.
        Tell the user plainly who replied (highlights) and call get_analytics for the numbers.
        since_days only matters the first time. Needs the mailbox set up (see
        check_send_readiness). Free.""",
        WRITE | {"open_world": True},
    )
    def sync_mailbox(
        since_days: Annotated[
            int, Field(description="First sync only: how many days back to read (1-365).")
        ] = 30,
    ) -> dict[str, Any]:
        return mailbox.sync_mailbox(since_days)

    @tool(
        "Get analytics",
        """Sent, bounced, delivered, replied and unsubscribed counts and rates for the emails
        sent from this tool, optionally by designation, sector, company, country or day, plus
        a per-day series and the local lemlist / LinkedIn counts. Out-of-office answers are
        not replies. A group with fewer than 20 delivered emails is flagged small_sample: say
        "too few to judge" and do not draw conclusions from it. The numbers are as fresh as
        the last sync_mailbox: call that first. LinkedIn results live in lemlist. Free.""",
        READ,
    )
    def get_analytics(
        campaign_id: Annotated[
            int | None, Field(description="Campaign number; leave out for all campaigns.")
        ] = None,
        group_by: Annotated[
            Literal["designation", "sector", "company", "country", "day"] | None,
            Field(description="designation, sector, company, country or day."),
        ] = None,
    ) -> dict[str, Any]:
        return analytics.get_analytics(campaign_id, group_by)

    @tool(
        "Add to do-not-contact list",
        """Adds emails, domains or LinkedIn profile links to the do-not-contact list that every
        check and the sender respect (a listed domain also covers its sub-domains). Use it when
        the user or a lead says not to contact someone. Values of the wrong kind are reported
        as invalid. You can only add: taking someone off the list is done by the user with the
        `leadgen suppression remove` command. Free.""",
        WRITE,
    )
    def add_suppression(
        values: Annotated[
            list[str], Field(description="Emails, domains or LinkedIn profile links (up to 500).")
        ],
        kind: Annotated[
            Literal["email", "domain", "linkedin"], Field(description="What the values are.")
        ],
        reason: Annotated[str, Field(description="Why, in a few words.")] = "",
    ) -> dict[str, Any]:
        return suppression.add(values, kind, reason)

    @tool(
        "Open the dashboard",
        """Starts the local dashboard page (leads, messages, API keys, export, analytics) if it is
        not running and returns its address (http://127.0.0.1:8765/ by default). Give the user
        the address; the page only opens on their own computer. Recommend it for browsing more
        than 20 rows and for pasting API keys safely. Changes nothing in the campaign.""",
        {"idempotent": True},
    )
    def open_dashboard() -> dict[str, Any]:
        return dashboard_ctl.ensure_running()

    # ---- 37-40: AI search mode and spot check -------------------------------------------------

    @tool(
        "Get search tasks",
        """AI search mode (free, no search key): returns up to 10 search tasks for you to run
        with YOUR OWN web search tool (kind people, email or company), plus the instructions.
        Run each query unchanged, then call submit_search_results. Report progress after each
        batch and continue with next_cursor until remaining is 0. If the reply has
        cache_pending above 0, some cached searches are still waiting to be loaded: call
        get_search_tasks again (with next_cursor if you have one, otherwise without) until it is 0,
        even when tasks is empty; each call loads one more chunk. Read
        get_workflow_guide section ai_search first. Check you really have a web search tool in this
        conversation; if not, tell the user how to switch it on or to add a search key.""",
        WRITE,
        keep=("instructions",),
    )
    def get_search_tasks(
        campaign_id: CampaignId,
        kind: Annotated[
            Literal["people", "email", "company"], Field(description="Kind of search task.")
        ] = "people",
        batch_size: Annotated[
            int, Field(description="Tasks per batch (10).")
        ] = ai_search.BATCH_DEFAULT,
        cursor: Cursor = None,
    ) -> dict[str, Any]:
        return ai_search.get_search_tasks(campaign_id, kind, batch_size, cursor)

    @tool(
        "Submit search results",
        """AI search mode: hands in the results of ONE task. results is [{title, url, snippet}]
        copied VERBATIM, character for character, from your search tool's output. Never build,
        guess, shorten or fix a URL, never add a result the tool did not return, never write a
        description in your own words. Submit an empty list when nothing relevant was found.
        tool_name is the name of your search tool. The tool applies the same confirmation rules
        as for paid searches plus checks that the link fits the person's name; results that
        fail go to review, and a submission with no valid link is refused (not_verbatim) and
        the task stays open. Free.""",
        WRITE,
    )
    def submit_search_results(
        task_id: Annotated[int, Field(description="task_id from get_search_tasks.")],
        results: Annotated[
            list[dict[str, Any]], Field(description="Verbatim [{title, url, snippet}] results.")
        ],
        tool_name: Annotated[str, Field(description="Name of the search tool you used.")],
    ) -> dict[str, Any]:
        return ai_search.submit_search_results(task_id, results, tool_name)

    @tool(
        "Skip a search task",
        """AI search mode: closes an open task without results, when you cannot run it or the
        user says to skip it. A reason is required. Free.""",
        WRITE,
    )
    def skip_task(
        task_id: Annotated[int, Field(description="task_id from get_search_tasks.")],
        reason: Annotated[str, Field(description="Why it is skipped.")],
    ) -> dict[str, Any]:
        return ai_search.skip_task(task_id, reason)

    @tool(
        "Get a spot check",
        """AI search mode: returns a random sample of the AI-search leads (5 people or 5%,
        whichever is larger, at most 15) with name, title, company and LinkedIn link. Ask the
        user to open each link themselves and say "ok" or "wrong" (right person, company and
        role); do not open the links yourself. AI-search leads cannot be exported until the
        check passes. resample=true draws a new sample after many leads were added.""",
        WRITE,
    )
    def get_spot_check(
        campaign_id: CampaignId,
        resample: Annotated[bool, Field(description="Draw a new sample.")] = False,
    ) -> dict[str, Any]:
        return spot_check.get_spot_check(campaign_id, resample)

    @tool(
        "Record spot-check answers",
        """Stores the user's answers: [{person_id, verdict: ok|wrong, note}]. A wrong person
        moves to review; two or more wrong in one sample keep every AI-search lead blocked
        until a second sample passes. Tell the user plainly when that happens. Free.""",
        WRITE,
    )
    def record_spot_check(
        campaign_id: CampaignId,
        results: Annotated[list[dict[str, Any]], Field(description="One answer per person.")],
    ) -> dict[str, Any]:
        return spot_check.record_spot_check(campaign_id, results)

    return server


# --- transports ---------------------------------------------------------------------------------


def run_stdio() -> None:
    """Serve over stdio (Claude Desktop, Claude Code). Logs go to stderr and the log file."""
    build_server().run("stdio")


def http_app(
    server: MCPServer,
    secret: str,
    host: str = "127.0.0.1",
    allowed_hosts: Sequence[str] = (),
    json_response: bool = False,
) -> Any:
    """The ASGI app for Streamable HTTP at `/mcp/<secret>`; every other path is a 404.

    `json_response=True` answers each call with plain JSON instead of an event stream (needed
    behind Cloudflare quick tunnels, which do not support server-sent events).

    On a loopback `host`, requests must carry a Host header for localhost (DNS-rebinding
    guard) plus any `allowed_hosts` given (for example a tunnel's host name).
    """
    if not _SECRET_RE.fullmatch(secret):
        raise ValueError("The MCP secret must be at least 16 letters, digits, - or _.")
    security = None
    if host in LOOPBACK:
        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *allowed_hosts]
        origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
        origins += [f"https://{h}" for h in allowed_hosts]
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
        )
    return server.streamable_http_app(
        streamable_http_path=f"/mcp/{secret}",
        host=host,
        transport_security=security,
        json_response=json_response,
    )


def run_http(
    host: str = "127.0.0.1", port: int | None = None, allowed_hosts: Sequence[str] = ()
) -> None:
    """Serve over Streamable HTTP at `/mcp/<secret>` (secret from settings, made if empty).

    Binds to localhost unless `host` is given. The address (with the secret) is printed once to
    stderr for the user; it is never written to a log.
    """
    import uvicorn

    secret = config.ensure_mcp_secret()
    if port is None:
        port = config.load_settings().mcp_http.port or DEFAULT_HTTP_PORT
    app = http_app(build_server(remote=True), secret, host, allowed_hosts, json_response=True)
    shown = "localhost" if host in LOOPBACK else host
    print(f"Lead Generator MCP server: http://{shown}:{port}/mcp/{secret}", file=sys.stderr)
    if host not in LOOPBACK:
        print(
            "Warning: this server is reachable from other computers. The secret in the address "
            "is the only protection.", file=sys.stderr,
        )  # fmt: skip
    # No access log: it would write the secret path into the console and logs.
    uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    ).run()


def tool_summaries(server: MCPServer | None = None) -> list[tuple[str, str]]:
    """`(name, first line of the description)` for every tool, in registration order."""
    import anyio

    target = server or build_server(init_db=False)
    tools = anyio.run(target.list_tools)
    return [(t.name, (t.description or "").split(". ")[0].rstrip(".") + ".") for t in tools]
