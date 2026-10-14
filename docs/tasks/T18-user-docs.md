# T18: User documentation

Depends on: T17. Audience: non-technical users.

## Goal
Clear, short guides so a non-technical person can install, connect, run a campaign, and understand costs, limits and rules.

## Deliverables (plain English, short sentences, numbered steps, no jargon without a one-line explanation)
- `README.md` (replace the planner's placeholder sections, keep the structure): what it does (5 bullets), install (one command per OS), connect Claude / ChatGPT (links), first campaign in 6 steps ("Say to Claude: 'Find me leads for <url> in <country>'"), costs table (free tiers), safety notes, links to guides, licence + credits.
- `docs/user/GETTING_STARTED.md`: a full worked example with a fictional company (no real people), showing what the AI asks and what you answer.
- `docs/user/PROVIDERS.md`: each provider: what it's for, free allowance, how to sign up, where to paste the key, what happens when credits run out.
- `docs/user/FREE_MODE.md`: running without paid credits using the AI's own web search (what it is, when to use it, how to turn on web search in Claude/ChatGPT, the spot check, limits).
- `docs/user/QUALITY_CHECKS.md`: every L/M code in plain English and what to do about it.
- `docs/user/DELIVERY.md`: CSV import into lemlist/Instantly (with the lemlist sequence recipe: blank invite → Accepted invite condition → LinkedIn message `{{linkedinMessage}}`, plan requirement), lemlist API push, sending email (readiness checklist, warm-up, caps, unsubscribe handling).
- `docs/user/COMPLIANCE.md`: generated from `core/compliance.py` (add `leadgen compliance --markdown` CLI to render it), with the "not legal advice" note and LinkedIn terms reminder.
- `docs/user/TROUBLESHOOTING.md`: Claude doesn't show tools; ChatGPT can't connect; "needs_key"; out of credits; a site blocks crawling; Excel shows odd characters (UTF-8 BOM note); antivirus blocks cloudflared.
- `CHANGELOG.md` started at `0.1.0`.

## Worker prep (Haiku)
1. Draft `docs/user/PROVIDERS.md` from PLAN sections 7 and 7a and the provider rows in `docs/DECISIONS.md`: one short section per provider (what it's for, free allowance, how to sign up, where to paste the key, what happens when credits run out), plus a "No credits? Use free mode" section linking FREE_MODE.md. Plain English, short sentences.
2. Draft `docs/user/QUALITY_CHECKS.md` from PLAN section 14: one row per L and M code (including L21-L23) with a plain-English meaning, whether it blocks or warns, and what the user should do.
The builder edits both drafts for accuracy and tone and writes the remaining docs.

## Acceptance
Reviewer (and ideally the owner) follows GETTING_STARTED on a clean machine without help.
