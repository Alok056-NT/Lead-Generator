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
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from leadgen import __version__, db
from leadgen.core import config
from leadgen.providers.base import ProviderError
from leadgen.services import (
    analytics,
    browse,
    dashboard_ctl,
    export,
    mailbox,
    people,
    preferences,
    review,
    sending,
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
STATUS_BY_CODE = {"not_found": 404, "csrf": 403, "bad_host": 400, "internal_error": 500}


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
    """The project's product or service: a website (one page or the whole site) or pasted text."""

    url: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=300_000)
    name: str | None = Field(default=None, max_length=200)
    scope: Literal["page", "site"] = "site"
    max_pages: int = Field(default=15, ge=1, le=50)


class SuppressionBody(_Body):
    kind: Literal["email", "domain", "linkedin"]
    values: list[str] = Field(max_length=200)
    reason: str = Field(default="", max_length=200)


class SettingsBody(_Body):
    sender: dict[str, str] | None = None
    postal_address: str | None = None
    locale: str | None = None
    budgets: dict[str, int] | None = None
    strict_review: bool | None = None


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

    # ---- project source -----------------------------------------------------------------------

    @app.get("/api/offer-source-help")
    def source_help() -> JSONResponse:
        return _json({"ok": True, "text": offer_svc.SOURCE_HELP})

    @app.post("/api/projects/{project_id}/source")
    def attach_source(project_id: int, body: SourceBody) -> JSONResponse:
        """Attach a website (scope page or site) or pasted text as the project's one source; it
        replaces the old one. Files are attached by the project page (T26)."""
        result = offer_svc.ingest_source_all(
            url=body.url, text=body.text, name=body.name, scope=body.scope,
            max_pages=body.max_pages, project_id=project_id,
        )  # fmt: skip
        result.pop("text", None)  # the dashboard shows the summary, not the page text
        result.pop("next_cursor", None)
        return _reply(result)

    # ---- campaigns, companies -----------------------------------------------------------------

    @app.get("/api/campaigns")
    def list_campaigns(include_archived: bool = False) -> JSONResponse:
        rows = []
        for short in campaign_svc.list_campaigns(include_archived):
            full = campaign_svc.get_campaign(short["id"])
            rows.append(
                {
                    **short, "counts": full["counts"], "sectors": full["sectors"],
                    "email_summary": full["email_summary"],
                }
            )
        return _json({"ok": True, "campaigns": rows})

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
        page: int = 1,
        page_size: int = 25,
    ) -> JSONResponse:
        return _reply(
            browse.list_leads(
                campaign_id, status=status, designation=designation, company_id=company_id,
                email_status=email_status, qa=qa, ai_verdict=ai_verdict, search=search,
                page=page, page_size=page_size,
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
