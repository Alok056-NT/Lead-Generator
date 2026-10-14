# T15: Mailbox sync and analytics

Depends on: T14. PLAN section 15 item 3 (analytics), tools 33–35.

## Goal
Detect replies, bounces and unsubscribes from the user's mailbox and turn them into honest analytics.

## Deliverables
### `services/mailbox.py`
- `sync_mailbox(since_days=30) -> dict`: IMAP (read-only `EXAMINE` on INBOX; never delete or move mail) fetch headers + first 4 KB of text for messages since the last sync (store `settings` key `imap_last_uid`).
- Matching to sends: `In-Reply-To`/`References` → `sends.provider_message_id`; else from-address = a sent recipient within 30 days.
- Bounces: Harvey's `looks_like_bounce` heuristics + DSN parsing (`Content-Type: multipart/report; report-type=delivery-status`, `Final-Recipient`, `Status: 5.x.x`) → event `bounce`, email status `invalid`, add to suppression.
- Unsubscribes: reply body/subject matching `unsubscribe`, `remove me`, `stop emailing`, `not interested, please remove` (case-insensitive, first 500 chars) → event `unsubscribe`, suppression add (email), pending sends for that person cancelled.
- Replies: everything else matched → event `reply`; no auto-response in v1.
- Never store full reply bodies: store first 300 chars in `events.detail` for the user to read in the dashboard.

### `services/analytics.py`
- `get_analytics(campaign_id=None, group_by=None|"designation"|"sector"|"company"|"country"|"day") -> dict`: sent, bounced, delivered (= sent − bounced), replied, unsubscribed, reply_rate (= replied/delivered), bounce_rate, unsubscribe_rate; per group rows sorted by sent desc; small-sample flag when delivered < 20 ("too few to judge").
- Time series per day for the dashboard.
- Also include LinkedIn/lemlist side counts available locally (exported/pushed counts); note that LinkedIn acceptance/reply data lives in lemlist.

### Line endings
Add `tests/fixtures/mail/*.eml -text` to `.gitattributes` so the fixtures keep their CRLF line endings (the repo default normalises to LF).

### Suppression
`services/suppression.py` (if not already present): `add(values, kind, reason)`, `is_suppressed(email=None, domain=None, linkedin_url=None)`, `list()`, `remove(value, kind)` (CLI only, not an MCP tool).

## Worker prep (Haiku)
1. Write synthetic RFC 5322 email files in `tests/fixtures/mail/`, all addresses on `example.com`/`example.org`/`example.net`, each with realistic headers (`From`, `To`, `Date`, `Subject`, `Message-ID`): `plain_reply.eml` (with `In-Reply-To` and `References` pointing at `<send-001@example.com>`), `reply_no_thread_headers.eml` (no In-Reply-To; From is a recipient of a previous send), `gmail_bounce_dsn.eml` (multipart/report; report-type=delivery-status with `Final-Recipient` and `Status: 5.1.1`, sender mailer-daemon), `outlook_ndr.eml` (Microsoft-style non-delivery report, 550 5.1.10), `out_of_office.eml` (`Auto-Submitted: auto-replied`, "Out of office" subject), `unsubscribe_reply.eml` (body starts "Please unsubscribe me" quoting the original), `not_interested_remove.eml` ("not interested, please remove me from your list"). Plain ASCII/UTF-8, CRLF line endings.

## Tests
- Synthetic `.eml` fixtures: plain reply, Gmail bounce DSN, Outlook NDR, auto-reply (out of office: treat as reply type `auto_reply`, excluded from reply rate), unsubscribe reply.
- Matching via headers and via address fallback.
- Analytics math and grouping; small-sample flag.
- IMAP access is read-only (assert `EXAMINE`, no `STORE`/`EXPUNGE`).

## Acceptance
Tests pass. Manual: after T14's manual send, reply from another account and see it counted.
