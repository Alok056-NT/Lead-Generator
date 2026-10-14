# How to build Lead Generator (for Alok)

The build is split into 21 small tasks (`docs/tasks/T01…T21`), built in this order: T01 … T11, **T21**, T12 … T20.

## Who does what
| Role | Model | Effort | Does |
|---|---|---|---|
| **Orchestrator** (the session you start) | Opus 5.5 | extra-high | Plans each task, decides which model does which part, runs the loop, reports to you |
| **worker** | Haiku 5.5 | high | Repetitive, mechanical jobs listed in a task card under "Worker prep (Haiku)": data tables, synthetic test data, sample files, first drafts of reference docs |
| **builder** | Sonnet 5.5 | high | All real code, logic, research and tests; checks and owns the worker's output |
| **reviewer** | Opus 5.5 | extra-high | Runs the tests, checks the work against the task card and safety rules, approves and pushes to GitHub, or sends back fixes |

Why Haiku at high effort: its jobs are mostly accurate data entry (time zones, country codes, exact test cases), where a careless mistake costs more than the small price difference. Everything Haiku writes is checked twice (Sonnet, then Opus).

Settings live in `.claude/settings.json` (session model and effort) and `.claude/agents/*.md` (each helper's model and effort).

## Start the build
1. Open a **new Claude Code session** in `D:\Claude Files\Lead-Generator` (it picks Opus 5.5 extra-high automatically from the repo settings).
2. Type one of:
   - `/build`: do the next task only, then stop (good for the first run).
   - `/build all`: keep going until everything is done or something needs you.
   - `/build T05`: do one specific task.
3. The first push may open a **GitHub sign-in window**: sign in once.

For each task: (Haiku prep, if the task has any) → Sonnet builds → Opus reviews → fixes if needed (mechanical ones to Haiku, logic to Sonnet; up to 2 rounds) → Opus approves and pushes → next task. You get a 2-3 line plain-English update after each.

**It stops and asks you when:**
- it needs a key, account or click only you can do;
- a task is stuck after 2 fix rounds;
- a push fails;
- before the optional T20 and before the v1.0 release.

Tests that need your real keys don't stop the build: they're listed as "manual check pending" in `docs/STATUS.md`.

**To pause:** press Esc or close the session. `/build all` picks up from `docs/STATUS.md`.

## Things you'll be asked for along the way
| When | What | Cost |
|---|---|---|
| T04 check | Serper API key (serper.dev) | free (2,500 searches, one-time) |
| T09 check | Hunter key (hunter.io), Reoon key (reoon.com) | free tiers |
| T12 check | lemlist API key + a test lemlist campaign | needs a lemlist plan |
| T14 check | A Gmail/Workspace app password for a test mailbox | free |
| T17 check | ChatGPT paid plan with Developer Mode (for the ChatGPT connector) | your ChatGPT plan |

Keys go into the tool's own key store (`leadgen keys set …` or the dashboard), **never** into the code or GitHub.

## Rough size
21 tasks; each has one Sonnet build and one Opus review, six tasks also have Haiku prep, plus fix rounds when needed: roughly 50–75 helper runs in total. Opus at extra-high effort is the most expensive part (orchestrating and reviewing), so running a few tasks at a time with `/build` lets you watch your usage.

## Manual fallback (to run a task by hand)
- Sonnet session: `Read CLAUDE.md, then do task T05 (docs/tasks/T05-offer-intake.md). Follow the task protocol exactly. Don't push to GitHub.`
- Opus session: `Review task T05 against its task card. If it's good, mark it done in docs/STATUS.md and push to GitHub. If not, list the fixes.`
