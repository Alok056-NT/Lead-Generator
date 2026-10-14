# Lead Generator: Master Plan

Status: approved design, v1. Owner: Alok (Alok056-NT). Planner/reviewer: Claude Opus. Builder: Claude Sonnet 5.5 (high), one task card at a time.

This document is the single source of truth for *what* we are building. Task cards in `docs/tasks/` say *how* to build each piece. If a task card and this plan disagree, stop and ask; do not guess.

---

## 1. What it is

A plug-and-play lead generation toolkit that an AI assistant (Claude or ChatGPT) drives through a conversation. A non-technical user says "find me leads for this product" and the AI:

1. learns the product or service from a web page, pasted text or a document,
2. asks which country, sectors, company size, job roles and channels (email, LinkedIn or both),
3. builds a list of target companies,
4. asks for free API keys when it needs them (paid only if no free option exists),
5. finds people at those companies (LinkedIn profile URLs via Google search, never by logging into LinkedIn),
6. finds and verifies their work email (when email is a chosen channel),
7. writes a personalised message per person per channel,
8. runs a rigorous quality check on every lead and every message, including the AI re-reading each message,
9. exports to CSV (generic / lemlist / Instantly), or pushes into a lemlist campaign via API, or sends emails itself with analytics.

Everything runs on the user's own computer. Lead data and API keys never leave it except to call the providers the user has chosen.

### Non-goals (v1)
- No LinkedIn login, LinkedIn automation or LinkedIn scraping. LinkedIn sending is done by tools built for it (lemlist).
- No hosted multi-user SaaS. Local only. (ChatGPT needs a public URL; we provide a tunnel, see section 9.)
- No paid data providers by default (Apollo, ZoomInfo, etc.). The provider framework allows adding them later.
- The tool never invents leads, facts or claims. The AI drafts; the tool checks.

### Design principles
1. **The AI drives, the tool does.** Reasoning, conversation, company ideas and writing come from the host AI. Fetching, storing, verifying, checking, exporting and sending are deterministic tool code.
2. **Honest data.** Every lead carries evidence (the search snippet) and a confirmation status with reasons. Every email carries a verification status. Nothing unconfirmed is exported unless the user explicitly asks.
3. **Spend only with consent.** Any tool call that spends provider credits estimates first. Above a threshold it requires `confirm=true`.
4. **Free first.** Default providers are the ones with free tiers. Paid providers are opt-in and labelled.
5. **Plug and play.** One install command. One line to connect Claude Desktop. A guided path for ChatGPT.
6. **No real personal data in the repo, ever.** The repo is public. Tests use synthetic people only.

---

## 2. Users and AI hosts

- Primary users: a non-technical team that only chats with the AI, plus a simple local web page to browse leads, messages and analytics.
- AI hosts, in priority order:
  1. **Claude Desktop** (local MCP server over stdio; best experience).
  2. **Claude Code** (same server, `claude mcp add`).
  3. **ChatGPT** (needs a *remote* MCP server over HTTPS: paid plan + Developer Mode; we run the local server over Streamable HTTP and expose it with a Cloudflare quick tunnel and a secret URL path).
- The server must work with any MCP client, so nothing may depend on Claude-only features. ChatGPT may ignore MCP *prompts*, so the workflow guide is also available as a tool (`get_workflow_guide`).

---

## 3. Architecture

```
            ┌───────────────────────── user's computer ──────────────────────────┐
 Claude ───►│ MCP stdio ─┐                                                       │
 Desktop    │            ├─► leadgen.mcp_server (tools, prompt, guide)           │
 ChatGPT ──►│ HTTPS tunnel ─► Streamable HTTP ─┘          │                      │
 (remote)   │                                             ▼                      │
            │                                  leadgen.services (use cases)      │
            │      ┌────────────┬───────────┬───────────┼────────────┬────────┐  │
            │   offer.py    campaign.py  companies.py people.py   emails.py  qa/  │
            │      │            │           │           │            │      │   │
            │      └────────────┴─────┬─────┴───────────┴────────────┴──────┘   │
            │                    leadgen.db (SQLite, ~/LeadGenerator/leadgen.db)  │
            │   providers/ (search, email finder, verifier) ── HTTPS ──► Serper,  │
            │                                                   Hunter, Reoon…   │
            │   export/ (CSV, lemlist API)   send/ (SMTP/IMAP, analytics)         │
            │   dashboard (FastAPI + static HTML on 127.0.0.1:8765)               │
            │   cli.py (`leadgen …`)                                              │
            └──────────────────────────────────────────────────────────────────────┘
```

Layers (strict: upper layers may call lower layers only):
1. `mcp_server.py`, `cli.py`, `dashboard/`: thin adapters. No business logic.
2. `services/`: one module per use case. Pure Python, synchronous, fully unit-tested.
3. `providers/`: HTTP clients for third-party APIs behind small interfaces, with fakes for tests.
4. `db.py`: SQLite access (stdlib `sqlite3`, WAL mode). No ORM.
5. `core/`: config, workspace paths, secrets, logging, countries, compliance tables, text utilities.

Concurrency: synchronous code everywhere. Network-heavy loops (searching 100 companies) use a simple thread pool in the service with a small worker count (default 4) and per-provider rate limits. No asyncio in services (simpler for maintainers). The MCP SDK may run async; tool functions call sync services via `anyio.to_thread.run_sync` if the SDK requires async handlers.

Long operations: MCP tool calls have time limits (Claude: ~240 s per call). Any operation that can exceed 60 s must be **chunked**: the tool processes up to N items per call and returns `{"done": false, "next_cursor": …}` so the AI calls it again. Never block for minutes.

---

## 4. Repository layout

```
Lead-Generator/
  README.md                 user-facing quick start (non-technical)
  CLAUDE.md                 rules for AI agents building this repo
  LICENSE                   MIT (Alok)
  NOTICE.md                 third-party credits (Harvey, MIT, Ethan Rogers)
  pyproject.toml            package "lead-generator", import "leadgen", CLI "leadgen"
  install.ps1 / install.sh  one-line installers (T17)
  docs/
    PLAN.md                 this file
    STATUS.md               task tracker (builder updates it)
    DECISIONS.md            verified external facts + design decisions, with sources and dates
    tasks/Txx-*.md          task cards
    user/                   end-user guides (T18)
  src/leadgen/
    __init__.py  __main__.py  cli.py  mcp_server.py
    core/   config.py  paths.py  secrets.py  logging.py  countries.py  compliance.py  text.py  http.py
    db.py   schema.sql
    providers/  base.py  serper.py  brave.py  ddg.py  hunter.py  reoon.py  zerobounce.py  fake.py  registry.py
    services/   offer.py  campaign.py  companies.py  people.py  designations.py  confirm.py
                emails.py  drafting.py  qa_leads.py  qa_messages.py  review.py
                export.py  lemlist.py  sending.py  mailbox.py  analytics.py  status.py  guide.py
    send/       smtp_imap.py  gmail_api.py(optional)  footer.py
    dashboard/  app.py  static/(index.html, app.js, app.css)
    guides/     workflow.md  messaging_style.md  (packaged text the AI reads)
  tests/        unit tests per module, fixtures/ (synthetic only), e2e/
  .github/workflows/tests.yml
```

Python 3.11+. Dependencies (pin lower bounds, upper major bounds): `httpx`, `beautifulsoup4`, `dnspython`, `pydantic>=2`, `pyyaml`, `python-dotenv`, `fastapi`, `uvicorn`, `mcp` (official MCP Python SDK v2; verify the server API from the installed package source before writing code), `pypdf`, `python-docx`. Dev: `pytest`, `pytest-cov`, `ruff`, `respx` (httpx mocking).

---

## 5. Workspace (user data, outside the repo)

Default `LEADGEN_HOME` = `%USERPROFILE%\LeadGenerator` on Windows, `~/LeadGenerator` elsewhere. Override with env var `LEADGEN_HOME`.

```
LeadGenerator/
  leadgen.db          all data
  secrets.env         API keys (KEY=value), file permissions user-only where the OS supports it
  settings.yaml       user settings (sender identity, postal address, defaults, budgets)
  exports/            CSV exports, one folder per campaign
  logs/leadgen.log    rotating log; never logs secrets or full email bodies
  cache/              HTTP/search cache (search results cached 30 days by query hash)
```

Secrets: read from `secrets.env`, then process env. Keys are never returned by any tool, never logged, never written to exports. Tools show only `configured: true/false` and the last 4 characters.

---

## 6. Data model (SQLite)

All tables have `id INTEGER PRIMARY KEY`, `created_at`, `updated_at` (ISO UTC text). JSON columns are TEXT holding JSON.

| Table | Key columns | Notes |
|---|---|---|
| `offers` | name, source_type (url/text/file), source_ref, raw_text, profile_json | `profile_json` validated by `OfferProfile` (below) |
| `campaigns` | name, offer_id, countries_json, sectors_json, company_size, channels_json, designations_json, seniority_min, exclusions_json, budget_json, options_json, locale, status | status: draft/active/archived; options: `{invite_note: false, allow_risky_emails: false}` |
| `companies` | campaign_id, name, aliases_json, domain, country, sector, size_note, source, domain_ok, status, notes | unique(campaign_id, domain); status: candidate/approved/excluded |
| `people` | campaign_id, company_id, first_name, last_name, greeting_name, raw_name, title, designation, seniority, country, linkedin_url, evidence_title, evidence_snippet, source_query, confirmed, reasons_json, status | unique(campaign_id, linkedin_url); status: confirmed/review/rejected/removed |
| `emails` | person_id, email, method, pattern, status, verifier, checked_at | status: verified/risky/guess/invalid/unknown |
| `messages` | person_id, channel, subject, body, status, qa_json, ai_review, ai_review_notes | channel: email/linkedin_invite/linkedin_message; status: draft/qa_fail/qa_pass/approved/sent |
| `qa_issues` | entity_type, entity_id, code, severity, detail | severity: block/warn; recomputed by each QA run |
| `suppression` | value, kind (email/domain/linkedin), reason | global do-not-contact; checked everywhere |
| `sends` | message_id, provider, to_email, provider_message_id, status, sent_at, error | |
| `events` | send_id, type, at, detail | type: reply/bounce/unsubscribe/open/click |
| `api_usage` | provider, day, calls, credits | for quota display and budgets |
| `search_cache` | provider, query_hash, query, results_json, fetched_at | 30-day TTL |
| `settings` | key, value | small key/value store |
| `schema_version` | version | simple forward-only migrations |

### OfferProfile (pydantic)
```
name: str
one_liner: str                       # one sentence
description: str                     # 2-5 sentences
services: list[{name, what, best_for: list[str]}]
audiences: list[str]                 # who buys
confirmed_claims: list[str]          # facts the messages MAY state (verbatim-ish)
forbidden_claims: list[str]          # things the messages must NOT state (unconfirmed numbers, client names...)
proof_points: list[{claim, source}]  # numbers/case studies allowed, each with a source
ctas: list[str]                      # e.g. "15-minute call", "short example"
sender: {name, company, role, email?, website?}
tone: str                            # e.g. "professional, plain, no hype"
language: str                        # e.g. "en-GB"
```

### Designations (campaign-level)
A campaign has 1–8 designation groups, each:
```
{ "label": "L&D / Training heads",
  "keywords": ["learning", "l&d", "training", ...],     # matched on whole words in the role part of a title
  "search_terms": ["learning and development", "L&D", "head of training", ...] }  # used in search queries
```
Order matters (first match wins). Templates ship in `services/designations.py` (B2B defaults per function: L&D, HR, Safety/Operations, Owners/MDs, Marketing, Sales, IT, Finance, Procurement, Facilities). The AI proposes groups from the offer; the user confirms.

Seniority levels: `individual < manager < director < vp < c_suite`. `seniority_min` default `manager`. "Partner" counts as manager only in "HR Business Partner"/"Managing Partner"; "Talent Acquisition Partner", "Junior …", "Associate …", "Executive" (not "Executive Director"), "Specialist", "Coordinator", "Intern" are `individual`.

---

## 7. Providers (free first)

Verified on 2026-10-14 from secondary sources (re-verify in T04/T09 and record in DECISIONS.md):

| Purpose | Provider | Free allowance | Key env var | Default? |
|---|---|---|---|---|
| Google search (people + companies) | Serper.dev | 2,500 searches, one-time, no card | `SERPER_API_KEY` | Yes |
| Web search fallback | Brave Search API | ~$5 monthly credit (≈1,000 searches), card required | `BRAVE_API_KEY` | Optional |
| Last-resort search | DuckDuckGo HTML | free, unreliable, rate-limited | none | Fallback only, off by default |
| Email pattern + finder | Hunter.io | 50 credits/month | `HUNTER_API_KEY` | Yes (email channel) |
| Email verification | Reoon | ~20/day (~600/month) + 100 on signup | `REOON_API_KEY` | Yes (email channel) |
| Email verification | ZeroBounce | small free monthly allowance (verify) | `ZEROBOUNCE_API_KEY` | Optional |
| LinkedIn campaign push | lemlist API | needs lemlist plan | `LEMLIST_API_KEY` | Optional |
| Sending | SMTP/IMAP (Gmail/Workspace app password, Outlook, any) | user's mailbox | `SMTP_*`, `IMAP_*` | Optional |

Rules:
- Every provider implements a small interface, reports `configured`, `test()` (cheapest possible call), and records usage in `api_usage`.
- Search results are cached by `(provider, normalized query, country)` for 30 days; cached hits cost nothing.
- Budgets: per campaign `budget_json = {"search": 300, "hunter": 25, "verify": 200}` defaults; services refuse to exceed without `confirm=true`.
- When a key is missing, tools return a structured `needs_key` response: provider, why, free allowance, signup URL, and the two ways to add it (dashboard Keys page, recommended, or `save_api_key`).

---

## 8. The guided workflow (what the AI does)

Shipped as `guides/workflow.md`, exposed as MCP prompt `lead_generation` and tool `get_workflow_guide`. Summary:

0. `get_status`: what's configured, existing campaigns, suggested next step.
1. **Offer**: ask for URL/text/file → `ingest_offer_source` → read returned text → draft an `OfferProfile` → show the user the *confirmed claims* and *forbidden claims* lists and ask them to confirm/correct → `save_offer_profile`. Never treat marketing copy marked "to confirm" as confirmed.
2. **Targeting**: ask country(ies), sectors, company size, channels; propose designation groups from templates → user confirms → `create_campaign`.
3. **Companies**: AI proposes companies from its own knowledge, *plus* uses `search_web` for "largest X companies in Y" style research → `add_companies` (verifies domains) → show list → user removes/adds → `approve_companies`.
4. **Keys**: when a step needs a provider, call `list_providers`; ask the user to add the key (dashboard link preferred).
5. **People**: `estimate_people_search` → tell the user the credit cost → `find_people(confirm=true)` in chunks → report confirmed vs review counts → optionally walk the review list.
6. **Emails** (if email channel): `estimate_email_search` → `find_emails` in chunks → report verified/risky/guess.
7. **Drafting**: loop `get_drafting_batch` → write messages following `guides/messaging_style.md` and the offer's confirmed claims → `save_drafts` (instant deterministic QA; fix and resave failures).
8. **QA**: `run_qa` → fix blockers → `get_review_batch` → the AI *reads every message with its lead details* and records `pass/fix/reject` via `record_review` → repeat until every message is reviewed.
9. **Deliver**: ask how: CSV (`export_campaign`), lemlist API (`push_to_lemlist`, dry-run first), or send emails (`check_send_readiness` → `send_emails(dry_run)` → `send_emails(confirm=true)`).
10. **Analytics** (sending only): `sync_mailbox` then `get_analytics`.

The AI must: ask before spending credits; never fabricate companies, people, emails, titles or claims; tell the user plainly what was skipped and why.

---

## 9. Connecting AI hosts

- **Claude Desktop**: `leadgen connect claude` merges an entry into `claude_desktop_config.json` (Windows `%APPDATA%\Claude\`, macOS `~/Library/Application Support/Claude/`), backing up the old file first and never removing other servers. Entry runs `<venv>/leadgen mcp` (stdio). Later: `.mcpb` Desktop Extension (T20).
- **Claude Code**: `leadgen connect claude-code` prints/executes `claude mcp add lead-generator -- <path-to-leadgen> mcp`.
- **ChatGPT**: `leadgen serve --public`:
  1. starts the MCP server over Streamable HTTP on 127.0.0.1:8766 at path `/mcp/<secret>` (32-char random secret stored in settings, rotatable with `leadgen serve --rotate-secret`),
  2. starts `cloudflared tunnel --url http://127.0.0.1:8766` (quick tunnel; detect cloudflared, explain install if missing),
  3. prints the HTTPS URL to paste into ChatGPT → Settings → Developer Mode / Connectors → add custom MCP server, authentication "none".
  The secret path is the only protection; warn the user, stop the tunnel when they quit, and never enable this mode by default. Requirements to document (verify in T17): paid ChatGPT plan, Developer Mode, workspace admin permission on Business/Enterprise, write actions may be limited on some plans.

---

## 10. MCP tool catalogue (v1)

All tools return JSON objects. Errors return `{"ok": false, "error": {"code", "message", "hint"}}`. Lists are paginated (`page`, `page_size` ≤ 50). Spending tools take `confirm: bool=false` and return an estimate when confirmation is needed.

| # | Tool | Purpose |
|---|---|---|
| 1 | `get_status` | Workspace, keys configured, campaigns summary, next suggested step |
| 2 | `get_workflow_guide` | Full workflow text (for hosts without prompt support) |
| 3 | `list_providers` | Providers, purpose, free tier, configured?, usage this month, signup URL |
| 4 | `save_api_key(provider, key)` | Validate with test call, store; returns masked key |
| 5 | `ingest_offer_source(url? text? file_path?)` | Crawl/parse; returns offer_id + cleaned text (chunked, ≤ 40k chars per call, `next_cursor`) |
| 6 | `save_offer_profile(offer_id, profile)` | Validate + store |
| 7 | `get_offer(offer_id)` | |
| 8 | `list_designation_templates` | Built-in groups |
| 9 | `create_campaign(...)` / 10 `update_campaign(...)` / 11 `get_campaign(id)` / 12 `list_campaigns` | |
| 13 | `search_web(query, country, num)` | Raw Google results (cached; spends search credits) |
| 14 | `add_companies(campaign_id, companies[])` | Normalise, dedupe, verify domain (DNS + HTTP), store as candidates |
| 15 | `list_companies(campaign_id, status?, page)` | |
| 16 | `set_company_status(ids[], status, reason)` | approve / exclude |
| 17 | `estimate_people_search(campaign_id)` | Searches needed, credits, cached share |
| 18 | `find_people(campaign_id, company_ids?, confirm, cursor?)` | Chunked; stores confirmed + review |
| 19 | `list_people(campaign_id, filters, page)` | |
| 20 | `set_person_status(ids[], status, reason)` | promote review→confirmed, reject, remove |
| 21 | `estimate_email_search(campaign_id)` | |
| 22 | `find_emails(campaign_id, person_ids?, confirm, cursor?)` | Pattern → candidate → verify |
| 23 | `get_drafting_batch(campaign_id, channel, batch_size, cursor?)` | People + offer + channel rules |
| 24 | `save_drafts(drafts[])` | Store + instant message QA; returns issues to fix |
| 25 | `run_qa(campaign_id)` | Full lead + message QA; summary + top issues |
| 26 | `list_qa_issues(campaign_id, severity?, code?, page)` | |
| 27 | `get_review_batch(campaign_id, batch_size, cursor?)` | Messages + full lead context for AI reading |
| 28 | `record_review(results[])` | pass / fix (with corrected text) / reject |
| 29 | `export_campaign(campaign_id, format, include)` | generic / lemlist / instantly CSV; returns path + counts |
| 30 | `push_to_lemlist(campaign_id, lemlist_campaign_id, dry_run=true, confirm)` | Creates leads with custom variables |
| 31 | `check_send_readiness(campaign_id)` | Mailbox, sender identity, postal address, compliance, caps |
| 32 | `send_emails(campaign_id, limit, dry_run=true, confirm)` | Respects daily cap + warm-up + quiet hours + suppression |
| 33 | `sync_mailbox()` | IMAP poll: replies, bounces, unsubscribes |
| 34 | `get_analytics(campaign_id, group_by?)` | Sent, bounced, replied, unsubscribed, rates by designation/sector/company/day |
| 35 | `add_suppression(values[], kind, reason)` | Do-not-contact |
| 36 | `open_dashboard()` | Starts dashboard if needed; returns local URL |

---

## 11. People finding and confirmation (core IP)

Port and generalise `harvey/collectors/accounts.py` (local copy, see NOTICE) and `harvey/export.py::classify_designation`.

Query per (company × designation group):
`site:linkedin.com/in (<search_terms OR-joined, quoted>) "<company name>" <country name>` with `gl=<country gl>`; `num=10`. One search per pair. Cache results.

A result becomes a **confirmed** person only if all hold (reasons recorded otherwise; failed ones go to `review` status, never silently dropped):
1. URL is a personal profile: `^https://([a-z]{2,3}\.|www\.)?linkedin\.com/in/[^/?#]+/?$`.
2. Name parses to first + last (credentials like "MBA", "IHRP-CP", "(Max)" stripped into `greeting_name` logic).
3. Current employer confirmed: alias in headline; or snippet shows `Experience · <alias>`, `at <alias>`, `@ <alias>`, `Name. Headline. <alias> <school>.` layout, or `<alias> … Present`. If headline says `at <Other company>` → reject reason "different employer". Truncated employer (`at Rolls`) matches alias prefix.
4. Country: LinkedIn subdomain matches the country table, or country name / a major city of that country appears in the snippet.
5. Not a former role (`former`, `ex-`, `retired`, `previously`).
6. Designation: role part of title (before ` at ` / `@`) matches a campaign group; weak-fit function patterns (IT/tech/data/finance/treasury/sales/commercial/investment/data-centre operations) do **not** count for an Operations group unless safety/EHS/facilities words are present.
7. Seniority ≥ campaign minimum; owner/MD group requires a true head-of-company term (CEO, founder, owner, chairman, managing partner, country manager/head, general manager, president not vice) because at large organisations "Managing Director" and "Principal" are ranks.
8. Not on suppression list, not at an excluded/competitor company.
9. Not a job seeker hint (`looking for`, `open to work`, `seeking`).

Short all-caps aliases (≤5 chars) match case-sensitively as whole words. Alias lists must avoid ambiguous short forms (e.g. "BD", "SAL").

Every rule above has unit tests built from *synthetic* snippets in the exact shapes Google returns (see T08 for the catalogue of shapes).

---

## 12. Email finding

Port `harvey/integrations/email_finder.py` (MIT). Flow per person: MX lookup (classify google/microsoft/gateway/other) → domain pattern (cache → Hunter domain search → known addresses → default `first.last`) → single candidate → verify (Reoon → ZeroBounce → Hunter verifier → SMTP only for small self-hosted MX) → catch-all detection → status. Only `verified` (and `risky` if the user opts in) are exportable for email sending. Role addresses (info@, hr@, careers@) are never assigned to a person.

---

## 13. Messages

Channels and hard limits (QA enforces):

| Channel | Limit | Notes |
|---|---|---|
| `linkedin_invite` | ≤ 200 characters | Optional; many users send invites without a note |
| `linkedin_message` | 150–700 characters | Sent after acceptance; no links |
| `email` | subject ≤ 60 chars; body 40–160 words | Footer (sender, postal address, opt-out line) is appended at send time, not drafted |

Style guide (`guides/messaging_style.md`): greet by `greeting_name`; one specific line about their role/company from evidence; one relevant service line chosen from the offer's services; one soft question CTA; no claims outside `confirmed_claims`/`proof_points`; no client names unless listed; no stats unless listed with source; locale spelling; no emojis/exclamation spam; never pretend to know them or to have read their posts.

---

## 14. Quality assurance (rigorous check)

Deterministic checks run on save and on `run_qa`. `block` issues prevent export/sending; `warn` issues are shown.

**Lead checks (L)**
| Code | Check | Severity |
|---|---|---|
| L01 | LinkedIn URL is a valid personal profile | block |
| L02 | Duplicate person (URL, email, or name+company) | block |
| L03 | Company approved and not excluded/competitor | block |
| L04 | Current employer confirmed | block |
| L05 | Country confirmed | block |
| L06 | Designation in campaign groups | block |
| L07 | Seniority ≥ minimum | block |
| L08 | Former role | block |
| L09 | Headline names a different employer | block |
| L10 | Owner group but rank-only title (MD/Principal) | block |
| L11 | Name quality (initial only, credentials, brackets, ALL CAPS) → greeting_name must be set | warn |
| L12 | Title garbled/truncated ("View mutual connections", "can introduce you", ends with " at") | warn |
| L13 | Weak-fit function for the group | warn |
| L14 | Junior keyword in title | block |
| L15 | Job-seeking hint | warn |
| L16 | On suppression list | block |
| L17 | Email channel: no email or status not verified/risky-allowed | block for email |
| L18 | Email domain ≠ company domain (or known alias domain) | warn |
| L19 | Role-based email address | block |
| L20 | Profile language/location hint mismatch (e.g. Spanish profile, overseas city) | warn |

**Message checks (M)**
| Code | Check | Severity |
|---|---|---|
| M01 | Greeting uses the person's greeting_name | block |
| M02 | Mentions their company (or alias) and no *other* campaign company | block / warn |
| M03 | Unresolved placeholders (`{{`, `[Name]`, `<company>`, `TODO`) | block |
| M04 | Channel length limits | block |
| M05 | Contains a forbidden claim (offer) or global forbidden phrase (guarantee, "100%", "risk-free") | block |
| M06 | Any number/percentage not present in proof_points | block |
| M07 | Client/brand names not in confirmed claims | warn |
| M08 | Email spam-trigger words | warn |
| M09 | Subject line rules (length, caps, `Re:`/`Fwd:` fakery) | block |
| M10 | Ends with a question or clear CTA | warn |
| M11 | Link in LinkedIn invite/message | warn |
| M12 | Near-duplicate of another message in campaign (similarity ≥ 0.92) | warn |
| M13 | Locale spelling (e.g. en-GB "organise") | warn |
| M14 | Mentions a job title that contradicts the lead's title | warn |
| M15 | Exclamation/emoji overuse | warn |
| M16 | AI review missing | block for export when strict mode (default on) |

**AI review**: `get_review_batch` returns each message with full lead evidence; the AI must read and judge: Is it accurate to the evidence? Is it relevant to this person's role? Is every claim allowed? Is the greeting right? Would a busy professional find it respectful? Verdicts are stored per message with notes. Strict mode requires a `pass` on every exported/sent message.

QA report: `run_qa` returns counts per code, and writes `exports/<campaign>/qa_report.csv` on export.

---

## 15. Delivery options

1. **CSV export**: `generic` (all fields), `lemlist` (camelCase: email, firstName, lastName, companyName, jobTitle, linkedinUrl, greetingName, designation, linkedinMessage, linkedinInvite, emailSubject, emailBody, companyDomain), `instantly` (email, first_name, last_name, company_name, personalization, website + custom). UTF-8 with BOM for Excel.
2. **lemlist API**: create leads in an existing lemlist campaign with custom variables. Email may be required by lemlist (unconfirmed): test in T12 with a dry run and one real lead; if email is required, push only leads with email and export the rest as CSV with a clear message.
3. **Send email** (opt-in, the most guarded path):
   - Mailbox via SMTP+IMAP (Gmail/Workspace app password, Outlook, others); Gmail API optional later.
   - Readiness gate: sender identity, physical postal address (CAN-SPAM), opt-out text, compliance acknowledgement for each target country (see `core/compliance.py`), suppression list, daily cap (default 20/day, warm-up ramp +5/day to max 50), quiet hours in recipient country, random 60–180 s spacing, stop on bounce rate > 5%.
   - Every email gets a footer with sender, address and "Reply 'unsubscribe' and I won't email you again". Unsubscribe replies are detected and suppressed automatically.
   - Analytics: sent, delivered (not bounced), bounced, replied, unsubscribed, reply rate; by designation, sector, company, country, day. Open/click tracking is **off** (needs a public server and is unreliable due to Apple Mail privacy); documented as a future option.

---

## 16. Compliance tables (`core/compliance.py`)

Per country: email cold-outreach regime, consent model (opt-out vs opt-in), required elements (identity, address, unsubscribe, subject labelling such as Singapore's `<ADV>` rule for unsolicited commercial email; verify), and a default policy: `allow_with_optout`, `warn`, or `block_without_consent` (e.g. Canada CASL, Germany, Australia: verify each). T03 researches each with sources and dates into DECISIONS.md. The tool is not legal advice; it shows the summary and requires the user to acknowledge per country before sending. LinkedIn messaging is out of scope for these rules but the guide reminds users of LinkedIn's terms.

---

## 17. Dashboard (local web page)

FastAPI on `127.0.0.1:8765`, static HTML/CSS/JS (no build step). Pages: Home (status + next step), Keys (paste keys locally; recommended over chat), Campaigns, Companies, Leads (filter by designation / company / status / QA / email status; open evidence; export button), Messages (per person, QA issues, AI review notes), Analytics (sending). Read-mostly; edits limited to keys, statuses and settings. Binds to localhost only.

---

## 18. Testing and quality gates

- `pytest` must pass on Windows and Ubuntu (GitHub Actions, Python 3.11 and 3.13).
- Coverage target ≥ 85% for `services/` and `core/`.
- No network in unit tests: providers are mocked with `respx` or replaced by `providers/fake.py`.
- E2E test (T19) runs the whole workflow through the MCP tool functions with the fake provider and a synthetic company/person universe.
- Real-provider smoke test is manual (`leadgen doctor --live`) and spends at most 3 credits.
- `ruff check` clean.

---

## 19. Phases and task cards

| Phase | Tasks | Outcome |
|---|---|---|
| A. Foundation | T01 scaffold+config, T02 database, T03 countries+compliance | Installable package, workspace, schema |
| B. Data | T04 providers+search, T05 offer intake, T06 campaigns+designations, T07 companies | Can learn an offer and build a company list |
| C. People & email | T08 people finder+confirmation, T09 email finder+verify | Confirmed leads with LinkedIn + email |
| D. Messages & QA | T10 drafting, T11 QA engine+AI review | Every lead/message checked |
| E. Delivery | T12 export+lemlist, T14 sending, T15 mailbox+analytics | All three delivery options |
| F. Interfaces | T13 MCP server, T16 dashboard, T17 installers+connectors | Plug-and-play for Claude + ChatGPT |
| G. Ship | T18 user docs, T19 e2e + release, T20 .mcpb (optional) | v1.0 |

Order: T01 → T02 → T03 → T04 → T05 → T06 → T07 → T08 → T09 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 (→ T20). T13 can start after T11 with stubs for later tools.

Review gate: after each task the builder stops; the reviewer (Opus) checks the diff against the card's acceptance criteria before the next task starts. Push to GitHub only after review.
