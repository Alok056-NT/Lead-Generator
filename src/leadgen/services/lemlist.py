"""Push approved leads into an existing lemlist campaign (PLAN section 15.2, tool 30).

`list_lemlist_campaigns` shows the campaigns the user can pick from. `push_to_lemlist` takes the
same people as `export_campaign(include="approved")` (`export.build_rows`), builds one lead per
person and sends it with `providers.lemlist.create_lead`.

* `dry_run=True` (the default) shows exactly what would be sent for the first 3 leads and the
  counts; it makes no HTTP call and needs no key.
* A real run needs `confirm=True`, sends at most `CHUNK_SIZE` leads per call and returns a
  `next_cursor` until `done` is true.
* It is idempotent: each person's result is kept in the `settings` table under
  `lemlist_push:<campaign id>:<lemlist campaign id>` (person ids only, no personal data), and a
  person already pushed to that lemlist campaign is skipped. lemlist itself also refuses a
  second lead for the same contact in one campaign (treated as already pushed).
* lemlist's docs do not say whether a lead may have no email (the CSV import needs one). Until
  the owner's real test shows otherwise (`EMAIL_REQUIRED`), a person without an email is skipped
  with the reason `no_email_for_lemlist_api` and counted (the CSV import needs an email too, so
  the user must find an email first or add those leads by hand in lemlist).
* A lead that has an email address but no exportable email message while the campaign has the
  email channel, or no exportable LinkedIn message while LinkedIn is a channel, is held back by
  default (reason `messages_incomplete`): lemlist shows missing variables only in its manual
  Launch review, so blank steps could go out. `allow_incomplete=True` (the user's explicit
  choice) pushes them anyway. `guess` email addresses are counted and warned about.

Callers must run `db.init_db()` first.
"""

from __future__ import annotations

import json
import re
from typing import Any

from leadgen import db
from leadgen.providers.base import ProviderError
from leadgen.providers.lemlist import LemlistProvider
from leadgen.services import export

CHUNK_SIZE = 25
PREVIEW_SIZE = 3
EMAIL_REQUIRED = True  # see the module docstring; flip after the live test (DECISIONS, T12)
LOG_KEY_PREFIX = "lemlist_push"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Row key -> lemlist field. The first seven are lemlist's own fields; the rest become custom
# variables ({{greetingName}} ...).
_PAYLOAD_KEYS = (
    "email", "firstName", "lastName", "companyName", "jobTitle", "linkedinUrl", "companyDomain",
    "greetingName", "designation", "linkedinMessage", "linkedinInvite", "emailSubject",
    "emailBody", "emailStatus",
)  # fmt: skip


class LemlistError(Exception):
    """A lemlist request failed. `code` is stable text; `hint` tells the AI what to do."""

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


def _from_provider(exc: ProviderError) -> LemlistError:
    if exc.code == "not_configured":
        return LemlistError(
            "needs_key",
            "No lemlist API key is set.",
            "Ask the user to add LEMLIST_API_KEY (dashboard, Keys page, or save_api_key with "
            "provider 'lemlist'). The key is in lemlist under Settings, Integrations.",
        )
    if exc.code == "auth":
        return LemlistError(
            "lemlist_auth", exc.message, "The lemlist key looks wrong; add it again."
        )
    if exc.code == "rate_limited":
        return LemlistError("lemlist_rate_limited", exc.message, "Wait a minute and call again.")
    return LemlistError(
        f"lemlist_{exc.code}", exc.message, "Try again; if it repeats, tell the user."
    )


def _lemlist_id(value: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.match(text):
        raise LemlistError(
            "invalid_value",
            "lemlist_campaign_id is not valid.",
            "Use the id from list_lemlist_campaigns (it looks like cam_xxxxxxxx).",
        )
    return text


def list_lemlist_campaigns(provider: LemlistProvider | None = None) -> list[dict[str, str]]:
    """The user's lemlist campaigns as `{id, name, status}`. Raises `LemlistError`."""
    provider = provider or LemlistProvider()
    try:
        return provider.list_campaigns()
    except ProviderError as exc:
        raise _from_provider(exc) from None


# --- idempotency log ------------------------------------------------------------------------------


def _log_key(campaign_id: int, lemlist_campaign_id: str) -> str:
    return f"{LOG_KEY_PREFIX}:{campaign_id}:{lemlist_campaign_id}"


def _load_log(key: str) -> dict[str, dict[str, Any]]:
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    if not rows:
        return {}
    try:
        data = json.loads(rows[0]["value"])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_log(key: str, log: dict[str, dict[str, Any]]) -> None:
    with db.session() as conn:
        db.upsert(conn, "settings", ["key"], key=key, value=json.dumps(log, sort_keys=True))


# --- payload --------------------------------------------------------------------------------------


def lead_payload(row: dict[str, Any]) -> dict[str, str]:
    """The JSON body for one lead: lemlist fields plus custom variables, empty values left out."""
    return {k: str(row[k]) for k in _PAYLOAD_KEYS if str(row.get(k) or "").strip()}


def _cursor(value: str | int | None) -> int:
    if value in (None, ""):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = -1
    if n < 0:
        raise LemlistError("invalid_cursor", "The cursor is not valid.", "Start again without one.")
    return n


# --- push -----------------------------------------------------------------------------------------


def push_to_lemlist(
    campaign_id: int,
    lemlist_campaign_id: str,
    dry_run: bool = True,
    confirm: bool = False,
    cursor: str | int | None = None,
    provider: LemlistProvider | None = None,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    """Create leads in an existing lemlist campaign (see the module docstring).

    Returns counts (`ready`, `already_pushed`, `no_email_for_lemlist_api`, `messages_incomplete`,
    `guess_emails`, `to_push`) and `warnings`; a dry run
    adds `preview` (the first 3 payloads), a real run adds this call's results and `next_cursor`
    / `done`. Raises `LemlistError`, `CampaignError` or `ReviewError`.
    """
    target = _lemlist_id(lemlist_campaign_id)
    start = _cursor(cursor)
    data = export.build_rows(campaign_id, "approved")
    key = _log_key(data.campaign["id"], target)
    log = _load_log(key)

    done_ids = {
        r["personId"]
        for r in data.rows
        if log.get(str(r["personId"]), {}).get("status") in ("created", "duplicate")
    }
    no_email = [
        r for r in data.rows if r["personId"] not in done_ids and EMAIL_REQUIRED and not r["email"]
    ]
    no_email_ids = {r["personId"] for r in no_email}
    remaining = [
        r for r in data.rows if r["personId"] not in done_ids and r["personId"] not in no_email_ids
    ]
    incomplete = [r for r in remaining if export.missing_messages(data.campaign, r)]
    incomplete_ids = {r["personId"] for r in incomplete}
    held = [] if allow_incomplete else incomplete
    pending = [r for r in remaining if allow_incomplete or r["personId"] not in incomplete_ids]
    guesses = sum(1 for r in pending if r["emailStatus"] == "guess")

    base: dict[str, Any] = {
        "ok": True,
        "campaign_id": data.campaign["id"],
        "lemlist_campaign_id": target,
        "dry_run": dry_run,
        "ready": len(data.rows),
        "already_pushed": len(done_ids),
        "no_email_for_lemlist_api": len(no_email),
        "messages_incomplete": len(held),
        "guess_emails": guesses,
        "to_push": len(pending),
        "blocked_by_code": dict(data.gate["blocked_by"]),
    }
    warnings: list[str] = []
    if no_email:
        base["no_email_hint"] = (
            f"{len(no_email)} lead(s) have no email, so the lemlist API push skips them "
            "(reason no_email_for_lemlist_api). lemlist's CSV import also needs an email on "
            "every row, so run find_emails for them first, or add those leads by hand in lemlist "
            "from their LinkedIn profile."
        )
    if held:
        base["messages_incomplete_hint"] = (
            f"{len(held)} lead(s) were held back (reason messages_incomplete): the campaign "
            "needs an email and/or LinkedIn message for them that is not approved yet, and "
            "lemlist would send a blank step. Finish QA and the AI review, then push again "
            "(already pushed leads are skipped). To push them anyway, ask the user and call "
            "again with allow_incomplete=true."
        )
    elif incomplete:
        warnings.append(
            f"{len(incomplete)} lead(s) are pushed without a message for every channel of the "
            "campaign (allow_incomplete=true). Their lemlist steps will show missing variables."
        )
    if guesses:
        warnings.append(
            f"{guesses} lead(s) have an unverified guessed email (emailStatus = guess). "
            "Verify them before you launch the lemlist campaign."
        )
    base["warnings"] = warnings

    if dry_run:
        base["preview"] = [
            {"person_id": r["personId"], "payload": lead_payload(r)} for r in pending[:PREVIEW_SIZE]
        ]
        base["message"] = (
            "Nothing was sent. This is exactly what would be sent for the first leads. "
            "To push, call again with dry_run=false and confirm=true."
        )
        return base

    if not confirm:
        base["needs_confirmation"] = True
        base["message"] = (
            f"{len(pending)} lead(s) would be created in lemlist campaign {target}. Ask the "
            "user, then call again with dry_run=false and confirm=true."
        )
        return base

    provider = provider or LemlistProvider()
    if not provider.configured():
        return _from_provider(ProviderError("not_configured")).as_response()

    todo = [r for r in pending if r["personId"] > start]
    chunk, rest = todo[:CHUNK_SIZE], todo[CHUNK_SIZE:]
    results: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    last = start
    try:
        for row in chunk:
            pid = row["personId"]
            try:
                outcome = provider.create_lead(target, lead_payload(row))
            except ProviderError as exc:
                failure = _from_provider(exc).as_response()
                break
            last = pid
            entry: dict[str, Any] = {"status": outcome.status, "at": db.now()}
            if outcome.lead_id:
                entry["lead_id"] = outcome.lead_id
            if outcome.status == "rejected":
                entry["detail"] = outcome.detail
            log[str(pid)] = entry
            results.append(
                {
                    "person_id": pid,
                    "status": outcome.status,
                    "detail": outcome.detail,
                    "warnings": outcome.warnings,
                }
            )
    finally:
        _save_log(key, log)

    summary = {
        s: sum(1 for r in results if r["status"] == s) for s in ("created", "duplicate", "rejected")
    }
    if failure is not None:
        return {
            **failure,
            "pushed_so_far": summary,
            "results": results,
            "next_cursor": str(last) if last else None,
        }
    done = not rest
    finished = (
        "All leads are in lemlist."
        if not held
        else f"All other leads are in lemlist; {len(held)} held back (messages_incomplete)."
    )
    return {
        **base,
        "pushed": summary,
        "results": results,
        "done": done,
        "next_cursor": None if done else str(last),
        "message": (
            finished
            if done
            else f"{len(rest)} more lead(s) to push: call again with next_cursor."
        ),
        "next_steps": list(export.LEMLIST_NEXT_STEPS),
    }
