# T24: Import old contacts (CSV / Excel template, LinkedIn export)

Depends on: T23. Sprint v1.1 item 7 (and the free-extraction part of item 3).

## Goal
The owner uploads contacts they already have (old LinkedIn connections, past email lists, customers, do-not-contact lists). Each upload is used to **filter out** (don't contact these again within the cooldown, or never) and/or as a **signal** (these companies/people are warm: flag them so campaigns can prefer or target them). Bad files never crash anything; the user always sees a preview first.

## Template (ship both; same columns)
`src/leadgen/templates/contacts_template.csv` and `contacts_template.xlsx` (generate the xlsx in code with openpyxl at install-free runtime, or commit a tiny generated file; add `openpyxl` dependency). Columns (header row required, order free, case/space-insensitive, unknown columns ignored and reported):
`first_name, last_name, full_name, email, linkedin_url, company, company_domain, title, country, last_contacted, channel, relationship, notes`
- `full_name` is used only when first/last are empty.
- `last_contacted`: `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, Excel date cells, or empty (empty = treat as "contacted at import date").
- `channel`: `email`, `linkedin`, `both`, or empty.
- `relationship`: `contacted` (default), `replied`, `customer`, `partner`, `do_not_contact`.
- A first sheet named "Contacts" plus a second sheet "How to fill this in" with one line per column (xlsx only).
Also accept, auto-detected, **LinkedIn's official "Connections.csv"** (Settings → Data privacy → Get a copy of your data → Connections): it starts with "Notes:" lines before the header; columns First Name, Last Name, URL, Email Address, Company, Position, Connected On (e.g. `14 Oct 2026`).

## Service `services/contacts_import.py`
- `read_contacts(path) -> ImportPreview`: `.csv` (utf-8-sig, then cp1252 fallback; delimiter sniffing `,` `;` tab), `.xlsx` (openpyxl read-only, first sheet or "Contacts"); reject other types and files > 20 MB or > 50,000 rows with a clear message. Never raise on bad rows: collect `errors` (row number, column, problem) and `warnings` (e.g. no email and no LinkedIn URL → can only match by name+company).
- Normalise: emails lower-case and syntax-checked; LinkedIn URLs canonical (same function as the people finder; reject non-`/in/` URLs); names trimmed; dates parsed; duplicate rows within the file merged.
- `import_contacts(project_id, path, mode, dry_run=True) -> dict`, `mode` in:
  - `exclude`: write `contact_log` rows (channel `imported`, `at` = last_contacted) → L24 blocks them during the cooldown; rows with relationship `do_not_contact` also go to the global suppression list (never contacted).
  - `signal`: store in new table `known_contacts` (project_id, keys as in contact_log, company, company_domain, title, relationship, notes, imported_at); companies are flagged "warm" (count of known contacts) and matching people get a `known_contact` flag shown in the dashboard and passed to drafting (`get_drafting_batch` includes `known_contact: {relationship, last_contacted}` so the AI can write "we spoke before" only when true).
  - `both`.
  - dry run returns counts (rows read, valid, errors, duplicates, would-exclude, would-signal, do-not-contact) and the first 20 errors; nothing is written.
- `add_signal_companies(project_id, campaign_id)`: adds the warm companies from `known_contacts` to a campaign as candidates (normal company checks apply).
- `export_template(path, fmt="csv"|"xlsx")`.

## MCP + CLI
- Tools: `get_contacts_template` (returns template path and the column guide), `import_contacts(project_id, file_path, mode, dry_run=true)`, `add_signal_companies`.
- CLI: `leadgen contacts template [--xlsx] [--out PATH]`, `leadgen contacts import PATH --project ID --mode exclude|signal|both [--apply]`.

## Tests (synthetic files created in the test)
Template round-trip (write template, fill 3 rows, import); LinkedIn Connections.csv with Notes header; cp1252 file with accents; semicolon CSV; xlsx with Excel date cells and blank rows; bad emails/URLs reported not raised; 50,001 rows refused; dry run writes nothing; each mode's effect (L24 blocks an imported person; do_not_contact suppressed; known_contact flag in drafting batch); re-importing the same file creates no duplicates.

## Acceptance
Owner can download the template, fill it in Excel, upload it in a dry run, see a clear preview, then apply it; nothing crashes on a messy real-world file.
