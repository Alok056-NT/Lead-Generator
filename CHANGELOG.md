# Changelog

All notable changes to Lead Generator are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-10-20

First version, built task by task (see `docs/STATUS.md`). Not yet released as 1.0.

### Added
- Install scripts for Windows (`install.ps1`) and Mac/Linux (`install.sh`), and `leadgen doctor`.
- Connectors: Claude Desktop and Claude Code (`leadgen connect claude`, `leadgen connect claude-code`) and ChatGPT through a temporary secure link (`leadgen connect chatgpt`).
- An MCP server with 45 tools and a written workflow guide for the AI.
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
