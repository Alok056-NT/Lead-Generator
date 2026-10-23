# T23: Projects, contact memory and campaign size/split

Depends on: T22. Sprint v1.1 items 1, 2, 4. Read `docs/SPRINT_v1.1.md`.

## Goal
Work is organised as **projects** (one per product, service, content piece or website). A project has one offer and many campaigns. The tool remembers everyone already found or contacted in a project and does not offer them again until the project's cooldown has passed. Campaigns can be sized (n people, c companies, p people per company) and people at one company can be spread across campaigns.

## Data (first real migration, schema v2; keep all v1 data)
- `projects`: id, name (unique, case-insensitive), description, offer_id (nullable FK offers), cooldown_days (int, default 90, 0 = never block repeats, max 3650), status (active/archived), created_at, updated_at.
- `campaigns.project_id` (FK projects, NOT NULL after migration). Migration: for each distinct `offer_id` among existing campaigns create a project named after the offer ("<offer name>", de-duplicated with " (2)") and assign its campaigns; offers with no campaign also get a project.
- `contact_log`: id, project_id, campaign_id (nullable for imports), person_id (nullable), key_linkedin (canonical URL or ''), key_email (lower-case or ''), key_name_company (`first last|company` normalised or ''), channel (`found`/`exported_csv`/`lemlist`/`email_sent`/`imported`), at (ISO UTC), source (text). Indexes on (project_id, key_linkedin), (project_id, key_email), (project_id, key_name_company).
- People statuses gain `reserve` (confirmed person beyond the campaign's size limits, kept for a later campaign).
- Add a v1 → v2 migration test: build a v1 database with the T19 universe, migrate, assert row counts unchanged and every campaign has a project.

## Services
`services/projects.py`:
- `create_project(name, description="", cooldown_days=90, offer_id=None)`, `update_project(id, **changes)`, `get_project(id)` (with counts: campaigns, people by status, contacted in cooldown), `list_projects()`, `archive_project(id)`.
- `set_project_offer(project_id, offer_id)`.
- `log_contacts(project_id, rows, channel, campaign_id=None, source="")` and `recently_contacted(project_id, person_or_keys) -> {"at", "channel", "campaign_id"} | None` (within cooldown). Matching: LinkedIn URL, else email, else name+company.
`services/campaign.py`:
- `create_campaign(..., project_id=...)`: `project_id` required for new campaigns (MCP and dashboard always pass it); `offer_id` defaults to the project's offer; error if the project has no offer yet.
- New options in `options_json`: `max_people` (n, default null = no limit), `max_companies` (c), `max_per_company` (p), validated positive ints ≤ 10,000.
`services/people.py`:
- `find_people`: only the first `max_companies` approved companies (stable order: approval time, then id); after confirmation, a person who is (a) already in another active campaign of the same project (status confirmed/reserve), or (b) `recently_contacted` in the project → stored as `review` with reason `L24:` / `L25:` (below), never silently dropped; when a company already has `max_per_company` confirmed people in this campaign, further confirmed people become `reserve`; when the campaign has `max_people` confirmed, stop searching (return `done` with a "limit reached" note). Ranking when choosing who stays confirmed vs reserve: campaign designation group order, then seniority (c_suite > vp > director > manager), then found order.
- Every newly confirmed person is logged to `contact_log` with channel `found`.
- `move_reserve(from_campaign_id, to_campaign_id, max_per_company=None, max_people=None)`: moves reserve people (same project only) into the target campaign as `confirmed`, respecting the target's limits; returns counts.
`services/export.py`, `lemlist.py`, `sending.py`: log exported/pushed/sent people to `contact_log` (`exported_csv` / `lemlist` / `email_sent`).

## QA
- **L24** (block): contacted in this project within the cooldown ("Already contacted on 2026-09-01 in campaign 'X'; can be contacted again after 2026-11-30").
- **L25** (block): already in another active campaign of this project.
- Register through the existing extra-checks hook; plain-English detail text.

## MCP tools (keep all existing tools working)
`create_project`, `list_projects`, `get_project`, `update_project` (incl. cooldown), `move_reserve_people`; `create_campaign` gains `project_id` and `max_people`/`max_companies`/`max_per_company`; `get_status` lists projects and gives a project-aware next step; `guides/workflow.md` step 0–2 updated: pick or create a project first, then the offer, then campaigns.

## Tests
Migration test; project CRUD/validation; cooldown matching by each key and expiry; find_people limits (n, c, p) with FakeSearchProvider; reserve + `move_reserve`; L24/L25; exports write contact_log; existing e2e still passes (update it to create a project first).

## Acceptance
All existing tests pass (updated only where they must pass a project); new tests pass; migrating a copy of the owner's real v1 workspace (if one exists) loses nothing.

## Planner note for the reviewer (added 24 Oct during the build)
- `people._searched_companies` must order the first `max_companies` companies by a value that never changes (company `id`, the order they were added), not `updated_at`: any edit bumps `updated_at`, so a chunked search can cover more than c companies. Require this fix (one line + the test that sets `updated_at`).
