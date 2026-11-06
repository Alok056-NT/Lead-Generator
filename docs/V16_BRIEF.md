# v1.3.3 brief: put contacts back, a calm wave Overview, rewrite from a template (owner, 6 Nov 2026)

Flow decided by Opus. Builder: one Sonnet session, no sub-agents. Plain HTML/CSS/JS, no new dependencies, plain-English labels (static-text tests ban jargon).

**HARD RULE (owner was upset that V15 removed a list without asking):** remove nothing.
- Every option, list, button and link that exists today must still be reachable.
- You may *move* things, or put them in a closed "More" section, only where this brief says so.
- The final message must list everything that moved.

## 1. Put the contacts back (owner: "you unnecessarily removed the contacts")
- On the project page **Waves** tab, under the Waves card, restore the **Ready contacts** card exactly as it was in v1.3.1 (commit `1d45fda`, `resultsBody` in `studio_project.js`). That means:
  - the wave picker, defaulting to the newest (running) wave;
  - the table and paging;
  - rows opening the review drawer.
- Keep the wave list's click → wave page.

## 2. Messages, templates and searches are core features: give them their own tab
- Project page tabs become **Activity | Waves | Messages | Settings**. The tab memory keeps working; map old saved values.
- **Messages** tab: Your sign-off, Message templates (see, add and edit your own), and Searches and message style (which template each wave uses, and changing it).
- **Settings** tab: Your product, Don't contact again, Contacts you already have.
- "Next step" and the Progress steps point to the right tab.

## 3. Rewrite a message from a template (review drawer, owner's 3rd screenshot)
- Under each message in the drawer (email and LinkedIn), add a **"Rewrite from a template"** row: a select of all templates (the 9 built-in ones by name plus the project's own, current one marked) and a **Rewrite** button.
- **Rewrite** asks the AI, through the project's existing chat engine, to rewrite only that person's message for that channel from that template. It must:
  - keep the facts;
  - save it as a new draft that is "Not reviewed yet";
  - send nothing.
- **Plumbing:**
  - Reuse the drafting batch / `save_drafts` tools.
  - If the AI can't target one person today, add the smallest option needed (e.g. `lead_ids` on `get_drafting_batch`), with a test.
  - It must also work from the wave pages, where no chat pane is shown: post to the project chat API.
  - The drawer shows "The AI is rewriting this message…" and a link "See it in the chat". When the message text changes it reloads (check every 3 s, stop after 2 min with a plain note).
- Update `guides/studio.md` so the AI knows this request.
- **Instant option, no AI:** next to Rewrite, add **"Show template"**. It shows the template's text read-only, so the person can copy from it.

## 4. Redo the wave Overview page (`app.js` `pageOverview`): calm, one clear flow
The owner finds it messy: too many count links, flags, zeros and sections.
- **Header:** wave name and one status pill, using the same words as the Waves list. The current "Not started" on a finished wave is wrong; fix it. Under it, a single targeting line, e.g. "SA · events agencies, retail… · Marketing, owners · Managers and above".
- **One "Next step" bar** with one primary button, e.g. "Review 59 messages" or "Export or send".
- **Four step tiles in a row**, wrapping on narrow screens. Each has a big number, one short line and one button to its page:
  1. **Companies:** approved count → "See companies".
  2. **People:** ready count, plus "4 with a verified email" → "See people".
  3. **Messages:** written count, plus "58 approved" → "Review messages".
  4. **Export or send:** sent count → "Export or send".
  - Don't show zero parts ("0 waiting, 0 left out"). Show at most one amber line for something that needs attention (e.g. "529 people need a look"), linking to that filtered list.
- **Checks:** one line, e.g. "Checks: 76 things to look at, 3 to fix", with **Run the checks**. The table goes in a closed "Show the list" section, grouped by what was found with a count per group, and expanding to the rows.
- **Closed "More about this wave" section:** About this campaign, People saved for a later campaign (keep both move actions), and The AI's web search.

## 5. A clear "see and export" flow on each wave's pages
- **Companies, People and Messages pages:** replace the rows of count links and pills above the tables with one **Show** filter whose options carry the counts (e.g. "Ready (26)"). Keep all filters and bulk buttons. Fewer badges in rows: keep the status badge, and drop decorative duplicates.
- **Export or send page** (`pageExport`), as two clear steps:
  1. **Download:** what to include, the format (Excel CSV / lemlist CSV) and one Download button.
  2. **Send from your mailbox (optional).**
  - Keep every existing option and check. Make sure the checks-before-export rule still holds.

## Rules
- Keep every v1.3.2 behaviour, including closing on an outside click, the unsaved-edits guard, Back to project, and Export all waves.
- No database change unless one is needed; if it is, forward-only with a test.
- While working, run only the tests for touched files. At the end run `.venv/Scripts/python -m pytest -q -n auto` once, and fix and re-run until it fully passes. Run `.venv/Scripts/ruff check src tests`.
- **Browser check**, with real clicks, using the fake claude (`LEADGEN_CLAUDE_BIN`, temp `LEADGEN_HOME`, `--port 8792`) and enough sample data to fill the tables. Check the Ready contacts card, the 4 tabs, the new Overview, the Export page steps, and Rewrite (the fake claude may not rewrite; check the request goes out and the waiting note shows). Then stop the server.
- Bump the version to 1.3.3, add STATUS row T34, commit `V16: contacts back, calm Overview, rewrite from template` (trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`), and push to origin main.
- **Final message:** 10 lines max, plain English: what changed, what moved (rule above), and what was not done.
