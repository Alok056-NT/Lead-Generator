# T14: Email sending

Depends on: T03 (compliance), T11, T12. PLAN sections: 15 item 3, 16, tools 31–32.
Port from: `reference/harvey/integrations/smtp_mail.py`, `mail_provider.py` (MIT). Gmail API (`gmail.py`) is a follow-up, not this task.

## Goal
Opt-in sending of approved emails from the user's own mailbox, with every safety rail on by default.

## Verify first (DECISIONS.md)
- Gmail/Google Workspace SMTP + IMAP with an app password: host/ports, 2-Step Verification requirement, daily sending limits for consumer vs Workspace accounts.
- Microsoft 365/Outlook: current status of SMTP AUTH (basic auth) for sending; if disabled by default, document it and treat Outlook as "may need admin to enable SMTP AUTH".

## Deliverables
### `send/smtp_imap.py`
`SmtpImapMailbox(host, port, user, password, imap_host, imap_port, from_name, from_email)` with `test() -> (ok, msg)`, `send(to, subject, body_text, headers) -> SendResult(message_id)`, used by T15 for IMAP. Plain-text emails only in v1 (best deliverability); `Message-ID` generated; `List-Unsubscribe: <mailto:{from}?subject=unsubscribe>` header.

### `send/footer.py`
`footer(settings) -> str`: sender name, role, company, website (if set), postal address, and the line "If you'd rather not hear from me, reply 'unsubscribe' and I won't email you again." Appended at send time with a blank line + `--`.

### `services/sending.py`
- `check_send_readiness(campaign_id) -> dict` with a checklist (each item ok/missing + how to fix):
  mailbox configured and `test()` passes; sender name/company set; postal address set; locale; compliance acknowledgement recorded for **every** target country (`settings` key `compliance_ack:<campaign_id>:<iso2>` with timestamp); for countries whose rule is `block_without_consent`: block unless the user records `consent_basis` text per country; subject label applied automatically when the rule requires it (e.g. `<ADV>` prefix if verified for Singapore); approved messages available; suppression list loaded; today's cap remaining.
- `acknowledge_compliance(campaign_id, iso2, consent_basis="")`: stores the acknowledgement (the AI must show the rule summary to the user first; the workflow guide says so).
- `send_emails(campaign_id, limit=10, dry_run=True, confirm=False) -> dict`:
  - dry run: returns the next N emails exactly as they would be sent (with footer and any subject label), no network;
  - real run requires `confirm=True` and readiness ok;
  - daily cap = `min(settings.sending.daily_cap + warmup_step * days_since_first_send, max_daily_cap)`; counts all campaigns;
  - quiet hours evaluated in the recipient country's main timezone (add `timezone` to the T03 country table if missing; follow-up note for T03 if so);
  - random gap `min_gap_s..max_gap_s` between sends; because MCP calls must stay < 60 s, a single call sends at most `floor(50 / avg_gap)` emails (usually 0–1). So the real sending loop runs in a **background worker**: `leadgen send-worker` (CLI) / started by `send_emails(confirm=True)` as a detached subprocess that writes progress to the DB; `send_emails` returns immediately with a queue summary;
  - stop conditions: bounce rate > `max_bounce_rate` over the last 20 sends, 3 consecutive SMTP errors, or kill switch `settings.sending.paused=true` (`leadgen send pause|resume`);
  - each send recorded in `sends`; message status `sent`; suppressed/role/invalid addresses skipped with reasons.
- Every email is to one recipient; never BCC/CC.

## Tests
- Readiness checklist per missing item; compliance block for a `block_without_consent` country until consent basis recorded.
- Footer content; subject label application.
- Dry run makes no SMTP connection (monkeypatch smtplib).
- Cap and warm-up math; quiet hours with a fixed clock; bounce-rate stop; pause switch.
- Worker processes the queue with a fake mailbox and respects gaps via an injectable sleep.

## Acceptance
Tests pass. Manual: send 1 real email to the owner's own address via an app password.
