"""Message templates: nine built-in ones (receiver level x depth), the project's own ones, and the
choice each search makes. The AI writes from the template the drafting batch hands it.
Raises `TemplateError`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from leadgen import db
from leadgen.core.text import normalize_whitespace
from leadgen.services import campaign as campaign_mod
from leadgen.services.campaign import KEY_RE

LEVELS = ("csuite", "director", "manager")
DEPTHS = ("short", "standard", "detailed")
LEVEL_LABELS = {"csuite": "Top leaders", "director": "Directors and heads", "manager": "Managers",
                "any": "Anyone"}  # fmt: skip
DEPTH_LABELS = {"short": "short", "standard": "standard", "detailed": "detailed"}
LEVEL_OF_SENIORITY = {"c_suite": "csuite", "vp": "director", "director": "director",
                      "manager": "manager", "individual": "manager"}  # fmt: skip
DEFAULT_KEY = "auto-standard"
MAX_CUSTOM = 30
MAX_NAME, MAX_USE_CASE, MAX_SUBJECT, MAX_BODY = 80, 200, 120, 3000
PLACEHOLDERS: dict[str, str] = {
    "first_name": "The person's greeting name, exactly as in the batch (greeting_name).",
    "company": "Their company's name.",
    "their_title": "Their job title as in the batch (title).",
    "topic": "Two to four words naming what you help with, for the subject line.",
    "evidence_line": "One specific sentence about their role or company, using only what we "
                     "found about them.",
    "offer_line": "One sentence on the single service that fits this person best.",
    "outcome_line": "The result that service brings, as the offer is allowed to claim it. "
                    "No new numbers.",
    "proof_line": "One proof point from the offer, as written. Leave the sentence out if there "
                  "is none.",
    "sender_line": "One sentence on who you are: your name or company and what the company does.",
    "cta": "One soft question that is easy to answer, from the offer's suggested next steps "
           "when one fits.",
    "sign_off": "Your sign-off block: name, job title and company, each on its own line.",
}  # fmt: skip
CLOSING = "\n\nKind regards,\n{sign_off}"
_PH_RE = re.compile(r"\{([A-Za-z_]+)\}")
AUTO_KEYS = ("auto-short", "auto-standard", "auto-detailed")

BUILTIN: dict[str, dict[str, str]] = {
    "csuite-short": {
        "name": "Top leaders, short", "level": "csuite", "depth": "short",
        "use_case": "First note to a CEO, founder or managing director: four lines, the business "
                    "result first.",
        "notes": "Lead with the business result. No features, no flattery, peer tone. At most 70 "
                 "words before the sign-off.",
        "subject": "{topic} at {company}",
        "body": "Hi {first_name},\n\n{evidence_line}\n\n{offer_line} {outcome_line}\n\n{cta}"
                "\n\nKind regards,\n{sign_off}",
    },
    "csuite-standard": {
        "name": "Top leaders, standard", "level": "csuite", "depth": "standard",
        "use_case": "A top leader who needs a little context: who you are, one result and one "
                    "proof point.",
        "notes": "Business result first, then who you are in one sentence. One proof point at "
                 "most. At most 110 words before the sign-off.",
        "subject": "{company} and {topic}",
        "body": "Hi {first_name},\n\n{evidence_line}\n\n{sender_line} {offer_line}\n\n"
                "{outcome_line} {proof_line}\n\n{cta}\n\nKind regards,\n{sign_off}",
    },
    "csuite-detailed": {
        "name": "Top leaders, detailed", "level": "csuite", "depth": "detailed",
        "use_case": "A top leader in a long decision, or a second note after no reply: the "
                    "business case in short paragraphs.",
        "notes": "Respect their time: short paragraphs, the result and the proof, then one easy "
                 "question. At most 145 words before the sign-off.",
        "subject": "A thought on {topic} for {company}",
        "body": "Hi {first_name},\n\n{evidence_line}\n\nI will keep this brief, as {topic} is one "
                "of many things on your desk.\n\n{sender_line} {offer_line}\n\n{outcome_line}"
                "\n\n{proof_line}\n\n{cta}\n\nKind regards,\n{sign_off}",
    },
    "director-short": {
        "name": "Directors and heads, short", "level": "director", "depth": "short",
        "use_case": "A head of a function: their team, the one service that fits, one easy "
                    "question.",
        "notes": "Speak to their team's goals. One service only. At most 70 words before the "
                 "sign-off.",
        "subject": "{topic} for your team at {company}",
        "body": "Hi {first_name},\n\n{evidence_line}\n\n{offer_line}\n\n{cta}"
                "\n\nKind regards,\n{sign_off}",
    },
    "director-standard": {
        "name": "Directors and heads, standard", "level": "director", "depth": "standard",
        "use_case": "The usual choice for directors and heads: what you do for teams like theirs, "
                    "one proof point, a soft next step.",
        "notes": "Team results over features. One proof point. At most 110 words before the "
                 "sign-off.",
        "subject": "{topic} for {company}'s team",
        "body": "Hi {first_name},\n\n{evidence_line}\n\n{sender_line} {offer_line}\n\n"
                "{proof_line} {outcome_line}\n\n{cta}\n\nKind regards,\n{sign_off}",
    },
    "director-detailed": {
        "name": "Directors and heads, detailed", "level": "director", "depth": "detailed",
        "use_case": "A head who will pass it to their team or their boss: how it works, the "
                    "result and the proof, ready to forward.",
        "notes": "Write so it can be forwarded: plain, complete, no inside references. At most 145 "
                 "words before the sign-off.",
        "subject": "A practical look at {topic}",
        "body": "Hi {first_name},\n\n{evidence_line}\n\n{sender_line} {offer_line}\n\n"
                "How it works for a team like yours: {outcome_line}\n\n{proof_line}\n\n"
                "If it helps to have something to share with your team, {cta}"
                "\n\nKind regards,\n{sign_off}",
    },
    "manager-short": {
        "name": "Managers, short", "level": "manager", "depth": "short",
        "use_case": "A manager who runs the work day to day: practical, short, no sales words.",
        "notes": "Practical and friendly. Say what changes in their week. At most 70 words before "
                 "the sign-off.",
        "subject": "Quick question about {topic}",
        "body": "Hi {first_name},\n\n{evidence_line}\n\n{offer_line}\n\n{cta}"
                "\n\nKind regards,\n{sign_off}",
    },
    "manager-standard": {
        "name": "Managers, standard", "level": "manager", "depth": "standard",
        "use_case": "The usual choice for managers: what the service changes in their week, one "
                    "proof point, an easy next step.",
        "notes": "Concrete and practical. One proof point. At most 110 words before the sign-off.",
        "subject": "{topic} for your team",
        "body": "Hi {first_name},\n\n{evidence_line}\n\n{sender_line} {offer_line}\n\n"
                "{outcome_line} {proof_line}\n\n{cta}\n\nKind regards,\n{sign_off}",
    },
    "manager-detailed": {
        "name": "Managers, detailed", "level": "manager", "depth": "detailed",
        "use_case": "A manager who will need to make the case to their boss: the practical detail "
                    "and a proof point they can pass on.",
        "notes": "Give them what they need to argue for it: how it works day to day and one proof "
                 "point. At most 145 words before the sign-off.",
        "subject": "{topic}: a practical option",
        "body": "Hi {first_name},\n\n{evidence_line}\n\n{sender_line} {offer_line}\n\n"
                "What it looks like day to day: {outcome_line}\n\n{proof_line}\n\n"
                "If it would help you make the case internally, {cta}"
                "\n\nKind regards,\n{sign_off}",
    },
}  # fmt: skip


class TemplateError(Exception):
    """A template request failed. `code` is stable text; `hint` tells the AI what to do."""

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


def _shape(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "key": f"custom-{row['id']}", "id": row["id"], "name": row["name"],
        "level": row["level"], "depth": row["depth"], "use_case": row["use_case"],
        "notes": "", "subject": row["subject"], "body": row["body"], "custom": True,
    }  # fmt: skip


def _check_project(conn: Any, project_id: int) -> None:
    if db.get(conn, "projects", project_id) is None:
        raise TemplateError(
            "not_found", f"There is no project with id {project_id}.",
            "Use list_projects to see them.",
        )  # fmt: skip


def _custom_rows(conn: Any, project_id: int) -> list[dict[str, Any]]:
    return db.query(
        conn, "SELECT * FROM message_templates WHERE project_id = ? ORDER BY id", (project_id,)
    )


def list_templates(project_id: int) -> dict[str, Any]:
    """{"ok", "builtin": [BUILTIN items with key and custom False], "custom": [...],
    "placeholders": PLACEHOLDERS, "choices": [{"key", "label"}]}
    (raises TemplateError not_found)."""
    with db.session() as conn:
        _check_project(conn, project_id)
        custom = [_shape(r) for r in _custom_rows(conn, project_id)]
    builtin = [{**tpl, "key": key, "custom": False} for key, tpl in BUILTIN.items()]
    return {
        "ok": True, "builtin": builtin, "custom": custom, "placeholders": PLACEHOLDERS,
        "choices": choices(project_id),
    }  # fmt: skip


def choices(project_id: int) -> list[dict[str, str]]:
    """In this order: auto-standard "Match each person's level (standard)", auto-short "Match each
    person's level (short)", auto-detailed "Match each person's level (detailed)", then each
    built-in "<LEVEL_LABELS>, <depth>" e.g. "Top leaders, short", then each custom
    "Yours: <name>"."""
    out = [
        {"key": f"auto-{d}", "label": f"Match each person's level ({d})"}
        for d in ("standard", "short", "detailed")
    ]
    out += [
        {"key": key, "label": f"{LEVEL_LABELS[t['level']]}, {DEPTH_LABELS[t['depth']]}"}
        for key, t in BUILTIN.items()
    ]
    with db.session() as conn:
        custom = _custom_rows(conn, project_id)
    out += [{"key": f"custom-{r['id']}", "label": f"Yours: {r['name']}"} for r in custom]
    return out


def _text(value: Any, label: str, limit: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise TemplateError("invalid_value", f"{label} cannot be empty.")
    if len(text) > limit:
        raise TemplateError("invalid_value", f"{label} can be at most {limit} characters.")
    return text


def save_template(
    project_id: int, data: Mapping[str, Any], template_id: int | None = None
) -> dict[str, Any]:
    """Validate and insert (template_id None) or update one custom template of the project.
    name 1..80 chars, use_case 0..200, subject 0..120, body 1..3000 (all text, stripped), level in
    LEVELS + ("any",), depth in DEPTHS. Every {placeholder} in subject and body must be a key of
    PLACEHOLDERS, else TemplateError("unknown_placeholder", "These are not placeholders we know:
    {x}, {y}.", "Use only the buttons under the message box."). A body without {sign_off} gets
    CLOSING appended. More than MAX_CUSTOM -> TemplateError("too_many", ...). Updating a template
    of another project -> TemplateError("not_found", "That template was not found.").
    Returns {"ok": True, "template": _shape(row)}."""
    name = normalize_whitespace(str(data.get("name") or ""))
    if not name or len(name) > MAX_NAME:
        raise TemplateError("invalid_value", f"The name must be 1 to {MAX_NAME} characters.")
    level = data.get("level", "any")
    depth = data.get("depth", "standard")
    if level not in (*LEVELS, "any") or depth not in DEPTHS:
        raise TemplateError("invalid_value", "Level and length must be one of the choices shown.")
    use_case = _text(data.get("use_case"), "When to use it", MAX_USE_CASE)
    subject = _text(data.get("subject"), "The subject", MAX_SUBJECT)
    body = _text(data.get("body"), "The message", MAX_BODY, required=True)
    unknown = sorted({m for m in _PH_RE.findall(subject + body) if m not in PLACEHOLDERS})
    if unknown:
        raise TemplateError(
            "unknown_placeholder",
            "These are not placeholders we know: " + ", ".join("{" + m + "}" for m in unknown)
            + ".",
            "Use only the buttons under the message box.",
        )  # fmt: skip
    if "{sign_off}" not in body:
        body += CLOSING
    values = {
        "name": name, "level": level, "depth": depth, "use_case": use_case, "subject": subject,
        "body": body,
    }  # fmt: skip
    with db.session() as conn:
        _check_project(conn, project_id)
        if template_id is None:
            if len(_custom_rows(conn, project_id)) >= MAX_CUSTOM:
                raise TemplateError(
                    "too_many", f"A project can have at most {MAX_CUSTOM} templates of its own.",
                    "Delete one that is not used, then try again.",
                )  # fmt: skip
            template_id = db.insert(conn, "message_templates", project_id=project_id, **values)
        else:
            row = db.get(conn, "message_templates", template_id)
            if row is None or row["project_id"] != project_id:
                raise TemplateError(
                    "not_found", "That template was not found.",
                    "Use list_templates to see this project's templates.",
                )  # fmt: skip
            db.update(conn, "message_templates", template_id, **values)
        row = db.get(conn, "message_templates", template_id)
    return {"ok": True, "template": _shape(row or {})}


def delete_template(project_id: int, template_id: int) -> dict[str, Any]:
    """Delete it, then call campaign_mod.update_campaign(cid, template_key=DEFAULT_KEY) for every
    search of the project whose template_key was "custom-<id>". Returns
    {"ok": True, "deleted": True, "searches_reset": n}."""
    key = f"custom-{template_id}"
    with db.session() as conn:
        _check_project(conn, project_id)
        row = db.get(conn, "message_templates", template_id)
        if row is None or row["project_id"] != project_id:
            raise TemplateError(
                "not_found", "That template was not found.",
                "Use list_templates to see this project's templates.",
            )  # fmt: skip
        conn.execute("DELETE FROM message_templates WHERE id = ?", (template_id,))
        searches = [
            r["id"] for r in db.query(
                conn, "SELECT id, options_json FROM campaigns WHERE project_id = ?", (project_id,)
            )
            if (r["options_json"] or {}).get("template_key") == key
        ]  # fmt: skip
    for campaign_id in searches:
        campaign_mod.update_campaign(campaign_id, template_key=DEFAULT_KEY)
    return {"ok": True, "deleted": True, "searches_reset": len(searches)}


def get_template(project_id: int | None, key: str) -> dict[str, Any] | None:
    """A built-in by key, a custom one of this project by "custom-<id>", else None."""
    if key in BUILTIN:
        return {**BUILTIN[key], "key": key, "custom": False}
    match = re.fullmatch(r"custom-(\d+)", key)
    if project_id is None or match is None:
        return None
    with db.session() as conn:
        row = db.get(conn, "message_templates", int(match.group(1)))
    if row is None or row["project_id"] != project_id:
        return None
    return _shape(row)


def resolve(project_id: int | None, key: str, seniority: str) -> tuple[str, dict[str, Any]]:
    """The concrete (key, template) for one person: auto-<depth> picks the level from seniority
    (LEVEL_OF_SENIORITY, default manager); an unknown or deleted custom key falls back to
    auto-standard."""
    if key.startswith("auto-"):
        level = LEVEL_OF_SENIORITY.get(seniority, "manager")
        key = f"{level}-{key.removeprefix('auto-')}"
    tpl = get_template(project_id, key)
    if tpl is None:
        return resolve(project_id, DEFAULT_KEY, seniority)
    return key, tpl


def set_campaign_template(campaign_id: int, key: str) -> dict[str, Any]:
    """Check the key exists for the campaign's project (KEY_RE and get_template, or auto-*), then
    campaign_mod.update_campaign(campaign_id, template_key=key). Returns
    {"ok": True, "campaign_id": id, "template_key": key}. Unknown -> TemplateError(
    "unknown_template", "That message style does not exist.")."""
    project_id = campaign_mod.get_campaign(campaign_id)["project_id"]
    known = (
        key in AUTO_KEYS
        or key in BUILTIN
        or (bool(KEY_RE.match(key)) and get_template(project_id, key) is not None)
    )
    if not known:
        raise TemplateError(
            "unknown_template", "That message style does not exist.",
            "Use list_templates to see the choices.",
        )  # fmt: skip
    campaign_mod.update_campaign(campaign_id, template_key=key)
    return {"ok": True, "campaign_id": campaign_id, "template_key": key}
