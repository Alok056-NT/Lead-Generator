# Changelog

All notable changes to Lead Generator are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-10-20

First release.

### Added
- An end-to-end test (`tests/e2e/`): a made-up VR training studio, 12 invented companies in Singapore and the UK and about 120 canned search results are taken through every step an AI would drive, from reading the offer to analytics, in two ways (paid search and the AI's own web search) with the same confirmed people at the end. A second test runs the same kind of flow over the real MCP connection (`python -m leadgen mcp` as a child process).
- `leadgen doctor --live`: one small real call per service you have a key for (a search, an email check, Hunter's free account call), at most 3 credits, asking first (`--yes` skips the question).
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
