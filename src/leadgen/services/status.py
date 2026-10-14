"""Status of the tool: which providers exist, which are set up and how much has been used."""

from __future__ import annotations

from typing import Any

from leadgen.core import secrets
from leadgen.providers import base, registry

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
