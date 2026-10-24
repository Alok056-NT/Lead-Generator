"""The guides the AI reads: the workflow script and the three topic guides (PLAN section 8)."""

from __future__ import annotations

from importlib import resources
from typing import Any

from leadgen.services import ai_search, drafting, offer

# Section name -> what it is. "all" joins every section.
SECTIONS: dict[str, str] = {
    "workflow": "The step-by-step script for the whole job (the same text as the prompt).",
    "ai_search": "AI search mode: running the searches with your own web search tool.",
    "messaging_style": "How to write the messages (also sent with the first drafting batch).",
    "offer_profile": "How to write the offer profile from the ingested text.",
}


def workflow_text() -> str:
    """The workflow script (`guides/workflow.md`); also served as the `lead_generation` prompt."""
    return resources.files("leadgen").joinpath("guides", "workflow.md").read_text(encoding="utf-8")


def studio_text() -> str:
    """The Studio brief for the chat AI (`guides/studio.md`)."""
    return resources.files("leadgen").joinpath("guides", "studio.md").read_text(encoding="utf-8")


def _section_text(name: str) -> str:
    if name == "workflow":
        return workflow_text()
    if name == "ai_search":
        return ai_search.guide_text()
    if name == "messaging_style":
        return drafting.guide_text()
    return offer.guide_text()


def get_guide(section: str = "workflow") -> dict[str, Any]:
    """The guide text for `section` (`workflow`, `ai_search`, `messaging_style`, `offer_profile`
    or `all`), with the list of sections. An unknown section gives the structured error."""
    name = (section or "workflow").strip().lower()
    if name == "all":
        names = list(SECTIONS)
        text = "\n\n---\n\n".join(_section_text(n) for n in names)
    elif name in SECTIONS:
        names = [name]
        text = _section_text(name)
    else:
        return {
            "ok": False,
            "error": {
                "code": "unknown_section",
                "message": f"There is no guide section called '{section}'.",
                "hint": f"Use one of: {', '.join([*SECTIONS, 'all'])}.",
            },
        }
    return {
        "ok": True,
        "section": name,
        "sections_included": names,
        "available_sections": [{"name": n, "about": about} for n, about in SECTIONS.items()],
        "text": text,
    }
