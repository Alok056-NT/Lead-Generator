# T27: Mailbox connection wizard (Gmail first, no tech knowledge needed)

Depends on: T26. Sprint v1.1 item 6. Builds on T14/T15 (`send/smtp_imap.py`).

## Verify first (DECISIONS.md)
Google's current rules for app passwords (needs 2-Step Verification; not available with Advanced Protection or when a Workspace admin disables it; 16 characters shown with spaces), the exact pages `https://myaccount.google.com/signinoptions/twosv` and `https://myaccount.google.com/apppasswords`, Gmail SMTP/IMAP hosts and ports, and whether IMAP must be enabled in Gmail settings. Microsoft 365/Outlook.com: current SMTP AUTH status for personal and work accounts.

## Wizard (dashboard page "Connect your mailbox", linked from Home, Settings and the campaign Send section)
1. "Which email do you use?" → Gmail / Google Workspace · Outlook / Microsoft 365 · Other (I have SMTP details).
2. Gmail path, one screen per step, each with one big button that opens the right Google page in a new tab:
   - Step 1 "Turn on 2-Step Verification" (skip button if already on).
   - Step 2 "Create an app password" (name it "Lead Generator"; Google shows 16 letters).
   - Step 3 "Paste it here": email address + app password (spaces removed automatically); hosts/ports filled in silently.
   - Step 4 **Test**: logs in to SMTP and IMAP, sends nothing; plain results ("Connected. Lead Generator can send from aria@example.com and read replies."). Optional second button "Send me a test email" (to the same address only).
3. Error messages in plain English mapped from real failures: wrong app password (535), app passwords not allowed on this account (admin/Advanced Protection), IMAP disabled, network blocked, Outlook basic auth disabled (with the one-line fix or "use Gmail/Other").
4. Disconnect button. Keys stay in `secrets.env`, never shown again except the last 4 characters.
- MCP: `get_mailbox_status` and `open_mailbox_wizard` (returns the dashboard link); the workflow guide sends users to the wizard instead of asking for passwords in chat.

## Tests
Wizard API: save + test with a fake SMTP/IMAP server (aiosmtpd or a stub), each error mapping, spaces stripped, test email only to own address, CSRF header required, password never returned.

## Acceptance
A non-technical person connects Gmail in under 5 minutes following only the screens.
