# T29: v1.1 regression, docs and owner test checklist

Depends on: T22–T27.

## Deliverables
- e2e (`tests/e2e/`): a projects flow: create project from text → two campaigns in the same project with p=2 → reserve moved to campaign 2 → no person appears in both → import a contacts file (exclude) → imported person blocked (L24) → export → re-run find_people: nobody already exported is offered again within cooldown. Existing API and AI-search flows still pass.
- Upgrade test: copy a v1.0 workspace (built by the e2e universe at v1.0 schema), run v1.1, check migration and that the dashboard loads every page.
- Docs: README "Your first campaign" now starts with "Create a project"; explain "campaign = your plan for email and/or LinkedIn; sending is optional"; GETTING_STARTED, DELIVERY, PROVIDERS (verified-only emails), new `docs/user/CONTACTS_IMPORT.md` (template, LinkedIn export steps), `docs/user/CONNECT_MAILBOX.md`; CHANGELOG 1.1.0; version 1.1.0.
- `docs/TESTING_CHECKLIST_v1.1.md` for the owner (Sunday): 15–20 numbered click-by-click checks with expected results, covering every sprint item, plus "how to report a problem".
- Do not tag; the reviewer tags `v1.1.0` after the owner approves.

## Acceptance
Full suite green (`-n auto`), CI green on push, checklist readable by a non-technical person.
