# T10: Drafting workflow

Depends on: T05, T06, T08 (T09 for email channel). PLAN sections: 13, tools 23–24.

## Goal
Give the AI exactly the context it needs to write one personalised message per person per channel, in batches, and store what it writes with instant checks.

## Deliverables
### `src/leadgen/guides/messaging_style.md`
The writing rules the AI follows (PLAN section 13), written as clear instructions with 3 good and 3 bad examples (synthetic people). Must cover:
- structure: greeting with `greeting_name` → one specific line about their role/company drawn **only** from the evidence given → one relevant offer line (choose the service that fits their designation and sector) → one soft question;
- allowed facts: only `confirmed_claims` and `proof_points`; never `forbidden_claims`; no client names unless listed; no numbers unless in `proof_points`;
- channel rules and limits (invite ≤ 200 chars, message 150–700 chars no links, email subject ≤ 60 chars + body 40–160 words, no footer in drafts);
- tone from the offer profile; locale spelling; no emojis; at most one exclamation mark; never claim to have read their posts or met them; never fake familiarity ("Hope you're well!" is fine; "Loved your post" is not);
- partner/competitor/recruiter leads (flagged in the batch) get a partnership-style note, not a sales pitch;
- vary sentence openings; don't reuse the same sentence for more than 10% of a campaign.

### `services/drafting.py`
- `CHANNEL_SPECS` (single source of truth used by QA too): `linkedin_invite`, `linkedin_message`, `email` with min/max chars or words, links allowed, subject rules.
- `channels_for(campaign) -> list[str]`: `linkedin` → `linkedin_message` (+ `linkedin_invite` only if `campaign.options.invite_note` is true; default false); `email` → `email`. Risky emails count as sendable only if `campaign.options.allow_risky_emails`.
- `get_drafting_batch(campaign_id, channel, batch_size=20, cursor=None) -> dict`:
  - eligible people: `status=confirmed`, no message yet for this channel (or message in `qa_fail`/`fix`), email channel requires a sendable email (`verified`, or `risky` if campaign allows);
  - per person: id, greeting_name, full name, title, designation, seniority, company (name, sector, size_note, notes), evidence snippet (cleaned, ≤ 300 chars), any flags (partner/competitor/recruiter/job-seeker), previous QA issues if redrafting;
  - plus once per batch: offer profile (name, one_liner, services, confirmed_claims, forbidden_claims, proof_points, ctas, sender, tone, language), channel spec, and the style guide text (only on the first batch of a session: `include_guide=true` default when cursor is None);
  - `next_cursor`, `remaining`.
- `save_drafts(campaign_id, drafts: list[dict]) -> dict`:
  - each draft: `person_id`, `channel`, `body`, `subject` (email only);
  - upsert into `messages` (one per person per channel; keep previous version in `qa_json.history` up to 3);
  - run message QA immediately (T11's `qa_messages.check_message`; until T11 exists, implement the interface with length + placeholder + greeting checks and leave a TODO the T11 builder replaces);
  - return per-draft `{person_id, channel, status: "qa_pass"|"qa_fail", issues: [...]}` so the AI fixes and resaves only failures.

## Tests
- Batch eligibility (email channel needs a sendable email; already-drafted people skipped; qa_fail included for redraft).
- Batch content contains offer claims and the guide only once per session.
- Save → upsert → history kept (max 3).
- Instant QA results returned per draft.
- Cursor walks all eligible people.

## Acceptance
Tests pass; guide reviewed by the reviewer for clarity.
