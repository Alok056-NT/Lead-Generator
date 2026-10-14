# How to build Lead Generator (for Alok)

The build is split into 21 small tasks (`docs/tasks/T01…T21`), built in this order: T01 … T11, **T21**, T12 … T20.

Two helpers live in this repo (`.claude/agents/`):
- **builder**: Claude Sonnet 5.5, high effort. Writes the code and tests for one task. Never pushes.
- **reviewer**: Claude Opus. Checks the work against the task card and the safety rules, runs the tests itself, then either approves it (marks it done and pushes to GitHub) or sends back a fix list.

The **`/build` command** runs them for you in a loop.

## Start the build (automatic, recommended)
1. Open a **new Claude Code session** in the folder `D:\Claude Files\Lead-Generator`.
2. Pick **Sonnet 5.5** for the session itself (it only coordinates, so it doesn't need Opus; the reviewer still runs on Opus).
3. Type one of:
   - `/build`: do the next task only, then stop (good for the first run, to see how it goes).
   - `/build all`: keep going task after task until everything is done or something needs you.
   - `/build T05`: do one specific task.
4. The first time it pushes, Windows may open a **GitHub sign-in window**: sign in once.

What happens for each task: builder writes it → reviewer checks it → if there are problems, builder fixes them (up to 2 rounds) → reviewer approves and pushes → next task. You get a 2-3 line plain-English update after each task.

**It stops and asks you when:**
- it needs a key, account or click only you can do;
- a task is stuck after 2 fix rounds;
- a push fails;
- before the optional task T20 and before the v1.0 release.

Tests that need your real keys don't stop the build: they're listed as "manual check pending" in `docs/STATUS.md` for you to do later.

**To pause:** press Esc or close the session. Run `/build all` again later; it picks up from `docs/STATUS.md`.

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
About 21 tasks. Each needs one builder run and one reviewer run, plus fix rounds when needed: expect roughly 45–65 agent runs in total. T08 (people finder) and T11 (quality checks) are the biggest. Running `/build` a few tasks at a time lets you watch your usage.

## Manual fallback (if you ever want to run a task by hand)
- Sonnet session: `Read CLAUDE.md, then do task T05 (docs/tasks/T05-offer-intake.md). Follow the task protocol exactly. Don't push to GitHub.`
- Opus session: `Review task T05 against its task card. If it's good, mark it done in docs/STATUS.md and push to GitHub. If not, list the fixes.`
