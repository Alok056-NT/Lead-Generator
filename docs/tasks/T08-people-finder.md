# T08: People finder and confirmation engine

Depends on: T04, T06, T07. PLAN sections: 11 (read it fully), 8 step 5, tools 17–20.
Port from: `reference/harvey/collectors/accounts.py` (most logic), `reference/harvey/export.py`.

## Goal
For every approved company × designation group, search Google for public LinkedIn profiles, and keep a person as **confirmed** only when the search result itself proves every rule in PLAN section 11. Everyone else is stored as **review** with reasons. This is the heart of the product: precision matters more than volume.

## Deliverables
### `services/confirm.py` (pure functions, no I/O)
- `parse_result(result: SearchResult, company: CompanyCtx, campaign: CampaignCtx) -> Candidate | None`
  - `None` when the link is not a personal profile (`/in/` regex in PLAN 11.1).
  - Name from the title's part before ` - `; strip trailing `| LinkedIn`, `…`, `...`.
  - Headline: title part after the name; if Google truncated it, prefer the longer headline from snippet layouts `Name. Headline. …` or `Headline at Company · Experience: …`.
  - `greeting_name` logic:
    - strip credentials and brackets: `, MBA`, `, PhD`, `(IHRP-CP)`, `IHRP-SP`, `GPHR`, `CSP`, `MSc`, `(Max)` → if a bracketed single word looks like a nickname (capitalised, 2–10 letters, not a credential), use it as greeting (e.g. "Lim Jun Hao (Max)" → "Max");
    - initial-only first name (`R KUMARASAMY`) → use the last name in title case;
    - ALL-CAPS → title case;
    - East Asian name order (use a built-in list of ~150 common Chinese, Korean, Vietnamese surnames and ~300 common Western given names; document the heuristic's limits in the module docstring):
      - 3 tokens, last token is a surname, first token not a Western given name → given name is tokens 1–2 ("Mei Xin Tan" → "Mei Xin");
      - 3 tokens, first token is a surname, last token is not → given name is tokens 2–3 ("Lim Wei Jie" → "Wei Jie");
      - 2 tokens, first is a surname and second is a Western given name → second ("Chen Rachel" → "Rachel");
      - otherwise the first token;
    - fall back to first name.
  - Reasons (strings with stable codes, e.g. `"L04:company mentioned but not confirmed as current employer"`) for every failed rule in PLAN 11.2–11.9, using `core.countries.matches_location`, `designations.classify`, `seniority.meets`, `seniority.is_top_leader`, campaign exclusions and the suppression list.
  - Snippet normalisation: treat `·`, `•`, `�` and ` - ` as separators; decode HTML entities (`&amp;`).
- `Candidate` dataclass: names, greeting_name, headline, designation, seniority, linkedin_url (canonical: lowercase host, strip query/trailing slash), evidence_title, evidence_snippet, reasons, confirmed (property).

### `services/people.py`
- `build_queries(campaign, company) -> list[(group_label, query)]`: PLAN 11 query format; quote each search term; use the company's primary name; append the country name; skip groups whose `search_terms` is empty.
- `estimate_people_search(campaign_id, company_ids=None) -> {"companies", "searches", "cached", "credits_needed", "budget_left"}`.
- `find_people(campaign_id, company_ids=None, confirm=False, cursor=None, max_searches_per_call=30) -> dict`:
  - If `credits_needed > settings.confirm_threshold_credits` and not `confirm` → return the estimate with `needs_confirmation: true`.
  - Process companies in a stable order; at most `max_searches_per_call` searches per call (keeps each call < 60 s); return `next_cursor` until done.
  - Store confirmed people (`status=confirmed`), review people (`status=review`, `reasons_json`), skip duplicates (same canonical URL in campaign; or same name+company).
  - Return counts so far + a sample of 5 confirmed and 5 review rows.
- `list_people(campaign_id, status=None, designation=None, company_id=None, has_email=None, page=1)`.
- `set_person_status(campaign_id, ids, status, reason)`; promoting `review → confirmed` requires a `reason` and stores it.
- `recheck_people(campaign_id)`: re-run `parse_result` on stored evidence with current rules (used after rule changes; moves people between confirmed/review; returns diff).

## Tests: synthetic snippet catalogue (`tests/fixtures/snippets.py`)
Create ≥ 45 synthetic search results (invented people/companies, e.g. "Mira Lee - Training Manager at Northwind Health") covering every shape and rule. At minimum:
1. Headline names company → confirmed.
2. `Experience · Company` form → confirmed.
3. `Headline @ Company` form → confirmed.
4. `Name. Headline. Company School. City.` form (company only after the headline) → confirmed.
5. `Company. Jul 2011 - Present` → confirmed.
6. Former employer only (`Experience · Chief People Officer. OtherCo · … Company`) → review L04.
7. Headline `at OtherCo` while snippet mentions Company → review L09.
8. Truncated `at North` for "Northwind Health" → confirmed.
9. Country: `ca.linkedin.com` + Toronto for an SG campaign → review L05; `www` + "Location: Singapore" → confirmed.
10. Location field overrides subdomain (see T03).
11. Machine Learning Lead → review L06.
12. Head of IT Operations for safety_ops → review L13/L06.
13. Senior Executive, Training → review L14/L07.
14. Talent Acquisition Partner → review L07.
15. HR Business Partner → confirmed (manager).
16. Managing Director, Group Head of … at a bank for owners group → review L10.
17. Deputy CEO → confirmed owners.
18. "Former Managing Director" → review L08.
19. Job seeker "Looking for next opportunity" → review L15 (warn reason, still review).
20. Company page `linkedin.com/company/…` → None.
21. Ambiguous short alias (company "Example Holdings (EH)") must not match "EH" inside other words; all-caps short alias matches case-sensitively.
22. Name with credentials, nickname in brackets, initial-only, ALL CAPS, three-token East Asian name: greeting_name expectations.
23. HTML entity `Housing &amp; Development` decoded.
24. Excluded competitor company → review L03; suppressed LinkedIn URL → review L16.
Plus service tests with `FakeSearchProvider`: chunking/cursor, confirmation threshold, budget refusal, dedupe across groups (same person found by two queries), `recheck_people` diff.

## Acceptance
- All tests pass; coverage ≥ 90% on confirm.py.
- Reviewer will run a small real search (3 companies) and compare with the NXT pilot behaviour.

## Notes
- Precision over recall: when unsure, `review`, never `confirmed`.
- Never store more than the title/link/snippet Google returned. No profile page fetching.
