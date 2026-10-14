"""Per-campaign budgets (PLAN section 7): how much of a campaign's allowance has been used.

A campaign's `budget_json` holds limits such as `{"search": 300, "hunter": 25, "verify": 200}`;
kinds it leaves out use `DEFAULT_BUDGET`. The amount used so far is kept in the `settings` table
under `budget_used:<campaign_id>:<kind>`. Services call `check` before spending and `charge`
afterwards. Callers must run `db.init_db()` first.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from leadgen import db
from leadgen.providers import registry

DEFAULT_BUDGET: dict[str, float] = {"search": 300, "hunter": 25, "verify": 200}


def _key(campaign_id: int, kind: str) -> str:
    return f"budget_used:{campaign_id}:{kind}"


def limit(budget: Mapping[str, Any] | None, kind: str) -> float:
    """The limit for `kind` from a campaign budget, falling back to `DEFAULT_BUDGET`."""
    if budget and kind in budget:
        return float(budget[kind])
    return float(DEFAULT_BUDGET.get(kind, 0))


def used(campaign_id: int, kind: str) -> float:
    """How much of `kind` the campaign has used so far."""
    with db.session() as conn:
        rows = db.query(
            conn, "SELECT value FROM settings WHERE key = ?", (_key(campaign_id, kind),)
        )
    try:
        return float(rows[0]["value"]) if rows else 0.0
    except ValueError:
        return 0.0


def charge(campaign_id: int, kind: str, amount: float) -> float:
    """Add `amount` to the campaign's usage of `kind` and return the new total."""
    if amount <= 0:
        return used(campaign_id, kind)
    stamp = db.now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO settings (created_at, updated_at, key, value) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET "
            "value = CAST(CAST(value AS REAL) + CAST(excluded.value AS REAL) AS TEXT), "
            "updated_at = excluded.updated_at",
            (stamp, stamp, _key(campaign_id, kind), str(float(amount))),
        )
    return used(campaign_id, kind)


def check(
    campaign_id: int,
    budget: Mapping[str, Any] | None,
    kind: str,
    needed: float,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """None when `needed` more fits the budget (or `confirm` is true); else the structured
    `budget_reached` reply for the AI."""
    if confirm:
        return None
    so_far = used(campaign_id, kind)
    cap = limit(budget, kind)
    if so_far + needed > cap:
        return registry.budget_response(kind, so_far, cap)
    return None
