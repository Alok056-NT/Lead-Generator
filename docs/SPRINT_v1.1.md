# Sprint v1.1: NXT release (deadline Monday 26 Oct 2026, morning IST)

Owner feedback after the demo (24 Oct). Goal: ship v1.1 to NXT Monday morning; owner tests Sunday afternoon/evening. **Nothing that works today may break**: every change keeps existing data (database migration, no deletions) and the full test suite stays green.

## Scope (owner's items → task)
| # | Owner asked | Task |
|---|---|---|
| 1 | Home page with several **projects** (one per product/content/website), each with several campaigns | T23 (data, tools), T26 (dashboard) |
| 2 | Remember contacts already found/contacted **per project**; no repeats until a cooldown the project sets | T23 |
| 3 | **Only verified emails** go out; list free ways to extract contacts | T25 (policy), T28 (free-tools list) |
| 4 | Split people at one company across campaigns; campaign size limits: **n** people, **c** companies, **p** people per company | T23 |
| 5 | "Campaign" means the whole plan for email **and** LinkedIn, inside a project; sending stays optional | T26 (wording), T29 (docs) |
| 6 | Gmail connection a non-technical person can do in a few clicks; plain language everywhere | T27 (wizard), T26 (language pass) |
| 7 | Upload old LinkedIn/email contacts (Excel/CSV template) to filter them out or use them as a signal | T24 |
| 8 | Pro/con report of free or open-source alternatives (tunnel, search, email finding/verification) | T28 (report, owner decides; no code change this sprint) |
| 9 | Clarify where the product/content/website goes, and one page vs whole site | T25 (option + clear wording), T26 |
| – | Running email/LinkedIn outreach is optional; finding verified emails + LinkedIn and drafting happen in the campaign; pushing/sending needs approval | already true; T26/T29 make it obvious |

Out of scope for Monday: T20 (.mcpb one-click extension), Gmail OAuth "Sign in with Google" (needs a Google-verified app; weeks), switching providers (T28 is a decision report only).

## Order and timing (IST, targets)
| When | Task |
|---|---|
| Sat 03:00–05:00 | T22 test speed-up (parallel tests) so every build/review round is fast |
| Sat 05:00–11:00 | T23 projects, contact memory, campaign size & split |
| Sat 11:00–15:00 | T24 old-contacts import (CSV/XLSX template, LinkedIn Connections.csv) |
| Sat 15:00–18:00 | T25 verified-only email policy + one-page/whole-site offer intake |
| Sat 18:00–Sun 02:00 | T26 dashboard v1.1 (projects home, project & campaign pages, plain language) |
| Sun 02:00–06:00 | T27 mailbox connection wizard (Gmail/Workspace, Outlook, other) |
| Sun 06:00–12:00 | T29 regression: e2e for projects, docs, CHANGELOG, owner test checklist |
| Sun 12:00–18:00 | **Owner testing** with `docs/TESTING_CHECKLIST_v1.1.md`; fixes |
| Mon morning | Tag `v1.1.0`, hand over |
T28 runs in parallel from Sat 03:00 (research only).

## Sprint rules (speed without breakage)
- Reviewer: Opus 5.5, effort **high** (not extra-high) for the sprint; **one** fix round, then the orchestrator approves with follow-ups recorded unless there is a blocker: failing tests, data loss, security problem, a broken existing flow, or an acceptance item not met.
- Builder: runs the tests for the files it touched while working, then the full suite **once** with `-n auto` (T22).
- Migrations are forward-only and tested on a copy of a v1.0 database (T23 adds the first real migration).
- Fallback: `v1.0.0` (current main) is the safe version to hand over if a sprint task can't land in time.
