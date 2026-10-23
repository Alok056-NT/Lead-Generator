# T25: Verified-only email policy and one-page / whole-site offer intake

Depends on: T23. Sprint v1.1 items 3 and 9.

## Part A: only verified emails go out
- New project option `email_policy` (default `verified_only`; alternative `allow_risky`, which replaces the campaign option `allow_risky_emails` for new campaigns while old campaigns keep their stored value). `guess` emails are never exportable as sendable and never sent; this is already the rule, so keep it and make it impossible to switch on from the dashboard (CLI-only expert flag stays as is).
- Exports: the `email` column contains only emails allowed by the policy; other found emails go to a separate `unverifiedEmail` column with status, so nobody imports them by mistake into an email sequence. Lemlist push sends only verified emails.
- Every email, wherever it came from (Hunter pattern, company website, AI web search, imported list), must pass a verifier before it counts as verified. When no verifier key is set, the tool says so plainly ("Add a free Reoon key: 20 checks a day") and the email stays unverified.
- Dashboard/API counts: "verified emails", "found but not verified (not exported)".

## Part B: where the product / content / website goes, one page or whole site
- `ingest_offer_source` (service + MCP + dashboard) gains `scope`: `page` (only that URL) or `site` (that URL plus up to `max_pages` pages on the same site; default 15, max 50). One source per project (owner decision 24 Oct): ingesting a new source for a project replaces the old one. Each call returns a plain summary: "Read 12 pages from example.com: /, /about, /services, …" or "Read 1 page".
- Project setup text (guide + dashboard) states plainly: "Attach your product or service here: a website (one page, or the whole site up to 15 pages), a PDF/Word/text file, or pasted text. To change it later, attach a new source; it replaces the old one."

## Tests
Policy defaults; export columns (verified vs unverifiedEmail); lemlist push skips non-verified; no-verifier message; `scope=page` fetches exactly one page; `scope=site` respects max_pages ; a new source for a project replaces the old one.

## Acceptance
No unverified email can reach the email column of any export or be sent; the owner can choose one page or whole site.
