# T12: Export (CSV) and lemlist push

Depends on: T11. PLAN section 15 items 1–2, tools 29–30.

## Goal
Deliver checked leads and messages: CSV files that import cleanly into lemlist, Instantly or a spreadsheet, and a direct push into an existing lemlist campaign.

## Verify first (DECISIONS.md)
- lemlist API: base URL, auth (API key; basic auth with empty username, or query param — check), "Create Lead in Campaign" path/method/body, how custom variables are passed, whether `email` is required, duplicate handling (`deduplicate` param?), rate limits, error shapes. Official docs: developer.lemlist.com. Also the endpoint to list campaigns (for picking the target).
- lemlist CSV import column naming (camelCase recognised variables, case-sensitive custom variables).
- Instantly CSV import columns.

## Deliverables
### `services/export.py`
- `export_campaign(campaign_id, format="generic"|"lemlist"|"instantly", include="approved"|"qa_pass"|"all_confirmed") -> dict`:
  - default `include="approved"` (AI-reviewed pass; strict mode) using `review.export_gate`;
  - writes `exports/<campaign-slug>/<format>-<YYYYMMDD-HHMM>.csv` (utf-8-sig) and `qa_report.csv` (every person with status, blocking/warning issues, AI verdict notes) and `removed.csv` (people removed/rejected with reasons);
  - columns per PLAN section 15; one row per person; messages per channel in their own columns; `website`/`companyDomain` = company domain (never the LinkedIn URL, a bug from the pilot);
  - returns paths and counts (`exported`, `blocked_by_code`, `not_reviewed`).
- Spreadsheet safety: prefix cells starting with `=`, `+`, `-`, `@` with `'` (CSV injection).

### `services/lemlist.py` + `providers/lemlist.py`
- `list_lemlist_campaigns() -> list[{id, name}]`.
- `push_to_lemlist(campaign_id, lemlist_campaign_id, dry_run=True, confirm=False, cursor=None) -> dict`:
  - dry run returns exactly what would be sent for the first 3 leads (payload preview with secrets removed) and counts;
  - real run requires `confirm=True`; chunked (≤ 25 leads/call); per-lead result stored in `settings`-backed log or a `pushes` note on the person (`qa_json.lemlist`); idempotent (skip leads already pushed to that lemlist campaign);
  - if lemlist requires email and a lead has none → skip it with reason `no_email_for_lemlist_api` and tell the user to use the CSV import for those (with a count).
  - custom variables: `greetingName`, `designation`, `linkedinMessage`, `linkedinInvite`, `emailSubject`, `emailBody`, `companyDomain`.
- `services/guides` text snippet for the lemlist sequence (blank invite → Accepted invite condition with a ≥ 1-day window → LinkedIn message `{{linkedinMessage}}`; email steps `{{emailSubject}}`/`{{emailBody}}`; plan requirement note) returned in the export result as `next_steps`.

## Tests
- Each CSV format: headers, row count, encoding BOM, injection escaping, include modes, domain column correct.
- qa_report and removed files content.
- lemlist provider with respx: auth, payload, dry run makes no HTTP calls, confirm required, chunking, idempotency, email-required fallback.

## Acceptance
Tests pass. Manual (with the owner's lemlist key): dry run, then push 1 lead to a test campaign; confirm variables appear in lemlist.
