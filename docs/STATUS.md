# Status

Statuses: `todo` → `in progress` → `review` (builder done) → `done` (reviewer approved) / `changes` (reviewer asked for fixes).

| Task | Title | Status | Notes |
|---|---|---|---|
| T01 | Scaffold, config, workspace, secrets, `leadgen doctor` | done | Reviewed 2026-10-14 (fix round 1) |
| T02 | Database layer and schema | done | Reviewed 2026-10-14 (fix round 1) |
| T03 | Countries and compliance tables | done | Reviewed 2026-10-14 (fix round 1) |
| T04 | Provider framework and search providers | done | Reviewed 2026-10-14 (fix round 2). manual check pending: `leadgen keys set serper` + `leadgen keys test serper` with the owner's real key (1 credit); note the status/body a wrong key returns |
| T05 | Offer intake (URL, text, file) and OfferProfile | done | Reviewed 2026-10-14 (fix round 2). manual check pending: ingest a real public website and a local DOCX; try one public and one private Google Doc link |
| T06 | Campaigns and designations | review | |
| T07 | Companies: add, verify, approve | todo | |
| T08 | People finder and confirmation engine | todo | |
| T09 | Email finder and verification | todo | |
| T10 | Drafting workflow | todo | |
| T11 | QA engine and AI review | todo | |
| T12 | Export (CSV) and lemlist push | todo | |
| T13 | MCP server | todo | |
| T14 | Email sending | todo | |
| T15 | Mailbox sync and analytics | todo | |
| T16 | Local dashboard | todo | |
| T17 | Installers and AI connectors (Claude, ChatGPT) | todo | |
| T18 | User documentation | todo | |
| T19 | End-to-end test and v1.0 release | todo | |
| T20 | Claude Desktop extension (.mcpb), optional | todo | |
| T21 | AI search fallback (free mode), **build right after T11** | todo | |

## Task summaries
(Builder adds a short summary per task here.)

### T01 (done)
Installable `lead-generator` package (`src/leadgen`, console script `leadgen`) with workspace paths, atomic `secrets.env` handling (comments preserved, masked display), pydantic `Settings` in `settings.yaml` (unknown keys kept), rotating-file + stderr logging (stdout stays clean), text and HTTP helpers, and `leadgen doctor | version | paths`. 41 tests pass (1 POSIX-only skip), ruff clean, 97% coverage. CI workflow runs Windows/Ubuntu x Python 3.11/3.13. `.gitattributes` added and files renormalised to LF.
Files: pyproject.toml, .gitattributes, .github/workflows/tests.yml, src/leadgen/{__init__,__main__,cli}.py, src/leadgen/core/{__init__,paths,secrets,config,logging,text,http,fsutil}.py, tests/{conftest,test_paths,test_secrets,test_config,test_text,test_http,test_logging,test_cli,test_fsutil}.py, docs/DECISIONS.md, docs/STATUS.md.
Extra beyond card: `core/fsutil.py` (shared atomic write helper), `shutdown_logging()`, `ConfigError`.

### T02 (done)
SQLite layer: `schema.sql` holds all 15 PLAN section 6 tables (cascades, unique constraints incl. partial unique indexes for empty domain / LinkedIn URL, FK and lookup indexes). `db.py` has `connect` (foreign keys, WAL, 5 s busy timeout, Row factory), `session` (commit/rollback/close), `init_db` (idempotent, forward-only `MIGRATIONS` list tracked in `schema_version`, each migration atomic), `now`, and whitelisted helpers `insert/update/get/query/upsert` that auto-encode and decode `_json` columns. `leadgen doctor` now creates the database on first run and prints `Database : ok (schema v1)`. 81 tests pass (1 POSIX-only skip), ruff clean, db.py coverage 98%.
Files: src/leadgen/{schema.sql,db.py,cli.py}, tests/{test_db.py,test_cli.py}, docs/{DECISIONS.md,STATUS.md}.
Extra beyond card: `current_version()`; `session(path)`/`init_db(path)` accept an explicit path (used by tests); helpers take `conn` as first argument.

### T03 (done)
`core/countries.py` holds the 40-country table (dataclass `Country`, `COUNTRIES`, `get`, `all_countries`, `matches_location`) built from the worker's `countries_data.py`; matching is accent- and case-insensitive, whole-word, longest-name-first, and follows the card's three rules (explicit `Location:` value, then LinkedIn subdomain, then names/cities). `core/compliance.py` has one researched `EmailRule` per country (EU/EEA share a base with national overrides; DE, AT, NL, ES, IT, DK, PL, CH and 9 non-EU countries default to `block_without_consent`), plus `rule_for` and `send_gate`. Sources, read/secondary status and dates are in DECISIONS.md "Compliance sources". Added `tzdata; sys_platform == "win32"` to pyproject. 157 tests pass (1 POSIX-only skip), ruff clean. Review fixes: IT and DK now strict (sources say business recipients are covered; DK cites Dansk Erhverv), CA and CH require a postal address and CA a truthful subject (Competition Act s.52.01), removed the KR alias "ROK" (clashes with Polish "rok").
Files: pyproject.toml, src/leadgen/core/{countries,countries_data,compliance}.py, tests/{test_countries,test_compliance}.py, docs/{DECISIONS,STATUS}.md.
Corrections to the worker draft: removed ambiguous aliases (RSA, ROC/Republic of China, Holland, Nippon) and the city Salvador; added AU alias "New South Wales" and US aliases "New Mexico", "New England" so longer place names are not mistaken for Wales, Mexico, England.

### T04 (done)
`providers/` has the framework (`base.py`: `ProviderError`, `ProviderInfo`, `record_usage`/`usage` on `api_usage`, thread-safe `RateLimiter`, 2-retry backoff helper that never retries 401/403), the search interface with the 30-day `cached_search`, Serper (default), Brave (optional), DuckDuckGo HTML (off unless `allow_ddg_fallback`), `FakeSearchProvider`, and `registry.py` (all eight `ProviderInfo`, `get_search_provider`, `needs_key_response`, `quota_response`, `budget_response`, `error_response`, `save_api_key`). `services/status.py` has `list_providers` (+ `set_key`/`check_key` for the CLI) and `leadgen keys list|set|test` was added. Serper's own country list uses `gb`, so T03's GB `gl` changed from `uk` to `gb`. 224 tests pass (1 POSIX-only skip), ruff clean, providers/services coverage 97%. Real Serper call (`leadgen keys set serper`, `leadgen keys test serper`, 1 credit): **manual check pending** (owner).
Files: src/leadgen/providers/{__init__,base,search,serper,brave,ddg,fake,registry}.py, src/leadgen/services/{__init__,status}.py, src/leadgen/{cli.py,core/secrets.py,core/countries_data.py}, tests/{conftest,test_providers_base,test_serper,test_brave_ddg,test_search_cache,test_registry,test_countries}.py, tests/fixtures/ddg_results.html, docs/{DECISIONS,STATUS}.md.
Reviewer fixes: `RateLimiter` uses `perf_counter` (Windows `monotonic` ticks every 15.6 ms); Brave `_quota_hit` pairs `X-RateLimit-Policy`/`Remaining` windows by position and reports quota only when a window over a day has 0 left; Brave `country` is sent only if in `BRAVE_COUNTRIES` (else `ALL`); DDG challenge is detected by markup (`anomaly-modal`/`anomaly.js`) and only when no results parse. Round 2: Brave `_quota_hit` ignores windows whose limit is 0 (Brave: "0 for unlimited"), so an uncapped plan's per-second 429 stays `rate_limited`; DECISIONS Brave row repaired.
Extra beyond card: `secrets.stored()`, `registry.check_provider`/`error_response`/`get_provider`, `SearchResult.low_confidence`.

### T05 (done)
`services/offer.py` has `ingest_url` (website crawl or public Google Doc), `ingest_text`, `ingest_file` (PDF, DOCX incl. tables, TXT, MD; 20 MB), chunked `read_offer_text` (cursor, 40,000 chars, line-break aware), `save_profile`, `get_offer`, `list_offers` and the pydantic `OfferProfile` (whitespace normalised, lists de-duplicated, overlapping confirmed/forbidden claims rejected). `ingest_*` return `offer_id`, `unconfirmed_snippets` (lines with `[CONFIRM`, `TBC`, `TODO`, `XX%`, highlighted DOCX text, ...) and a structured error with a paste/file hint when a page is dead, blocked or empty. The crawler (`offer_web.py`, ported from Harvey's `FallbackCrawler`) is same-site, honours robots.txt and Crawl-delay, 1 request/s, max 15 pages, 50 s limit. `guides/offer_profile.md` tells the AI how to write the profile. Reviewer fixes: a DOCX run with `w:highlight="none"` no longer makes the file unreadable; python-docx pinned `>=1.1`; the URL guard is also applied to every redirect hop (crawler and Google Docs). Round 2: the URL guard also refuses trailing-dot hosts (`localhost.`, `foo.local.`) and old-style IPv4 forms (`127.1`, `2130706433`, `0x7f000001`, read via `socket.inet_aton`); the subpage-redirect test now registers its routes before the catch-all 404 and fails if the guard is disabled. 303 tests pass (1 POSIX-only skip), ruff clean. Real website, real DOCX and real Google Doc ingestion: **manual check pending** (owner).
Files: pyproject.toml, src/leadgen/services/{offer,offer_web,offer_files}.py, src/leadgen/guides/offer_profile.md, tests/{test_offer,test_offer_web,test_offer_files}.py, docs/{DECISIONS,STATUS}.md.
Extra beyond card: `guide_text()`, `find_unconfirmed()`, hard URL checks (no localhost/private IPs, also on redirects), Crawl-delay support, `[HIGHLIGHT: ...]` marking in DOCX text. `ingest_*` return a dict, not a bare id (see DECISIONS).

### T06 (review)
`services/designations.py` has `DesignationGroup`, the 11 `TEMPLATES` (data in `designation_templates.py`; ld/hr/safety_ops/owners keep Harvey's keyword lists), `list_templates`, `resolve_groups`, `matches` and `classify` (Harvey's whole-word rules: false friends removed first, `vice president` -> `vp`, role part before ` at `/`@` only, `L&D` spellings). `services/seniority.py` has `seniority`, `meets`, `is_top_leader` and `LEVELS`. `services/campaign.py` has `create_campaign`, `update_campaign`, `get_campaign` (with counts of companies, people, emails, messages by status), `list_campaigns`, `archive_campaign` and `CampaignError` (stable `code`, `as_response()`). 755 tests pass (1 POSIX-only skip), ruff clean, coverage designations 99%, seniority 100%, campaign 98%.
Files: src/leadgen/services/{designations,designation_templates,seniority,campaign}.py, tests/{test_designations,test_seniority,test_campaign}.py, tests/fixtures/titles.py (121 synthetic titles), docs/{DECISIONS,STATUS}.md.
Corrections to the worker drafts: finance, marketing, sales, procurement, facilities and customer_service keyword lists rewritten to drop redundant entries and ambiguous words (`seo*`, `cro`, bare `controller`); fixture "Accounts Payable Manager" now `finance`; fixture header comments updated; "IT Network Operations Manager" -> `it` is correct (the false friend leaves "IT Manager"). Extra beyond the card (see DECISIONS T06): CMO/CIO/CISO/CPO as c_suite, trailing "X Head" as vp, false-rank guards (product owner, lead generation, assistant to ...), `search_mode`/option shortcuts on `update_campaign`, unique export-folder names.

## Follow-ups
(Ideas or issues found during tasks that are out of scope for the current card.)
- T01: `guides/*.md` and `schema.sql` are listed as package data but do not exist yet (T02/T18 create them).
- T01: settings fields are type-checked only (e.g. `quiet_start` format, `daily_cap <= max_daily_cap` are not validated); add when the sending task (T14) uses them.
- T01: CLI output on Windows: when stdout is piped it uses the ANSI code page (e.g. cp1252), so an unencodable character (e.g. in a workspace path) would raise UnicodeEncodeError. Consider `sys.stdout.reconfigure(errors="replace")` in T17.
- T01: the hand-rolled secrets.env parser keeps inline `# comments` as part of an unquoted value.
- T13: the MCP set-key tool should accept only known/provider keys, because `secrets.set` accepts any UPPER_CASE name.
- T06 (done in T06): campaign names are checked for a unique export-folder slug.
- T06: "General Manager"/"Country Manager" have seniority `manager` (the card gives no rank); owner to decide if `director` is better for `seniority_min=director` campaigns. T08 must apply the PLAN 11.6 weak-fit rule and the 11.7 owner rule itself (`seniority.is_top_leader` is provided).
- T06: after `update_campaign` changes countries, designations or seniority, people already found are not re-classified; T08 should offer a re-classify step.
- T06: `create_campaign` does not require the offer to have a saved profile; T10 must check it before drafting.
- T02: adapters (MCP server T13, dashboard T16) must call `db.init_db()` at startup; `connect()`/`session()` do not create the schema.
- T02: no `delete` helper yet (cascades are done with plain SQL); add one in the first service that needs it. `qa_issues` and `search_cache` have no foreign keys, so services must clear a deleted entity's `qa_issues` rows.
- T02: `upsert` conflict targets for partial unique indexes are listed in `db._PARTIAL_CONFLICT`; add an entry if a later migration adds another partial unique index.
- T02: the migration runner is not safe when two processes start at the same time (MCP server and dashboard both run `init_db`). Once a migration exists, both can read the same applied version and the second fails (e.g. "duplicate column"). Whoever adds the first migration should use `BEGIN IMMEDIATE` and re-read `current_version` inside the transaction. No code change is needed now because `MIGRATIONS` is empty.
- T03: LinkedIn subdomains are unverified (see DECISIONS); check against real Serper results in T08's pilot. (T04 resolved: Serper's country list uses `gb`; GB `gl` is now `gb`.)
- T03: most non-core compliance rows rest on DLA Piper summaries (type S in DECISIONS); several official sites blocked automated fetches (EUR-Lex, legislation.gov.uk, ACMA, CRTC, Swiss/Austrian portals). An owner or lawyer spot-check of the `block_without_consent` and `warn` rows is advisable before sending, and rows older than 6 months must be re-checked.
- T03: South Korea's subject-line marking rule and Hong Kong's UEMO section detail were not verified from primary text; Singapore's `<ADV>` label legally applies to bulk sending only, but the table applies it to all SG email (T14 should add it to every SG subject).
- T14: `send_gate` takes ISO codes or names and returns `EmailRule`s; `block_without_consent` means a recorded per-recipient basis is needed (guessed or scraped addresses do not qualify under CA, AU, NZ, JP etc.); `required_elements` lists what each message template must carry.
- T04: Serper error responses (bad key, out of credits) and the credit cost for `num` above 10 are undocumented where I could read; the mapping in `providers/base.error_for_status` is defensive. The owner's real-key check should confirm a wrong key gives `auth` and note the actual status/body (DECISIONS T04 rows). Default `num=10` costs 1 credit.
- T04: Brave quota detection now follows the documented header format but the 429 headers themselves are undocumented; verify with a real key if Brave becomes the main provider.
- T04: Hunter, Reoon, ZeroBounce, lemlist and SMTP have `ProviderInfo` only; each task must add its class to `registry._FACTORIES` (then `save_api_key`/`keys test` work for it). Their sign-up URLs and free-tier text are unverified (T09/T12/T14).
- T04: `record_usage`/`cached_search` need the schema; adapters must call `db.init_db()` first (the `keys` CLI commands do).
- T04/T08: services must call `registry.error_response` so `ProviderError("quota")` and `not_configured` reach the AI as the structured `needs_key` / `quota_exhausted` replies. DDG ignores the country; put it in the query text.
- T05: callers must run `db.init_db()` before the offer functions (same as T04). T13 should call `offer.OfferError.as_response()` to build error replies and expose `offer.guide_text()` with the workflow guide.
- T05: JavaScript-only sites give little or no text (no rendering; Harvey's Cloudflare crawler was not ported). The error hint tells the AI to ask for pasted text or a file.
- T13/T17: for remote ChatGPT mode, resolve DNS for each requested host and redirect hop and refuse private/loopback/link-local IPs (the T05 URL guard checks only the typed address and redirect targets as written, not what a hostname resolves to).
- T05: `proof_points` claims are not checked against `forbidden_claims` (the card only asks for confirmed vs forbidden); T11 M05/M06 should treat both.
- T05: `offer_web._check_public_url` lets a `ValueError` escape (not `WebError`) when the typed host contains a NUL character (`socket.inet_aton` rejects it before the OSError path); `ingest_url` then raises instead of returning the structured error. Fails closed, so harmless; catch `ValueError` there too when next touching the module.
