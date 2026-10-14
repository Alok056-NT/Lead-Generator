# How to build Lead Generator, task by task (for Alok)

The build is split into 20 small tasks (`docs/tasks/T01…T20`). Sonnet does each task; Opus reviews it before the next one starts. Do them **in order**, one per session.

## For each task

1. **Open a new Claude Code session** in the folder `D:\Claude Files\Lead-Generator`.
2. **Pick the model**: Sonnet 5.5, effort **High** (model menu in the app).
3. **Paste this message** (change the task number):

   ```
   Read CLAUDE.md, then do task T01 (docs/tasks/T01-scaffold-config.md).
   Follow the task protocol exactly. Don't push to GitHub.
   ```

4. Let it work. If it asks you a question you can't answer, say: "Write the question into docs/STATUS.md under Follow-ups and stop."
5. When it says it's done, **open a session with Opus** (or come back to the planning session) and paste:

   ```
   Review task T01 in D:\Claude Files\Lead-Generator against its task card.
   If it's good, mark it done in docs/STATUS.md and push to GitHub. If not, list the fixes.
   ```

6. If the reviewer lists fixes, go back to a Sonnet session and paste:

   ```
   Read CLAUDE.md. The reviewer asked for these fixes to T01: <paste the list>. Make them, rerun the tests, update STATUS.md.
   ```

7. Repeat for the next task.

## Things you'll be asked for along the way
| When | What | Cost |
|---|---|---|
| T04 test | Serper API key (serper.dev) | free (2,500 searches, one-time) |
| T09 test | Hunter key (hunter.io), Reoon key (reoon.com) | free tiers |
| T12 test | lemlist API key + a test lemlist campaign | needs a lemlist plan |
| T14 test | A Gmail/Workspace app password for a test mailbox | free |
| T17 test | ChatGPT paid plan with Developer Mode (for the ChatGPT connector) | your ChatGPT plan |

Keys go into the tool's own key store (`leadgen keys set …` or the dashboard), **never** into the code or GitHub.

## Rough effort
Foundation and data (T01–T07): ~7 sessions. People, email, drafting, QA (T08–T11): ~4–6 sessions (T08 and T11 are the biggest). Delivery and interfaces (T12–T17): ~6–8 sessions. Docs and release (T18–T19): ~2 sessions.
