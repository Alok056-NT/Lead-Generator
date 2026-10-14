# Rules for AI agents working on this repo

You are building **Lead Generator**, a local, AI-driven lead generation toolkit. Read `docs/PLAN.md` before any task. It is the source of truth.

## How work is organised
- Automated loop: `/build` (see `docs/BUILD_GUIDE.md`) runs on Opus 5.5 (extra-high) and dispatches three agents defined in `.claude/agents/`: `worker` (Haiku 5.5: mechanical prep listed under `## Worker prep (Haiku)` in a card), `builder` (Sonnet 5.5, high: code, logic, tests) and `reviewer` (Opus 5.5, extra-high). Their instructions add to these rules.

- Work is split into task cards: `docs/tasks/Txx-*.md`. Do **exactly one task card per session** unless told otherwise.
- The builder is Claude Sonnet 5.5 (high), helped by a Haiku 5.5 worker for mechanical prep. The reviewer and orchestrator are Claude Opus 5.5 (extra-high). The owner is Alok (non-technical; explain results in plain English).
- Progress lives in `docs/STATUS.md`. Verified external facts and design decisions live in `docs/DECISIONS.md`.

## Task protocol (follow every time)
1. Read `docs/PLAN.md`, the task card, `docs/STATUS.md` and `docs/DECISIONS.md`.
2. Check the card's **Depends on** tasks are marked `done` in STATUS.md. If not, stop and say so.
3. If anything in the card is ambiguous or contradicts PLAN.md, stop and ask. Do not guess.
4. Before using any third-party API or library API, **verify it from the source**: read the installed package's code (`python -c "import x; print(x.__file__)"`) or the provider's official docs. Never write API calls from memory. Record what you verified (URL or file, date, what it says) in `docs/DECISIONS.md`.
5. Implement only what the card lists. Anything extra goes in STATUS.md under "Follow-ups".
6. Write the tests the card lists, plus any needed to reach the coverage target. Run `python -m pytest -q` and `ruff check src tests`. Both must pass.
7. Update `docs/STATUS.md`: set the task to `review`, add a 3-6 line summary, list files changed, list follow-ups.
8. Commit on the current branch with message `Txx: <short title>` and the attribution trailer below. **Do not push.** Pushing happens after review.
9. Report to the user in plain English: what was built, test results, anything skipped and why.

## Hard rules
- **The repo is public.** Never commit API keys, `.env`/`secrets.env`, databases, exports, logs, or real people's data (names, emails, LinkedIn URLs). Test fixtures must use invented people and companies (e.g. "Aria Tan at Northwind Logistics") and `example.com`-style domains.
- No network calls in unit tests. Mock HTTP with `respx` or use `providers/fake.py`.
- Never log secrets or full message bodies. Never return secrets from a tool.
- Never add LinkedIn login, LinkedIn scraping or automation.
- Keep layers strict (PLAN.md section 3): adapters (mcp_server, cli, dashboard) contain no business logic.
- Synchronous services; long operations must be chunked with a `cursor` (PLAN.md section 3).
- Windows is a first-class platform: use `pathlib`, no shell-specific commands in code, UTF-8 everywhere (`encoding="utf-8"` on every open; CSV exports use `utf-8-sig`).
- Python 3.11+. Type hints on public functions. Docstrings on modules and public functions, short.
- Match the existing style; don't reformat unrelated code.
- Porting from Harvey: source files are in `reference/harvey/` (MIT, Ethan Rogers). Keep a header comment `# Adapted from Harvey (MIT, Ethan Rogers): <original path>` on ported modules. Don't import from `reference/`.

## Commit trailer
End every commit message with:
```
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
```

## Commands
```
python -m venv .venv
.venv\Scripts\activate          (Windows)   |   source .venv/bin/activate (macOS/Linux)
pip install -e ".[dev]"
python -m pytest -q
ruff check src tests
```
