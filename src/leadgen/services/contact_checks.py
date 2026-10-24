"""Lead checks L24 and L25 (T23), registered through `qa_leads.EXTRA_LEAD_CHECKS`.

- L24 (block): the project already found or contacted this person inside its cooldown
  ("Already contacted on 2026-09-01 in campaign 'X'; can be contacted again after 2026-11-30").
- L25 (block): the person is also in another active campaign of the project.

Both look only at the person's own project (`campaign["project_id"]`); a campaign without a
project, or a person without keys, passes. A person promoted by hand (a `MANUAL:` entry) keeps
the finding as a warning: the owner accepted it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from leadgen.services import projects
from leadgen.services import qa_leads as qa
from leadgen.services.qa_leads import Issue, LeadContext

_CACHE_KEY = "contact_screen"


def check(
    person: Mapping[str, Any],
    company: Mapping[str, Any],
    campaign: Mapping[str, Any],
    email_row: Mapping[str, Any] | None,
    context: LeadContext,
) -> list[Issue]:
    """L24 / L25 for one person (the project data is read once per `LeadContext`)."""
    project_id = campaign.get("project_id")
    if project_id is None:
        return []
    screen = context.extras.get(_CACHE_KEY)
    if screen is None:
        screen = projects.ContactScreen(project_id, campaign["id"])
        context.extras[_CACHE_KEY] = screen
    keys = projects.make_keys(
        {
            **person,
            "company": company.get("name") or "",
            "email": (email_row or {}).get("email") or "",
        }
    )
    manual = qa._is_manual(person)
    out: list[Issue] = []
    for code, text in screen.findings(keys, below_person_id=int(person.get("id") or 0) or None):
        severity = qa.LEAD_SEVERITY[code]
        if manual and severity == "block":
            severity, text = "warn", f"{text} (accepted by hand: {manual})"
        out.append(Issue(code, severity, text))
    return out


def register() -> None:
    """Add `check` to the lead checks once (importing this module does it)."""
    if check not in qa.EXTRA_LEAD_CHECKS:
        qa.EXTRA_LEAD_CHECKS.append(check)


register()
