# T26: Dashboard v1.1 (projects home, project and campaign pages, plain language)

Depends on: T23, T24, T25. Sprint v1.1 items 1, 5, 6 (language), 7, 9. Keep the T16 safety model (loopback only, `X-LeadGen` header + same-origin on every change, CSP, `textContent` only).

## Pages
1. **Home = Projects.** Cards: project name, what it's about (offer one-liner or "not set up yet"), campaigns, leads, verified emails, last activity. Button **New project**: name, "Attach your product or service" (website URL with a choice: *Just this page* / *Whole website (up to 15 pages)*; or upload a PDF/Word/text file; or paste text), "Don't contact the same person again for [90] days". On create: ingest the source and show "Next: open Claude and say *Set up project <name>*" (the AI writes the offer profile with the user).
2. **Project page.** Sections: *About this project* (offer summary, sources read, add another source), *Campaigns* (list + **New campaign**), *Contacts you already have* (download template CSV/Excel, upload, choose "Don't contact these again" / "Use as warm leads" / both, preview, then apply), *Contact memory* (cooldown setting, how many people are currently blocked and until when).
3. **New campaign form**: name; countries; sectors; channels as two checkboxes **Email** and **LinkedIn** (a campaign plans both; sending is optional later); job roles (role groups); minimum seniority; size: *How many people in total (n)*, *How many companies (c)*, *Max people per company (p)*; "Take reserve people from campaign …" option.
4. **Campaign page**: rename headings to a clear flow: *1 Companies → 2 People → 3 Emails (verified only) → 4 Messages → 5 Checks → 6 Export / Send (optional)*. Show reserve people and the move-reserve action. Sending section titled "Send emails from your mailbox (optional)" with a link to the mailbox wizard (T27); LinkedIn: "Export for lemlist or push to lemlist (optional)".
5. Keep Leads/Messages/Export/Analytics/Keys/Settings working; filters gain project and "known contact" / "reserve".

## Plain-language pass (every page)
- Every label, help text, error and empty state in short plain English; no internal words (no "QA", "designation", "L04", "ai_web_search", "confirm=true"). Codes may appear only in small grey text after the plain sentence.
- Replace: Quality check → "Checks"; AI review → "Checked by AI"; designation/role group → "Job role"; evidence → "Where we found them"; confirmed/review/reserve → "Ready" / "Needs a look" / "Saved for a later campaign"; email statuses → "Verified" / "Not verified (won't be used)".
- A short "What's this?" line on each page.
- Owner mentioned a "signal page": there is no Signals page in Lead Generator (that was the Harvey demo). Make sure warm-lead ("signal") wording from T24 is plain.

## Tests
FastAPI routes for projects (CRUD, cooldown), campaign create with n/c/p, contacts upload (dry run then apply) via multipart, move reserve; security tests still pass for new POST routes; a text-scan test that fails if any of the banned internal words appear in `static/*.html|js` user-visible strings (allow-list for code values).

## Acceptance
Reviewer clicks through: create project from a URL (one page and whole site), create two campaigns with p=2 and splits people via reserve, upload a template file (dry run + apply), and finds no jargon on any page.
