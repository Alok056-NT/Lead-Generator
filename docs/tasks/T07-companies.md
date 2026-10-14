# T07: Companies (add, verify, approve) and web research

Depends on: T04, T06. PLAN sections: 8 step 3, 10 tools 13–16.

## Goal
Let the AI build a target-company list: it proposes companies (from its knowledge and from `search_web` research), the tool normalises, de-duplicates and verifies them, and the user approves.

## Deliverables
### `services/companies.py`
- `search_web(query, country=None, num=10) -> list[dict]`: thin wrapper over `providers.search.cached_search` with the country's `gl`; returns title/link/snippet; records budget usage under the campaign when `campaign_id` is given; respects `budgets.search`.
- `add_companies(campaign_id, companies: list[dict]) -> dict`:
  - Input per company: `name` (required), `aliases` (optional list), `domain` (optional; may be a URL), `sector`, `size_note`, `source` ("ai_knowledge" | "search" | "user"), `notes`.
  - Normalise: strip, collapse spaces; domain → registrable host lower-case without `www.`, scheme or path.
  - Alias hygiene (PLAN section 11): always include `name`; drop aliases shorter than 3 characters; flag (warn, keep) all-caps aliases ≤ 5 chars that are common words or ambiguous abbreviations (keep a small stoplist: BD, SAL, SIT, SP, AI, HR, IT, PR, CS, OD, MD); drop generic words ("Group", "Holdings", "Singapore") as standalone aliases.
  - De-duplicate within the campaign by domain, else by normalised name.
  - Excluded/competitor names or domains from the campaign → stored with status `excluded`.
  - Domain check (parallel, 4 workers, 5 s timeout each): DNS A/AAAA or MX exists, then `HEAD`/`GET` the homepage (follow redirects; record final host). `domain_ok` = True/False with `notes` on failure. Never block insertion on a failed check.
  - Returns `{"added": n, "duplicates": [...], "excluded": [...], "domain_failures": [...], "alias_warnings": [...]}`.
- `list_companies(campaign_id, status=None, page=1, page_size=50)`.
- `set_company_status(campaign_id, ids, status, reason="")`: candidate/approved/excluded.
- `approve_all(campaign_id)`: approves every candidate with `domain_ok` (or no domain) and no warnings; returns what it skipped.
- `import_companies_csv(campaign_id, path)`: columns `company,aliases,domain,industry,notes` (the NXT file format; aliases `;`-separated).
- `research_queries(campaign_id) -> list[str]`: helper suggestions for the AI, e.g. `"largest {sector} companies in {country}"`, `"top {sector} employers {country} list"`, `"{sector} companies {country} 1000 employees"`, `site:wikipedia.org list of {sector} companies {country}`. (The AI decides which to run.)

## Tests
- Domain normalisation table (URLs, `www.`, paths, uppercase, IDN).
- Alias hygiene warnings and drops.
- Dedup by domain and by name.
- Exclusions → status `excluded`.
- Domain check with mocked DNS (monkeypatch `dns.resolver.resolve`) and respx HTTP.
- CSV import of a synthetic file.
- `search_web` uses the cache and respects the budget (raises a structured error when over budget without confirm).

## Acceptance
Tests pass. Manual: add 5 real companies for a test campaign and see `domain_ok` values.
