"""Status of the tool: providers and keys, campaigns with counts, and the suggested next step."""

from __future__ import annotations

from typing import Any

from leadgen import __version__, db
from leadgen.core import config, paths, secrets
from leadgen.providers import base, registry
from leadgen.providers.base import ProviderError
from leadgen.services import campaign as campaign_mod
from leadgen.services import offer as offer_mod
from leadgen.services import spot_check

# Free allowance we can work out: (amount, period). "all" = one-time allowance, "month" = resets.
# Amounts come from the providers' sites (DECISIONS.md); they are estimates, not account balances.
_FREE_ALLOWANCE: dict[str, tuple[int, str]] = {
    "serper": (2500, "all"),
    "brave": (1000, "month"),
    "hunter": (50, "month"),
}


def _configured(info: base.ProviderInfo) -> bool:
    provider = registry.get_provider(info.key)
    if provider is not None:
        return provider.configured()
    return bool(info.env_keys) and all(secrets.get(name) for name in info.env_keys)


def list_providers() -> list[dict[str, Any]]:
    """Every provider with: info, configured, masked key, usage this month, remaining free."""
    out: list[dict[str, Any]] = []
    for info in registry.ALL_PROVIDERS.values():
        month = base.usage(info.key, "month")
        remaining: float | None = None
        allowance = _FREE_ALLOWANCE.get(info.key)
        if allowance:
            amount, period = allowance
            used = month["credits"] if period == "month" else base.usage(info.key, "all")["credits"]
            remaining = max(0.0, amount - used)
        out.append(
            {
                "key": info.key,
                "name": info.name,
                "purpose": info.purpose,
                "env_keys": list(info.env_keys),
                "free_tier": info.free_tier,
                "signup_url": info.signup_url,
                "paid": info.paid,
                "default": info.default,
                "implemented": registry.get_provider(info.key) is not None,
                "configured": _configured(info),
                "masked_key": secrets.masked(info.env_keys[0]) if len(info.env_keys) == 1 else "",
                "usage_month": month,
                "remaining_free": remaining,
            }
        )
    return out


def set_key(provider_key: str, key: str) -> dict[str, Any]:
    """Save and test a provider key (see `registry.save_api_key`)."""
    return registry.save_api_key(provider_key, key)


def check_key(provider_key: str) -> dict[str, Any]:
    """Test a provider's saved key (see `registry.check_provider`)."""
    return registry.check_provider(provider_key)


# --- get_status (MCP tool 1) --------------------------------------------------------------------

MAX_LISTED = 20


def _has_search_key() -> bool:
    try:
        registry.get_search_provider()
    except ProviderError:
        return False
    return True


def _ai_people(campaign_id: int) -> list[dict[str, Any]]:
    with db.session() as conn:
        return db.query(
            conn,
            "SELECT id, spot_check FROM people WHERE campaign_id = ? AND status = 'confirmed' "
            "AND evidence_origin = 'ai_web_search'",
            (campaign_id,),
        )


def _step(step: str, tool: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"step": step, "tool": tool, "message": message, **extra}


def next_step_for(
    camp: dict[str, Any], *, has_profile: bool, has_search_key: bool, spot: str = ""
) -> dict[str, Any]:
    """The next step for one campaign, from its counts (rule-based; `camp` is `get_campaign`).

    `spot` is the AI-search spot-check state ("" none, "ok", "incomplete", "failed").
    """
    cid = camp["id"]
    counts = camp["counts"]
    companies, people, messages = counts["companies"], counts["people"], counts["messages"]
    mode = camp["options"].get("search_mode", "auto")
    if not has_profile:
        return _step(
            "offer_profile", "save_offer_profile",
            "The offer has no saved profile yet. Read the offer text, write the profile with "
            "the user and save it.", campaign_id=cid,
        )  # fmt: skip
    if companies["approved"] == 0:
        if companies["candidate"] > 0:
            return _step(
                "approve_companies", "approve_companies",
                "Companies are added but none is approved. Show the list, then approve.",
                campaign_id=cid,
            )  # fmt: skip
        return _step(
            "companies", "add_companies",
            "No companies yet. Propose target companies and add them.", campaign_id=cid,
        )  # fmt: skip
    if people["confirmed"] + people["review"] == 0:
        if mode == "ai":
            return _step(
                "people_ai", "get_search_tasks",
                "Run the people searches with your own web search tool "
                "(read get_workflow_guide section ai_search first).", campaign_id=cid,
            )  # fmt: skip
        if not has_search_key:
            return _step(
                "keys", "list_providers",
                "No search key is set. Ask the user to add a free Serper key, or offer AI "
                "search mode with update_campaign(search_mode='ai').", campaign_id=cid,
            )  # fmt: skip
        return _step(
            "people", "estimate_people_search",
            "Estimate the people search, tell the user the cost, then find_people.",
            campaign_id=cid,
        )  # fmt: skip
    if people["confirmed"] == 0:
        return _step(
            "people_review", "list_people",
            "Nobody is confirmed yet; look at the review list or search more.", campaign_id=cid,
        )  # fmt: skip
    if "email" in camp["channels"] and sum(counts["emails"].values()) == 0:
        return _step(
            "emails", "estimate_email_search",
            "Email is a channel but no addresses were searched yet. Estimate, ask, then "
            "find_emails.", campaign_id=cid,
        )  # fmt: skip
    if sum(messages.values()) == 0:
        return _step(
            "drafting", "get_drafting_batch",
            "Write the messages for each channel.", campaign_id=cid,
        )  # fmt: skip
    if messages["qa_fail"] > 0 or messages["draft"] > 0:
        return _step(
            "fix_drafts", "run_qa",
            "Some messages fail the checks. Run run_qa, fix them and save the drafts again.",
            campaign_id=cid,
        )  # fmt: skip
    if messages["qa_pass"] > 0:
        return _step(
            "review", "get_review_batch",
            "Messages are waiting for your review (run_qa first if the data changed).",
            campaign_id=cid,
        )  # fmt: skip
    if spot in ("incomplete", "failed"):
        return _step(
            "spot_check", "get_spot_check",
            "AI-search leads need the user's spot check before export.", campaign_id=cid,
        )  # fmt: skip
    if messages["approved"] > 0:
        return _step(
            "deliver", "export_campaign",
            "Everything is reviewed. Ask how to deliver: CSV, lemlist or sending.",
            campaign_id=cid,
        )  # fmt: skip
    if messages["sent"] > 0:
        return _step(
            "analytics", "get_analytics", "Emails were sent; check the results.",
            campaign_id=cid,
        )  # fmt: skip
    return _step(
        "run_qa", "run_qa", "Run the quality check to see where the campaign stands.",
        campaign_id=cid,
    )  # fmt: skip


def get_status() -> dict[str, Any]:
    """Workspace, version, keys (masked), offers, campaigns with counts and the next step."""
    notes: list[str] = []
    try:
        config.load_settings()
    except config.ConfigError as exc:
        notes.append(f"settings.yaml has a problem: {exc}")
    has_key = _has_search_key()
    keys = [
        {
            "provider": item["key"],
            "name": item["name"],
            "configured": item["configured"],
            "masked_key": item["masked_key"],
            "implemented": item["implemented"],
            "paid": item["paid"],
        }
        for item in list_providers()
    ]
    offers = offer_mod.list_offers()
    profile_by_offer = {o["id"]: o["has_profile"] for o in offers}
    campaigns: list[dict[str, Any]] = []
    for short in campaign_mod.list_campaigns(include_archived=False)[:MAX_LISTED]:
        camp = campaign_mod.get_campaign(short["id"])
        step = next_step_for(
            camp,
            has_profile=profile_by_offer.get(camp["offer_id"], False),
            has_search_key=has_key,
            spot=spot_check.spot_state(_ai_people(camp["id"]), camp["options"]),
        )
        campaigns.append(
            {
                "id": camp["id"],
                "name": camp["name"],
                "status": camp["status"],
                "countries": camp["countries"],
                "channels": camp["channels"],
                "search_mode": camp["options"].get("search_mode"),
                "counts": camp["counts"],
                "next_step": step,
            }
        )
    if campaigns:
        next_step = campaigns[0]["next_step"]
    elif not offers:
        next_step = _step(
            "offer", "ingest_offer_source",
            "Start by learning the offer: ask for a website address, pasted text or a document.",
        )  # fmt: skip
    elif not any(profile_by_offer.values()):
        next_step = _step(
            "offer_profile", "save_offer_profile",
            "The offer has no saved profile yet. Read the offer text, write the profile with "
            "the user and save it.", offer_id=offers[0]["id"],
        )  # fmt: skip
    else:
        next_step = _step(
            "campaign", "create_campaign",
            "Ask about countries, sectors, company size, channels and job roles, then create "
            "the campaign.", offer_id=next(o["id"] for o in offers if o["has_profile"]),
        )  # fmt: skip
    if not has_key:
        notes.append(
            "No search key is set. A free Serper key gives 2,500 searches; or use AI search "
            "mode (the AI's own web search)."
        )
    return {
        "ok": True,
        "version": __version__,
        "workspace": str(paths.home()),
        "keys": keys,
        "offers": [
            {"id": o["id"], "name": o["name"], "has_profile": o["has_profile"]}
            for o in offers[:MAX_LISTED]
        ],
        "campaigns": campaigns,
        "next_step": next_step,
        "notes": notes,
    }


def save_key(provider_key: str, key: str) -> dict[str, Any]:
    """`save_api_key` for the MCP tool: only known providers, replies in the tool error shape.

    A key that fails its test is not kept (see `registry.save_api_key`). The reply holds only
    the masked key.
    """
    if provider_key not in registry.ALL_PROVIDERS:
        return {
            "ok": False,
            "error": {
                "code": "unknown_provider",
                "message": f"There is no provider called '{provider_key}'.",
                "hint": "Use a provider key from list_providers.",
            },
        }
    result = registry.save_api_key(provider_key, key)
    if result["ok"]:
        return result
    return {
        "ok": False,
        "provider": provider_key,
        "error": {
            "code": "key_not_saved",
            "message": str(result.get("message", "The key was not saved.")),
            "hint": "Check the key and try again, or add it with `leadgen keys set "
            f"{provider_key}` in a terminal.",
        },
    }
