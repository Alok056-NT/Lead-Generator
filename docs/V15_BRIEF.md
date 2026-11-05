# v1.3.2 brief: one clear path, fewer repeats (owner request, 6 Nov 2026)

Flow decided by Opus. Builder: one Sonnet session, no sub-agents. A Haiku session works at the same time on Mac + upgrade checks (tests, `.github/`, installers only): do not edit those files; it commits only its own paths and does not push. Plain HTML/CSS/JS, no new dependencies, plain-English labels (static-text tests ban jargon).

## The flow: Projects → Project → Wave → back
Owner said: "it feels repetitive and cluttered". Rule: **each thing has one home**.

1. **Project page** (`studio_project.js`): the right panel's tabs become **Activity | Waves | Settings** (rename "Results" to "Waves"; keep the remembered tab per project, map an old saved "results" to "waves").
   - The Waves tab contains only the "Next step" line and the **Waves** card: the list, **Launch a new wave**, **Export all waves**, and an Export link on each row.
   - **Remove from the project page:** the "Ready contacts" card, the in-panel wave table (B2 `waveTable` + its back button), and the "Export and send" card. Their homes are now the wave pages. Delete the dead code and update the tests that pinned them.
   - Per-row Export goes to `#/c/{id}/export`.
   - Progress steps and "Show me" (`goTo` / `STEP_TARGET`) go to the newest wave's matching page: companies → `#/c/{id}/companies`, people/emails → `/leads`, messages → `/messages`, export → `/export`, product → Settings tab. With no wave yet, go to the Waves tab.
2. **Click a wave** (its name, or anywhere on its row except buttons and links) → `#/c/{id}` (the wave's Overview page, which already exists in `app.js`).
3. **Wave pages** (`app.js` `renderNav` / `CAMPAIGN_NAV`):
   - The first thing in the sub-bar is a clear button **"← Back to {project name}"**. It goes to `#/p/{pid}` with the Waves tab chosen (write the tab memory before navigating). It replaces the plain crumb link.
   - Sub-tabs lose the numbers (they skip 3 and 5): **Overview, Companies, People, Messages, Export or send, Results**.
   - The top bar highlights **Projects**, not Advanced, on wave pages (`NAV_GROUP.c`). For a wave without a project, keep Advanced.
4. **Review in one way:** on the wave's People and Messages pages, clicking a person opens the same review drawer as the project page (expose `reviewDrawer` on `window.Studio`; feed it the leads in the shape `/api/projects/{id}/results?campaign_id=` returns).
   - If a page's rows can't be mapped cleanly, keep its old side panel. It must still follow rule 5.
   - Say which pages use which in the final message.

## 5. Pop-ups close like people expect (bug the owner hit)
The message preview that opens from the chat only closes with Esc.

- **Covered:** the review drawer (`studio_project.js`), the side panel (`app.js` `openPanel`), every `<dialog>` (`studio.js` `dialog` / `confirmDialog` and any others), and any menu or popover.
- **Each one closes on:** a click or tap outside it, Esc, and its Close button. Focus then returns to what opened it.
- **Unsaved edits:** if the drawer has unsaved edits, an outside click does not close it. It shows "You have unsaved changes: Save or Discard" with both buttons.
- **Clicks that open it:** the click that opened a pop-up must not close it at once. A click on another row while the drawer is open switches to that person.
- **Sweep:** look for any other pop-up with the same fault and fix it the same way. List what you found.

## 6. Advanced page buttons (`studio.js` `pageAdvanced`)
- Replace the plain link list with button-cards: a heading, one plain line saying what it is for, and one button.
  - **All waves** (`#/campaigns`): every wave in every project.
  - **Results** (`#/analytics`): sent, opened, replied.
  - **Your mailbox** (`#/mailbox`): send from your own email.
  - **Email footer, spelling and spending limits** (`#/settings`).
  - **Earlier project pages:** in a closed `<details>`, together with "Projects, the earlier view".
- Click every button on Advanced and on each page it leads to. Make each page have one primary (blue) button for its main action and plain buttons for the rest. No dead buttons, no two buttons doing the same thing on one page. Fix the labels to plain words.

## Rules
- Keep every v1.3.1 behaviour: verified-only emails, cooldown, checks before export, nothing sent without a click, approve/skip, export all waves, launch dialog.
- No database changes in this brief. If one turns out to be needed, it must be forward-only with a test.
- Run only the tests for files you touch while working. At the end run `.venv/Scripts/python -m pytest -q -n auto` once, then fix and re-run until it passes fully. Run `.venv/Scripts/ruff check src tests`.
- **Browser check** with the fake claude (`LEADGEN_CLAUDE_BIN`, temp `LEADGEN_HOME`, `--port 8791`). Use real clicks, not simulated events:
  - wave → wave page → Back;
  - drawer and side panel close on an outside click;
  - the unsaved-edits guard;
  - dialogs close on a backdrop click;
  - every Advanced button.
- Stop the test server when done.
- Bump the version to 1.3.2. Add a STATUS row T33. Commit `V15: one clear path` (trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`). Then `git pull --rebase origin main` (Haiku's commits) and push to origin main.
- Final message: 10 lines max, plain English, including anything not done.
