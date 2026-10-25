"""The dashboard web app: a thin JSON API over the services plus the static page.

No business logic lives here (PLAN section 3). Every route calls one service function and turns
its errors into the `{"ok": false, "error": {...}}` shape. The app only listens on the loopback
address; every request must carry a loopback Host header (against DNS rebinding) and every
change (POST/PUT/PATCH/DELETE) must carry `X-LeadGen: 1` and a same-origin `Origin`.
API keys are accepted but never returned; error replies never echo the request body.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from leadgen import __version__, db
from leadgen.core import config, countries
from leadgen.providers.base import ProviderError
from leadgen.services import (
    ai_setup,
    analytics,
    browse,
    chat,
    contacts_import,
    dashboard_ctl,
    designations,
    export,
    lemlist,
    mailbox,
    mailbox_connect,
    offer_files,
    people,
    preferences,
    projects,
    review,
    sending,
    seniority,
    suppression,
)
from leadgen.services import campaign as campaign_svc
from leadgen.services import companies as companies_svc
from leadgen.services import offer as offer_svc
from leadgen.services import status as status_svc

log = logging.getLogger("leadgen.dashboard")

STATIC_DIR = Path(__file__).parent / "static"
CSRF_HEADER = "x-leadgen"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")
QUIET_LOGGERS = ("httpx", "httpcore", "httpx2", "httpcore2")  # some request URLs carry a key
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
STATUS_BY_CODE = {
    "not_found": 404, "csrf": 403, "bad_host": 400, "internal_error": 500,
    "turn_running": 409, "ai_not_ready": 409, "sending_in_progress": 409,
}
UPLOAD_LIMIT = 21 * 1024 * 1024  # a little over the 20 MB the readers accept
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}  # fmt: skip


# --- request bodies (unknown fields are refused; the body is never echoed back) ------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyBody(_Body):
    key: str = Field(max_length=1000)


class StatusBody(_Body):
    ids: list[int] = Field(max_length=500)
    status: str = Field(max_length=20)
    reason: str = Field(default="", max_length=300)


class ExportBody(_Body):
    format: Literal["generic", "lemlist", "instantly"] = "generic"
    include: Literal["approved", "qa_pass", "all_confirmed"] = "approved"


class SourceBody(_Body):
    """The project's product or service: a website (one page or the whole site) or pasted text.

    A website read is done in steps: the first call has `url`; while the reply says `done: false`
    the page calls again with the same `offer_id` and the `cursor` of the reply."""

    url: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=300_000)
    name: str | None = Field(default=None, max_length=200)
    scope: Literal["page", "site"] = "site"
    max_pages: int = Field(default=15, ge=1, le=50)
    offer_id: int | None = Field(default=None, ge=1)
    cursor: str | None = Field(default=None, max_length=40)


class ProjectBody(_Body):
    name: str = Field(max_length=200)
    description: str = Field(default="", max_length=1000)
    cooldown_days: int = Field(default=projects.DEFAULT_COOLDOWN_DAYS, ge=0, le=3650)
    email_policy: Literal["verified_only", "allow_risky"] = "verified_only"


class ProjectChangeBody(_Body):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    cooldown_days: int | None = Field(default=None, ge=0, le=3650)
    email_policy: Literal["verified_only", "allow_risky"] | None = None
    sender_name: str | None = Field(default=None, max_length=120)
    sender_title: str | None = Field(default=None, max_length=120)
    sender_company: str | None = Field(default=None, max_length=120)


class CampaignBody(_Body):
    name: str = Field(max_length=200)
    project_id: int = Field(ge=1)
    countries: list[str] = Field(max_length=40)
    sectors: list[str] = Field(default=[], max_length=40)
    channels: list[Literal["email", "linkedin"]] = Field(max_length=2)
    designations: list[str] = Field(max_length=8)
    seniority_min: str = Field(default="manager", max_length=20)
    max_people: int | None = Field(default=None, ge=1, le=10_000)
    max_companies: int | None = Field(default=None, ge=1, le=10_000)
    max_per_company: int | None = Field(default=None, ge=1, le=10_000)
    take_reserve_from: int | None = Field(default=None, ge=1)


class MoveReserveBody(_Body):
    from_campaign_id: int = Field(ge=1)
    max_people: int | None = Field(default=None, ge=1, le=10_000)
    max_per_company: int | None = Field(default=None, ge=1, le=10_000)


class SuppressionBody(_Body):
    kind: Literal["email", "domain", "linkedin"]
    values: list[str] = Field(max_length=200)
    reason: str = Field(default="", max_length=200)


class GmailBody(_Body):
    address: str = Field(max_length=300)
    app_password: str = Field(max_length=300)


class MailboxBody(_Body):
    host: str = Field(max_length=300)
    port: str = Field(max_length=10)
    address: str = Field(max_length=300)
    password: str = Field(max_length=500)
    imap_host: str = Field(default="", max_length=300)


class SettingsBody(_Body):
    sender: dict[str, str] | None = None
    postal_address: str | None = None
    locale: str | None = None
    budgets: dict[str, int] | None = None
    strict_review: bool | None = None


class AiModeBody(_Body):
    mode: Literal["claude_plan", "api_key"]


class AiKeyBody(_Body):
    key: str = Field(max_length=500)


class ChatBody(_Body):
    message: str = ""
    attachments: list[str] = Field(default=[], max_length=10)


class DeleteProjectBody(_Body):
    confirm_name: str = Field(max_length=200)


class SendBody(_Body):
    limit: int = Field(default=10, ge=1, le=50)
    dry_run: bool = True
    confirm: bool = False


class LemlistPushBody(_Body):
    lemlist_campaign_id: str = Field(max_length=100)
    dry_run: bool = True
    confirm: bool = False
    cursor: str | None = Field(default=None, max_length=40)


# --- replies ------------------------------------------------------------------------------------


def _error(code: str, message: str, hint: str = "", status: int | None = None) -> JSONResponse:
    body = {"ok": False, "error": {"code": code, "message": message, "hint": hint}}
    return JSONResponse(body, status_code=status or STATUS_BY_CODE.get(code, 400))


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(data, default=str)), status_code=status)


def _reply(result: Any) -> JSONResponse:
    """Service result to a response: `ok: false` bodies get a 4xx status."""
    if isinstance(result, dict) and result.get("ok") is False:
        code = str((result.get("error") or {}).get("code", ""))
        return _json(result, STATUS_BY_CODE.get(code, 400))
    if isinstance(result, dict):
        return _json(result)
    return _json({"ok": True, "items": result})


def _failure(exc: Exception) -> JSONResponse:
    """The reply for an exception: services give their own, anything else a safe summary."""
    as_response = getattr(exc, "as_response", None)
    if callable(as_response):
        return _reply(as_response())
    if isinstance(exc, ProviderError):
        return _error("provider_" + str(exc.code), exc.message, "Check the key on the Keys page.")
    if isinstance(exc, config.ConfigError):
        return _error(
            "settings_problem", str(exc), "Fix settings.yaml in the Lead Generator folder."
        )
    if isinstance(exc, ValidationError):
        return _error("invalid_value", "Some values are not valid.")
    # Class name and place only: exception text can hold addresses, keys or message bodies.
    where = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
    log.error(
        "dashboard request failed: %s at %s:%s",
        type(exc).__name__,
        Path(where.filename).name if where else "?",
        where.lineno if where else "?",
    )
    return _error(
        "internal_error",
        "Something went wrong inside the dashboard.",
        "Try again; if it keeps happening, check logs/leadgen.log.",
    )


def _host_ok(header: str) -> bool:
    host = header.strip().lower()
    if host.startswith("["):
        host = host.split("]")[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host in ALLOWED_HOSTS


def _csrf_problem(request: Request) -> str:
    """Why a changing request must be refused, or "" when it is fine."""
    if request.headers.get(CSRF_HEADER) != "1":
        return "Missing the X-LeadGen header."
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    if origin is not None and origin.strip().lower() != f"http://{host.strip().lower()}":
        return "The request does not come from this page."
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        return "The request does not come from this page."
    return ""


class _UploadRefused(Exception):
    """An uploaded file that cannot be used; `reply` is the error to send back."""

    def __init__(self, reply: JSONResponse) -> None:
        super().__init__("upload refused")
        self.reply = reply


def _save_upload(
    request: Request, upload: UploadFile, folder: Path, allowed: tuple[str, ...]
) -> Path:
    """Copy an uploaded file into `folder` under its own (cleaned) name and return the path.

    Only the base name is used, so a client cannot choose a folder. The size is limited.
    """
    too_big = _error(
        "file_too_big", "That file is too large (the limit is 20 MB).",
        "Save a smaller copy, or paste the text instead.", status=413,
    )  # fmt: skip
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        declared = 0
    if declared > UPLOAD_LIMIT + 65_536:
        raise _UploadRefused(too_big)
    name = Path((upload.filename or "").replace("\\", "/")).name
    clean = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")[:120]
    suffix = Path(clean).suffix.lower()
    if not clean or suffix not in allowed:
        raise _UploadRefused(
            _error(
                "unsupported_type",
                "That kind of file cannot be used here. Use " + ", ".join(allowed) + ".",
                status=400,
            )
        )
    if Path(clean).stem.upper() in _RESERVED_NAMES:
        clean = "file-" + clean
    target = folder / clean
    written = 0
    with target.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > UPLOAD_LIMIT:
                raise _UploadRefused(too_big)
            out.write(chunk)
    return target


# --- the app ------------------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build the app (and make sure the database exists)."""
    db.init_db()
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    app = FastAPI(
        title="Lead Generator dashboard",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        telemetry={"auto_configure": False, "tracing": False, "metrics": False, "logs": False},
    )

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted(
            {".".join(str(p) for p in err["loc"][1:]) or "value" for err in exc.errors()}
        )
        return _error(
            "invalid_value", f"These fields are not valid: {', '.join(fields)}.", status=422
        )

    @app.middleware("http")
    async def _guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not _host_ok(request.headers.get("host", "")):
            return _error("bad_host", "This page only answers on 127.0.0.1 or localhost.")
        if request.method not in SAFE_METHODS:
            problem = _csrf_problem(request)
            if problem:
                return _error("csrf", problem, "Use the dashboard page itself.", status=403)
        try:
            response = await call_next(request)
        except Exception as exc:  # the one place that turns every failure into a reply
            response = _failure(exc)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    _register_routes(app)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _source_reply(result: dict[str, Any]) -> dict[str, Any]:
    """One step of reading a project's source, shaped for the page: no page text, a count of the
    pages read so far and the `done` / `cursor` pair while a website read is going on."""
    if not result.get("ok"):
        return result
    keep = ("ok", "offer_id", "done", "cursor", "summary", "message", "project_id",
            "replaced_offer_id", "old_offer_deleted")  # fmt: skip
    out = {k: result[k] for k in keep if k in result}
    out.setdefault("done", True)
    if "pages" in result:
        out["pages_read"] = len(result["pages"])
    return out


def _register_routes(app: FastAPI) -> None:
    @app.get("/api/ping")
    def ping() -> JSONResponse:
        return _json({"ok": True, "app": dashboard_ctl.APP_ID, "version": __version__})

    # ---- status, keys ------------------------------------------------------------------------

    @app.get("/api/status")
    def get_status() -> JSONResponse:
        return _reply(status_svc.get_status())

    @app.get("/api/providers")
    def list_providers() -> JSONResponse:
        return _reply({"ok": True, "providers": status_svc.list_providers()})

    @app.post("/api/providers/{provider}/key")
    def save_key(provider: str, body: KeyBody) -> JSONResponse:
        return _reply(status_svc.save_key(provider, body.key))

    @app.delete("/api/providers/{provider}/key")
    def delete_key(provider: str) -> JSONResponse:
        return _reply(status_svc.delete_key(provider))

    @app.post("/api/providers/{provider}/test")
    def test_key(provider: str) -> JSONResponse:
        return _reply(status_svc.probe_key(provider))

    # ---- projects -----------------------------------------------------------------------------

    @app.get("/api/projects")
    def list_projects() -> JSONResponse:
        return _json({"ok": True, "projects": browse.project_cards()})

    @app.post("/api/projects")
    def create_project(body: ProjectBody) -> JSONResponse:
        return _reply(
            {"ok": True, "project": projects.create_project(
                body.name, body.description, body.cooldown_days, None, body.email_policy
            )}
        )  # fmt: skip

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: int) -> JSONResponse:
        return _reply(browse.project_page(project_id))

    @app.patch("/api/projects/{project_id}")
    def change_project(project_id: int, body: ProjectChangeBody) -> JSONResponse:
        return _reply(
            {"ok": True, "project": projects.update_project(
                project_id, **body.model_dump(exclude_none=True)
            )}
        )  # fmt: skip

    @app.delete("/api/projects/{project_id}")
    def remove_project(project_id: int, body: DeleteProjectBody) -> JSONResponse:
        chat.cancel_turn(project_id)
        chat.wait(project_id, 10.0)
        return _reply({"ok": True, **projects.delete_project(project_id, body.confirm_name)})

    @app.get("/api/projects/{project_id}/studio")
    def project_studio(project_id: int) -> JSONResponse:
        progress = status_svc.project_progress(project_id)
        project = projects.get_project(project_id)
        ai = ai_setup.get_status()
        return _json({
            **progress,
            "project": {k: project[k] for k in (
                "id", "name", "sender_name", "sender_title", "sender_company", "cooldown_days",
            )},
            "ai": {"ready": ai["ready"], "mode": ai["mode"]},
            "turn": chat.turn_state(project_id),
        })  # fmt: skip

    @app.get("/api/projects/{project_id}/results")
    def project_results(
        project_id: int, campaign_id: int | None = None, page: int = 1, page_size: int = 25
    ) -> JSONResponse:
        return _reply(browse.studio_results(project_id, campaign_id, page, page_size))

    # ---- project source -----------------------------------------------------------------------

    @app.get("/api/offer-source-help")
    def source_help() -> JSONResponse:
        return _json({"ok": True, "text": offer_svc.SOURCE_HELP})

    @app.post("/api/projects/{project_id}/source")
    def attach_source(project_id: int, body: SourceBody) -> JSONResponse:
        """Attach a website (scope page or site) or pasted text as the project's one source; it
        replaces the old one. One call reads at most about 50 seconds of a website: while the
        reply has `done: false` the page calls again with `offer_id` and `cursor`, so it can show
        progress. Files are attached by `/source-file`."""
        if body.offer_id is not None:
            if not body.cursor:
                return _error("invalid_value", "These fields are not valid: cursor.", status=422)
            if projects.get_project(project_id)["offer_id"] != body.offer_id:
                return _error("not_found", "That website read does not belong to this project.")
            result = offer_svc.ingest_source(offer_id=body.offer_id, cursor=body.cursor)
        else:
            result = offer_svc.ingest_source(
                url=body.url, text=body.text, name=body.name, scope=body.scope,
                max_pages=body.max_pages, project_id=project_id,
            )  # fmt: skip
        return _reply(_source_reply(result))

    @app.post("/api/projects/{project_id}/source-file")
    def attach_source_file(
        request: Request, project_id: int, file: UploadFile = File(...)  # noqa: B008
    ) -> JSONResponse:
        """Attach an uploaded PDF, Word or text file as the project's one source."""
        with tempfile.TemporaryDirectory(prefix="leadgen-upload-") as folder:
            try:
                saved = _save_upload(
                    request, file, Path(folder), tuple(offer_files.SUPPORTED_SUFFIXES)
                )
            except _UploadRefused as refused:
                return refused.reply
            result = offer_svc.ingest_source(file_path=str(saved), project_id=project_id)
        return _reply(_source_reply(result))

    # ---- contacts the owner already has -------------------------------------------------------

    @app.get("/api/contacts-template/{kind}")
    def contacts_template(kind: Literal["csv", "xlsx"]) -> Response:
        path = contacts_import.export_template(contacts_import.template_dir(), kind)
        return FileResponse(path, filename=path.name)

    @app.post("/api/projects/{project_id}/contacts")
    def import_contacts(
        request: Request,
        project_id: int,
        file: UploadFile = File(...),  # noqa: B008
        mode: Literal["exclude", "signal", "both"] = Form("exclude"),  # noqa: B008
        dry_run: bool = Form(True),  # noqa: B008
    ) -> JSONResponse:
        """Read an uploaded contacts file. With `dry_run` nothing is written (a preview with the
        exact counts); the page sends the same file again with `dry_run` off to apply it."""
        with tempfile.TemporaryDirectory(prefix="leadgen-upload-") as folder:
            try:
                saved = _save_upload(request, file, Path(folder), (".csv", ".xlsx"))
            except _UploadRefused as refused:
                return refused.reply
            result = contacts_import.import_contacts(project_id, saved, mode, dry_run)
        result.pop("next_step", None)  # that line is written for the AI, not for this page
        return _reply(result)

    # ---- campaigns, companies -----------------------------------------------------------------

    @app.get("/api/campaigns")
    def list_campaigns(
        include_archived: bool = False, project_id: int | None = None
    ) -> JSONResponse:
        names = {p["id"]: p["name"] for p in projects.list_projects()}
        rows = []
        for short in campaign_svc.list_campaigns(include_archived, project_id):
            full = campaign_svc.get_campaign(short["id"])
            rows.append(
                {
                    **short, "counts": full["counts"], "sectors": full["sectors"],
                    "email_summary": full["email_summary"],
                    "project": names.get(short["project_id"], ""),
                }
            )
        return _json({"ok": True, "campaigns": rows})

    @app.get("/api/campaign-options")
    def campaign_options() -> JSONResponse:
        """The choices of the New campaign form."""
        return _json(
            {
                "ok": True,
                "countries": [{"code": c.iso2, "name": c.name} for c in countries.all_countries()],
                "roles": [
                    {"key": t["key"], "label": t["label"]} for t in designations.list_templates()
                ],
                "seniority": list(seniority.LEVELS),
            }
        )

    @app.post("/api/campaigns")
    def create_campaign(body: CampaignBody) -> JSONResponse:
        limits = {
            "max_people": body.max_people, "max_companies": body.max_companies,
            "max_per_company": body.max_per_company,
        }  # fmt: skip
        return _reply(
            people.create_campaign_taking_reserve(
                body.take_reserve_from,
                name=body.name, project_id=body.project_id, countries=body.countries,
                sectors=body.sectors, channels=body.channels, designations=body.designations,
                seniority_min=body.seniority_min,
                options={k: v for k, v in limits.items() if v is not None},
            )
        )  # fmt: skip

    @app.post("/api/campaigns/{campaign_id}/move-reserve")
    def move_reserve(campaign_id: int, body: MoveReserveBody) -> JSONResponse:
        return _reply(
            people.move_reserve(
                body.from_campaign_id, campaign_id, body.max_per_company, body.max_people
            )
        )

    @app.get("/api/campaigns/{campaign_id}")
    def get_campaign(campaign_id: int) -> JSONResponse:
        return _reply(browse.campaign_overview(campaign_id))

    @app.get("/api/campaigns/{campaign_id}/companies")
    def list_companies(
        campaign_id: int, status: str | None = None, page: int = 1, page_size: int = 50
    ) -> JSONResponse:
        return _reply(companies_svc.list_companies(campaign_id, status or None, page, page_size))

    @app.post("/api/campaigns/{campaign_id}/companies/status")
    def set_company_status(campaign_id: int, body: StatusBody) -> JSONResponse:
        return _reply(
            companies_svc.set_company_status(campaign_id, body.ids, body.status, body.reason)
        )

    # ---- leads --------------------------------------------------------------------------------

    @app.get("/api/campaigns/{campaign_id}/leads")
    def list_leads(
        campaign_id: int,
        status: str | None = None,
        designation: str | None = None,
        company_id: int | None = None,
        email_status: str | None = None,
        qa: str | None = None,
        ai_verdict: str | None = None,
        search: str | None = None,
        known: bool = False,
        page: int = 1,
        page_size: int = 25,
    ) -> JSONResponse:
        return _reply(
            browse.list_leads(
                campaign_id, status=status, designation=designation, company_id=company_id,
                email_status=email_status, qa=qa, ai_verdict=ai_verdict, search=search,
                known_only=known, page=page, page_size=page_size,
            )
        )  # fmt: skip

    @app.get("/api/campaigns/{campaign_id}/lead-filters")
    def lead_filters(campaign_id: int) -> JSONResponse:
        return _reply(browse.lead_filters(campaign_id))

    @app.get("/api/leads/{person_id}")
    def get_lead(person_id: int) -> JSONResponse:
        return _reply(browse.get_person(person_id))

    @app.post("/api/campaigns/{campaign_id}/leads/status")
    def set_lead_status(campaign_id: int, body: StatusBody) -> JSONResponse:
        return _reply(people.set_person_status(campaign_id, body.ids, body.status, body.reason))

    # ---- messages and QA ----------------------------------------------------------------------

    @app.get("/api/campaigns/{campaign_id}/messages")
    def list_messages(
        campaign_id: int,
        channel: str | None = None,
        status: str | None = None,
        ai_review: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> JSONResponse:
        return _reply(
            browse.list_messages(
                campaign_id, channel=channel, status=status, ai_review=ai_review,
                search=search, page=page, page_size=page_size,
            )
        )  # fmt: skip

    @app.get("/api/campaigns/{campaign_id}/qa")
    def list_qa(
        campaign_id: int,
        severity: str | None = None,
        code: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> JSONResponse:
        return _reply(
            review.list_qa_issues(campaign_id, severity or None, code or None, page, page_size)
        )

    @app.post("/api/campaigns/{campaign_id}/qa/run")
    def run_qa(campaign_id: int) -> JSONResponse:
        return _reply(review.run_qa(campaign_id))

    # ---- export -------------------------------------------------------------------------------

    @app.get("/api/campaigns/{campaign_id}/exports")
    def list_exports(campaign_id: int) -> JSONResponse:
        return _reply(browse.list_exports(campaign_id))

    @app.post("/api/campaigns/{campaign_id}/exports")
    def make_export(campaign_id: int, body: ExportBody) -> JSONResponse:
        return _reply(export.export_campaign(campaign_id, body.format, body.include))

    @app.post("/api/campaigns/{campaign_id}/exports/open-folder")
    def open_folder(campaign_id: int) -> JSONResponse:
        return _reply(browse.open_exports_folder(campaign_id))

    @app.post("/api/campaigns/{campaign_id}/send")
    def send_by_email(campaign_id: int, body: SendBody) -> JSONResponse:
        return _reply(sending.send_emails(campaign_id, body.limit, body.dry_run, body.confirm))

    @app.get("/api/lemlist/campaigns")
    def lemlist_campaigns() -> JSONResponse:
        return _reply({"ok": True, "campaigns": lemlist.list_lemlist_campaigns()})

    @app.post("/api/campaigns/{campaign_id}/lemlist")
    def push_lemlist(campaign_id: int, body: LemlistPushBody) -> JSONResponse:
        return _reply(lemlist.push_to_lemlist(
            campaign_id, body.lemlist_campaign_id, body.dry_run, body.confirm, body.cursor))

    # ---- analytics, replies, sending ----------------------------------------------------------

    @app.get("/api/analytics")
    def get_analytics(campaign_id: int | None = None, group_by: str | None = None) -> JSONResponse:
        return _reply(analytics.get_analytics(campaign_id, group_by or None))

    @app.get("/api/replies")
    def recent_replies(campaign_id: int | None = None) -> JSONResponse:
        return _reply(browse.recent_replies(campaign_id))

    @app.get("/api/sending")
    def sending_status(campaign_id: int | None = None) -> JSONResponse:
        return _reply({"ok": True, **sending.queue_status(campaign_id)})

    @app.post("/api/sending/pause")
    def pause_sending() -> JSONResponse:
        sending.pause()
        return _reply({"ok": True, **sending.queue_status()})

    @app.post("/api/sending/resume")
    def resume_sending() -> JSONResponse:
        sending.resume()
        return _reply({"ok": True, **sending.queue_status()})

    @app.post("/api/mailbox/sync")
    def sync_mailbox() -> JSONResponse:
        return _reply(mailbox.sync_mailbox())

    @app.get("/api/mailbox")
    def mailbox_state() -> JSONResponse:
        return _reply({"ok": True, **mailbox_connect.get_state()})

    @app.post("/api/mailbox/gmail")
    def connect_gmail(body: GmailBody) -> JSONResponse:
        return _reply(mailbox_connect.connect_gmail(body.address, body.app_password))

    @app.post("/api/mailbox/other")
    def connect_other(body: MailboxBody) -> JSONResponse:
        return _reply(
            mailbox_connect.connect_other(
                body.host, body.port, body.address, body.password, body.imap_host
            )
        )

    @app.post("/api/mailbox/test")
    def test_mailbox() -> JSONResponse:
        return _reply(mailbox_connect.test_saved())

    @app.get("/api/suppression")
    def list_suppression(kind: str | None = None) -> JSONResponse:
        return _reply({"ok": True, "entries": suppression.list(kind or None)})

    @app.post("/api/suppression")
    def add_suppression(body: SuppressionBody) -> JSONResponse:
        return _reply(suppression.add(body.values, body.kind, body.reason or "added in dashboard"))

    # ---- settings -----------------------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings() -> JSONResponse:
        return _reply(preferences.get_preferences())

    @app.post("/api/settings")
    def save_settings(body: SettingsBody) -> JSONResponse:
        return _reply(preferences.update_preferences(body.model_dump(exclude_none=True)))

    # ---- your AI (Claude Code) ----------------------------------------------------------------

    @app.get("/api/ai")
    def ai_status(refresh: bool = False) -> JSONResponse:
        return _reply(ai_setup.get_status(refresh))

    @app.post("/api/ai/mode")
    def ai_mode(body: AiModeBody) -> JSONResponse:
        preferences.set_ai_mode(body.mode)
        return _reply(ai_setup.get_status())

    @app.post("/api/ai/login")
    def ai_login() -> JSONResponse:
        return _reply(ai_setup.start_login())

    @app.post("/api/ai/api-key")
    def ai_api_key(body: AiKeyBody) -> JSONResponse:
        return _reply(ai_setup.save_api_key(body.key))

    @app.post("/api/ai/test")
    def ai_test() -> JSONResponse:
        return _reply(ai_setup.check_connection())

    # ---- Studio chat --------------------------------------------------------------------------

    @app.get("/api/projects/{project_id}/chat")
    def chat_history(project_id: int) -> JSONResponse:
        projects.get_project(project_id)  # not_found
        ai = ai_setup.get_status()
        return _json({"ok": True, "messages": chat.history(project_id),
                      "turn": chat.turn_state(project_id),
                      "ai": {"ready": ai["ready"], "mode": ai["mode"]}})

    @app.post("/api/projects/{project_id}/chat")
    def chat_send(project_id: int, body: ChatBody) -> JSONResponse:
        return _reply(chat.start_turn(project_id, body.message, body.attachments))

    @app.get("/api/projects/{project_id}/chat/stream")
    def chat_stream(request: Request, project_id: int, turn: str, after: int = 0) -> Response:
        last = request.headers.get("last-event-id", "").strip()
        start = int(last) if last.isdigit() else after
        events = chat.stream_events(project_id, turn, start)  # raises not_found before streaming
        return StreamingResponse(events, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/projects/{project_id}/chat/stop")
    def chat_stop(project_id: int) -> JSONResponse:
        return _reply(chat.cancel_turn(project_id))

    @app.post("/api/projects/{project_id}/chat/upload")
    def chat_upload(
        request: Request, project_id: int, file: UploadFile = File(...)  # noqa: B008
    ) -> JSONResponse:
        projects.get_project(project_id)
        try:
            saved = _save_upload(request, file, chat.uploads_dir(project_id), chat.UPLOAD_SUFFIXES)
        except _UploadRefused as refused:
            return refused.reply
        kind = "contacts" if saved.suffix.lower() in (".csv", ".xlsx") else "product"
        size = saved.stat().st_size
        return _json({"ok": True, "file": {"name": saved.name, "size": size, "kind": kind}})


def serve(port: int | None = None) -> int:
    """Run the dashboard in this process until it is stopped. Returns an exit code."""
    import uvicorn

    settings = config.load_settings()
    host = settings.dashboard.host or "127.0.0.1"
    if host not in dashboard_ctl.LOOPBACK:
        print("The dashboard only runs on this computer. Set dashboard.host to 127.0.0.1.")
        return 1
    chosen = int(port or settings.dashboard.port)
    app = create_app()
    dashboard_ctl.write_pid(chosen)
    print(f"Dashboard running at {dashboard_ctl.url_for(host, chosen)}  (Ctrl+C to stop)")
    try:
        uvicorn.run(app, host=host, port=chosen, log_level="warning", access_log=False)
    finally:
        dashboard_ctl.clear_pid()
    return 0
