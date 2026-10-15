"""Spot check for AI-search leads (PLAN section 7a step 5).

Leads whose evidence came from the host AI's own web search (`evidence_origin =
"ai_web_search"`) carry a small risk that a result was paraphrased or invented. Before export a
random sample of them is shown to the user, who opens each LinkedIn link and says whether it is
the right person, company and role. `get_spot_check` draws the sample (marks it `pending`),
`record_spot_check` stores the answers. A `wrong` answer moves that person to review; two or more
`wrong` answers in one sample set the campaign option `ai_search_needs_recheck`, which keeps every
AI-search lead blocked (L21) until a second sample passes. Callers must run `db.init_db()` first.
"""

from __future__ import annotations

import json
import math
import random
import secrets
from collections.abc import Mapping, Sequence
from typing import Any

from leadgen import db
from leadgen.services import campaign as campaign_mod

ORIGIN = "ai_web_search"
MIN_SAMPLE = 5
MAX_SAMPLE = 15
SAMPLE_SHARE = 0.05
WRONG_LIMIT = 2  # this many wrong answers in one sample fail the whole AI-search batch
VERDICTS = ("ok", "wrong")
INSTRUCTION = (
    "Ask the user to open each link and reply 'ok' or 'wrong' for each (right person, right "
    "company, right role). Then call record_spot_check with their answers."
)


class SpotCheckError(Exception):
    """A spot-check request failed. `code` is stable text; `hint` tells the AI what to do."""

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


def sample_size(total: int) -> int:
    """How many leads to check: 5 or 5%, whichever is larger, at most 15 (and never more than
    there are)."""
    return min(total, MAX_SAMPLE, max(MIN_SAMPLE, math.ceil(total * SAMPLE_SHARE)))


# --- stored state -----------------------------------------------------------------------------


def _key(campaign_id: int) -> str:
    return f"spot_check:{campaign_id}"


def _load_state(campaign_id: int) -> dict[str, Any]:
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (_key(campaign_id),))
    try:
        state = json.loads(rows[0]["value"]) if rows else {}
    except ValueError:
        state = {}
    return state if isinstance(state, dict) else {}


def _save_state(campaign_id: int, state: Mapping[str, Any]) -> None:
    stamp = db.now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO settings (created_at, updated_at, key, value) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (stamp, stamp, _key(campaign_id), json.dumps(state)),
        )


def _ai_people(campaign_id: int) -> list[dict[str, Any]]:
    """Confirmed AI-search people with their company name."""
    with db.session() as conn:
        return db.query(
            conn,
            "SELECT p.*, c.name AS company_name FROM people p "
            "JOIN companies c ON c.id = p.company_id "
            "WHERE p.campaign_id = ? AND p.status = 'confirmed' AND p.evidence_origin = ? "
            "ORDER BY p.id",
            (campaign_id, ORIGIN),
        )


def _already_checked(campaign_id: int) -> bool:
    """True when any AI-search person of the campaign (any status) has a recorded answer."""
    with db.session() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM people WHERE campaign_id = ? AND evidence_origin = ? "
            "AND spot_check IN ('ok', 'wrong')",
            (campaign_id, ORIGIN),
        ).fetchone()[0]
    return n > 0


def _shape(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "person_id": row["id"],
        "name": row["raw_name"],
        "title": row["title"],
        "company": row["company_name"],
        "linkedin_url": row["linkedin_url"],
    }


# --- public -----------------------------------------------------------------------------------


def get_spot_check(
    campaign_id: int, resample: bool = False, seed: int | None = None
) -> dict[str, Any]:
    """The AI-search leads the user should click-check before export.

    If the campaign has confirmed AI-search leads and no spot check yet, picks a random sample
    (5 people or 5%, whichever is larger, at most 15), marks them `pending` and returns each with
    name, title, company and LinkedIn link plus the `instruction` for the AI. The `seed` used is
    stored (and returned), so the same sample can be reproduced; pass `seed` to force one. Calling
    it again while answers are pending returns the same people. After a failed check
    (`ai_search_needs_recheck`) it draws a new sample from leads not checked yet. `resample=True`
    draws a new sample on request (for example after more AI-search leads were added).
    """
    camp = campaign_mod.get_campaign(campaign_id)
    leads = _ai_people(campaign_id)
    if not leads:
        return {
            "ok": True,
            "needed": False,
            "message": "No confirmed AI-search leads in this campaign, so no spot check is needed.",
        }
    state = _load_state(campaign_id)
    pending = [p for p in leads if p["spot_check"] == "pending"]
    recheck = bool(camp["options"].get("ai_search_needs_recheck"))
    if pending:
        chosen = pending
    elif not (recheck or resample) and _already_checked(campaign_id):
        return {
            "ok": True,
            "needed": False,
            "status": "passed",
            "message": "The spot check for this campaign is done and passed.",
        }
    else:
        pool = [p for p in leads if not p["spot_check"]] or [
            p for p in leads if p["spot_check"] == "ok"
        ]
        used_seed = secrets.randbits(32) if seed is None else int(seed)
        ids = sorted(p["id"] for p in pool)
        picked = set(random.Random(used_seed).sample(ids, sample_size(len(ids))))
        chosen = [p for p in pool if p["id"] in picked]
        with db.session() as conn:
            for person in chosen:
                db.update(conn, "people", person["id"], spot_check="pending")
        state = {
            **state,
            "seed": used_seed,
            "round": int(state.get("round") or 0) + 1,
            "wrong": 0,
            "sample": sorted(p["id"] for p in chosen),
        }
        _save_state(campaign_id, state)
    return {
        "ok": True,
        "needed": True,
        "round": state.get("round"),
        "seed": state.get("seed"),
        "sample_size": len(chosen),
        "people": [_shape(p) for p in chosen],
        "instruction": INSTRUCTION,
        "after_recheck": recheck,
    }


def _clean_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        raise SpotCheckError(
            "invalid_value", "results must be a list of {person_id, verdict, note} objects."
        )
    out: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, Mapping):
            raise SpotCheckError("invalid_value", "Each result must be an object.")
        person_id = item.get("person_id")
        verdict = item.get("verdict")
        if isinstance(person_id, bool) or not isinstance(person_id, int):
            raise SpotCheckError("invalid_value", "person_id must be a whole number.")
        if verdict not in VERDICTS:
            raise SpotCheckError("invalid_value", "verdict must be 'ok' or 'wrong'.")
        out.append(
            {
                "person_id": person_id,
                "verdict": verdict,
                "note": " ".join(str(item.get("note") or "").split())[:200],
            }
        )
    return out


def record_spot_check(campaign_id: int, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Store the user's answers (`[{person_id, verdict: "ok"|"wrong", note}]`).

    `wrong` moves that person to `review` (reason `L21:spot check failed`). Two or more `wrong`
    answers in one sample set the campaign option `ai_search_needs_recheck`: every AI-search lead
    stays blocked (L21) until the user records a second spot check that passes (fewer than two
    wrong), which clears the flag. People not in the pending sample are refused.
    """
    camp = campaign_mod.get_campaign(campaign_id)
    items = _clean_results(results)
    state = _load_state(campaign_id)
    recorded = {"ok": 0, "wrong": 0}
    refused: list[dict[str, Any]] = []
    with db.session() as conn:
        for item in items:
            row = db.get(conn, "people", item["person_id"])
            if row is None or row["campaign_id"] != campaign_id:
                refused.append({"person_id": item["person_id"], "why": "not found"})
                continue
            if row["spot_check"] != "pending":
                refused.append({"person_id": item["person_id"], "why": "not in the pending sample"})
                continue
            if item["verdict"] == "ok":
                db.update(conn, "people", row["id"], spot_check="ok")
            else:
                reasons = list(row["reasons_json"] or [])
                note = f": {item['note']}" if item["note"] else ""
                reasons.append(f"L21:spot check failed{note}")
                db.update(
                    conn, "people", row["id"], spot_check="wrong", status="review", confirmed=0,
                    reasons_json=reasons,
                )  # fmt: skip
            recorded[item["verdict"]] += 1
    round_wrong = int(state.get("wrong") or 0) + recorded["wrong"]
    state["wrong"] = round_wrong
    with db.session() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM people WHERE campaign_id = ? AND evidence_origin = ? "
            "AND status = 'confirmed' AND spot_check = 'pending'",
            (campaign_id, ORIGIN),
        ).fetchone()[0]
    flagged = bool(camp["options"].get("ai_search_needs_recheck"))
    this_round = int(state.get("round") or 0)
    if round_wrong >= WRONG_LIMIT and not flagged:
        campaign_mod.update_campaign(campaign_id, ai_search_needs_recheck=True)
        state["flagged_round"] = this_round
        flagged = True
    elif (
        flagged
        and left == 0
        and round_wrong < WRONG_LIMIT
        and this_round > int(state.get("flagged_round") or 0)
    ):
        campaign_mod.update_campaign(campaign_id, ai_search_needs_recheck=False)
        flagged = False
    _save_state(campaign_id, state)
    if flagged:
        message = (
            "Two or more answers were wrong, so every AI-search lead stays blocked (L21). Call "
            "get_spot_check for a second sample and record it; fewer than two wrong clears it."
        )
    elif left:
        message = f"{left} answer(s) still pending."
    else:
        message = "Spot check recorded; the AI-search leads are no longer blocked by L21."
    return {
        "ok": True,
        "recorded": recorded,
        "refused": refused,
        "round_wrong": round_wrong,
        "pending_left": left,
        "needs_recheck": flagged,
        "message": message,
    }
