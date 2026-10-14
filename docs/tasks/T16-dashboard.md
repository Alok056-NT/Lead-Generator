# T16: Local dashboard

Depends on: T12 (T15 for the analytics page). PLAN section 17, tool 36.

## Goal
A simple local web page where non-technical users browse campaigns, leads, messages and QA, paste API keys safely, export files, and see analytics.

## Deliverables
- Add `fastapi`, `uvicorn` to dependencies.
- `dashboard/app.py`: FastAPI app; JSON API under `/api/...` calling services only; static files from `dashboard/static/`. Binds to `127.0.0.1` (settings). Basic CSRF protection for POSTs: require header `X-LeadGen: 1` and same-origin `Origin`.
- `dashboard/static/index.html`, `app.js`, `app.css`: no build step, no CDN (works offline), single page with hash routing:
  - **Home**: status + next step + quick links.
  - **Keys**: list providers (configured/masked/usage/free tier/signup link); paste key → `save_api_key` → result; delete key. Keys are never sent back to the page.
  - **Campaigns**: list with counts; open a campaign.
  - **Companies**: table with status/domain check; approve/exclude.
  - **Leads**: table with filters (designation, company, status, QA severity, email status, AI verdict); search box; click → side panel with evidence snippet, LinkedIn link, email + status, issues, messages.
  - **Messages**: per channel list; text, issues, AI review notes; copy button.
  - **Export**: buttons for each format; shows the latest files with "Open folder".
  - **Analytics**: totals, by designation/sector/day (simple inline SVG bars; no chart library).
  - **Settings**: sender identity, postal address, locale, budgets, strict review toggle.
- Accessible: semantic HTML, keyboard navigable, readable at 100% zoom on a laptop, light and dark themes via `prefers-color-scheme`.
- `leadgen dashboard [--port]` CLI; `open_dashboard` MCP tool starts it as a background process if not running (pid file in workspace) and returns the URL.

## Tests
- FastAPI TestClient: each API route returns expected shapes; keys route never returns a key; POST without the header → 403.
- Static files served.

## Acceptance
Tests pass. Reviewer opens the dashboard with a synthetic e2e database and checks every page.
