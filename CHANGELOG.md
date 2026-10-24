# Changelog

All notable changes to Lead Generator are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.2.0] - 2026-10-26

Lead Generator Studio: a chat with your AI inside each project, a button that opens the app, and a new look.

### Added
- **Lead Generator Studio.** One desktop (and Start menu) button opens a web app. Each project has a chat with the AI you connect (your Claude plan or an API key), a live progress list and a table of ready contacts with their LinkedIn link, verified email and the messages written for them. Export and sending stay behind buttons that ask you to confirm; the AI cannot send.
- **Connect your AI** screen: checks Claude Code, signs you in, tests the connection.
- **Edit and delete projects**, and a sign-off name per project that the messages use.
- `leadgen app`.
- An Apple-style look with light and dark.

### Changed
- The earlier pages are under **Advanced**.

## [1.1.0] - 2026-10-26

Owner feedback after the first demo. Everything from 1.0 keeps working: your existing workspace is upgraded in place the first time you open it (nothing is deleted).

### Added
- **Projects.** Work is now organised in projects (one per product, service, content piece or website). A project holds one offer and any number of campaigns. The dashboard opens on your projects, and each project has its own page.
- **Contact memory.** A project remembers everyone who was found, exported, pushed to lemlist, emailed or imported. Nobody is offered again until the project's cooldown (90 days unless you change it, 0 turns it off). New checks L24 (already found or contacted inside the cooldown) and L25 (already in another active campaign of the project).
- **Campaign size.** A campaign can be limited by n (people in all), c (companies searched) and p (people per company). Extra good people are kept as "saved for a later campaign" (reserve) and can be moved into another campaign of the same project (`move_reserve_people`, or the dashboard).
- **Old contacts import.** A CSV or Excel template, and LinkedIn's own contacts export (`Connections.csv`) works as it is. Use it to hold those people back (exclude), to mark them as people you already know (signal), or both. Always a dry run first. `leadgen contacts template` and `leadgen contacts import`, tools `get_contacts_template` and `import_contacts`, and an upload page in the dashboard.
- **One page or the whole site.** When you attach a website you choose just that page or the whole site (up to 15 pages, at most 50). A big site is read in steps. A plain summary says what was read.
- **Connect your mailbox** help page in the dashboard: Gmail in three numbered steps with an app password, a test button that sends nothing, and an Outlook / other section.
- `docs/user/CONTACTS_IMPORT.md`, `docs/user/CONNECT_MAILBOX.md`, `docs/ALTERNATIVES.md` (free and open-source options, for decisions; nothing switched) and `docs/TESTING_CHECKLIST_v1.1.md`.
- The tool `list_people` can now list people with status `reserve`.

### Changed
- **Only verified email addresses** are exported (the `email` column), pushed to lemlist or sent. Other found addresses go to separate `unverifiedEmail` columns; a guessed address is never used. A project can allow "risky" (catch-all) addresses too; a campaign cannot override its project.
- "Campaign" now means the whole plan for finding and writing to people by email and LinkedIn; sending is optional and always needs your yes.
- The dashboard is written in plain language, with a "What's this?" line on every page.
- Tests run in parallel (`python -m pytest -q -n auto`, about 4 to 5 minutes).
- Database schema version 4 (forward-only migrations, tested on copies of older databases).
- Version number 1.1.0. The MCP server now has 53 tools.

### Upgrading
- Run the installer again (the same command as the first time). If you installed by hand from the source folder, run `pip install -e .` again: Excel files (`openpyxl`) and file uploads (`python-multipart`) are now dependencies.
- The first time you open v1.1 on an older workspace, every existing campaign is put in a project (one per offer) and the people already found are remembered. Copy your LeadGenerator folder first if you want a backup.

## [1.0.0] - 2026-10-20

First release.

### Added
- An end-to-end test (`tests/e2e/`): a made-up VR training studio, 12 invented companies in Singapore and the UK and about 120 canned search results are taken through every step an AI would drive, from reading the offer to analytics, in two ways (paid search and the AI's own web search) with the same confirmed people at the end. A second test runs the same kind of flow over the real MCP connection (`python -m leadgen mcp` as a child process).
- `leadgen doctor --live`: one small real call per service you have a key for (a search, an email check, Hunter's free account call), at most 2 credits, asking first (`--yes` skips the question).
- A test-only switch for canned search answers, `LEADGEN_FAKE_PROVIDERS=1`, honoured only when `LEADGEN_ALLOW_FAKES=1` is also set.

### Changed
- Version number 1.0.0.

## [0.1.0] - 2026-10-20

First version, built task by task (see `docs/STATUS.md`). Not yet released as 1.0.

### Added
- Install scripts for Windows (`install.ps1`) and Mac/Linux (`install.sh`), and `leadgen doctor`.
- Connectors: Claude Desktop and Claude Code (`leadgen connect claude`, `leadgen connect claude-code`) and ChatGPT through a temporary secure link (`leadgen connect chatgpt`).
- An MCP server with 46 tools and a written workflow guide for the AI.
- Learning an offer from a web page, a pasted text, a Word, PDF or text file, or a public Google Doc.
- Campaigns with job-role groups, minimum seniority, exclusions and competitors.
- Company lists with name and website checks.
- People finding from Google results (Serper, optional Brave and DuckDuckGo), with evidence-based confirmation rules and a review list.
- Email finding and checking (Hunter, Reoon, ZeroBounce, website scan, optional SMTP probe).
- Message drafting rules, 23 lead checks and 16 message checks, and AI review before export (strict by default).
- Free mode: the AI's own web search as a free path, with anti-fabrication checks and a spot check.
- CSV export (generic, lemlist, Instantly) with a QA report, and a push into a lemlist campaign.
- Email sending from your own mailbox with daily cap and warm-up, quiet hours, country rule acknowledgement, kill switch and bounce stop.
- Mailbox reading (read-only) for replies, bounces and unsubscribes, and an analytics summary.
- A local dashboard (`leadgen dashboard`) for keys, campaigns, companies, leads, messages, export, analytics and settings.
- `leadgen compliance [--markdown]` to show the email rules by country.
- User guides in `docs/user/`: getting started, providers, free mode, quality checks, delivery, compliance, troubleshooting, and how to connect Claude and ChatGPT.
