# T27: Connect your mailbox help page (Gmail first)

Depends on: T26. Sprint v1.1 item 6. Owner decision 24 Oct: a **help page**, not a multi-screen wizard (full wizard is v1.2). Sending is optional; NXT's main path is export → lemlist.

## Verify first (DECISIONS.md)
Google app passwords: need 2-Step Verification; unavailable with Advanced Protection or when a Workspace admin turns them off; shown as 16 letters with spaces. Pages: `https://myaccount.google.com/signinoptions/twosv`, `https://myaccount.google.com/apppasswords`. Gmail SMTP/IMAP hosts/ports, and whether IMAP must be switched on.

## Deliverable: one dashboard page "Connect your mailbox (optional)"
Linked from Home, Settings and the campaign's Send section. Reuse the existing mailbox settings save and test code (T14/T15); no new sending logic.
- One sentence on why ("only needed if you want Lead Generator to send emails from your own mailbox; exporting to lemlist doesn't need it").
- Gmail / Google Workspace, 3 numbered steps, each with a button that opens the Google page in a new tab: 1 turn on 2-Step Verification, 2 create an app password named "Lead Generator", 3 paste your email and the 16 letters here (spaces removed automatically; hosts/ports filled in silently).
- **Test** button: logs in to SMTP and IMAP, sends nothing, plain result. The two most common failures in plain English (wrong app password; app passwords not allowed on this account, ask your admin or use another mailbox).
- A short "Outlook / other mailbox" section with the existing manual fields.
- Password never shown again (last 4 characters only).

## Tests
Save + test via the existing fake mailbox; spaces stripped; the two error messages; CSRF header required; password never returned.

## Acceptance
A non-technical person connects Gmail by following the page.
