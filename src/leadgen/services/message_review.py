"""The owner's review of single messages (Studio review drawer): edit the text, approve or skip.

Editing goes through `drafting.save_drafts`, so the text is cleaned and checked again exactly like
a draft from the AI (it needs a fresh AI review and the owner's choice is cleared). Approve and
skip are the owner's own mark in `messages.owner_choice`; a skipped message never leaves
(`review.evaluate` drops it from export, sending and lemlist). A sent message cannot be changed.
Raises `MessageReviewError`.
"""

from __future__ import annotations

from typing import Any

from leadgen import db
from leadgen.services import drafting

CHOICES = ("approved", "skipped", "")


class MessageReviewError(Exception):
    """A review action failed. `code` is stable text; `as_response` gives the API error shape."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, "error": {...}}` shape used by every service."""
        error = {"code": self.code, "message": self.message, "hint": self.hint}
        return {"ok": False, "error": error}


def _load(message_id: int) -> dict[str, Any]:
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT m.*, p.campaign_id AS campaign_id FROM messages m "
            "JOIN people p ON p.id = m.person_id WHERE m.id = ?",
            (message_id,),
        )
    if not rows:
        raise MessageReviewError("not_found", "There is no such message.")
    return rows[0]


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "channel": row["channel"], "subject": row["subject"], "body": row["body"],
        "status": row["status"], "owner_choice": row["owner_choice"],
    }


def set_choice(message_id: int, choice: str) -> dict[str, Any]:
    """Mark a message `approved` or `skipped` (or "" to undo). Returns `{"ok": True, "message"}`."""
    if choice not in CHOICES:
        raise MessageReviewError("invalid_value", "The choice must be approved, skipped or empty.")
    row = _load(message_id)
    if row["status"] == "sent":
        raise MessageReviewError("already_sent", "This message was already sent.")
    with db.session() as conn:
        db.update(conn, "messages", message_id, owner_choice=choice)
    return {"ok": True, "message": _shape({**row, "owner_choice": choice})}


def edit_message(message_id: int, body: str, subject: str | None = None) -> dict[str, Any]:
    """Replace a message's text (and an email's subject) and check it again.

    Returns `{"ok": True, "message", "issues"}`; `message.status` is `qa_fail` when a check
    blocks the new text (it is still saved so the owner can keep fixing it). Raises
    `MessageReviewError` when the text cannot be saved (sent, person no longer ready, ...).
    """
    row = _load(message_id)
    draft = {
        "person_id": row["person_id"], "channel": row["channel"], "body": body,
        "subject": row["subject"] if subject is None else subject,
    }
    result = drafting.save_drafts(row["campaign_id"], [draft])["results"][0]
    if result["status"] == "not_saved":
        first = result["issues"][0]
        raise MessageReviewError(first["code"], first["detail"])
    with db.session() as conn:
        db.update(conn, "messages", message_id, owner_choice="")
    return {"ok": True, "message": _shape(_load(message_id)), "issues": result["issues"]}
