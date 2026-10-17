"""The footer appended to every email at send time (sender, postal address, opt-out line)."""

from __future__ import annotations

from leadgen.core.config import Settings

OPT_OUT_LINE = "If you'd rather not hear from me, reply 'unsubscribe' and I won't email you again."


def footer(settings: Settings) -> str:
    """The footer text: `--`, sender name, role and company, website (if set), postal address,
    then the opt-out line. Empty parts are left out. The caller adds a blank line before it."""
    sender = settings.sender
    lines = ["--"]
    if sender.name.strip():
        lines.append(sender.name.strip())
    role_company = ", ".join(p.strip() for p in (sender.role, sender.company) if p.strip())
    if role_company:
        lines.append(role_company)
    if sender.website.strip():
        lines.append(sender.website.strip())
    address_lines = [ln.strip() for ln in settings.postal_address.splitlines() if ln.strip()]
    lines.extend(address_lines)
    lines.extend(["", OPT_OUT_LINE])
    return "\n".join(lines)


def with_footer(body: str, settings: Settings) -> str:
    """`body` followed by a blank line and the footer (line endings normalised to `\\n`)."""
    text = body.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    return f"{text}\n\n{footer(settings)}\n"
