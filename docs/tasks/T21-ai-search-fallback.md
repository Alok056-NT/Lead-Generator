# T21: AI search fallback (free path when credits run out)

Depends on: T08, T09, T11. Build it **right after T11**, before T12. PLAN section 7a (read fully), tools 37–40, QA codes L21–L23.

## Goal
Let the host AI's own web search tool replace Serper/Hunter when the user has no credits or doesn't want to pay. The AI runs the queries; the Lead Generator still does all the confirming and checking. Fabricated or paraphrased results must not get through.

## Verify first (DECISIONS.md)
- Claude (Desktop/claude.ai): how a user turns web search on for a chat, and that it can be used in the same conversation as a local MCP connector.
- ChatGPT: whether its web search can be used in the same conversation as a custom MCP connector in Developer Mode, and how to enable it. If it can't, document that AI search mode is Claude-only for now.
- What a typical result from each AI's search tool contains (title, URL, snippet?) so the guide tells the AI exactly which fields to copy.

## Deliverables
### Refactor (small, in `services/people.py`)
Extract the per-result processing from `find_people` into `process_results(campaign, company, group_label, query, results: list[SearchResult], origin: str) -> ProcessSummary` so the API path and the AI path share **identical** confirmation code. `find_people` calls it with `origin="api"`. No behaviour change for the API path (existing tests must still pass unchanged).

### `services/ai_search.py`
- `ensure_tasks(campaign_id, kind="people"|"email"|"company") -> int`: creates `search_tasks` rows for work not yet done:
  - people: one per approved company × designation group, using `people.build_queries` (same strings as the API path); skip pairs already searched via API (search_cache or existing people with that company+group);
  - email: for each approved company domain with confirmed people and no derived pattern: `"@<domain>"`, and `site:<domain> (contact OR email)`;
  - company: the `companies.research_queries` strings (optional helper; the AI may also research freely).
- `get_search_tasks(campaign_id, kind="people", batch_size=10, cursor=None) -> dict`: open tasks + instructions block (verbatim copying rules from PLAN 7a step 3, the fields to copy, "submit an empty list if nothing relevant", "do not open LinkedIn pages; search results only") + `remaining`.
- `submit_search_results(task_id, results: list[dict], tool_name: str) -> dict`:
  - validate shape: each result needs `url` and `title`; `snippet` optional but required for confirmation (results without a snippet can only reach `review`);
  - anti-fabrication checks (PLAN 7a step 4): URL format, slug–name consistency (new helper `confirm.slug_matches_name(url, first, last) -> "match"|"no_tokens"|"mismatch"`, transliterate accents, ignore trailing hex/numeric IDs, accept initials), snippet minimum length 40 chars, paraphrase markers (`appears to be`, `likely`, `probably`, `I found`, `seems to`, `based on`), same URL for different names in the campaign → both review;
  - if **every** result fails the URL check → reject the whole submission with `{"ok": false, "error": {"code": "not_verbatim", "hint": "Copy titles, URLs and snippets exactly from your search tool; don't build URLs."}}` and keep the task open;
  - people tasks → `people.process_results(..., origin="ai_web_search")`; email tasks → pass addresses found on the domain to `emails.learn_pattern_from_examples(domain, emails, source_urls)` (add this function to `services/emails.py`; reuse `infer_pattern_from_email`);
  - mark task `submitted`, store `result_count`; return per-result outcome (confirmed/review/rejected + reasons) so the AI can report progress.
- `skip_task(task_id, reason)`.
- Mode switching: `services/campaign.update_campaign(search_mode=…)` already exists (T06); add `people.find_people` behaviour: when `search_mode == "ai"` return `{"ok": false, "use_ai_search": true, "hint": "call get_search_tasks"}`; when `auto` and the provider raises `quota`/`not_configured` or the budget is reached, return the estimate plus `fallback_available: "ai_web_search"` (T04 already defines the field).

### Emails without keys (`services/emails.py`)
- `scrape_site_emails(domain, max_pages=5) -> list[(email, url)]`: fetch homepage, /contact, /contact-us, /about, /about-us, /team, /people; collect `mailto:` and plain-text addresses on that domain; ignore role addresses for pattern inference except to confirm the domain receives mail. Free, no key; respects robots.txt; 1 req/s.
- In `find_emails`, when no Hunter key or credits: use cached pattern → AI-submitted examples → scraped examples → default; status `guess` unless a verifier key is available.

### Spot check (`services/spot_check.py`)
- `get_spot_check(campaign_id) -> dict`: if any confirmed people have `evidence_origin = "ai_web_search"` and no spot check exists, pick a random sample (max(5, 5%), cap 15; seed stored so it's reproducible) and set `spot_check = "pending"`; return each with name, title, company, LinkedIn URL and the instruction for the AI: "Ask the user to open each link and reply 'ok' or 'wrong' for each (right person, right company, right role)."
- `record_spot_check(campaign_id, results: list[{person_id, verdict: "ok"|"wrong", note}])`: `wrong` → person to review with reason `L21:spot check failed`; if ≥ 2 wrong, set campaign flag `ai_search_needs_recheck` (all AI-search leads get an L21 block until the user records a second spot check that passes).

### QA (`services/qa_leads.py`)
Add L21, L22, L23 exactly as PLAN section 14. L21 blocks export for AI-search leads while the spot check is pending/failed.

### Exports (`services/export.py`, if T12 is already built; otherwise note in STATUS.md for T12)
Add `evidenceOrigin` column (lemlist/generic) and include origin in `qa_report.csv`.

### Guide (`guides/workflow.md` section, created in T13; if T13 isn't built yet, write `guides/ai_search.md` and T13 will include it)
Explain to the AI: when to offer AI mode (no key, quota, budget, user preference); the capability check; batch size 10; verbatim copying; never visit LinkedIn profile pages; report progress every batch; run the spot check before export; for big runs recommend a key.

## Tests
- `process_results` refactor: all existing T08 tests pass untouched.
- Task creation: correct queries; no duplicates on re-run; pairs already searched via API skipped.
- Submission: valid verbatim results confirm exactly like the API path (feed the same synthetic snippets through both paths and assert identical outcomes apart from `evidence_origin`).
- Anti-fabrication: invented URL with a slug for a different name → L22 block; paraphrased snippet → review; all-bad submission → `not_verbatim`; same URL for two names → both review; result without snippet → review.
- `slug_matches_name` table (accents, initials, numeric suffixes, random-ID slugs).
- Email: pattern learned from AI-submitted examples; site scrape with respx; status `guess` without a verifier; L23 behaviour for export vs sending.
- Spot check: sample size rules, reproducible seed, wrong verdicts → review + campaign flag, L21 block lifecycle.
- Mode switching: `find_people` in `ai` mode returns the hint; `auto` + quota error returns `fallback_available`.

## Acceptance
Tests pass; coverage ≥ 90% on ai_search.py and spot_check.py. Reviewer runs a 3-company campaign in AI mode from Claude Desktop and checks that results match an API run of the same companies.
