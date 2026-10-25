# Lead Generator v1.3: design

Status: ready for the coder. Written 2026-10-25 by the architect from `docs/V13_BRIEF.md`, `docs/V13_RESEARCH.md`, `docs/V12_DESIGN.md` and the code at commit `6ddc337`. Read `CLAUDE.md` first: invented people only, no network in tests, no secrets or message bodies in logs, UTF-8 everywhere, `encoding="utf-8"` on every `open`.

**How to use this document.** Section 8 lists seven chunks (C1 to C7). Do them in order, one per session. Sections 1 to 7 are the specification the chunks point to. A chunk never asks you to choose a design; if something is unclear or impossible, stop and report `BLOCKED: <what>`.

**Ground rules for every chunk**
- Adapters stay thin: `dashboard/app.py` and `mcp_server.py` validate input, call one service function and shape the reply. New logic goes in `services/` or `providers/`.
- Nothing that works today may break: old routes, MCP tools and replies keep their names; new fields are additions. The old pages stay reachable under **Advanced**.
- No new dependency (Python or JS). No CDN, no build step, no inline `<script>` or `style=""` attributes (the CSP is `default-src 'self'`; setting `el.style.setProperty(...)` from JS is allowed, `setAttribute('style', ...)` is not).
- Before writing any provider call, read the provider's page named in the chunk and add a row to `docs/DECISIONS.md` (CLAUDE.md step 4). The research in `docs/V13_RESEARCH.md` section 3 is the starting point; this document adds four facts the architect read on 2026-10-25 (section 3.1, marked "read by the architect").
- Tests: `.venv/Scripts/python -m pytest -q -n auto` and `.venv/Scripts/ruff check src tests` must pass at the end of every chunk. HTTP is mocked with `respx`; the AI is the fake `tests/fake_claude.py`.
- Words the person sees are plain English and must pass the banned-word scan in `tests/test_dashboard_projects.py` (`BANNED`). In particular never show: designation, cooldown, reserve, QA, MCP, JSON, cursor, endpoint, payload, dry run, confirmed, signals, evidence. Use "job title", "don't contact again for", "saved for later", "checks".
- Commit message `V13 Cn: <title>` with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push.

---

## 0. What this builds on (verified in the code at `6ddc337`)

| Thing | Where | Used for |
|---|---|---|
| Dashboard guard (loopback Host, `X-LeadGen: 1` + same Origin on changes, CSP `'self'`), `_reply`, `_json`, `_error`, `_Body` (`extra="forbid"`) | `dashboard/app.py` | Every new route |
| Static files: `app.css` then `studio.css`; scripts `studio.js`, `studio_project.js`, `app.js` (in that order, `defer`). `app.js` owns the helpers `h, api, clear, setMain, toast, notice, errorText, badge, extLink, field, selectBox, pager, copyText, query, fmtNum, fmtDay, setStatus, go, route, bar, routeToken` and the old pages (`providerCard`, `memoryBox`, `pageSettings`, `pageContacts`, `pageNewCampaign`, ...) | `dashboard/static/` | New files are loaded before `app.js` and only define functions; they call the helpers at run time |
| Static rules enforced by tests: no `innerHTML`, no `http://`/`https://` in scripts or `index.html`, no `allow_guess` anywhere in the dashboard, banned words, `--accent: #0071e3`, `prefers-color-scheme: dark`, `backdrop-filter`, `prefers-reduced-motion` in `studio.css`; the labels listed in `tests/test_studio_static.py` | `tests/test_studio_static.py`, `tests/test_dashboard*.py`, `tests/test_verified_emails.py` | Every UI chunk keeps them |
| `test_no_dashboard_route_changes_campaign_options`: `POST/PUT/PATCH /api/campaigns/{id}` and `POST /api/campaigns/{id}/options` must stay 404/405 | `tests/test_verified_emails.py` | The template choice uses `POST /api/campaigns/{id}/template` (different path, one field) |
| Chat engine: `chat.build_command`, `Mapper` (stream-json to SSE events `turn, text, tool, refresh, chips, error, done`), `chat.build_prompt` (Studio context block), `stream_events` | `services/chat.py` | Model and effort flags, two new events (`thinking`, `usage`) |
| `drafting.get_drafting_batch` (offer, people, `sender_name`), `qa_messages.check_message` (M01-M15), `review.evaluate` / `_record_pass`, `drafting.save_context` | services | Sign-off block, templates, M17 |
| `emails.find_emails` (chunked, `_Run`, `verify`, `store_email`, `_budget_gate`, `_estimate`), `budget.check/charge`, `registry.error_response` | `services/emails.py`, `services/budget.py`, `providers/registry.py` | Finder step from a LinkedIn profile, phones |
| Provider pattern: `INFO = ProviderInfo(...)`, class with `configured()`, `test()`, `base.send_with_retry`, `base.error_for_status`, `base.json_object`, `base.record_usage`, `secrets.get(ENV_KEY)` | `providers/reoon.py`, `providers/serper.py` | Five new provider modules copy this shape |
| Forward-only migrations `db.MIGRATIONS` (now v5), `db.TABLES` whitelist | `db.py` | v6 |
| `preferences.get_ai_mode/set_ai_mode` (a row in `settings`) | `services/preferences.py` | Model and effort are stored the same way |
| `mcp_server._clip` shortens strings over 2000 characters except keys in `keep` | `mcp_server.py` | `get_drafting_batch` keeps `templates` |

---

## 1. Site map, flow and screens

### 1.1 Two scopes, never mixed (research section 2, rule 1)

| Scope | What lives there | Where |
|---|---|---|
| **App** (once per computer) | Your AI (sign-in, model, effort), Connectors (every key, grouped by segment), Usage (tokens and cost) | Top bar: `Your AI` (`#/connect`), `Connectors` (`#/keys`), `Usage` (`#/usage`) |
| **Project** (one per product) | The product, the sign-off (name, job title, company), don't-contact-again days, contacts you already have, searches, message templates and the message style of each search, results | `#/p/<id>` only |
| Old pages | Everything from v1.1/v1.2 (campaign pages, old project page, old settings: postal address, spelling, spending limits, strict checks; mailbox) | `#/advanced` |

A setting appears in exactly one scope. No key is ever asked for on a project screen; no project data appears on an app screen (the Usage page lists projects by name only).

### 1.2 Routes (hash) and top bar

Top bar (frosted, existing `.top`): brand, nav, status pill.

`TOP_NAV` in `app.js` becomes, in this order: `['home', 'Projects']`, `['connect', 'Your AI']`, `['keys', 'Connectors']`, `['usage', 'Usage']`, `['advanced', 'Advanced']`. `NAV_GROUP` gains `settings: 'advanced'`.

| Route | Page | Function |
|---|---|---|
| `#/connect` | Connect your AI (+ new "Model and effort" card) | `Studio.pageConnect` (existing) |
| `#/home` | Projects | `Studio.pageHome` (existing, one new tip line) |
| `#/p/<id>` | Project workspace | `Studio.pageProject` (rewritten, section 1.6) |
| `#/keys` | Connectors | `Studio.pageConnectors` (new) |
| `#/usage` | Usage | `Studio.pageUsage` (new) |
| `#/advanced` | Advanced (adds the link "Email footer, spelling and spending limits" to `#/settings`) | existing |
| `#/settings`, `#/mailbox`, `#/campaigns`, `#/c/...`, `#/p/<id>/details`, `/contacts`, `/new-campaign` | old pages, unchanged | existing |

**Status pill** (`#ai-pill`, every page, `Studio.pill`): when the AI is not ready, a grey dot and the link text **AI not connected** to `#/connect`. When ready, a green dot and the link text `<Model> · <Effort> · today <amount>` to `#/usage`, where `<Model>` is `Sonnet` or `Opus`, `<Effort>` is `Low`, `Medium`, `High`, `Extra high` or `Max`, and `<amount>` is `about $0.42` when today's cost is known, else `12.3k tokens`, else (no turns today) `nothing used yet`.

### 1.3 First run and flow

1. No AI mode chosen and "Skip for now" never pressed: `#/home` sends you to `#/connect` (existing rule).
2. After **Test the connection** succeeds the Connect page shows **Model and effort** and the button **Go to my projects**.
3. Home: if no search key is set, one quiet line under the title: "Tip: add a free search key on the Connectors page so the AI can look for people." with the link text **Open Connectors** (`#/keys`). Never a blocker.
4. Project: chat first. The AI asks for the product, the sign-off and the channel (studio guide, section 5.4). Everything else is in collapsed cards.
5. When a task needs a key the AI says so and shows the chip button **Open Connectors** (`[[go: keys]]`).

### 1.4 Connect your AI (`#/connect`)

Unchanged cards: **1. Install Claude Code**, the choice **How do you want to use the AI?** (**Use my Claude plan** / **Use an API key**), **2. Sign in to Claude** or **2. Your API key**, **3. Test it**, link **Skip for now**.

New card, shown when `ai.ready` is true, after "3. Test it": title **4. Model and effort**.
- Text: "Which Claude model the assistant uses, and how hard it thinks before it answers. More effort is slower and uses more of your plan or credit."
- Field **Model** (select): `Sonnet (recommended)` [`sonnet`], `Opus (strongest, uses more)` [`opus`].
- Field **Effort** (select): `Low (fastest)` [`low`], `Medium` [`medium`], `High (recommended)` [`high`], `Extra high` [`xhigh`], `Max (slowest)` [`max`].
- Button **Save** (primary). On success: toast "Saved. Your next message uses it." and the pill refreshes.
- Small text: "Defaults: Sonnet, high."

### 1.5 Connectors (`#/keys`)

- Title **Connectors**. Lead: "Services Lead Generator can use with your own keys. Every key stays on this computer and is never shown again (only its last four characters). All of them are optional: the AI tells you when one would help."
- Row of buttons: **Collapse all** / **Expand all** (one button that toggles its label).
- Four collapsible cards, in this order, each titled with the segment label and a count badge `2 connected` (or `none connected`):
  1. **Search** (open by default): Serper, Tavily, Brave, DuckDuckGo.
  2. **Email and phone from a LinkedIn profile**: Apollo, Prospeo, GetProspect, Hunter.
  3. **Email checking**: Reoon, ZeroBounce, Emailable.
  4. **Sending**: Your mailbox, lemlist.
- Under the card title, one line per segment (muted):
  - Search: "Used to find companies and people. Serper and Tavily have free allowances without a card."
  - Finding: "Given a person's LinkedIn profile address, these look up a work email (and a phone number only if you ask). Every email found is still checked before it is used. Phone numbers are shown as found, not checked."
  - Checking: "Checks that an email address really exists. Only checked addresses are ever exported or sent."
  - Sending: "Optional. Send from your own mailbox, or hand the contacts to lemlist."
- Each provider is the existing `providerCard(p)` from `app.js` (title, badge **Set ••••1234** or **Not set**, purpose, free allowance and used-this-month line, key field **Key** with **Save and test**, **Test the saved key (may use 1 credit)**, **Remove the key**, link **Get a key (<name> sign-up page)**), with two changes made in `app.js`:
  - A new muted line "Costs: <p.costs>" under the purpose when `p.costs` is not empty.
  - For `smtp`: instead of the terminal hint, the button-link **Connect your mailbox** (`#/mailbox`) and the line "Connected" or "Not connected yet" from `p.configured`.

### 1.6 Project workspace (`#/p/<id>`)

**Header row** (one line, wraps on narrow screens):
- Link **All projects** (`#/home`).
- `h1` the project name.
- Select **Switch project** (visually hidden label) listing every project by name; choosing one goes to `#/p/<id>`.
- Static chip `Signs as <name>` or `Sign-off not set yet` (muted).
- Static chip `This project: <tokens> tokens · about $<cost>` (from `GET /api/usage?project_id=`; cost part left out when unknown).
- Segmented control **Layout**: **Chat wide** / **Side by side** / **Results wide** (saved per browser under `lg.layout.project`; default **Side by side**). Hidden below 900 px (the tabs take over).
- Button **Collapse all** / **Expand all** (toggles every card on the right).

**Narrow screens (< 900 px):** existing tabs **Chat** and **Progress and results**.

**Left column: chat card** (`.chat-card`, fills the column height, scrolls inside):
- Title **Chat with your AI**.
- The chat log (`role="log"`). AI replies are flat text (no bubble), your messages are dark bubbles on the right (ElevenLabs "flat" variant). Tool rows from history are compact muted lines with a dot. A round **↓** button ("Scroll to the latest message") appears when you are not at the bottom.
- Empty project: the existing greeting bubble.
- Chips and file pills (existing).
- Input bar: a raised card with the growing textarea (placeholder "Type your message..."), **Attach a file**, and **Send** (turns into **Stop** while the AI works).
- When the AI is not connected: the existing notice with **Connect your AI**.

**Right column: stack of collapsible cards** (each remembers open/closed per browser under `lg.card.<id>`; defaults in brackets):

| # | Card id | Title | Default | Content |
|---|---|---|---|---|
| 1 | `p.activity` | **What the AI is doing** | open | The agent strip (orb, shimmering status line, 15-bar visualizer) and the live timeline (section 6.2). Footer: "This reply: <n> tokens · about $<x> (estimate)". Idle text: "Waiting for your message". |
| 2 | `p.progress` | **Progress** | open | Existing stepper |
| 3 | `p.results` | **Ready contacts** | open | Existing results table plus a column **Phone (found, not checked)** (section 3.6). Count shown as the card's badge. |
| 4 | `p.send` | **Export and send** | closed | Existing export/send/lemlist body ("Nothing is sent until you click and confirm.") |
| 5 | `p.searches` | **Searches and message style** | closed | Table: **Search**, **Status**, **Ready**, **Message style** (a select per search, section 5.5). Link **New search (form)** to `#/p/<id>/new-campaign`. Text: "Or tell the AI who you want to reach." Empty: "No searches yet. Tell the AI who you want to reach." |
| 6 | `p.templates` | **Message templates** | closed | Section 5.4 |
| 7 | `p.signoff` | **Your sign-off** | closed | Section 4.1 |
| 8 | `p.product` | **Your product** | closed | "What it is about: <about>" and "What we read: <source kind text>", or the warning "No product is attached yet. Send your website address or a file in the chat." Link **Change it or other project settings** (`#/p/<id>/details`). |
| 9 | `p.memory` | **Don't contact again** | closed | The existing `memoryBox(pr, mem)` body (its own heading hidden by CSS) |
| 10 | `p.contacts` | **Contacts you already have** | closed | Text "Upload people you have already spoken to, so the AI can skip them or treat them as warm leads." The known-contacts sentence (same wording as the old page), and three buttons **Download the Excel template**, **Download the CSV template**, **Upload your contacts** (`#/p/<id>/contacts`). |

**Minimal scrolling:** on screens 900 px and wider the page itself does not scroll: the grid is `calc(100vh - 150px)` high, the chat log and the right column scroll inside themselves, and an open card body is at most 60vh high with its own scroll.

### 1.7 Usage (`#/usage`)

- Title **Usage**. Lead: "How much of your AI this app has used. Costs are estimates from Claude Code, not your bill. On a Claude plan you pay your plan, not these amounts."
- Line "Model now: <Model> · effort <Effort>" with link **Change** (`#/connect`).
- Three tiles (`.tiles` of `.card.tile`): **Today** (replies, tokens, about $), **This month** (same), **All time** (same). Each tile: big number = total tokens; under it "<n> replies · about $<x>" (cost part only when known).
- Card **By project (all time)** (open): table **Project**, **Replies**, **Tokens**, **About**, plus a bar (`bar(value, max)` from `app.js`) of the project's share of tokens. Projects that were deleted show as "Deleted project".
- Card **Last 20 replies** (closed): table **When** (`fmtDate`), **Project**, **Model**, **Effort**, **Read** (input tokens), **Written** (output tokens), **From memory** (cache read), **Saved to memory** (cache write), **About** (cost), and a red badge **Stopped** for error turns.
- Empty state: "Nothing used yet. Usage appears here after your first chat message."

### 1.8 Home (`#/home`)

Unchanged except the tip line in 1.3 (step 3). Cards keep **Open**, **Edit**, **Delete**.

---
## 2. Design system (ElevenLabs UI look in plain CSS/JS)

Source: research section 1. Neutral grey surfaces, hairline borders, 14 px card radius, `shadow-sm` cards, 14 px text, 36 px controls, 150/200/300 ms motion, fade-in-up entries, and three "alive" visuals (orb, bar visualizer, shimmering text). Our blue accent stays for the one main action per screen (and the `--accent: #0071e3` test). System font stack (no Geist, no CDN).

### 2.1 `static/studio.css`: replace the whole file with this

It keeps every class the existing scripts use and adds the new components. It is loaded after `app.css`, so the old pages are re-coloured by the same variable names.

```css
/* Lead Generator design system (v1.3): neutral surfaces after ElevenLabs UI (MIT; look only, no code
   copied), blue accent for the main action only. Plain CSS, no fonts or files from outside. */
:root {
  /* colour: light */
  --bg: #fafafa;
  --surface: #ffffff;
  --surface-2: #f5f5f5;
  --text: #0a0a0a;
  --muted: #737373;
  --line: #e5e5e5;
  --hairline: #e5e5e5;
  --primary: #171717;
  --primary-text: #fafafa;
  --accent: #0071e3;
  --accent-hover: #0077ed;
  --accent-press: #006edb;
  --accent-text: #ffffff;
  --accent-soft: rgba(0, 113, 227, 0.10);
  --ok: #15803d;
  --ok-bg: #f0fdf4;
  --warn: #a16207;
  --warn-bg: #fefce8;
  --bad: #e7000b;
  --bad-bg: #fef2f2;
  --on-bad: #ffffff;
  --info: #1d4ed8;
  --info-bg: #eff6ff;
  --bar: #3b82f6;
  --bar2: #15803d;
  --focus: #737373;
  --bubble-me: #171717;
  --bubble-me-text: #fafafa;
  --bar-bg: rgba(255, 255, 255, 0.8);
  --scrim: rgba(0, 0, 0, 0.35);
  --orb-a: #cadcfc;
  --orb-b: #a0b9d1;
  /* shape */
  --r-xs: 6px;
  --r-sm: 8px;
  --r-md: 10px;
  --r-lg: 14px;
  --r-pill: 999px;
  /* shadow (sm, xs, md, lg) */
  --shadow-1: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1);
  --shadow-xs: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-2: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
  --shadow-3: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
  /* spacing (4 px unit) */
  --s-1: 4px;
  --s-2: 8px;
  --s-3: 12px;
  --s-4: 16px;
  --s-5: 24px;
  --s-6: 32px;
  --s-7: 48px;
  --s-8: 72px;
  /* type */
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", system-ui, sans-serif;
  --font-display: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Mono", Consolas, monospace;
  /* motion */
  --ease: cubic-bezier(0.4, 0, 0.2, 1);
  --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --t-fast: 150ms var(--ease);
  --t: 200ms var(--ease);
  --t-slow: 300ms var(--ease-out);
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0a0a0a;
    --surface: #171717;
    --surface-2: #262626;
    --text: #fafafa;
    --muted: #a1a1a1;
    --line: rgba(255, 255, 255, 0.15);
    --hairline: rgba(255, 255, 255, 0.10);
    --primary: #e5e5e5;
    --primary-text: #171717;
    --accent: #4aa3ff;
    --accent-hover: #6ab4ff;
    --accent-press: #3a96f5;
    --accent-text: #001a33;
    --accent-soft: rgba(74, 163, 255, 0.16);
    --ok: #4ade80;
    --ok-bg: #052e16;
    --warn: #facc15;
    --warn-bg: #422006;
    --bad: #ff6467;
    --bad-bg: #450a0a;
    --on-bad: #2b0006;
    --info: #93c5fd;
    --info-bg: #172554;
    --bar: #60a5fa;
    --bar2: #4ade80;
    --focus: #a1a1a1;
    --bubble-me: #e5e5e5;
    --bubble-me-text: #171717;
    --bar-bg: rgba(23, 23, 23, 0.8);
    --scrim: rgba(0, 0, 0, 0.6);
    --shadow-1: 0 1px 2px rgb(0 0 0 / 0.5);
    --shadow-xs: 0 1px 2px rgb(0 0 0 / 0.4);
    --shadow-2: 0 4px 12px rgb(0 0 0 / 0.5);
    --shadow-3: 0 12px 32px rgb(0 0 0 / 0.6);
  }
}
@media (prefers-reduced-motion: reduce) {
  :root { --t-fast: 0ms; --t: 0ms; --t-slow: 0ms; }
  *, *::before, *::after { animation: none !important; transition: none !important; }
}

/* ---- base ---------------------------------------------------------------------------------- */
html { -webkit-text-size-adjust: 100%; }
body {
  font-family: var(--font); font-size: 14px; background: var(--bg); color: var(--text); line-height: 1.5;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
::selection { background: var(--text); color: var(--surface); }
h1, h2, h3 { font-family: var(--font-display); letter-spacing: -0.01em; }
h1 { font-size: 1.5rem; line-height: 1.2; font-weight: 650; margin: 0; }
h2 { font-size: 1.25rem; font-weight: 600; }
h3 { font-size: 1rem; font-weight: 600; }
.lead { font-size: 1rem; color: var(--muted); margin: 0 0 var(--s-4); }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; border-radius: var(--r-xs); }
.center { text-align: center; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}
@keyframes fade-in-up { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes pulse { 0%, 100% { opacity: 0.35; transform: scale(0.85); } 50% { opacity: 1; transform: scale(1.1); } }

/* ---- top bar (frosted) ----------------------------------------------------------------------- */
.top {
  position: sticky; top: 0; z-index: 40; padding: var(--s-2) var(--s-5); border-bottom: 1px solid var(--hairline);
  background: var(--bar-bg); -webkit-backdrop-filter: saturate(180%) blur(20px); backdrop-filter: saturate(180%) blur(20px);
}
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .top { background: var(--surface); }
}
.brand { font-family: var(--font-display); font-weight: 650; font-size: 1rem; }
nav a { border-radius: var(--r-sm); padding: 6px 12px; color: var(--muted); font-weight: 500; transition: background var(--t-fast), color var(--t-fast); }
nav a:hover { background: var(--surface-2); color: var(--text); }
nav a[aria-current="page"] { background: var(--surface-2); color: var(--text); }
.pill { margin-left: auto; font-size: 13px; }
.pill-link { display: inline-flex; align-items: center; gap: var(--s-2); text-decoration: none; color: var(--text);
  border: 1px solid var(--hairline); border-radius: var(--r-pill); padding: 4px 12px; background: var(--surface); box-shadow: var(--shadow-xs); }
.pill-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--line); }
.pill-link.on .pill-dot { background: var(--ok); }

main { max-width: 1440px; padding: var(--s-4) var(--s-5); }

/* ---- cards, buttons, fields -------------------------------------------------------------------- */
.card {
  background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--r-lg);
  box-shadow: var(--shadow-1); padding: var(--s-5); transition: box-shadow var(--t), transform var(--t);
}
.project-card:hover { box-shadow: var(--shadow-2); transform: translateY(-1px); }
.cards { grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--s-4); }
.card-actions { margin-top: var(--s-3); }
.empty { text-align: center; padding: var(--s-7) var(--s-5); }
button, .button {
  height: 36px; display: inline-flex; align-items: center; justify-content: center; gap: var(--s-2);
  border-radius: var(--r-sm); padding: 0 14px; font-size: 14px; font-weight: 500; border: 1px solid var(--line);
  background: var(--surface); color: var(--text); box-shadow: var(--shadow-xs);
  transition: background var(--t-fast), border-color var(--t-fast), opacity var(--t-fast), transform var(--t-fast);
}
button:hover:not(:disabled), .button:hover { background: var(--surface-2); border-color: var(--line); }
button:active:not(:disabled) { transform: scale(0.98); }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.primary, .button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); box-shadow: none; }
button.primary:hover:not(:disabled), .button.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
button.primary:active:not(:disabled) { background: var(--accent-press); }
button.dark { background: var(--primary); border-color: var(--primary); color: var(--primary-text); box-shadow: none; }
button.dark:hover:not(:disabled) { opacity: 0.9; background: var(--primary); }
button.big, .button.big { height: 40px; padding: 0 22px; font-size: 15px; border-radius: var(--r-pill); }
button.danger { color: var(--bad); background: transparent; border-color: transparent; box-shadow: none; }
button.danger.solid { background: var(--bad); border-color: var(--bad); color: var(--on-bad); }
button.link { height: auto; background: none; border: none; box-shadow: none; padding: 0; color: var(--accent); text-decoration: underline; }
button.ghost { background: transparent; border-color: transparent; box-shadow: none; }
button.round { width: 36px; padding: 0; border-radius: 50%; }
input, select, textarea { border-radius: var(--r-sm); padding: 8px 10px; background: var(--surface); border: 1px solid var(--line); font-size: 14px; }
input:focus, textarea:focus, select:focus { outline: none; border-color: var(--focus); box-shadow: 0 0 0 3px var(--accent-soft); }
.notice { border-radius: var(--r-md); border-color: var(--hairline); }
.badge { border-radius: var(--r-pill); font-weight: 500; }
.table-wrap { border-radius: var(--r-md); border-color: var(--hairline); box-shadow: none; }
th { font-size: 12px; text-transform: none; letter-spacing: 0; font-weight: 500; }
pre.command, pre.msg {
  font-family: var(--font-mono); font-size: 13px; background: var(--surface-2); border: 1px solid var(--hairline);
  border-radius: var(--r-sm); padding: var(--s-3); white-space: pre-wrap; overflow-wrap: anywhere; margin: var(--s-2) 0;
}
.chip-static { display: inline-flex; align-items: center; height: 28px; padding: 0 10px; border-radius: var(--r-pill);
  background: var(--surface-2); color: var(--muted); font-size: 13px; white-space: nowrap; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: var(--s-3); }
.tile .num { font-size: 1.75rem; font-weight: 650; letter-spacing: -0.02em; }

/* segmented control (tabs look) */
.segmented { display: inline-flex; gap: 2px; height: 36px; padding: 3px; border-radius: var(--r-md); background: var(--surface-2); }
.segmented button { height: 30px; border: 1px solid transparent; background: transparent; box-shadow: none; color: var(--muted); padding: 0 12px; }
.segmented button[aria-checked="true"] { background: var(--surface); color: var(--text); border-color: var(--hairline); box-shadow: var(--shadow-1); }

/* ---- collapsible cards ------------------------------------------------------------------------- */
.ui-card { padding: 0; animation: fade-in-up 0.4s var(--ease) both; }
.card-head { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-4); min-height: 48px; }
.card-toggle {
  flex: 1; justify-content: flex-start; height: auto; min-height: 32px; padding: 0; border: none; background: none;
  box-shadow: none; font-size: 15px; font-weight: 600; text-align: left;
}
.card-toggle:hover:not(:disabled) { background: none; }
.chev { width: 7px; height: 7px; flex: none; border-right: 2px solid var(--muted); border-bottom: 2px solid var(--muted);
  transform: rotate(-45deg); transition: transform var(--t); margin: 0 4px 0 2px; }
.ui-card[data-open="true"] .chev { transform: rotate(45deg); }
.card-title { flex: 1; }
.card-count { color: var(--muted); font-weight: 500; font-size: 13px; }
.card-body { display: grid; grid-template-rows: 0fr; transition: grid-template-rows var(--t-slow); }
.ui-card[data-open="true"] .card-body { grid-template-rows: 1fr; }
.card-inner { min-height: 0; overflow: hidden; padding: 0 var(--s-4); }
.ui-card[data-open="true"] .card-inner { overflow-y: auto; max-height: 60vh; padding-bottom: var(--s-4); }
.card-inner > * + * { margin-top: var(--s-3); }
/* old cards shown inside a new card become plain rows */
.card-inner > .card { border: none; border-top: 1px solid var(--hairline); border-radius: 0; box-shadow: none; padding: var(--s-3) 0; background: none; }
.card-inner > .card > h3:first-child { display: none; }

/* ---- connect your AI ------------------------------------------------------------------------- */
.connect { max-width: 720px; margin: 0 auto; }
.choose { border: none; padding: 0; margin: 0; }
.choose legend { font-weight: 600; padding: 0; margin-bottom: var(--s-2); }
.choices { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-3); }
button.choice {
  height: auto; display: flex; flex-direction: column; align-items: flex-start; gap: var(--s-1); text-align: left;
  padding: var(--s-4); border-radius: var(--r-lg); border: 1px solid var(--line); background: var(--surface);
}
button.choice.on { border-color: var(--text); box-shadow: 0 0 0 1px var(--text); }
.choice-title { font-weight: 600; font-size: 15px; }
.choice-text { color: var(--muted); }

/* ---- dialogs ------------------------------------------------------------------------------------ */
dialog.dialog {
  border: 1px solid var(--hairline); border-radius: var(--r-lg); padding: var(--s-5); width: 100%;
  max-width: min(560px, calc(100vw - 32px)); background: var(--surface); color: var(--text); box-shadow: var(--shadow-3);
}
dialog.dialog::backdrop { background: var(--scrim); -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px); }
dialog.dialog[open] { animation: fade-in-up 0.3s var(--ease) both; }
.dialog-actions { display: flex; justify-content: flex-end; gap: var(--s-2); margin-top: var(--s-4); flex-wrap: wrap; }

/* ---- project workspace ------------------------------------------------------------------------- */
.page-head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2) var(--s-3); margin-bottom: var(--s-3); }
.page-head h1 { flex: 1 1 auto; }
.studio-grid {
  display: grid; gap: var(--s-4); grid-template-columns: minmax(360px, 5fr) 7fr; height: calc(100vh - 150px); min-height: 520px;
  transition: grid-template-columns var(--t-slow);
}
.studio-grid[data-layout="chat"] { grid-template-columns: 8fr minmax(320px, 4fr); }
.studio-grid[data-layout="results"] { grid-template-columns: minmax(320px, 4fr) 8fr; }
.studio-grid > * { min-height: 0; }
.right { overflow-y: auto; display: flex; flex-direction: column; gap: var(--s-3); padding: 0 2px var(--s-4); }
.studio-tabs { display: none; gap: var(--s-2); margin-bottom: var(--s-3); }
.tab[aria-selected="true"] { background: var(--primary); color: var(--primary-text); border-color: var(--primary); }

/* chat (ElevenLabs "flat" message variant) */
.chat-card { display: flex; flex-direction: column; height: 100%; padding: var(--s-4); position: relative; }
.chat-card > h3 { margin: 0 0 var(--s-2); }
.chat-log { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: var(--s-2); padding: var(--s-2) 2px; scroll-behavior: smooth; }
.bubble { white-space: pre-wrap; overflow-wrap: anywhere; animation: fade-in-up 0.3s var(--ease) both; }
.bubble.assistant { align-self: stretch; padding: var(--s-1) 0; color: var(--text); }
.bubble.user { align-self: flex-end; max-width: 80%; padding: var(--s-3) var(--s-4); border-radius: var(--r-md); background: var(--bubble-me); color: var(--bubble-me-text); }
.tool-line { display: flex; align-items: center; gap: var(--s-2); color: var(--muted); font-size: 13px; }
.tool-line .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--line); flex: none; }
.tool-line.running .dot { background: var(--accent); animation: pulse 1s ease-in-out infinite; }
.tool-line.ok .dot { background: var(--ok); }
.tool-line.bad .dot { background: var(--bad); }
.to-bottom { position: absolute; left: 50%; bottom: 128px; transform: translateX(-50%); box-shadow: var(--shadow-2); z-index: 2; }
.chips { display: flex; flex-wrap: wrap; gap: var(--s-2); padding: var(--s-2) 0; }
.chip { height: 30px; border-radius: var(--r-pill); padding: 0 12px; }
.file-pills { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.file-pill { display: inline-flex; align-items: center; gap: var(--s-1); background: var(--surface-2); border-radius: var(--r-pill); padding: 2px 4px 2px 12px; font-size: 13px; }
.pill-x { height: 24px; border: none; background: none; box-shadow: none; padding: 0 8px; color: inherit; }
.chat-box { display: grid; gap: var(--s-2); padding: var(--s-2); border: 1px solid var(--hairline); border-radius: var(--r-lg); background: var(--surface); box-shadow: var(--shadow-3); }
.chat-input { width: 100%; min-height: 44px; max-height: 160px; resize: none; border: none; box-shadow: none; padding: var(--s-2); transition: height var(--t-slow); }
.chat-input:focus { box-shadow: none; }

/* progress stepper */
.stepper { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s-2); }
.step { display: flex; gap: var(--s-3); align-items: flex-start; }
.step-mark { width: 18px; height: 18px; flex: none; margin-top: 2px; border-radius: 50%; border: 2px solid var(--line); position: relative; transition: background var(--t), border-color var(--t); }
.step.done .step-mark { background: var(--primary); border-color: var(--primary); }
.step.done .step-mark::after { content: ""; position: absolute; left: 4px; top: 1px; width: 4px; height: 8px; border: solid var(--primary-text); border-width: 0 2px 2px 0; transform: rotate(45deg); }
.step.active .step-mark { border-color: var(--accent); }
.step.active .step-mark::after { content: ""; position: absolute; inset: 3px; border-radius: 50%; background: var(--accent); animation: pulse 1.4s ease-in-out infinite; }
.step.skipped .step-mark { border-style: dashed; }
.step.todo .step-title, .step.skipped .step-title { color: var(--muted); }
.step-title { font-weight: 500; }

/* ---- the agent: orb, bar visualizer, shimmering text, timeline ------------------------------- */
.agent-head { display: flex; align-items: center; gap: var(--s-3); }
.orb {
  width: 44px; height: 44px; flex: none; border-radius: 50%;
  background: radial-gradient(circle at 32% 28%, #ffffff 0, var(--orb-a) 38%, transparent 72%),
              radial-gradient(circle at 70% 72%, var(--orb-b) 0, var(--orb-a) 65%);
  box-shadow: 0 0 24px rgba(160, 185, 209, 0.45), inset 0 0 8px rgba(255, 255, 255, 0.6);
  animation: orb-drift 8s ease-in-out infinite;
}
.orb[data-state="thinking"] { animation-duration: 3s; }
.orb[data-state="working"] { animation-duration: 1.4s; }
.orb[data-state="talking"] { animation: orb-talk 0.9s ease-in-out infinite; }
@keyframes orb-drift { 0%, 100% { transform: rotate(0deg) scale(1); } 50% { transform: rotate(180deg) scale(1.04); } }
@keyframes orb-talk { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.08); } }
.bars { display: flex; align-items: center; gap: 3px; height: 18px; margin-top: var(--s-1); }
.bars i { flex: 1; max-width: 6px; height: 30%; border-radius: var(--r-pill); background: var(--muted); opacity: 0.25; transition: height 150ms var(--ease); }
.bars[data-state="thinking"] i { animation: bar-sweep 2.25s linear infinite; animation-delay: calc(var(--i) * 150ms); }
.bars[data-state="working"] i { animation: bar-sweep 1.5s linear infinite; animation-delay: calc(var(--i) * 100ms); }
.bars[data-state="talking"] i { background: var(--text); opacity: 0.8; animation: bar-talk 0.6s ease-in-out infinite alternate; animation-delay: calc(var(--i) * 40ms); }
@keyframes bar-sweep { 0%, 6% { opacity: 1; height: 90%; background: var(--text); } 7%, 100% { opacity: 0.25; height: 30%; } }
@keyframes bar-talk { from { height: 25%; } to { height: 95%; } }
.tl-status { margin: 0; font-weight: 500; }
.shimmer {
  background: linear-gradient(90deg, var(--muted) 0%, var(--muted) 40%, var(--text) 50%, var(--muted) 60%, var(--muted) 100%);
  background-size: 250% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: shimmer 2.5s linear infinite;
}
@keyframes shimmer { 0% { background-position: 100% 0; } 80%, 100% { background-position: 0% 0; } }
.tl { list-style: none; margin: var(--s-2) 0 0; padding: 0; max-height: 240px; overflow-y: auto; }
.tl-step { position: relative; display: flex; gap: var(--s-2); align-items: baseline; padding: 3px 0 3px 22px; font-size: 13px; animation: fade-in-up 0.4s var(--ease) both; }
.tl-step::before { content: ""; position: absolute; left: 5px; top: 15px; bottom: -5px; width: 1px; background: var(--line); }
.tl-step:last-child::before { display: none; }
.tl-dot { position: absolute; left: 0; top: 6px; width: 11px; height: 11px; border-radius: 50%; border: 2px solid var(--line); background: var(--surface); }
.tl-step[data-state="active"] .tl-dot { border-color: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
.tl-step[data-state="ok"] .tl-dot { background: var(--ok); border-color: var(--ok); }
.tl-step[data-state="bad"] .tl-dot { background: var(--bad); border-color: var(--bad); }
.tl-step[data-kind="tool"] .tl-label { font-weight: 500; }
.tl-label { flex: 1; }
.tl-time { color: var(--muted); font-variant-numeric: tabular-nums; }
.tl-foot { margin: var(--s-2) 0 0; }
.agent.flash { animation: flash 0.6s var(--ease); }
@keyframes flash { 0% { box-shadow: 0 0 0 0 var(--accent-soft); } 50% { box-shadow: 0 0 0 6px var(--accent-soft); } 100% { box-shadow: 0 0 0 0 transparent; } }

/* ---- narrow screens (tablet and phone) ------------------------------------------------------- */
@media (max-width: 900px) {
  main { padding: var(--s-3); }
  .studio-tabs { display: flex; }
  .page-head .segmented { display: none; }
  .studio-grid, .studio-grid[data-layout] { grid-template-columns: 1fr; height: auto; }
  .studio-grid[data-pane="chat"] .right { display: none; }
  .studio-grid[data-pane="results"] .chat-card { display: none; }
  .chat-card { height: calc(100vh - 220px); }
  .choices { grid-template-columns: 1fr; }
}
```

### 2.2 `static/ui.js` (new file): collapse, layout, agent visuals, timeline

Loaded before `studio.js` (section 7.3). It only defines `window.UI`; it calls `h`, `clear` and `fmtNum` from `app.js` at run time. State is per browser in `localStorage` under keys starting `lg.`; every read and write is wrapped so a private window or blocked storage still works (the page then simply uses the defaults). Write it exactly:

```js
/* Lead Generator UI kit: collapsible cards, the layout switch, the agent strip and the live timeline.
   Plain JavaScript, no libraries. Loaded before studio.js; uses app.js's h, clear and fmtNum at run time.
   Card and layout choices are remembered per browser (localStorage keys start with "lg."). */
(function () {
  'use strict';

  function load(key, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (err) { return fallback; }
  }
  function save(key, value) {
    try { window.localStorage.setItem(key, value); } catch (err) { /* a private window: fine */ }
  }

  // A card that opens and closes. id: stable text such as 'p.progress'. opts: { open, action, count }.
  // Returns the <section>; put content in node.inner, open or close it with node.setOpen(bool).
  function card(id, title, opts, ...kids) {
    const o = opts || {};
    const key = 'lg.card.' + id;
    const open = load(key, o.open ? '1' : '0') === '1';
    const bodyId = 'cb-' + id.replace(/[^a-z0-9]/gi, '-');
    const count = h('span', { class: 'card-count' });
    if (o.count !== undefined && o.count !== null) count.textContent = String(o.count);
    const toggle = h('button', { type: 'button', class: 'card-toggle', 'aria-expanded': String(open), 'aria-controls': bodyId },
      h('span', { class: 'chev', 'aria-hidden': 'true' }), h('span', { class: 'card-title', text: title }), count);
    const inner = h('div', { class: 'card-inner' }, ...kids);
    const body = h('div', { class: 'card-body', id: bodyId }, inner);
    const node = h('section', { class: 'card ui-card', 'data-card': id, 'data-open': String(open) },
      h('header', { class: 'card-head' }, toggle, o.action || null), body);
    function setOpen(next) {
      node.dataset.open = String(next);
      toggle.setAttribute('aria-expanded', String(next));
      body.inert = !next;
      save(key, next ? '1' : '0');
    }
    toggle.addEventListener('click', () => setOpen(node.dataset.open !== 'true'));
    body.inert = !open;
    node.setOpen = setOpen;
    node.inner = inner;
    node.setCount = (text) => { count.textContent = text === null || text === undefined ? '' : String(text); };
    return node;
  }

  // Open or close every card inside root.
  function setAll(root, open) {
    for (const node of root.querySelectorAll('.ui-card')) node.setOpen(open);
  }

  // One button that closes every card in root, then opens them all again.
  function collapseAllButton(root) {
    const button = h('button', { type: 'button', class: 'ghost', text: 'Collapse all' });
    button.addEventListener('click', () => {
      const closing = button.textContent === 'Collapse all';
      setAll(root, !closing);
      button.textContent = closing ? 'Expand all' : 'Collapse all';
    });
    return button;
  }

  // Segmented control that sets grid.dataset.layout. choices: [[value, label], ...].
  function layoutSwitch(grid, name, choices, fallback) {
    const key = 'lg.layout.' + name;
    let current = load(key, fallback);
    if (!choices.some(([value]) => value === current)) current = fallback;
    grid.dataset.layout = current;
    const group = h('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Layout' });
    for (const [value, labelText] of choices) {
      const b = h('button', { type: 'button', role: 'radio', 'aria-checked': String(value === current), text: labelText });
      b.addEventListener('click', () => {
        grid.dataset.layout = value;
        save(key, value);
        for (const other of group.children) other.setAttribute('aria-checked', String(other === b));
      });
      group.appendChild(b);
    }
    return group;
  }

  // "12.3k" style token counts.
  function tokens(n) {
    const v = Number(n) || 0;
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e4) return (v / 1e3).toFixed(1) + 'k';
    return fmtNum(v);
  }
  function money(usd) {
    if (usd === null || usd === undefined) return '';
    return 'about $' + (usd < 0.01 && usd > 0 ? '0.01' : Number(usd).toFixed(2));
  }
  function usageLine(d) {
    const total = (d.input_tokens || 0) + (d.output_tokens || 0) + (d.cache_read_tokens || 0) + (d.cache_write_tokens || 0);
    const parts = ['This reply: ' + tokens(total) + ' tokens'];
    if (d.cost_usd !== null && d.cost_usd !== undefined) parts.push(money(d.cost_usd) + ' (estimate)');
    return parts.join(' · ');
  }

  // The agent strip and live timeline. Returns { node, start, thinking, text, tool, refreshed, usage, error, done, idle }.
  function timeline() {
    const orb = h('div', { class: 'orb', 'data-state': 'idle', 'aria-hidden': 'true' });
    const bars = h('div', { class: 'bars', 'data-state': 'idle', 'aria-hidden': 'true' });
    for (let i = 0; i < 15; i += 1) {
      const bar = h('i');
      bar.style.setProperty('--i', String(i));
      bars.appendChild(bar);
    }
    const status = h('p', { class: 'tl-status', role: 'status', text: 'Waiting for your message' });
    const list = h('ol', { class: 'tl', 'aria-label': 'What the AI is doing' });
    const foot = h('p', { class: 'tl-foot muted small' });
    const node = h('div', { class: 'agent' },
      h('div', { class: 'agent-head' }, orb, h('div', { class: 'grow' }, status, bars)), list, foot);
    const steps = {};
    let active = null; // the thinking or writing step now running (tools are tracked by id)
    let started = 0;
    let n = 0;

    function mode(state, text) {
      orb.dataset.state = state;
      bars.dataset.state = state;
      status.textContent = text;
      status.classList.toggle('shimmer', state !== 'idle');
    }
    function finish(step, outcome) {
      if (!step || step.li.dataset.state !== 'active') return;
      step.li.dataset.state = outcome;
      const secs = Math.round((Date.now() - step.at) / 1000);
      step.time.textContent = secs ? secs + ' s' : '';
    }
    function add(id, labelText, kind, state) {
      const time = h('span', { class: 'tl-time' });
      const li = h('li', { class: 'tl-step', 'data-state': state || 'active', 'data-kind': kind },
        h('span', { class: 'tl-dot', 'aria-hidden': 'true' }), h('span', { class: 'tl-label', text: labelText }), time);
      list.appendChild(li);
      list.scrollTop = list.scrollHeight;
      const step = { li, time, kind, at: Date.now() };
      steps[id] = step;
      return step;
    }
    function phase(kind, labelText) {
      if (active && active.kind === kind) return;
      finish(active, 'ok');
      n += 1;
      active = add(kind + n, labelText, kind);
    }

    return {
      node,
      start() {
        clear(list);
        clear(foot);
        started = Date.now();
        active = null;
        add('read', 'Read your message', 'note', 'ok');
        phase('think', 'Thinking');
        mode('thinking', 'Thinking...');
      },
      thinking() { phase('think', 'Thinking'); mode('thinking', 'Thinking...'); },
      text() { phase('write', 'Writing a reply'); mode('talking', 'Writing a reply...'); },
      tool(d) {
        if (d.state === 'started') {
          finish(active, 'ok');
          active = null;
          add(d.id, d.label || 'Working...', 'tool');
          mode('working', d.label || 'Working...');
        } else if (steps[d.id]) {
          finish(steps[d.id], d.ok ? 'ok' : 'bad');
          mode('thinking', 'Thinking...');
        }
      },
      refreshed() {
        node.classList.remove('flash');
        void node.offsetWidth; // restart the animation
        node.classList.add('flash');
      },
      usage(d) { foot.textContent = usageLine(d); },
      error(d) {
        finish(active, 'bad');
        active = null;
        add('error' + Date.now(), d.message || 'Stopped with a problem', 'note', 'bad');
      },
      done(d) {
        finish(active, d.ok ? 'ok' : 'bad');
        active = null;
        for (const step of Object.values(steps)) finish(step, d.ok ? 'ok' : 'bad');
        const secs = Math.max(1, Math.round((Date.now() - started) / 1000));
        const text = d.cancelled ? 'Stopped' : d.ok ? 'Done in ' + secs + ' s' : 'Stopped';
        add('done' + Date.now(), text, 'note', d.ok ? 'ok' : 'bad');
        mode('idle', d.ok ? 'Ready for your next message' : 'Stopped');
      },
      idle() { mode('idle', 'Waiting for your message'); },
    };
  }

  window.UI = { card, setAll, collapseAllButton, layoutSwitch, timeline, tokens, money, usageLine, load, save };
})();
```

---
## 3. Connectors

### 3.1 Which providers, and the facts they rest on

| Key | Segment | Module | Facts | Status |
|---|---|---|---|---|
| `tavily` | search | `providers/tavily.py` | Research 3.2 (search). **Read by the architect 2026-10-25:** `GET https://api.tavily.com/usage`, `Authorization: Bearer <key>`, returns `key.usage`, `key.limit`, `account.plan_usage`, `account.plan_limit` (no credit cost stated; it runs no search) | build |
| `apollo` | finder | `providers/apollo.py` | Research 3.1 (people match, `poll_only`, webhook result poll, credits, rate limits) | build |
| `prospeo` | finder | `providers/prospeo.py` | Research 3.1. **Read by the architect:** errors are `{"error": true, "error_code": "..."}` with HTTP 400 for `NO_MATCH`, `INVALID_DATAPOINTS`, `INSUFFICIENT_CREDITS`, `INVALID_API_KEY`, `INVALID_REQUEST`, `INTERNAL_ERROR`, and 429 for rate limit (https://prospeo.io/api-docs/enrich-person); `GET https://api.prospeo.io/account-information` with `X-KEY` is free and returns `response.remaining_credits` (https://prospeo.io/api-docs/account-information) | build |
| `getprospect` | finder | `providers/getprospect.py` | Research 3.1. **Read by the architect:** invalid key = HTTP 401 with `"name": "UNAUTHORIZED"`; out of credits = 402 with `X-Limit-Reached: true`; there is **no** documented account or balance endpoint (https://getprospect.com/api-docs/authentication, `/credits-and-limits`) | build |
| `emailable` | verify | `providers/emailable.py` | Research 3.3. **Read by the architect:** `GET https://api.emailable.com/v1/account` returns `owner_email`, `available_credits` (https://emailable.com/docs/api/account.md; credit cost not stated, it verifies nothing) | build |
| `exa` | search | none | Request shape unverified (research 5.3) | skipped: STATUS follow-up |
| `snov` | finder | none | No API on the free plan (research 3.1) | dropped |

Every new provider: official API, the person's own key, never LinkedIn itself. The LinkedIn profile address is only passed to the provider's API as data.

### 3.2 `providers/base.py` additions

```python
@dataclass(frozen=True)
class ProviderInfo:
    ...                     # existing fields unchanged
    costs: str = ""         # one plain line shown on the Connectors page ("1 credit per email found")


@dataclass(frozen=True)
class Found:
    """What a finder returned for one LinkedIn profile. Empty strings mean "not found".
    `credits` is what this call cost (an estimate when the provider does not say);
    `phone_request` is an Apollo request id whose phone numbers arrive later."""

    source: str
    email: str = ""
    phone: str = ""
    phone_kind: str = ""
    phone_request: str = ""
    credits: float = 0.0


@runtime_checkable
class Finder(Provider, Protocol):
    """A provider that looks up a work email (and optionally a phone) from a LinkedIn profile URL."""

    def find(self, linkedin_url: str, want_phone: bool = False) -> Found:
        """One lookup. Raises ProviderError; never raises for "not found" (returns an empty Found)."""
```

Add `costs=` to the existing INFOs (text exactly):

| Key | `costs` |
|---|---|
| serper | "1 credit per search" |
| brave | "About $5 per 1,000 searches after the monthly free credit" |
| ddg | "" |
| hunter | "1 credit per company pattern lookup, half a credit per email check" |
| reoon | "1 credit per checked address (unknown answers are free)" |
| zerobounce | "1 credit per checked address" |
| lemlist | "" |
| smtp | "" |

### 3.3 New provider modules (copy the shape of `reoon.py`)

All five: `INFO`, a class with `__init__(self, client: httpx.Client | None = None)`, `configured()`, `test()`, a module `_limiter`, and the request through `base.send_with_retry(send, retry_read_timeout=False)` for any call that can spend credits (never retried on a read timeout). Keys are sent in headers only, never in URLs. `base.record_usage(INFO.key, calls=1, credits=<what this call cost>)` after every answered call.

**`providers/tavily.py`**

```python
"""Tavily web search (free 1,000 credits a month, no card). See DECISIONS (V13)."""
SEARCH_URL = "https://api.tavily.com/search"
USAGE_URL = "https://api.tavily.com/usage"
ENV_KEY = "TAVILY_API_KEY"
MAX_RESULTS = 20
INFO = ProviderInfo(
    key="tavily", name="Tavily (web search)",
    purpose="Finds companies and people with a general web search",
    env_keys=[ENV_KEY], free_tier="1,000 free credits every month, no card",
    signup_url="https://app.tavily.com", costs="1 credit per search", paid=False, default=False,
)
_limiter = base.RateLimiter(5)
_SITE_RE = re.compile(r"\bsite:(\S+)", re.IGNORECASE)


class TavilyProvider:
    info = INFO

    def search(self, query: str, country_gl: str = "", num: int = 10) -> list[SearchResult]:
        """One basic search. `site:x` in the query becomes include_domains=[x] (Tavily is not
        Google). `country_gl` is ignored. shortcut: no country filter, add one if results are off."""
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        if not query.strip():
            raise ValueError("query must not be empty")
        sites = [s.strip("/").lower() for s in _SITE_RE.findall(query)]
        body: dict[str, Any] = {
            "query": _SITE_RE.sub(" ", query).strip() or query,
            "search_depth": "basic", "max_results": max(1, min(int(num), MAX_RESULTS)),
        }
        if sites:
            body["include_domains"] = sites
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        # send(): _limiter.acquire(); client.post(SEARCH_URL, json=body, headers=headers)
        # retry as Serper does (a search is charged once even if repeated? unknown: use retry_read_timeout=False)
        # error_for_status(response, "Tavily"); data = json_object(...)
        # results: data["results"] list of {title, url, content}; SearchResult(title, link=url,
        #          snippet=content, position=index starting at 1); skip items without a str url
        # record_usage(INFO.key, calls=1, credits=1)

    def test(self) -> tuple[bool, str]:
        """GET USAGE_URL (runs no search). 2xx -> (True, "Tavily key works (<limit-usage> credits
        left)." when account.plan_limit and plan_usage are numbers, else "Tavily key works.");
        a ProviderError -> (False, exc.message)."""
```

`registry.get_search_provider` order becomes Serper, **Tavily**, Brave, then DuckDuckGo (Tavily is free without a card; Brave asks for one).

**`providers/apollo.py`**

```python
"""Apollo people match by LinkedIn profile URL; phone numbers arrive later and are polled
(poll_only, no webhook: this app has no public address). See DECISIONS (V13)."""
MATCH_URL = "https://api.apollo.io/api/v1/people/match"
RESULT_URL = "https://api.apollo.io/api/v1/webhook_result/{request_id}"
ENV_KEY = "APOLLO_API_KEY"
INFO = ProviderInfo(
    key="apollo", name="Apollo",
    purpose="Finds a work email (and, if you ask, a mobile number) from a LinkedIn profile",
    env_keys=[ENV_KEY],
    free_tier="Free plan with a small monthly credit allowance (amount not published; the API "
              "may need a paid plan)",
    signup_url="https://app.apollo.io/#/settings/integrations/api",
    costs="1 credit per person found; 8 more if a mobile number is found", paid=False,
)
_limiter = base.RateLimiter(0.8)   # free plan: 50 per minute
_LOCKED = "email_not_unlocked"     # Apollo's placeholder for an email the plan cannot show


class ApolloProvider:
    def _headers(self) -> dict[str, str]:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        return {"x-api-key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"}

    def find(self, linkedin_url: str, want_phone: bool = False) -> Found:
        params = {"linkedin_url": linkedin_url, "reveal_personal_emails": "false"}
        if want_phone:
            params |= {"reveal_phone_number": "true", "poll_only": "true"}
        # POST MATCH_URL with params=params (query parameters, NO json body), headers=self._headers()
        # error_for_status(response, "Apollo") -> raise (403 also means "this key may not call
        #   this endpoint": message "Apollo refused the key for this lookup (HTTP 403). Make a key
        #   that may call People Enrichment and Webhook Result, or a master key.")
        # data = json_object(...); person = data.get("person") or {}
        # email = str(person.get("email") or "").strip().lower(); "" if it starts with _LOCKED or has no "@"
        # matched = bool(person) and person.get("match_confidence") != "none"
        # request = str(data.get("request_id") or "") if want_phone and matched else ""
        # credits = 1.0 if matched else 0.0 ; record_usage(INFO.key, 1, credits)
        # return Found("apollo", email=email, phone_request=request, credits=credits)

    def collect_phone(self, request_id: str) -> tuple[str, str, str]:
        """Poll one phone request (free). Returns (state, number, kind): state "ready" (number may be
        "" when none was found), "pending" or "gone" (unknown or older than 30 days).
        GET RESULT_URL.format(request_id=request_id) with self._headers().
        200 -> people = data["webhook_result"]["people"] (list); first phone of the first person:
               number = sanitized_number or raw_number, kind = type_cd ("" if missing);
               if data["webhook_status"] == "in_progress" -> ("pending", "", "").
        404 with "result_pending" in the body -> ("pending", "", "").
        404 (request_id_unknown), 410, 400 -> ("gone", "", "").
        Anything else -> error_for_status -> raise.
        record_usage(INFO.key, 1, 8 if number else 0)."""

    def test(self) -> tuple[bool, str]:
        """GET RESULT_URL with request_id "1" (free). 401 or 403 -> (False, "Apollo did not accept
        the key (HTTP <n>). Make a key that may call People Enrichment and Webhook Result, or a
        master key."). Any other answer (200, 400, 404, 410) -> (True, "Apollo key works.").
        A network ProviderError -> (False, exc.message)."""
```

**`providers/prospeo.py`**

```python
"""Prospeo Enrich Person by LinkedIn URL (synchronous). See DECISIONS (V13)."""
ENRICH_URL = "https://api.prospeo.io/enrich-person"
ACCOUNT_URL = "https://api.prospeo.io/account-information"
ENV_KEY = "PROSPEO_API_KEY"
INFO = ProviderInfo(
    key="prospeo", name="Prospeo",
    purpose="Finds a work email (and, if you ask, a mobile number) from a LinkedIn profile",
    env_keys=[ENV_KEY], free_tier="Free credits every month (amount set by Prospeo)",
    signup_url="https://app.prospeo.io/signup",
    costs="1 credit per email found; 10 per mobile number found (email included)", paid=False,
)
_limiter = base.RateLimiter(4)
_CODES = {"INSUFFICIENT_CREDITS": "quota", "INVALID_API_KEY": "auth"}


def _body_error(data: dict) -> ProviderError | None:
    """None for NO_MATCH (not found is not an error) and for success."""
    if data.get("error") is not True:
        return None
    code = str(data.get("error_code") or "")
    if code in ("NO_MATCH", "INVALID_DATAPOINTS"):
        return None
    kind = _CODES.get(code, "http")
    return ProviderError(kind, f"Prospeo said {code or 'error'}.")


class ProspeoProvider:
    def find(self, linkedin_url: str, want_phone: bool = False) -> Found:
        body = {"data": {"linkedin_url": linkedin_url}, "only_verified_email": False,
                "enrich_mobile": bool(want_phone)}
        # POST ENRICH_URL json=body headers {"X-KEY": key, "Content-Type": "application/json"}
        # status 429 -> ProviderError("rate_limited", ...); status 400 or 2xx -> read JSON:
        #   err = _body_error(data); raise err if err
        #   if data.get("error") is True (NO_MATCH...) -> record_usage(key, 1, 0); return Found("prospeo")
        # any other non-2xx -> error_for_status -> raise
        # person = data.get("person") or {}; e = person.get("email") or {}; m = person.get("mobile") or {}
        # email = e.get("email") if e.get("revealed") else ""     (lower-cased, must contain "@")
        # phone = (m.get("mobile_international") or m.get("mobile")) if m.get("revealed") else ""
        # credits = 0 if data.get("free_enrichment") else (10 if phone else (1 if email else 0))
        # record_usage(key, 1, credits); return Found("prospeo", email, phone, "mobile" if phone else "", "", credits)

    def test(self) -> tuple[bool, str]:
        """GET ACCOUNT_URL (free). error False -> (True, "Prospeo key works (<remaining_credits>
        credits left)."); INVALID_API_KEY or 401/403 -> (False, "Prospeo did not accept the key.")."""
```

**`providers/getprospect.py`**

```python
"""GetProspect people enrich by LinkedIn URL. See DECISIONS (V13)."""
ENRICH_URL = "https://api.getprospect.com/v2/people/enrich"
ENV_KEY = "GETPROSPECT_API_KEY"
READ_TIMEOUT_S = 45.0   # GetProspect may take over a minute; we stop at 45 s (calls must end < 60 s)
TIMEOUT = httpx.Timeout(READ_TIMEOUT_S, connect=10.0)
INFO = ProviderInfo(
    key="getprospect", name="GetProspect",
    purpose="Finds a work email (and, on paid plans, a phone number) from a LinkedIn profile",
    env_keys=[ENV_KEY], free_tier="50 emails and 100 checks a month, no card",
    signup_url="https://getprospect.com/signup",
    costs="1 email credit per new email found; 1 phone credit per phone (not on the free plan)",
    paid=False,
)
_limiter = base.RateLimiter(2)
_CHECK_URL = "https://www.linkedin.com/in/leadgen-key-check-0"   # invented profile: never matches


class GetProspectProvider:
    def _post(self, body: dict) -> httpx.Response:
        # headers {"x-api-key": key, "Content-Type": "application/json"}; timeout=TIMEOUT;
        # send_with_retry(send, retry_read_timeout=False)
        ...

    def find(self, linkedin_url: str, want_phone: bool = False) -> Found:
        body = {"data": {"linkedin_url": linkedin_url}, "enrich_email": True,
                "enrich_phone": bool(want_phone)}
        # 408 -> ProviderError("http", "GetProspect is still searching. Try again later (not charged).")
        # 402 -> ProviderError("quota", "GetProspect has no credits left.")
        # 401 -> ProviderError("auth", "GetProspect did not accept the key.")
        # other non-2xx -> error_for_status
        # data = json_object(...)
        # if data.get("success") is False: names = [e.get("name") for e in data.get("errors") or []]
        #     "NOT_FOUND" in names -> record_usage(key, 1, 0); return Found("getprospect")
        #     else -> ProviderError("http", "GetProspect reported a problem.")
        # d = data.get("data") or {}; e = d.get("email") or {}; m = d.get("mobile_phone") or {}
        # if e.get("status") == "searching" -> ProviderError("http", "GetProspect is still searching. Try again later.")
        # email = e.get("address") if e.get("address") and e.get("status") not in ("invalid", "not_found") else ""
        # phone = m.get("number") if m.get("status") == "found" and m.get("number") else ""
        # credits = (1 if email else 0) + (1 if phone else 0)   (estimate: a re-reveal is free)
        # record_usage(key, 1, credits); return Found("getprospect", email, phone, "mobile" if phone else "", "", credits)

    def test(self) -> tuple[bool, str]:
        """There is no free balance endpoint, so: POST ENRICH_URL with
        {"data": {"linkedin_url": _CHECK_URL}, "enrich_email": False, "enrich_phone": False}
        (matching is free and nothing is revealed). 401 -> (False, "GetProspect did not accept the
        key."); 2xx or 402 -> (True, "GetProspect key works."); other -> (False, message)."""
```

**`providers/emailable.py`**

```python
"""Emailable email verification (250 free credits at sign-up). See DECISIONS (V13)."""
VERIFY_URL = "https://api.emailable.com/v1/verify"
ACCOUNT_URL = "https://api.emailable.com/v1/account"
ENV_KEY = "EMAILABLE_API_KEY"
INFO = ProviderInfo(
    key="emailable", name="Emailable",
    purpose="Checks that an email address exists before sending",
    env_keys=[ENV_KEY], free_tier="250 free checks when you sign up, no card",
    signup_url="https://app.emailable.com/signup",
    costs="1 credit per checked address", paid=False,
)
_limiter = base.RateLimiter(10)
_STATES = {"deliverable": "verified", "undeliverable": "invalid", "risky": "risky", "unknown": "unknown"}


class EmailableProvider:
    def verify(self, email: str) -> Verdict:
        """GET VERIFY_URL params {"email": email, "timeout": 10}, header Authorization: Bearer <key>,
        httpx timeout 20 s, retry_read_timeout=False.
        HTTP 249 -> Verdict("unknown", "pending", "emailable") and record_usage(key, 1, 0).
        Else error_for_status; data = json_object; raw = str(data.get("state") or "").lower();
        status = _STATES.get(raw, "unknown"); catch_all = data.get("accept_all") is True;
        if catch_all and status != "invalid": status = "risky";
        record_usage(key, 1, 0 if status == "unknown" else 1); return Verdict(status, raw, "emailable", catch_all)."""

    def test(self) -> tuple[bool, str]:
        """GET ACCOUNT_URL with the Bearer header. 2xx -> (True, "Emailable key works
        (<available_credits> credits left)."); 401/403 -> (False, "Emailable did not accept the key.")."""
```

`emails._default_verifiers()` becomes Reoon, ZeroBounce, **Emailable**, Hunter (Hunter last, it costs half a Hunter credit).

### 3.4 `providers/registry.py` changes

```python
from leadgen.providers import (apollo, brave, ddg, emailable, fake, getprospect, hunter, lemlist,
                               prospeo, reoon, serper, tavily, zerobounce)

ALL_PROVIDERS = {info.key: info for info in (
    serper.INFO, brave.INFO, ddg.INFO, hunter.INFO, reoon.INFO, zerobounce.INFO, lemlist.INFO,
    smtp_imap.INFO, tavily.INFO, apollo.INFO, prospeo.INFO, getprospect.INFO, emailable.INFO,
)}   # the eight old ones keep their order; new ones are appended
_FACTORIES |= {"tavily": tavily.TavilyProvider, "apollo": apollo.ApolloProvider,
               "prospeo": prospeo.ProspeoProvider, "getprospect": getprospect.GetProspectProvider,
               "emailable": emailable.EmailableProvider}

# The Connectors page groups (key, label, provider keys in display order).
SEGMENTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("search", "Search", ("serper", "tavily", "brave", "ddg")),
    ("finder", "Email and phone from a LinkedIn profile", ("apollo", "prospeo", "getprospect", "hunter")),
    ("verify", "Email checking", ("reoon", "zerobounce", "emailable")),
    ("sending", "Sending", ("smtp", "lemlist")),
)
FINDER_ORDER = ("apollo", "prospeo", "getprospect")


def finders() -> list[Any]:
    """Configured LinkedIn-profile finders in FINDER_ORDER (the first that finds an email wins)."""
    out = []
    for key in FINDER_ORDER:
        provider = get_provider(key)
        if provider is not None and provider.configured():
            out.append(provider)
    return out
```

`get_search_provider`: `for provider in (serper.SerperProvider(), tavily.TavilyProvider(), brave.BraveProvider()):`.

`core/secrets.py` `KNOWN_KEYS` gains, after `"LEMLIST_API_KEY"`: `"TAVILY_API_KEY", "APOLLO_API_KEY", "PROSPEO_API_KEY", "GETPROSPECT_API_KEY", "EMAILABLE_API_KEY"`.

`services/status.py`:
- `list_providers()` items gain `"costs": info.costs`.
- `_FREE_ALLOWANCE` gains `"tavily": (1000, "month")`, `"getprospect": (50, "month")`, `"emailable": (250, "all")`.
- New `def segments() -> list[dict[str, Any]]`: `[{"key": k, "label": label, "providers": list(keys)} for k, label, keys in registry.SEGMENTS]`.

`dashboard/app.py` `GET /api/providers` replies `{"ok": true, "providers": [...], "segments": status_svc.segments()}`.

### 3.5 How finders slot into the email pipeline (`services/emails.py`)

Flow per confirmed person in `find_emails` (new parts in bold):

1. Skip rules as today (`already_has_email`, `no_company_domain`, `no_usable_name`).
2. **If `use_finders` and the person has a LinkedIn URL and at least one finder is configured: ask the finders in `FINDER_ORDER` until one returns an email** (budget kind `finder`, gate before each call with that provider's maximum cost, charge after with `Found.credits`). Phones from the answer are stored as found, not checked; an Apollo phone request is stored as waiting.
3. **If a finder returned an email: verify it with the same chain as today (`verify(...)`), and store it with `method="finder:<key>"`, `pattern=""`. If the verdict is not `invalid`, the person is done.** An email from a finder is never stored as `verified` without a verifier saying so; with no verifier it stays `guess` (not exported, not sent).
4. Otherwise the existing pattern path (Hunter, website, default pattern, verify).

Code to add (exact names):

```python
from leadgen.providers.base import Found

# Most a finder can charge for one person: (email only, with phone). Used for the gate and the estimate.
FINDER_MAX_CREDITS: dict[str, tuple[float, float]] = {
    "apollo": (1, 9), "prospeo": (1, 10), "getprospect": (1, 2),
}
PHONE_POLLS_PER_CALL = 10
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-.]{5,28}$")


def _store_phone(person_id: int, found: Found) -> None:
    """Keep a phone from a finder (status found_not_verified) or Apollo's waiting request."""
    with db.session() as conn:
        if found.phone and _PHONE_RE.match(found.phone.strip()):
            db.upsert(conn, "phones", ("person_id", "number"), person_id=person_id,
                      number=found.phone.strip(), kind=found.phone_kind, source=found.source,
                      status="found_not_verified", request_id="")
        elif found.phone_request:
            db.upsert(conn, "phones", ("person_id", "number"), person_id=person_id, number="",
                      kind="", source=found.source, status="requested",
                      request_id=found.phone_request)


def _find_by_profile(
    camp: Mapping[str, Any], row: Mapping[str, Any], finders: Sequence[Any], confirm: bool,
    run: _Run, want_phone: bool,
) -> tuple[str, str] | dict[str, Any]:
    """(email, finder key) of the first finder that has one (("", "") when none), or a budget
    reply (the run must stop)."""
    for finder in finders:
        key = finder.info.key
        if key in run.dead:
            continue
        most = FINDER_MAX_CREDITS.get(key, (1, 1))[1 if want_phone else 0]
        blocked = _budget_gate(camp["id"], camp["budget"], "finder", most, confirm)
        if blocked is not None:
            return blocked
        try:
            found = finder.find(row["linkedin_url"], want_phone)
        except ProviderError as exc:
            run.failed(key, exc)
            continue
        if found.credits:
            budget.charge(camp["id"], "finder", found.credits)
            run.spent["finder"] += found.credits
        _store_phone(row["id"], found)
        if found.email and "@" in found.email:
            return found.email.strip().lower(), key
    return "", ""


def poll_phone_requests(campaign_id: int, limit: int = PHONE_POLLS_PER_CALL) -> dict[str, int]:
    """Collect waiting Apollo phone numbers of the campaign (free). Returns {"ready", "waiting", "gone"}."""
    # rows: SELECT ph.id, ph.person_id, ph.request_id FROM phones ph JOIN people p ON p.id = ph.person_id
    #       WHERE p.campaign_id = ? AND ph.status = 'requested' AND ph.source = 'apollo' ORDER BY ph.id LIMIT ?
    # nothing to do or Apollo not configured -> counts with waiting = len(rows)
    # for each: state, number, kind = ApolloProvider().collect_phone(request_id) (ProviderError: stop the loop)
    #   ready + number: UPDATE phones SET number=?, kind=?, status='found_not_verified', request_id='' WHERE id=?
    #                   (if that number already exists for the person: DELETE the requested row instead);
    #                   budget.charge(campaign_id, "finder", 8)
    #   ready without number or gone: UPDATE phones SET status='failed', request_id='' WHERE id=?
    #   pending: leave it
```

Changes to existing functions (keep everything else):

- `_people(...)`: select `p.linkedin_url` too.
- `_process_person(..., can_defer=False, finders=(), want_phone=False)`: before the MX/pattern step:

```python
    if finders and row.get("linkedin_url"):
        hit = _find_by_profile(camp, row, finders, confirm, run, want_phone)
        if isinstance(hit, dict):
            return hit
        email, finder_key = hit
        if email:
            verdict = verify(
                email, mx_type(clean_domain(email)), verifiers=chain, campaign_id=camp["id"],
                budget_map=camp["budget"], confirm=confirm, run=run,
            )  # fmt: skip
            if verdict.blocked is not None:
                return verdict.blocked
            if verdict.status != "invalid":
                # shortcut: a finder email left as a guess is looked up again on an explicit re-run
                # (Prospeo and GetProspect do not charge twice; Apollo may). Re-verify it instead if that bites.
                result = store_email(row["id"], email, verdict.status,
                                     method=f"finder:{finder_key}", verifier=verdict.verifier)
                return verdict.status if result == "stored" else result
        # an invalid finder email, or none: fall through to the pattern path below
```

  `store_email` already refuses role and do-not-contact addresses, and replaces an earlier `guess`/`unknown` row.

- `find_emails(..., use_finders: bool = True, want_phone: bool = False, finders=None)`: `finders` lets tests pass fakes; default `registry.finders() if use_finders else []`. At the very start (after `_check_channel`): `phones = poll_phone_requests(campaign_id)`. Pass `finders=chosen, want_phone=want_phone` to `_process_person`. Reply gains `"phones": phones` and `"credits_used"` naturally includes `finder`. `want_phone` without any finder: reply note "No finder key is set, so no phone numbers were looked up."
- `_estimate(campaign_id, ids, has_hunter, any_verifier, finder_key="", want_phone=False)`: `with_profile = [r for r in todo if r.get("linkedin_url")]`; `finder_credits = len(with_profile) * FINDER_MAX_CREDITS[finder_key][1 if want_phone else 0]` when `finder_key` else 0. Reply gains `"finder": finder_key or None`, `"finder_credits": finder_credits` ("at most"), `"phones_wanted": want_phone`, `"budget_left"["finder"]`, and `needs_confirmation = hunter_credits + verify_credits + finder_credits > threshold`. The `needs_confirmation` message in `find_emails` becomes: f"This needs at most {credits:g} credits (finding and checking emails). Ask the user, then repeat the call with confirm=true." where `credits` includes `finder_credits`.
- `estimate_email_search(campaign_id, person_ids=None, use_finders=True, want_phone=False)`: passes the first configured finder's key.

Budget: `services/budget.py` `DEFAULT_BUDGET` gains `"finder": 50`; `core/config.py` `Budgets` gains `finder: int = 50`; `preferences.BUDGET_FIELDS` gains `"finder"`; the old Settings page (`pageSettings` in `app.js`) gains the field `text('s-b-finder', 'Email and phone lookups from LinkedIn profiles', res.budgets.finder, 'number')` and sends `finder` in `budgets`.

MCP (`mcp_server.py`): `estimate_email_search` and `find_emails` get two parameters:

```python
        use_finders: Annotated[bool, Field(description="Look up emails from LinkedIn profiles first "
                               "with Apollo, Prospeo or GetProspect when a key is set.")] = True,
        want_phone: Annotated[bool, Field(description="Also look up phone numbers (much more "
                              "expensive). Only when the user asked for phone numbers.")] = False,
```

and the `find_emails` description adds: "With a finder key (Apollo, Prospeo, GetProspect) each person's LinkedIn profile is looked up first; every email found is still verified before it counts. Phone numbers are stored as found, not verified, and Apollo phones arrive on a later call."

### 3.6 Phones on the page

`browse.studio_results` adds per lead `"phone"` and `"phone_status"`: one query `SELECT person_id, number, status FROM phones WHERE person_id IN (...)`; prefer a `found_not_verified` row (`phone` = number, `phone_status` = "found"), else a `requested` row (`phone` = "", `phone_status` = "waiting"), else `""`/`""`. The Ready contacts table adds the column **Phone (found, not checked)**: the number, or "Requested, waiting" for `waiting`, or empty. The View dialog shows "Phone: <number> (found, not checked)" when there is one. Phones are not exported in v1.3 (STATUS follow-up).

### 3.7 Spend gate (confirm before spend)

Unchanged mechanism, now covering finders: the AI calls `estimate_email_search`, says the cost in plain words ("Looking up 40 people on Apollo can use up to 40 credits; checking them uses up to 40 Reoon checks."), waits for the chip **Yes, go ahead**, and only then passes `confirm=true`. Without `confirm`, a run above `confirm_threshold_credits` returns `needs_confirmation`, and the per-campaign `finder` budget stops it with `budget_reached`. Phone numbers are only looked up when the person asked for them (studio guide, 5.4).

### 3.8 Mocked tests (respx, invented people)

`tests/test_connectors.py` (new): for each provider at least:
- a found answer (Apollo person with email `aria.tan@northwind.example`, `match_confidence` "high", `request_id` 42 when phone wanted; Prospeo `person.email.revealed` true; GetProspect `data.email.address`), checking the request: method, URL, header name (key never in the URL: `"key" not in str(request.url)` and the key value not in the URL), body or params (`poll_only=true` and no `webhook_url` for Apollo with phone);
- a not-found answer (Apollo `{"person": null}`; Prospeo 400 `NO_MATCH`; GetProspect 200 `success: false`, `errors: [{"name": "NOT_FOUND"}]`) gives an empty `Found` and 0 credits;
- key errors (401/403 → `auth`, Prospeo `INSUFFICIENT_CREDITS` → `quota`, GetProspect 402 → `quota`, 408 → `http`);
- `test()` uses the free call (assert the URL) and returns ok on 200;
- Apollo `collect_phone`: 200 ready with `sanitized_number` "+6560000000"; 404 `result_pending` → pending; 410 → gone;
- Emailable: deliverable → verified, `accept_all` → risky with `catch_all`, 249 → unknown with 0 credits;
- Tavily: `site:linkedin.com/in Head of Learning` sends `include_domains: ["linkedin.com/in"]` and a query without `site:`; results map to `SearchResult`.

`tests/test_registry.py`: `test_all_providers_listed` expects the 13 keys in the order of 3.4; a new test checks every provider key appears in exactly one `SEGMENTS` group and `finders()` returns only configured ones in order; `test_search_provider_fallback_order` adds: with only `TAVILY_API_KEY` set, Tavily is chosen; with Serper and Tavily, Serper.

---
## 4. Sign-off block

Every email and LinkedIn message (after they accept) ends with:

```text
Kind regards,
Priya Nair
Head of Partnerships
Bright Harbour Training
```

The closing line ("Kind regards,", "Best regards,", "Thanks,") is the AI's choice; the last lines are the project's sender identity, one per line, in this order: name, job title, company. Lines that are empty in the project are left out. LinkedIn connection notes (200 characters) end with the first name only.

### 4.1 Data and the "Your sign-off" card

- `projects` gains `sender_title` and `sender_company` (migration v6, section 7.1). `projects._FIELDS` adds both; `_shape` returns both; `update_project` validates both with the existing `_sender_name` rules (text, whitespace collapsed, at most 120 characters, may be empty; the error text says "The job title ..." / "The company name ..." instead of "The sign-off name ...": add a `label` parameter to `_sender_name(value, label="The sign-off name")`).
- `dashboard/app.py` `ProjectChangeBody` adds `sender_title: str | None = Field(default=None, max_length=120)` and `sender_company: str | None = Field(default=None, max_length=120)`. `GET /api/projects/{id}/studio` returns `project` with `id, name, sender_name, sender_title, sender_company, cooldown_days`.
- MCP `update_project` adds `sender_title` ("The sender's job title for the sign-off. An empty string clears it.") and `sender_company` ("The sender's company for the sign-off. An empty string clears it."), passed through like `sender_name`; its description mentions them.
- Card **Your sign-off** (`p.signoff`): text "Every email and LinkedIn message ends with these lines." Fields **Your name** (`so-name`), **Your job title** (`so-title`), **Your company** (`so-company`), each `maxlength 120`. Under them a live preview `pre.msg` that shows "Kind regards," and the filled lines (updated on every keystroke). Button **Save** (primary) → `PATCH /api/projects/<id>` with `sender_name`, `sender_title`, `sender_company`; on success toast "Saved. New messages will end with this." and the header chip updates. Small text: "Messages already written keep their old ending. Ask the AI to rewrite them if you want."

### 4.2 Drafting (`services/drafting.py`)

Replace `_project_sender_name` with:

```python
def sender_identity(camp: Mapping[str, Any]) -> dict[str, str]:
    """The project's sign-off: name, title and company ("" each when unset or no project)."""
    if camp.get("project_id") is None:
        return {"name": "", "title": "", "company": ""}
    project = projects.get_project(camp["project_id"])
    return {"name": str(project.get("sender_name") or ""),
            "title": str(project.get("sender_title") or ""),
            "company": str(project.get("sender_company") or "")}


def sign_off_lines(sender: Mapping[str, Any]) -> list[str]:
    """The sign-off lines in order (name, title, company), empty ones left out."""
    return [normalize_whitespace(str(sender.get(k) or "")) for k in ("name", "title", "company")
            if normalize_whitespace(str(sender.get(k) or ""))]
```

In `get_drafting_batch`:

```python
    sender = sender_identity(camp)
    offer = _offer_payload(profile)
    if sender["name"]:
        offer["sender"] = {**(offer.get("sender") or {}), "name": sender["name"]}
        if sender["company"]:
            offer["sender"]["company"] = sender["company"]
    shown = sender if sender["name"] else {
        "name": str((offer.get("sender") or {}).get("name") or ""), "title": "", "company": ""}
    ...
    reply["sender_name"] = shown["name"]                      # kept for older callers
    reply["sign_off"] = {**shown, "block": "\n".join(sign_off_lines(shown)),
                         "required": bool(sender["name"])}
```

`_SaveContext` gains `sender: dict[str, str]`; `save_context` fills it with `sender_identity(camp)`; `_save_one` passes `sender=ctx.sender` to `check_message`.

### 4.3 The check: M17 (`services/qa_messages.py`)

- `MESSAGE_SEVERITY["M17"] = "block"`, `MESSAGE_TITLES["M17"] = "Sign-off block missing or wrong"`. Module docstring: "M01-M17".
- `check_message(..., *, companies=None, sender: Mapping[str, Any] | None = None)`.
- Only when `sender` and `sender.get("name")` and the channel is not `linkedin_invite`:

```python
def _check_sign_off(channel: str, body: str, sender: Mapping[str, Any] | None) -> list[Issue]:
    if not sender or not str(sender.get("name") or "").strip() or channel == "linkedin_invite":
        return []
    want = drafting.sign_off_lines(sender)
    lines = [normalize_whitespace(ln) for ln in body.splitlines() if ln.strip()]
    if [ln.casefold() for ln in lines[-len(want):]] == [w.casefold() for w in want]:
        return []
    return [_issue("M17", "End the message with the sign-off, each part on its own line: "
                   + " / ".join(want) + ".")]


def _without_sign_off(body: str, sender: Mapping[str, Any] | None) -> str:
    """The body without the trailing sign-off lines (so they do not trip M07, M10 or M14)."""
    want = drafting.sign_off_lines(sender) if sender else []
    lines = body.rstrip().splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    tail = [normalize_whitespace(ln).casefold() for ln in lines[-len(want):]] if want else []
    if want and tail == [w.casefold() for w in want]:
        lines = lines[: -len(want)]
    return "\n".join(lines)
```

- In `check_message`: `main = _without_sign_off(body, sender)`; use `main` instead of `body` for `_check_ending(main)`, `_check_titles(main, person, known)` and inside the text given to `_check_brands` (`text_main = f"{subject}\n{main}" if subject else main`). Everything else keeps using the full body (the length limit counts the sign-off, because it is sent). Append `issues += _check_sign_off(channel, body, sender)` after `_check_tone`.
- `review.evaluate` and `review._record_pass` pass `sender=drafting.sender_identity(camp)` (compute once per call in `evaluate`).
- `review.py` error text `(L01-L25, M01-M16)` becomes `(L01-L25, M01-M17)`; the MCP `run_qa` description says M01-M17.

Old projects without a sign-off name and old tests are unaffected (no `sender` name, no M17). Changing the sign-off later makes earlier drafts fail M17 on the next `run_qa`, so the AI rewrites them; the card says so (4.1).

### 4.4 Guides

`guides/messaging_style.md`, replace the paragraph that starts "Sign off with the sender's first name only" with:

```markdown
End every email and LinkedIn message with the batch's `sign_off`: a short closing line such as
"Kind regards," and then `sign_off.block` exactly as given, each part on its own line (name, job
title, company). When `sign_off.required` is true the tool checks this (M17) and fails a draft
without it. LinkedIn connection notes (`linkedin_invite`) end with the first name only. The good
examples below end with a first name because their batch had no job title or company. Do not add
a footer, postal address, unsubscribe line or links to the sender's website: the tool adds the
required footer when an email is sent.
```

In section 8 ("Fails the draft (block)"), add "; the sign-off block missing or changed (when `sign_off.required`)". The six example blocks stay as they are (their test checks them without a sign-off).

`guides/studio.md`, step 2 of "What you ask" becomes:

```markdown
2. The sign-off, if sender_name in the Studio context is "(not set yet)". Ask: "How should your messages be signed? Your name, your job title and your company, for example: Priya Nair, Head of Partnerships, Bright Harbour Training." Save it with update_project (project id, sender_name, sender_title, sender_company). Never ask again once it is set; the person can change it in the "Your sign-off" card.
```

`chat.build_prompt` adds two lines after `sender_name`: `f"sender_title: {project.get('sender_title') or '(not set)'}"` and `f"sender_company: {project.get('sender_company') or '(not set)'}"`.

---

## 5. Message templates

### 5.1 Model

- **Built-in templates**: 9, by receiver level (Top leaders / Directors and heads / Managers) × depth (short / standard / detailed). They live in code (`services/templates.py`, `BUILTIN`), keyed `csuite-short` ... `manager-detailed`.
- **Custom templates**: per project, in table `message_templates` (section 7.1), keyed `custom-<id>`, at most 30 per project.
- **Choice per search**: campaign option `template_key` (default `auto-standard`). Values: `auto-short`, `auto-standard`, `auto-detailed` (pick the level from each person's seniority), a built-in key, or `custom-<id>` of the same project.
- Level from seniority: `c_suite` → `csuite`; `vp`, `director` → `director`; `manager`, `individual`, empty → `manager`.
- Channels: templates apply to `email` (subject and body) and `linkedin_message` (body only, within 700 characters; a `detailed` template is written at `standard` length there). `linkedin_invite` ignores templates.
- Placeholders are written `{name}`. The AI replaces every one; a placeholder left in a draft fails the existing check M03, so nothing unfilled can be saved.

### 5.2 Placeholders (exact; the editor offers them as insert buttons)

| Placeholder | Meaning given to the AI |
|---|---|
| `{first_name}` | The person's greeting name, exactly as in the batch (greeting_name). |
| `{company}` | Their company's name. |
| `{their_title}` | Their job title as in the batch (title). |
| `{topic}` | Two to four words naming what you help with, for the subject line. |
| `{evidence_line}` | One specific sentence about their role or company, using only what we found about them. |
| `{offer_line}` | One sentence on the single service that fits this person best. |
| `{outcome_line}` | The result that service brings, as the offer is allowed to claim it. No new numbers. |
| `{proof_line}` | One proof point from the offer, as written. Leave the sentence out if there is none. |
| `{sender_line}` | One sentence on who you are: your name or company and what the company does. |
| `{cta}` | One soft question that is easy to answer, from the offer's suggested next steps when one fits. |

(The meanings are shown to the person as button tips, so they avoid internal words; the guide section 1a tells the AI which batch fields feed each one.)
| `{sign_off}` | Your sign-off block: name, job title and company, each on its own line. |

### 5.3 The nine built-in templates (exact text for `BUILTIN`)

Word targets are for the body before the sign-off: short at most 70 words, standard at most 110, detailed at most 145 (the email limit is 160 including the sign-off). Every template ends with "Kind regards," and `{sign_off}`. Plain-text lines in a template are suggestions: the AI keeps their meaning and words them for each person, so no two messages read the same.

**`csuite-short`** · name "Top leaders, short" · use case "First note to a CEO, founder or managing director: four lines, the business result first." · notes "Lead with the business result. No features, no flattery, peer tone. At most 70 words before the sign-off."
Subject: `{topic} at {company}`
```text
Hi {first_name},

{evidence_line}

{offer_line} {outcome_line}

{cta}

Kind regards,
{sign_off}
```

**`csuite-standard`** · name "Top leaders, standard" · use case "A top leader who needs a little context: who you are, one result and one proof point." · notes "Business result first, then who you are in one sentence. One proof point at most. At most 110 words before the sign-off."
Subject: `{company} and {topic}`
```text
Hi {first_name},

{evidence_line}

{sender_line} {offer_line}

{outcome_line} {proof_line}

{cta}

Kind regards,
{sign_off}
```

**`csuite-detailed`** · name "Top leaders, detailed" · use case "A top leader in a long decision, or a second note after no reply: the business case in short paragraphs." · notes "Respect their time: short paragraphs, the result and the proof, then one easy question. At most 145 words before the sign-off."
Subject: `A thought on {topic} for {company}`
```text
Hi {first_name},

{evidence_line}

I will keep this brief, as {topic} is one of many things on your desk.

{sender_line} {offer_line}

{outcome_line}

{proof_line}

{cta}

Kind regards,
{sign_off}
```

**`director-short`** · name "Directors and heads, short" · use case "A head of a function: their team, the one service that fits, one easy question." · notes "Speak to their team's goals. One service only. At most 70 words before the sign-off."
Subject: `{topic} for your team at {company}`
```text
Hi {first_name},

{evidence_line}

{offer_line}

{cta}

Kind regards,
{sign_off}
```

**`director-standard`** · name "Directors and heads, standard" · use case "The usual choice for directors and heads: what you do for teams like theirs, one proof point, a soft next step." · notes "Team results over features. One proof point. At most 110 words before the sign-off."
Subject: `{topic} for {company}'s team`
```text
Hi {first_name},

{evidence_line}

{sender_line} {offer_line}

{proof_line} {outcome_line}

{cta}

Kind regards,
{sign_off}
```

**`director-detailed`** · name "Directors and heads, detailed" · use case "A head who will pass it to their team or their boss: how it works, the result and the proof, ready to forward." · notes "Write so it can be forwarded: plain, complete, no inside references. At most 145 words before the sign-off."
Subject: `A practical look at {topic}`
```text
Hi {first_name},

{evidence_line}

{sender_line} {offer_line}

How it works for a team like yours: {outcome_line}

{proof_line}

If it helps to have something to share with your team, {cta}

Kind regards,
{sign_off}
```

**`manager-short`** · name "Managers, short" · use case "A manager who runs the work day to day: practical, short, no sales words." · notes "Practical and friendly. Say what changes in their week. At most 70 words before the sign-off."
Subject: `Quick question about {topic}`
```text
Hi {first_name},

{evidence_line}

{offer_line}

{cta}

Kind regards,
{sign_off}
```

**`manager-standard`** · name "Managers, standard" · use case "The usual choice for managers: what the service changes in their week, one proof point, an easy next step." · notes "Concrete and practical. One proof point. At most 110 words before the sign-off."
Subject: `{topic} for your team`
```text
Hi {first_name},

{evidence_line}

{sender_line} {offer_line}

{outcome_line} {proof_line}

{cta}

Kind regards,
{sign_off}
```

**`manager-detailed`** · name "Managers, detailed" · use case "A manager who will need to make the case to their boss: the practical detail and a proof point they can pass on." · notes "Give them what they need to argue for it: how it works day to day and one proof point. At most 145 words before the sign-off."
Subject: `{topic}: a practical option`
```text
Hi {first_name},

{evidence_line}

{sender_line} {offer_line}

What it looks like day to day: {outcome_line}

{proof_line}

If it would help you make the case internally, {cta}

Kind regards,
{sign_off}
```

### 5.4 `services/templates.py` (new) and the "Message templates" card

```python
"""Message templates: nine built-in ones (receiver level x depth), the project's own ones, and the
choice each search makes. The AI writes from the template the drafting batch hands it.
Raises `TemplateError`."""

LEVELS = ("csuite", "director", "manager")
DEPTHS = ("short", "standard", "detailed")
LEVEL_LABELS = {"csuite": "Top leaders", "director": "Directors and heads", "manager": "Managers",
                "any": "Anyone"}
DEPTH_LABELS = {"short": "short", "standard": "standard", "detailed": "detailed"}
LEVEL_OF_SENIORITY = {"c_suite": "csuite", "vp": "director", "director": "director",
                      "manager": "manager", "individual": "manager"}
DEFAULT_KEY = "auto-standard"
MAX_CUSTOM = 30
MAX_NAME, MAX_USE_CASE, MAX_SUBJECT, MAX_BODY = 80, 200, 120, 3000
PLACEHOLDERS: dict[str, str] = {...}            # section 5.2, exact meanings
BUILTIN: dict[str, dict[str, str]] = {...}      # section 5.3: key -> {name, level, depth, use_case, notes, subject, body}
KEY_RE = re.compile(r"^(?:auto-(?:short|standard|detailed)|(?:csuite|director|manager)-"
                    r"(?:short|standard|detailed)|custom-[1-9][0-9]{0,8})$")
_PH_RE = re.compile(r"\{([A-Za-z_]+)\}")
CLOSING = "\n\nKind regards,\n{sign_off}"


class TemplateError(Exception):  # code, message, hint, as_response() like ProjectError


def _shape(row) -> dict:          # custom row -> {key: "custom-<id>", id, name, level, depth,
                                  #   use_case, notes: "", subject, body, custom: True}
def list_templates(project_id: int) -> dict[str, Any]:
    """{"ok", "builtin": [BUILTIN items with key and custom False], "custom": [...],
    "placeholders": PLACEHOLDERS, "choices": [{"key", "label"}]} (raises ProjectError not_found)."""
def choices(project_id: int) -> list[dict[str, str]]:
    """In this order: auto-standard "Match each person's level (standard)", auto-short "Match each
    person's level (short)", auto-detailed "Match each person's level (detailed)", then each
    built-in "<LEVEL_LABELS> , <depth>" e.g. "Top leaders, short", then each custom "Yours: <name>"."""
def save_template(project_id: int, data: Mapping[str, Any], template_id: int | None = None) -> dict:
    """Validate and insert (template_id None) or update one custom template of the project.
    name 1..80 chars, use_case 0..200, subject 0..120, body 1..3000 (all text, stripped), level in
    LEVELS + ("any",), depth in DEPTHS. Every {placeholder} in subject and body must be a key of
    PLACEHOLDERS, else TemplateError("unknown_placeholder", "These are not placeholders we know:
    {x}, {y}.", "Use only the buttons under the message box."). A body without {sign_off} gets
    CLOSING appended. More than MAX_CUSTOM -> TemplateError("too_many", ...). Updating a template
    of another project -> TemplateError("not_found", "That template was not found.").
    Returns {"ok": True, "template": _shape(row)}."""
def delete_template(project_id: int, template_id: int) -> dict:
    """Delete it, then call campaign_mod.update_campaign(cid, template_key=DEFAULT_KEY) for every
    search of the project whose template_key was "custom-<id>". Returns
    {"ok": True, "deleted": True, "searches_reset": n}."""
def get_template(project_id: int | None, key: str) -> dict[str, str] | None:
    """A built-in by key, a custom one of this project by "custom-<id>", else None."""
def resolve(project_id: int | None, key: str, seniority: str) -> tuple[str, dict[str, str]]:
    """The concrete (key, template) for one person: auto-<depth> picks the level from seniority
    (LEVEL_OF_SENIORITY, default manager); an unknown or deleted custom key falls back to
    auto-standard."""
def set_campaign_template(campaign_id: int, key: str) -> dict[str, Any]:
    """Check the key exists for the campaign's project (KEY_RE and get_template, or auto-*), then
    campaign_mod.update_campaign(campaign_id, template_key=key). Returns
    {"ok": True, "campaign_id": id, "template_key": key}. Unknown -> TemplateError("unknown_template",
    "That message style does not exist.")."""
```

`services/campaign.py`: `DEFAULT_OPTIONS` gains `"template_key": "auto-standard"` (last). `_options` checks: `if not isinstance(out["template_key"], str) or not KEY_RE.match(out["template_key"])` → `CampaignError("invalid_value", "option template_key must be auto-standard, auto-short, auto-detailed, a built-in template key or custom-<id>.")`. Define `KEY_RE` in `campaign.py` and import it into `templates.py` (campaign must not import templates). `update_campaign` already passes direct options through.

MCP `update_campaign` gains `template_key: Annotated[str | None, Field(description="Message style: auto-standard (default), auto-short, auto-detailed, csuite-short ... manager-detailed, or custom-<id>.")] = None`, passed as `template_key=` when not None. Its description adds: "template_key sets the message style the drafts follow; change it only when the user asks for shorter, longer or a named template."

**Card "Message templates" (`p.templates`)**:
- Text: "Messages follow the style chosen for each search (see Searches and message style). You can read the built-in templates and write your own."
- Sub-heading **Built-in** (h4) and a compact table: **Template** (name), **When to use it** (use case), button **View**. View opens a dialog titled with the name: the use case (muted), "Subject: <subject>", the body in `pre.msg`, and buttons **Copy into my templates** (opens the editor pre-filled with name "<name> (my copy)", same level, depth, subject, body) and **Close**.
- Sub-heading **Yours** (h4): table **Template**, **For**, **Length**, **When to use it**, buttons **Edit** and **Delete** (delete asks "Delete this template? Searches that use it go back to matching each person's level." with **Delete** / **Keep it**). Empty: "You have not written your own yet."
- Button **New template** (primary) opens the editor dialog **Your template**:
  - **Name** (input, max 80), **Who it is for** (select: Top leaders / Directors and heads / Managers / Anyone), **Length** (select: Short / Standard / Detailed), **When to use it** (input, max 200), **Subject (email only)** (input, max 120), **Message** (textarea, 12 rows, max 3000).
  - Under the message: "Insert:" and one small button per placeholder, labelled with the placeholder text (e.g. `{first_name}`) and a `title` with its meaning; clicking inserts it at the cursor (`textarea.setRangeText(text, start, end, 'end')` then focus).
  - Muted help: "The AI fills in every {placeholder} for each person and words the rest naturally. Your sign-off is added at the end if you leave {sign_off} out."
  - Buttons **Cancel** and **Save** (primary). Errors show under the form (`setStatus`).

### 5.5 How a search selects one, and how the AI follows it

- Card **Searches and message style** (`p.searches`): for each search of the project (`GET /api/campaigns?project_id=<id>`), a select **Message style** (`aria-label` "Message style for <search name>") with the `choices` from `GET /api/projects/<id>/templates`, current value `row.template_key`. On change: `POST /api/campaigns/<cid>/template` `{template_key}`; success toast "Saved. New messages for this search follow it."; failure resets the select and shows the error.
- `drafting.get_drafting_batch` (when `channel != "linkedin_invite"`):

```python
    key = str((camp.get("options") or {}).get("template_key") or templates.DEFAULT_KEY)
    used: dict[str, dict[str, str]] = {}
    people = []
    for r in rows:
        person = _person_payload(r, camp, known.get(r["id"]))
        tkey, tpl = templates.resolve(camp.get("project_id"), key, r["seniority"])
        person["template"] = tkey
        used[tkey] = {k: tpl[k] for k in ("name", "use_case", "notes", "subject", "body")}
        people.append(person)
    reply["templates"] = used
    reply["placeholders"] = templates.PLACEHOLDERS
```

  (For `linkedin_invite` keep `people` as today and add neither key.)
- `mcp_server.get_drafting_batch` uses `keep=("guide", "templates")` so template bodies are never shortened; its description adds "Each person names the template to follow (templates)."
- `guides/messaging_style.md` gains a section after section 1 (exact text):

```markdown
## 1a. Templates

Each person in the batch names a `template`; its text is in the batch's `templates`. Follow it:

- Keep its order of paragraphs and its length (`notes` gives the word target).
- Replace every `{placeholder}` using `placeholders` for what each one means. Where they come
  from: `{evidence_line}` from the person's `evidence`, `title` and `company`; `{offer_line}` from
  `offer.services`; `{outcome_line}` from `offer.confirmed_claims`; `{proof_line}` from
  `offer.proof_points` (or a confirmed claim); `{sender_line}` from `offer.sender` and
  `sign_off`; `{cta}` from `offer.ctas`. A sentence whose data is missing (for example no proof
  point for `{proof_line}`) is left out, never invented.
- Plain-text lines in a template are suggestions: keep their meaning, word them for this person.
- Email: use the template's `subject` as the pattern for the subject line (60 characters at most).
- LinkedIn message (after they accept): use the body without the subject, within 700 characters;
  a detailed template is written at standard length. LinkedIn connection notes ignore templates.
- `{sign_off}` is the batch's `sign_off.block`, each part on its own line.
- The rules in sections 2 to 5 always win over a template.
```

### 5.6 Routes for templates

| Method and path | Body | Reply |
|---|---|---|
| `GET /api/projects/{id}/templates` | | `templates.list_templates(id)` |
| `POST /api/projects/{id}/templates` | `TemplateBody` | `templates.save_template(id, body)` |
| `PUT /api/projects/{id}/templates/{tid}` | `TemplateBody` | `templates.save_template(id, body, tid)` |
| `DELETE /api/projects/{id}/templates/{tid}` | | `templates.delete_template(id, tid)` |
| `POST /api/campaigns/{cid}/template` | `TemplateChoiceBody` | `templates.set_campaign_template(cid, key)` |

```python
class TemplateBody(_Body):
    name: str = Field(max_length=80)
    level: Literal["csuite", "director", "manager", "any"] = "any"
    depth: Literal["short", "standard", "detailed"] = "standard"
    use_case: str = Field(default="", max_length=200)
    subject: str = Field(default="", max_length=120)
    body: str = Field(max_length=3000)


class TemplateChoiceBody(_Body):
    template_key: str = Field(max_length=40)
```

`GET /api/campaigns` rows gain `"template_key": full["options"].get("template_key", "auto-standard")`. `TemplateError` has `as_response()`, so `_failure` turns it into a reply (`not_found` gives 404, every other code 400).

---
## 6. Usage meter and the visible process

### 6.1 Model and effort (`services/preferences.py`, `services/ai_setup.py`, `services/chat.py`)

```python
AI_MODELS: dict[str, str] = {"sonnet": "Sonnet (recommended)", "opus": "Opus (strongest, uses more)"}
AI_EFFORTS: dict[str, str] = {"low": "Low (fastest)", "medium": "Medium", "high": "High (recommended)",
                              "xhigh": "Extra high", "max": "Max (slowest)"}
AI_MODEL_KEY, AI_EFFORT_KEY = "ai_model", "ai_effort"
DEFAULT_MODEL, DEFAULT_EFFORT = "sonnet", "high"


def _stored(key: str) -> str:   # SELECT value FROM settings WHERE key = ?  ("" when none)
def get_ai_model() -> str:      # stored value if in AI_MODELS else DEFAULT_MODEL
def get_ai_effort() -> str:     # stored value if in AI_EFFORTS else DEFAULT_EFFORT
def set_ai_model(model: str, effort: str) -> dict[str, str]:
    """Store both (db.upsert on settings.key, like set_ai_mode); an unknown value raises
    PreferencesError("invalid_value", "Choose a model and an effort from the list.")."""
```

- Aliases, not full model names: `claude --help` (2.1.310) says `--model` takes "an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet') or a model's full name", so `sonnet` follows the newest Sonnet (5.5 today). `--effort` takes `low, medium, high, xhigh, max` (research section 4).
- `ai_setup.get_status()` adds `"model": preferences.get_ai_model()`, `"effort": preferences.get_ai_effort()`, `"models": [{"key": k, "label": v} for k, v in AI_MODELS.items()]`, `"efforts": [...]` (same shape).
- Route `POST /api/ai/model` with `AiModelBody(_Body)`: `model: Literal["sonnet", "opus"]`, `effort: Literal["low", "medium", "high", "xhigh", "max"]` → `preferences.set_ai_model(...)`, reply `ai_setup.get_status()`.
- `chat.build_command(binary, *, session_id, resume, mcp_config, prompt_file, model: str = "", effort: str = "")`: after `"--max-turns", str(MAX_TURNS)` and before the session flags, `if model: cmd += ["--model", model]` and `if effort: cmd += ["--effort", effort]`. (With both empty the command is exactly today's, so `test_build_command_is_exact` still passes.) `_run_turn` passes `model=preferences.get_ai_model(), effort=preferences.get_ai_effort()` and creates `Mapper(..., effort=effort)`.

### 6.2 Usage records (`services/usage.py`, new)

Fields from research 4.1 and 4.3. One row per finished turn that has a `result` line with usage or cost (error turns included). Costs are Claude Code's own estimate (`total_cost_usd`), cumulative per session on resume, so the turn's cost is the difference from the session's previous total.

```python
"""AI usage of this app: one row per chat reply, from Claude Code's own result line. Costs are the
program's estimate (not a bill); on a resumed session its total is cumulative, so each row keeps
the total and this turn's difference."""

from datetime import UTC, datetime

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _num(value: Any) -> int:            # int(value) for int/float that is not bool, else 0
def _tokens(result: Mapping[str, Any]) -> tuple[dict[str, int], str]:
    """Token counts and the first model name. Sums `modelUsage` over models when present (it
    includes subagents: inputTokens, outputTokens, cacheReadInputTokens, cacheCreationInputTokens),
    else reads `usage` (input_tokens, output_tokens, cache_read_input_tokens,
    cache_creation_input_tokens)."""
def record_turn(project_id: int, effort: str, model: str, result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Store one row and return what the page shows: the four token counts, cost_usd (float or
    None), model, effort. None (nothing stored) when the result has no usage, no modelUsage and no
    numeric total_cost_usd.
    cost: total = result["total_cost_usd"] when it is a number else None;
          prev = SELECT MAX(cost_total_usd) FROM ai_usage WHERE session_id = ? (only when session_id != "");
          cost_usd = None if total is None else (total - prev if prev is not None and total >= prev else total).
    is_error = 1 when result["is_error"] is true or result["subtype"] is not "success"."""
def _since(month: bool = False) -> str:
    """Local midnight today (or on the 1st of this month) as a UTC stamp like db.now()."""
    now = datetime.now().astimezone()
    start = now.replace(day=1 if month else now.day, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
def _totals(conn, where: str, params: Sequence[Any]) -> dict[str, Any]:
    """{"turns", the four token sums, "tokens" (their sum), "cost_usd" (SUM, None when all NULL)}.
    `where` is one of the fixed strings written in this module, never user text."""
def summary(project_id: int | None = None) -> dict[str, Any]:
    """{"ok": True, "model", "effort", "today", "month", "all", "project" (None without project_id),
    "by_project": [{"project_id", "name" ("" for a deleted project), totals...}] ordered by tokens
    descending, "recent": last 20 rows newest first with "project" name added}."""
```

Route `GET /api/usage?project_id=<optional int>` → `usage.summary(project_id)`.

### 6.3 SSE events (`services/chat.py` `Mapper`)

Two new events; every existing event keeps its name, order and data (tests depend on them).

| New event | When | Data |
|---|---|---|
| `thinking` | `stream_event` with `event.type == "content_block_start"` and `content_block.type == "thinking"` (the model starts thinking; shape **string only**, research did not run a turn: the tester confirms) | `{}` |
| `usage` | In `Mapper.finish`, right after `_flush_text()` and before the cancelled / error / chips / done events, when `usage.record_turn(...)` returned a dict | `{input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, model, effort}` |

`Mapper.__init__(self, turn, project_id, mode, new_session, effort: str = "")` keeps `self.effort = effort` and `self.model = ""`; `_init` sets `self.model = str(msg.get("model") or "")`. The `record_turn` call is wrapped in `try/except Exception` that logs only the class name (`log.error("usage not saved: %s", type(exc).__name__)`), so a usage problem never breaks a turn.

### 6.4 SSE events mapped to the timeline and the agent strip

| Event | Timeline (`UI.timeline()` method) | Orb and bars `data-state` | Status line |
|---|---|---|---|
| (page opens, no turn) | `idle()` | `idle` | "Waiting for your message" |
| `turn` (also when the page reconnects to a running turn: the stream replays every event from the start) | `start()`: steps "Read your message" (done) and "Thinking" (active) | `thinking` | "Thinking..." (shimmering) |
| `thinking` | `thinking()`: a new "Thinking" step unless one is active | `thinking` | "Thinking..." |
| `text` (first delta after anything else) | `text()`: "Writing a reply" step | `talking` | "Writing a reply..." |
| `tool` `started` | `tool(d)`: a step with the tool's plain label (`TOOL_LABELS`, e.g. "Finding people...") | `working` | the tool label |
| `tool` `finished` | `tool(d)`: that step turns green (ok) or red (failed), shows its seconds | `thinking` | "Thinking..." |
| `refresh` | `refreshed()`: the card glows once; the right column refreshes (existing 1.2 s debounce) | | |
| `chips` | nothing | | |
| `usage` | `usage(d)`: footer "This reply: 12.3k tokens · about $0.04 (estimate)" | | |
| `error` | `error(d)`: a red step with the message | | |
| `done` | `done(d)`: every running step closes; "Done in 14 s" (green) or "Stopped" (red) | `idle` | "Ready for your next message" or "Stopped" |

The chat log no longer gets live tool lines (the timeline shows them); tool rows from the saved history still show as compact lines when the page is opened, so nothing is lost after a reload.

### 6.5 Status pill (`studio.js` `pill`), exact replacement

```js
  async function pill() {
    const box = document.getElementById('ai-pill');
    if (!box) return;
    const [ai, use] = await Promise.all([api('/api/ai'), api('/api/usage')]);
    clear(box);
    if (!ai.ok) return;
    if (!ai.ready) {
      box.appendChild(h('a', { href: '#/connect', class: 'pill-link off' },
        h('span', { class: 'pill-dot', 'aria-hidden': 'true' }), 'AI not connected'));
      return;
    }
    const model = (ai.models || []).find((m) => m.key === ai.model);
    const effort = (ai.efforts || []).find((e) => e.key === ai.effort);
    const today = use.ok ? use.today : null;
    let amount = 'nothing used yet';
    if (today && today.turns) amount = today.cost_usd !== null ? UI.money(today.cost_usd) : UI.tokens(today.tokens) + ' tokens';
    const text = [model ? model.label.split(' ')[0] : 'Sonnet', effort ? effort.label.split(' (')[0] : 'High', 'today ' + amount].join(' · ');
    box.appendChild(h('a', { href: '#/usage', class: 'pill-link on' },
      h('span', { class: 'pill-dot', 'aria-hidden': 'true' }), text));
  }
```

---

## 7. Migration, API and file-by-file changes

### 7.1 Migration v6 (`db.py`), forward-only, keeps every row

```python
# v6 (V13): the sign-off's job title and company, the project's own message templates, phone
# numbers found from LinkedIn profiles, and the AI usage per chat reply. New columns have
# defaults and the rest are new tables, so a v5 database keeps every row.
_V6_SQL = """
ALTER TABLE projects ADD COLUMN sender_title TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN sender_company TEXT NOT NULL DEFAULT '';

CREATE TABLE message_templates (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'any',        -- csuite / director / manager / any
    depth      TEXT NOT NULL DEFAULT 'standard',   -- short / standard / detailed
    use_case   TEXT NOT NULL DEFAULT '',
    subject    TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL
);
CREATE INDEX idx_message_templates_project ON message_templates(project_id);

CREATE TABLE phones (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    number     TEXT NOT NULL DEFAULT '',           -- '' while an Apollo request is waiting
    kind       TEXT NOT NULL DEFAULT '',           -- mobile / work / ... as the provider says
    source     TEXT NOT NULL DEFAULT '',           -- apollo / prospeo / getprospect
    status     TEXT NOT NULL DEFAULT 'found_not_verified',  -- found_not_verified / requested / failed
    request_id TEXT NOT NULL DEFAULT '',
    UNIQUE (person_id, number)
);
CREATE INDEX idx_phones_person ON phones(person_id);

CREATE TABLE ai_usage (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    session_id         TEXT NOT NULL DEFAULT '',
    at                 TEXT NOT NULL,
    model              TEXT NOT NULL DEFAULT '',
    effort             TEXT NOT NULL DEFAULT '',
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_total_usd     REAL,                       -- Claude Code's running total for the session
    cost_usd           REAL,                       -- this reply's share (estimate)
    is_error           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_ai_usage_at ON ai_usage(at);
CREATE INDEX idx_ai_usage_project ON ai_usage(project_id);
"""
```

`MIGRATIONS` appends `(6, _V6_SQL)`; the comment above it adds "v6 (V13): sender title and company, message_templates, phones, ai_usage". `TABLES` adds `"message_templates"`, `"phones"`, `"ai_usage"`. Deleting a project removes its templates and its people's phones by cascade and keeps its usage rows with `project_id` NULL ("Deleted project" on the Usage page). Nothing in `delete_project` changes.

### 7.2 API summary (new and changed)

| Method and path | Request | Reply (`ok: true` plus) |
|---|---|---|
| `PATCH /api/projects/{id}` | adds `sender_title`, `sender_company` | `project` (with both) |
| `GET /api/projects/{id}/studio` | | `project` adds `sender_title`, `sender_company` |
| `GET /api/projects/{id}/results` | | each lead adds `phone`, `phone_status` (`found` / `waiting` / `""`) |
| `GET /api/projects/{id}/templates` | | `builtin`, `custom`, `placeholders`, `choices` |
| `POST /api/projects/{id}/templates` | `TemplateBody` | `template` |
| `PUT /api/projects/{id}/templates/{tid}` | `TemplateBody` | `template` |
| `DELETE /api/projects/{id}/templates/{tid}` | | `deleted`, `searches_reset` |
| `POST /api/campaigns/{cid}/template` | `{"template_key": "auto-short"}` | `campaign_id`, `template_key` |
| `GET /api/campaigns` | | rows add `template_key` |
| `GET /api/providers` | | adds `segments: [{key, label, providers: [keys]}]`; each provider adds `costs` |
| `GET /api/ai` | | adds `model`, `effort`, `models`, `efforts` |
| `POST /api/ai/model` | `{"model": "sonnet", "effort": "high"}` | the `GET /api/ai` reply |
| `GET /api/usage?project_id=` | | `model`, `effort`, `today`, `month`, `all`, `project`, `by_project`, `recent` |
| SSE `/chat/stream` | | new events `thinking`, `usage` (6.3) |

Example `GET /api/usage?project_id=3`:

```json
{"ok": true, "model": "sonnet", "effort": "high",
 "today": {"turns": 2, "input_tokens": 2400, "output_tokens": 600, "cache_read_tokens": 10000,
           "cache_write_tokens": 1600, "tokens": 14600, "cost_usd": 0.0123},
 "month": {"turns": 2, "...": "same keys"}, "all": {"turns": 2, "...": "same keys"},
 "project": {"turns": 2, "...": "same keys"},
 "by_project": [{"project_id": 3, "name": "Spring course", "turns": 2, "tokens": 14600, "cost_usd": 0.0123,
                 "input_tokens": 2400, "output_tokens": 600, "cache_read_tokens": 10000, "cache_write_tokens": 1600}],
 "recent": [{"at": "2026-10-26T09:14:03.120Z", "project_id": 3, "project": "Spring course",
             "model": "claude-sonnet-5-5", "effort": "high", "input_tokens": 1200, "output_tokens": 300,
             "cache_read_tokens": 5000, "cache_write_tokens": 800, "cost_usd": 0.0, "is_error": false}]}
```

MCP changes: `update_project` (+`sender_title`, `sender_company`), `update_campaign` (+`template_key`), `estimate_email_search` and `find_emails` (+`use_finders`, `want_phone`), `get_drafting_batch` (`keep` adds `templates`), `run_qa` description (M01-M17). No new MCP tool, so `chat.STUDIO_TOOLS` stays at 43.

### 7.3 File-by-file

| File | Change | Chunk |
|---|---|---|
| `src/leadgen/db.py` | v6 migration, `TABLES` | C1 |
| `src/leadgen/services/projects.py` | `sender_title`, `sender_company` | C1 |
| `src/leadgen/services/drafting.py` | `sender_identity`, `sign_off_lines`, `sign_off` in the batch, `_SaveContext.sender`; templates in the batch | C1, C2 |
| `src/leadgen/services/qa_messages.py` | M17, `_without_sign_off`, `sender=` | C1 |
| `src/leadgen/services/review.py` | pass `sender`, M01-M17 text | C1 |
| `src/leadgen/services/chat.py` | context lines (C1); model/effort, `thinking`, `usage` (C5) | C1, C5 |
| `src/leadgen/guides/messaging_style.md`, `guides/studio.md` | sign-off (C1), templates (C2), finders and phones (C4) | C1, C2, C4 |
| `src/leadgen/mcp_server.py` | see 7.2 | C1, C2, C4 |
| `src/leadgen/dashboard/app.py` | bodies and routes of 7.2 | C1, C2, C3, C5 |
| `src/leadgen/services/templates.py` (new) | section 5.4 | C2 |
| `src/leadgen/services/campaign.py` | `template_key` option, `KEY_RE` | C2 |
| `src/leadgen/providers/base.py` | `ProviderInfo.costs`, `Found`, `Finder` | C3 |
| `src/leadgen/providers/{tavily,apollo,prospeo,getprospect,emailable}.py` (new) | section 3.3 | C3 |
| `src/leadgen/providers/{serper,brave,ddg,hunter,reoon,zerobounce,lemlist}.py`, `send/smtp_imap.py` | `costs=` on INFO | C3 |
| `src/leadgen/providers/registry.py` | 3.4 | C3 |
| `src/leadgen/core/secrets.py` | five keys | C3 |
| `src/leadgen/services/status.py` | `costs`, allowances, `segments()` | C3 |
| `src/leadgen/services/emails.py` | Emailable in the chain (C3); finders, phones, estimate (C4) | C3, C4 |
| `src/leadgen/services/budget.py`, `core/config.py`, `services/preferences.py` | `finder` budget (C4); model and effort (C5) | C4, C5 |
| `src/leadgen/services/browse.py` | phones in `studio_results` | C4 |
| `src/leadgen/services/usage.py` (new) | 6.2 | C5 |
| `src/leadgen/services/ai_setup.py` | model and effort in `get_status` | C5 |
| `tests/fake_claude.py` | usage fields in the success result; a `think` scenario | C5 |
| `src/leadgen/dashboard/static/studio.css` | replaced (2.1) | C6 |
| `src/leadgen/dashboard/static/ui.js` (new) | 2.2 | C6 |
| `src/leadgen/dashboard/static/studio_app.js` (new) | Connectors, Usage, Model card | C6 |
| `src/leadgen/dashboard/static/studio.js` | pill (6.5), model card hook, home tip, Advanced link | C6 |
| `src/leadgen/dashboard/static/app.js` | `TOP_NAV`, `NAV_GROUP`, routes, `providerCard` costs and mailbox, `pageSettings` finder budget | C4, C6 |
| `src/leadgen/dashboard/static/index.html` | load `ui.js` and `studio_app.js` | C6 |
| `src/leadgen/dashboard/static/studio_project.js` | the workspace (1.6) | C7 |
| `src/leadgen/__init__.py`, `pyproject.toml` | version `1.3.0` | C7 |
| `docs/DECISIONS.md`, `docs/STATUS.md` | facts per chunk; status rows | every chunk |

---
## 8. Chunks

Order keeps the suite green: data and checks first (C1, C2), providers in isolation (C3), then wiring them into the pipeline (C4), the AI usage plumbing (C5), and the two UI chunks last (C6 the shell and app pages, C7 the workspace). Each chunk ends with the full suite and ruff passing, a STATUS.md row (`V13-Cn`, status `review`, 3-6 line summary, files, follow-ups) and one commit.

### C1. Migration v6, the sign-off block and M17

**Files:** `db.py`, `services/projects.py`, `services/drafting.py`, `services/qa_messages.py`, `services/review.py`, `services/chat.py` (`build_prompt` only), `mcp_server.py` (`update_project`, `run_qa` text), `dashboard/app.py` (`ProjectChangeBody`, `/studio` project fields), `guides/messaging_style.md`, `guides/studio.md`, tests.

**Do:**
1. Add `_V6_SQL` and `(6, _V6_SQL)` exactly as 7.1; add the three tables to `TABLES`.
2. Projects: section 4.1 (fields, validation with `label`, `_shape`, MCP params, `ProjectChangeBody`, `/studio` reply).
3. Drafting: section 4.2 (`sender_identity`, `sign_off_lines`, batch `sign_off`, `offer.sender` company, `_SaveContext.sender`).
4. Checks: section 4.3 (M17, `_without_sign_off`, `check_message(sender=...)`, review passes `sender`).
5. Guides and context block: section 4.4.

**Tests:**
- `tests/test_db.py`: `db.init_db() == db.SCHEMA_VERSION == 6`, `count(conn, "schema_version") == 6`. New test: build a v5 database (monkeypatch `db.MIGRATIONS` to `db.MIGRATIONS[:4]`, as `tests/test_studio_data.py` does with `[:3]`), insert a project with `sender_name="Priya"`, restore and run `init_db()`: the project keeps its name and `sender_name`, `sender_title == ""`, and the three new tables exist and are empty.
- `tests/test_studio_data.py`: `update_project(pid, sender_title="  Head of   Partnerships ", sender_company="Bright Harbour Training")` saves collapsed text; 121 characters and a non-string raise `ProjectError("invalid_value")` naming "job title" / "company name"; `PATCH /api/projects/{id}` with both fields works and `/studio` returns them; MCP `update_project` takes both.
- `tests/test_qa_messages.py`: change `range(1, 17)` to `range(1, 18)`. New M17 tests with `sender = {"name": "Priya Nair", "title": "Head of Partnerships", "company": "Bright Harbour Training"}`:
  - email ending "Kind regards,\nPriya Nair\nHead of Partnerships\nBright Harbour Training" → no M17, no M07, no M14, no M10;
  - the same without the title line → one M17 block whose detail contains "Priya Nair / Head of Partnerships / Bright Harbour Training";
  - "priya nair" in lower case with extra spaces → passes (case and spaces do not matter);
  - `linkedin_invite` ending "Priya" → no M17; no `sender` (None) → no M17 for any body.
- `tests/test_drafting.py`: a project with the full identity: the batch has `sign_off == {"name": ..., "title": ..., "company": ..., "block": "Priya Nair\nHead of Partnerships\nBright Harbour Training", "required": True}` and `offer.sender.company == "Bright Harbour Training"`; `save_drafts` of an email ending with only "Priya" is `qa_fail` with M17; the same ending with the block is `qa_pass`. A project without a sign-off name gives `required: False` and the old behaviour (existing tests untouched).
- `tests/test_review.py`: `run_qa` on a campaign whose project identity changed after saving reports M17 for the old draft.
- `tests/test_chat.py` `test_build_prompt_has_the_context_block`: project dict gains `sender_title: ""`, `sender_company: ""`; assert `"sender_title: (not set)\n"` and `"sender_company: (not set)\n"` in the prompt.

**Done when:** the suite and ruff pass; an old database upgrades to v6 with every row kept; a draft without the project's sign-off block cannot pass.

### C2. Message templates

**Files:** `services/templates.py` (new), `services/campaign.py`, `services/drafting.py`, `mcp_server.py` (`update_campaign`, `get_drafting_batch`), `dashboard/app.py` (5.6 routes, `/api/campaigns` rows), `guides/messaging_style.md` (section 1a), `guides/studio.md`, tests.

**Do:**
1. `campaign.py`: `KEY_RE` (5.4), `DEFAULT_OPTIONS["template_key"] = "auto-standard"`, the check in `_options`.
2. `templates.py`: everything in 5.4, with `PLACEHOLDERS` (5.2) and `BUILTIN` (5.3) copied exactly. Store `subject` and `body` of each built-in as Python strings with `\n` line breaks; the body ends with `"\n\nKind regards,\n{sign_off}"`.
3. Drafting batch: 5.5.
4. Routes: 5.6. MCP: 5.4 and 5.5.
5. `guides/studio.md`, add to "Everything else you work out yourself": "Messages follow the message style of the search (the drafting batch hands you the template for each person). If the person asks for shorter or longer messages, or names one of their own templates, set it with update_campaign(template_key=...): auto-short, auto-standard, auto-detailed, or custom-<id>."

**Tests (`tests/test_templates.py`, new):**
- `BUILTIN` has the nine keys `<level>-<depth>`; each has name, level, depth, use_case, notes, subject, body; every placeholder in subject and body is in `PLACEHOLDERS`; every body starts with "Hi {first_name}," and ends with "Kind regards,\n{sign_off}".
- Every built-in, filled with this sample, passes `qa_messages.check_message` for `email` with no `block` issue (the sample people are invented):

```python
FILL = {
    "first_name": "Aria", "company": "Northwind Logistics", "their_title": "Head of Learning",
    "topic": "manager training",
    "evidence_line": "I see you lead learning and development for the warehouse and driver teams "
                     "at Northwind Logistics.",
    "offer_line": "We run short, practical programmes that help first-time managers lead their "
                  "teams with confidence.",
    "outcome_line": "Courses are taught by working trainers.",
    "proof_line": "Sessions are delivered by certified trainers.",
    "sender_line": "I work with training teams in logistics firms.",
    "cta": "Would a short outline be useful?",
    "sign_off": "Priya Nair\nHead of Partnerships\nBright Harbour Training",
}
SENDER = {"name": "Priya Nair", "title": "Head of Partnerships", "company": "Bright Harbour Training"}
# fill with str.format_map(FILL); profile = PROFILE from test_drafting plus those two claims in
# confirmed_claims; person {"greeting_name": "Aria", "first_name": "Aria", "last_name": "Test",
# "title": "Head of Learning"}; company {"name": "Northwind Logistics"}; campaign {"locale": "en-GB"};
# check_message(..., companies=[], sender=SENDER); assert no issue has severity "block"; and the
# filled subject is at most 60 characters.
```

- `save_template`: inserts and updates; unknown `{price}` → `unknown_placeholder`; a body without `{sign_off}` gets "\n\nKind regards,\n{sign_off}"; the 31st → `too_many`; another project's id → `not_found`.
- `delete_template` resets a search that used it to `auto-standard` and returns `searches_reset: 1`.
- `resolve`: `auto-short` with `c_suite` → `csuite-short`; `vp` → `director-*`; `individual` and `""` → `manager-*`; `custom-<id>` of the project → that template; a deleted custom key → `manager-standard` for an unknown seniority.
- `set_campaign_template`: valid keys saved (`get_campaign(cid)["options"]["template_key"]`); `custom-<id>` of another project and `banana` → `unknown_template`; `update_campaign(cid, template_key="banana")` → `CampaignError("invalid_value")`.
- Drafting: an email batch has `templates` and each person a `template` key present in `templates`, plus `placeholders`; a `linkedin_invite` batch has neither key.
- MCP: `get_drafting_batch` keeps a 2,500-character custom body unshortened (call through the MCP test helpers used in `tests/test_mcp_server.py`).
- Routes: list, create, update, delete, `POST /api/campaigns/{cid}/template`; `POST /api/campaigns/{cid}/template` with `{"allow_guess_emails": true}` is refused (422, unknown field).
- `tests/test_campaign.py`: the expected `options` dict gains `"template_key": "auto-standard"`.

**Done when:** the suite and ruff pass; a search's message style can be read, changed and used by the drafting batch.

### C3. Connector providers (no pipeline change yet)

**Files:** `providers/base.py`, `providers/{tavily,apollo,prospeo,getprospect,emailable}.py` (new), `costs=` in the existing INFOs, `providers/registry.py`, `core/secrets.py`, `services/status.py`, `services/emails.py` (`_default_verifiers` only), `dashboard/app.py` (`/api/providers` segments), `docs/DECISIONS.md`, tests.

**Do:**
1. Read, before coding, each provider's page listed in 3.1 and the research rows; add one DECISIONS row per provider (date, URL, what it says: method, URL, auth header, fields used, credits, free allowance, what is unverified).
2. `base.py`: 3.2. Then the five modules: 3.3. Keys only in headers.
3. `registry.py`, `secrets.py`, `status.py`, `/api/providers`: 3.4.
4. `emails._default_verifiers()`: `(ReoonProvider(), ZeroBounceProvider(), EmailableProvider(), HunterProvider())`.

**Tests:** 3.8 (`tests/test_connectors.py`, `tests/test_registry.py`). Plus `tests/test_status.py` (or `test_registry.py`): every `list_providers()` item has `costs`; `GET /api/providers` has `segments` with the four keys in order; every provider key appears in exactly one segment.

**Done when:** the suite and ruff pass; the five providers work against mocked replies; nothing calls them yet except the Keys test button and (Emailable) the verifier chain.

### C4. Finders in the email pipeline, phones, finder budget

**Files:** `services/emails.py`, `services/budget.py`, `core/config.py`, `services/preferences.py`, `services/browse.py`, `mcp_server.py` (`estimate_email_search`, `find_emails`), `dashboard/static/app.js` (`pageSettings` finder budget only), `guides/studio.md`, tests.

**Do:**
1. Section 3.5 exactly (`FINDER_MAX_CREDITS`, `_store_phone`, `_find_by_profile`, `poll_phone_requests`, `_people`, `_process_person`, `find_emails`, `_estimate`, `estimate_email_search`).
2. Budget kind `finder` (3.5, last paragraph) and the old Settings field.
3. `browse.studio_results` phones (3.6).
4. MCP parameters (3.5).
5. `guides/studio.md`, in "Money, permission and safety", add after the first bullet: "- Finding emails from LinkedIn profiles (Apollo, Prospeo, GetProspect) costs up to 1 credit per person found. Phone numbers cost up to 10 credits each: look them up (want_phone true) only when the person asks for phone numbers, and say they are found, not checked. Every email found is still checked before it is used."

**Tests (`tests/test_email_finders.py`, new; invented people, a fake finder object instead of HTTP):**

```python
class FakeFinder:
    """Stands in for a finder; uses Prospeo's INFO so error replies have a known provider."""
    info = prospeo.INFO
    def __init__(self, found=None, error=None): self.found, self.error, self.calls = found, error, []
    def configured(self): return True
    def find(self, linkedin_url, want_phone=False):
        self.calls.append((linkedin_url, want_phone))
        if self.error: raise self.error
        return self.found or Found("prospeo")
```

- A confirmed person with `linkedin_url` and a finder returning `aria.tan@northwind.example` (1 credit) and a fake verifier saying `verified`: the stored email is `verified` with `method == "finder:prospeo"`; `budget.used(cid, "finder") == 1`; the pattern path was not used (a fake Hunter is not called).
- The finder returns nothing: the pattern path runs (existing behaviour).
- The finder email is `invalid`: the pattern path runs.
- No verifier configured: the finder email is stored as `guess` (never `verified`).
- A person without `linkedin_url`: the finder is not called.
- `use_finders=False`: the finder is not called.
- Finder budget: campaign `budget={"finder": 0}` → `budget_reached` reply and no finder call; with `confirm=True` it runs.
- A finder `ProviderError("quota")`: the run goes on with the pattern path and `provider_issues` lists Prospeo.
- `want_phone=True` with `Found(..., phone="+65 6000 0000", phone_kind="mobile")`: a `phones` row `found_not_verified`; with `phone_request="42"` (and `source="apollo"`): a `requested` row; `poll_phone_requests` with respx (Apollo 200 ready, `sanitized_number` "+6560000001") turns it into `found_not_verified` and charges 8 to `finder`; 410 turns it to `failed`.
- `estimate_email_search` with a configured Prospeo key (secrets set, no HTTP made): `finder == "prospeo"`, `finder_credits == <people with LinkedIn>`, and with `want_phone=True` ten times that; `needs_confirmation` true above the threshold.
- `browse.studio_results`: a found phone → `phone_status == "found"`; a requested one → `"waiting"`.
- `preferences.update_preferences({"budgets": {"finder": 30}})` round-trips; `tests/test_dashboard.py` settings tests still pass.

**Done when:** the suite and ruff pass; a LinkedIn profile can give a checked email; phones are stored as found, not checked; finder spending is estimated, capped by the budget and needs confirmation.

### C5. Model and effort, usage records, `thinking` and `usage` events

**Files:** `services/preferences.py`, `services/ai_setup.py`, `services/usage.py` (new), `services/chat.py`, `dashboard/app.py` (`POST /api/ai/model`, `GET /api/usage`), `tests/fake_claude.py`, tests.

**Do:**
1. Section 6.1 (preferences, `get_status`, route, `build_command`, `_run_turn`).
2. Section 6.2 (`usage.py`, route).
3. Section 6.3 (`Mapper`: `effort`, `model`, `thinking`, `usage`).
4. `tests/fake_claude.py`: the success `result` line becomes

```python
    out({"type": "result", "subtype": "success", "is_error": False, "result": "All set.",
         "session_id": sid, "num_turns": 2, "total_cost_usd": 0.0123,
         "usage": {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 5000,
                   "cache_creation_input_tokens": 800},
         "modelUsage": {"claude-fake-1": {"inputTokens": 1200, "outputTokens": 300,
                        "cacheReadInputTokens": 5000, "cacheCreationInputTokens": 800,
                        "costUSD": 0.0123}}})
```

   and a new scenario `think` (add it to the docstring list): right after the `init` line, `out({"type": "stream_event", "session_id": sid, "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}})`, then the normal chat.

**Tests:**
- `tests/test_chat.py`: `build_command(..., model="sonnet", effort="high")` ends with `[..., "--max-turns", "80", "--model", "sonnet", "--effort", "high", "--session-id", "sid-1"]`; the existing exact test is unchanged. A normal turn: the fake's argv has `--model sonnet --effort high` (defaults); a `usage` event comes before `done` with `input_tokens == 1200`, `cost_usd == 0.0123` and `model == "fake"` (the model named in the `init` line wins over the `modelUsage` key); one `ai_usage` row. A second turn (resumed, same session, same total) stores `cost_usd == 0.0`. Scenario `think` emits one `thinking` event before the first `text`. The `limit` scenario emits no `usage` event (its result has no usage), so `events[-2]` is still the error.
- `tests/test_usage.py` (new): `record_turn` with `modelUsage` sums two models; without `modelUsage` reads `usage`; `total_cost_usd` missing → `cost_usd` None and `summary` shows `cost_usd` None; a turn with `subtype: "error_max_turns"` is stored with `is_error 1`; `summary()` today excludes a row stamped yesterday (insert with `at` set to `db.now()` minus 2 days); `by_project` names a deleted project `""`.
- `tests/test_ai_setup.py`: `get_status()` has `model == "sonnet"`, `effort == "high"` by default, and the two lists; `POST /api/ai/model` stores `opus`/`max`; `{"model": "gpt"}` → 422.

**Done when:** the suite and ruff pass; every chat turn uses the chosen model and effort and leaves one usage row; the stream carries `thinking` and `usage`.

### C6. Look and feel, app-scope pages (Connectors, Usage, Model and effort)

**Files:** `static/studio.css` (replace with 2.1), `static/ui.js` (new, 2.2), `static/studio_app.js` (new), `static/studio.js`, `static/app.js`, `static/index.html`, `services/chat.py` (`GO_TARGETS` label), tests.

**Do:**
1. Replace `studio.css` with 2.1 and create `ui.js` with 2.2, both verbatim.
2. `index.html` scripts, in this order: `/static/ui.js`, `/static/studio.js`, `/static/studio_project.js`, `/static/studio_app.js`, `/static/app.js` (all `defer`).
3. `studio_app.js`, write exactly:

```js
/* Lead Generator Studio, part 3: the app-wide pages: Connectors (keys grouped by what they do), Usage,
   and the Model and effort card of the Connect page. Plain JavaScript, loaded before app.js; uses
   app.js's helpers and window.UI. Everything from the server is put on the page as text. */
(function () {
  'use strict';

  const SEGMENT_HELP = {
    search: 'Used to find companies and people. Serper and Tavily have free allowances without a card.',
    finder: "Given a person's LinkedIn profile address, these look up a work email (and a phone number only if you ask). Every email found is still checked before it is used. Phone numbers are shown as found, not checked.",
    verify: 'Checks that an email address really exists. Only checked addresses are ever exported or sent.',
    sending: 'Optional. Send from your own mailbox, or hand the contacts to lemlist.',
  };

  async function pageConnectors(ctx) {
    const reply = await api('/api/providers');
    if (ctx.token !== routeToken) return;
    if (!reply.ok) return setMain(notice('bad', errorText(reply)));
    const byKey = {};
    for (const p of reply.providers) byKey[p.key] = p;
    const list = h('div', { class: 'stack' });
    for (const seg of reply.segments) {
      const items = seg.providers.map((key) => byKey[key]).filter(Boolean);
      const on = items.filter((p) => p.configured).length;
      list.appendChild(UI.card('c.' + seg.key, seg.label,
        { open: seg.key === 'search', count: on ? on + ' connected' : 'none connected' },
        h('p', { class: 'muted small', text: SEGMENT_HELP[seg.key] || '' }), ...items.map(providerCard)));
    }
    setMain(
      h('div', { class: 'page-head' }, h('h1', { text: 'Connectors' }), UI.collapseAllButton(list)),
      h('p', { class: 'lead', text: 'Services Lead Generator can use with your own keys. Every key stays on this computer and is never shown again (only its last four characters). All of them are optional: the AI tells you when one would help.' }),
      list);
  }

  function totalsLine(t) {
    const parts = ['tokens', fmtNum(t.turns) + (t.turns === 1 ? ' reply' : ' replies')];
    if (t.cost_usd !== null && t.cost_usd !== undefined) parts.push(UI.money(t.cost_usd));
    return parts.join(' · ');
  }
  function table(heads, rows) {
    return h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...heads.map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...rows)));
  }
  function tile(title, t) {
    return h('div', { class: 'card tile' }, h('div', { class: 'muted small', text: title }),
      h('div', { class: 'num', text: UI.tokens(t.tokens) }), h('div', { class: 'muted small', text: totalsLine(t) }));
  }

  async function pageUsage(ctx) {
    const [use, ai] = await Promise.all([api('/api/usage'), api('/api/ai')]);
    if (ctx.token !== routeToken) return;
    if (!use.ok) return setMain(notice('bad', errorText(use)));
    const model = ai.ok ? (ai.models || []).find((m) => m.key === ai.model) : null;
    const effort = ai.ok ? (ai.efforts || []).find((e) => e.key === ai.effort) : null;
    const kids = [
      h('div', { class: 'page-head' }, h('h1', { text: 'Usage' })),
      h('p', { class: 'lead', text: 'How much of your AI this app has used. Costs are estimates from Claude Code, not your bill. On a Claude plan you pay your plan, not these amounts.' }),
      h('p', null, 'Model now: ' + (model ? model.label.split(' ')[0] : 'Sonnet') + ' · effort ' + (effort ? effort.label.split(' (')[0] : 'High') + ' ',
        h('a', { href: '#/connect', text: 'Change' })),
    ];
    if (!use.all.turns) {
      kids.push(h('div', { class: 'empty card' }, h('p', { class: 'muted', text: 'Nothing used yet. Usage appears here after your first chat message.' })));
      return setMain(...kids);
    }
    kids.push(h('div', { class: 'tiles' }, tile('Today', use.today), tile('This month', use.month), tile('All time', use.all)));
    const max = Math.max(1, ...use.by_project.map((r) => r.tokens));
    const projectRows = use.by_project.map((r) => h('tr', null,
      h('td', { text: r.name || 'Deleted project' }), h('td', { class: 'num', text: fmtNum(r.turns) }),
      h('td', { class: 'num', text: UI.tokens(r.tokens) }), h('td', { text: UI.money(r.cost_usd) }),
      h('td', { class: 'bar-cell' }, bar(r.tokens, max))));
    kids.push(UI.card('u.projects', 'By project (all time)', { open: true },
      table(['Project', 'Replies', 'Tokens', 'About', ''], projectRows)));
    const recentRows = use.recent.map((r) => h('tr', null,
      h('td', { text: fmtDate(r.at) }), h('td', { text: r.project || 'Deleted project' }),
      h('td', { text: r.model }), h('td', { text: r.effort }),
      h('td', { class: 'num', text: UI.tokens(r.input_tokens) }), h('td', { class: 'num', text: UI.tokens(r.output_tokens) }),
      h('td', { class: 'num', text: UI.tokens(r.cache_read_tokens) }), h('td', { class: 'num', text: UI.tokens(r.cache_write_tokens) }),
      h('td', { text: UI.money(r.cost_usd) }), h('td', null, r.is_error ? badge('Stopped', 'bad') : null)));
    kids.push(UI.card('u.recent', 'Last 20 replies', {},
      table(['When', 'Project', 'Model', 'Effort', 'Read', 'Written', 'From memory', 'Saved to memory', 'About', ''], recentRows)));
    setMain(...kids);
  }

  function modelCard(ai) {
    const model = selectBox('ai-model', (ai.models || []).map((m) => [m.key, m.label]), ai.model);
    const effort = selectBox('ai-effort', (ai.efforts || []).map((e) => [e.key, e.label]), ai.effort);
    const status = h('div', { role: 'status' });
    const save = h('button', { type: 'button', class: 'primary', text: 'Save' });
    save.addEventListener('click', async () => {
      save.disabled = true;
      const res = await api('/api/ai/model', { method: 'POST', body: { model: model.value, effort: effort.value } });
      save.disabled = false;
      if (res.ok) { toast('Saved. Your next message uses it.'); Studio.pill(); } else setStatus(status, 'bad', errorText(res));
    });
    return h('section', { class: 'card stack' }, h('h3', { text: '4. Model and effort' }),
      h('p', { class: 'muted', text: 'Which Claude model the assistant uses, and how hard it thinks before it answers. More effort is slower and uses more of your plan or credit.' }),
      field('ai-model', 'Model', model), field('ai-effort', 'Effort', effort),
      h('div', { class: 'row' }, save), h('p', { class: 'small muted', text: 'Defaults: Sonnet, high.' }), status);
  }

  window.Studio = Object.assign(window.Studio || {}, { pageConnectors, pageUsage, modelCard });
})();
```

4. `studio.js`: replace `pill` with 6.5. In `pageConnect`'s `render()`, after `if (ai.mode) root.appendChild(testCard());` add `if (ai.ready && Studio.modelCard) root.appendChild(Studio.modelCard(ai));`. In `pageHome`, fetch `/api/providers` in the same `Promise.all`; compute `const search = providers.ok ? (providers.segments.find((s) => s.key === 'search') || { providers: [] }).providers : [];` and when `providers.ok && !providers.providers.some((p) => search.includes(p.key) && p.configured)`, push `h('p', { class: 'small muted' }, 'Tip: add a free search key on the Connectors page so the AI can look for people. ', h('a', { href: '#/keys', text: 'Open Connectors' }))` after the AI warning. In `pageAdvanced`, add `['#/settings', 'Email footer, spelling and spending limits']` to `links`.
5. `app.js`: `TOP_NAV` and `NAV_GROUP` as 1.2; in `route()` `pages` set `keys: studio.pageConnectors || pageKeys, usage: studio.pageUsage || pageHome`; `providerCard`: after the purpose line add `p.costs ? h('p', { class: 'small muted', text: 'Costs: ' + p.costs }) : null`, and replace the `smtp` branch body with `lines.push(h('p', { class: 'small' }, p.configured ? 'Connected. ' : 'Not connected yet. ', h('a', { class: 'button', href: '#/mailbox', text: 'Connect your mailbox' })));`.
6. `chat.py` `GO_TARGETS["keys"] = ("Open Connectors", "#/keys")`.

**Tests (`tests/test_studio_static.py`):**
- `SCRIPTS = ("ui.js", "studio.js", "studio_project.js", "studio_app.js")` (the rules test then covers the new files too); `test_studio_files_are_served` checks the five scripts are served and in the order of step 2.
- `test_studio_css_has_the_tokens_and_dark_mode` adds `.ui-card`, `.card-body`, `.segmented`, `.orb`, `.bars`, `.shimmer`, `.tl-step`, `grid-template-rows`.
- New: `ui.js` contains `localStorage`, `'lg.card.'`, `'lg.layout.'`, `aria-expanded`, `inert`, `try {`; `studio_app.js` contains the labels "Connectors", "Usage", "4. Model and effort", "Model", "Effort", "Last 20 replies", "By project (all time)", "Deleted project"; `app.js` contains `['keys', 'Connectors']`, `['usage', 'Usage']` and still `['connect', 'Your AI']`.
- The node syntax check covers the two new files.
- `tests/test_chat.py`: the button label becomes "Open Connectors".
- `tests/test_studio_api.py`: `GET /api/providers` has `segments`; `GET /api/usage` and `GET /api/ai` new fields (route-level).

**Done when:** the suite and ruff pass; the top bar shows Projects, Your AI, Connectors, Usage, Advanced; the Connectors page groups every key by segment; the Usage page and the model card work against the fake data; dark mode and reduced motion are respected.

### C7. Project workspace, end-to-end test, version

**Files:** `static/studio_project.js`, `tests/test_studio_static.py`, `tests/e2e/test_studio_flow.py`, `src/leadgen/__init__.py`, `pyproject.toml`, `docs/STATUS.md`, `README` sections that mention v1.2 screens (if any).

**Do (in `studio_project.js`):**

1. `chatPane(projectId, first, hooks)`: `hooks` now also has `timeline` (a `UI.timeline()`).
   - In `follow(turnId)`: call `hooks.timeline.start()` before opening the stream (the replay rebuilds it). Handlers: `on('turn', () => {})` is not needed; `on('thinking', () => hooks.timeline.thinking())`; in `on('text', ...)` call `hooks.timeline.text()` first; `on('tool', (d) => { bubble = null; hooks.timeline.tool(d); })` (no more `toolLines` and no live `tool-line` in the chat log); `on('refresh', () => { hooks.timeline.refreshed(); hooks.refresh(false); })`; `on('usage', (d) => hooks.timeline.usage(d))`; in `on('error', ...)` also `hooks.timeline.error(d)`; in `on('done', ...)` also `hooks.timeline.done(d)`. Remove the `typing` line element and its uses (the timeline shows it). When no turn is running at load, call `hooks.timeline.idle()`.
   - Scroll-to-bottom: `const down = h('button', { type: 'button', class: 'round to-bottom', 'aria-label': 'Scroll to the latest message', text: '↓', hidden: true });` `log.addEventListener('scroll', () => { down.hidden = log.scrollHeight - log.scrollTop - log.clientHeight < 40; });` `down.addEventListener('click', toBottom);` and append `down` to the card.
   - Growing textarea: `input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; });` and reset `input.style.height = ''` after sending.
   - `send` keeps the texts **Send** and **Stop** (classes `primary` and `danger solid`).
2. `resultsBody`: headings `['Name', 'Job', 'Company', 'LinkedIn', 'Email (verified)', 'Phone (found, not checked)', 'Messages']`; the phone cell is `lead.phone_status === 'waiting' ? 'Requested, waiting' : lead.phone`. `viewDialog`: after the badge, `lead.phone ? h('p', { class: 'small', text: 'Phone: ' + lead.phone + ' (found, not checked)' }) : null`.
3. `actionsCard(state)`: return `h('div', { class: 'stack' }, ...)` with the same children except the `h3` (the card title says "Export and send").
4. New helpers (all labels exactly as in 1.6, 4.1, 5.4, 5.5): `signOffBody(project, onSaved)`, `searchesBody(projectId, campaigns, choices)`, `templatesBody(projectId, data, reload)` with `templateEditor(projectId, initial, onSaved)` and `templateView(t, onCopy)`, `productBody(page)` (uses `sourceKindText` from `app.js`), `contactsBody(page)` (uses `label(KNOWN, k)` from `app.js` for the counts sentence, the same wording as the old project page).

   The placeholder buttons in `templateEditor`:

```js
    const insertRow = h('div', { class: 'row small' }, 'Insert:', ...Object.entries(placeholders).map(([name, meaning]) => {
      const b = h('button', { type: 'button', class: 'ghost', title: meaning, text: '{' + name + '}' });
      b.addEventListener('click', () => {
        body.setRangeText('{' + name + '}', body.selectionStart, body.selectionEnd, 'end');
        body.focus();
      });
      return b;
    }));
```

5. `pageProject(ctx)`, skeleton (write it like this; the helpers come from step 4):

```js
  async function pageProject(ctx) {
    leave();
    const id = ctx.projectId;
    const [studio, chat, page, list, tpl, camps, use] = await Promise.all([
      api('/api/projects/' + id + '/studio'), api('/api/projects/' + id + '/chat'), api('/api/projects/' + id),
      api('/api/projects'), api('/api/projects/' + id + '/templates'),
      api('/api/campaigns' + query({ project_id: id })), api('/api/usage' + query({ project_id: id })),
    ]);
    if (ctx.token !== routeToken) return;
    if (!studio.ok) return setMain(notice('bad', errorText(studio)), h('a', { href: '#/home', text: 'Back to your projects' }));
    if (!chat.ok || !page.ok) return setMain(notice('bad', errorText(chat.ok ? page : chat)));

    const view = { campaignId: null, page: 1 };
    const tl = UI.timeline();
    let timer = null;
    let dead = false;
    const cards = {
      activity: UI.card('p.activity', 'What the AI is doing', { open: true }, tl.node),
      progress: UI.card('p.progress', 'Progress', { open: true }),
      results: UI.card('p.results', 'Ready contacts', { open: true }),
      send: UI.card('p.send', 'Export and send', {}, actionsCard({ campaignId: () => view.campaignId, refresh: () => refresh(true) })),
      searches: UI.card('p.searches', 'Searches and message style', {}),
      templates: UI.card('p.templates', 'Message templates', {}),
      signoff: UI.card('p.signoff', 'Your sign-off', {}, signOffBody(studio.project, () => route())),
      product: UI.card('p.product', 'Your product', {}, ...productBody(page)),
      memory: UI.card('p.memory', "Don't contact again", {}, memoryBox(page.project, page.memory)),
      contacts: UI.card('p.contacts', 'Contacts you already have', {}, ...contactsBody(page)),
    };
    const drawSearches = (rows, choices) => { clear(cards.searches.inner); cards.searches.inner.append(...searchesBody(id, rows, choices)); };
    const drawTemplates = (data) => { clear(cards.templates.inner); cards.templates.inner.append(...templatesBody(id, data, reloadTemplates)); };
    async function reloadTemplates() {
      const [t, c] = await Promise.all([api('/api/projects/' + id + '/templates'), api('/api/campaigns' + query({ project_id: id }))]);
      if (t.ok) drawTemplates(t);
      if (t.ok && c.ok) drawSearches(c.campaigns, t.choices);
    }
    function drawProgress(s) { clear(cards.progress.inner); cards.progress.inner.append(stepper(s.steps)); }
    function drawResults(r) {
      view.campaignId = r.campaign_id;
      clear(cards.results.inner);
      cards.results.setCount(fmtNum(r.total));
      cards.results.inner.append(...resultsBody(r, view, () => refresh(true)));
    }
    async function refresh(now) { /* as today: GET /studio and /results, then drawProgress and drawResults */ }

    const pane = chatPane(id, chat, { refresh, timeline: tl });
    const right = h('div', { class: 'right' }, ...Object.values(cards));
    const grid = h('div', { class: 'studio-grid', 'data-pane': 'chat' }, pane.node, right);
    const layout = UI.layoutSwitch(grid, 'project', [['chat', 'Chat wide'], ['split', 'Side by side'], ['results', 'Results wide']], 'split');
    const switcher = selectBox('p-switch', (list.ok ? list.projects : []).map((p) => [p.id, p.name]), id, (ev) => go('/p/' + ev.target.value));
    const signs = studio.project.sender_name ? 'Signs as ' + studio.project.sender_name : 'Sign-off not set yet';
    const spent = use.ok && use.project && use.project.turns
      ? 'This project: ' + UI.tokens(use.project.tokens) + ' tokens' + (use.project.cost_usd !== null ? ' · ' + UI.money(use.project.cost_usd) : '') : '';
    const tab = (key, text) => { /* as today */ };
    setMain(
      h('div', { class: 'page-head' }, h('a', { href: '#/home', text: 'All projects' }), h('h1', { text: studio.project.name }),
        h('label', { class: 'sr-only', for: 'p-switch', text: 'Switch project' }), switcher,
        h('span', { class: 'chip-static', text: signs }), spent ? h('span', { class: 'chip-static', text: spent }) : null,
        layout, UI.collapseAllButton(right)),
      h('div', { class: 'studio-tabs', role: 'tablist' }, tab('chat', 'Chat'), tab('results', 'Progress and results')),
      grid);
    drawProgress(studio);
    if (tpl.ok) drawTemplates(tpl);
    if (tpl.ok && camps.ok) drawSearches(camps.campaigns, tpl.choices);
    live = { close() { dead = true; clearTimeout(timer); pane.close(); } };
    refresh(true);
  }
```

6. Version `1.3.0` in `src/leadgen/__init__.py` and `pyproject.toml` (and any test that asserts the version string).

**Tests:**
- `tests/test_studio_static.py` `test_the_project_page_labels_and_pieces` adds: `"'What the AI is doing'"`, `"UI.timeline"`, `"'Searches and message style'"`, `"'Message templates'"`, `"'Your sign-off'"`, `"'Your job title'"`, `"'Your company'"`, `"'Your product'"`, `"\"Don't contact again\""`, `"'Contacts you already have'"`, `"'Chat wide'"`, `"'Side by side'"`, `"'Results wide'"`, `"'Phone (found, not checked)'"`, `"'New template'"`, `"'Copy into my templates'"`, `"setRangeText"`, `"/template'"`; the existing labels stay (`'Stop'`, `'Ready contacts'`, `'Export and send'`, `'Progress and results'`, ...). `test_every_send_asks_first_and_is_confirmed` still passes unchanged.
- `tests/e2e/test_studio_flow.py`, add after step 5: patch `sender_title` and `sender_company` and read them back from `/studio`; `GET /api/projects/{pid}/templates` lists nine built-ins and the three `auto-*` choices first; create a custom template and see it in `choices` as "Yours: ..."; after the chat turn, `GET /api/usage?project_id={pid}` has `project.turns >= 1`; the stream of the first turn contains a `usage` event before `done`. Seeded project: `POST /api/campaigns/{campaign_id}/template` `{"template_key": "auto-short"}` is ok and `GET /api/campaigns?project_id=` shows it.
- `tests/test_dashboard.py` and `tests/test_studio_static.py` still pass (no `innerHTML`, no URLs, no banned words in the new strings: note "Don't contact again" and "found, not checked" are fine; never write "cooldown", "designation", "confirmed", "evidence" or "reserve" in a visible string).

**Done when:** the suite and ruff pass; the project page shows the chat with the live timeline beside it, every card collapses and remembers its state after a reload, the layout switch works and is remembered, the sign-off and templates can be edited, a search's message style can be chosen, and phones show as found, not checked.

---

## 9. After the chunks (tester and reviewer, not the coder)

1. One real cheap turn on a Claude plan and one resumed turn: confirm the `result` line has `usage`/`modelUsage`, whether `total_cost_usd` is filled on a plan login, that the resumed turn's `cost_usd` is a small positive difference (research open item 4), and whether a `thinking` content block appears with `--effort high` (the `thinking` event is "string only" until then).
2. Owner key checks (research open items 1, 2): one Apollo match on a profile of the owner's own choosing (does a free key work; does `poll_only` without `webhook_url` accept `reveal_phone_number`), one Prospeo and one GetProspect lookup, one Emailable check, one Tavily search with `site:linkedin.com/in`. Record the answers in DECISIONS.
3. Click-through in a real browser, light and dark: collapse/expand every card, reload and see the states kept, switch the layout, switch projects, a full chat turn with the orb, bars and timeline moving, the Usage page and the pill, Connectors grouped, keyboard-only use of the cards and the segmented control, and a narrow window (tabs).

STATUS follow-ups to add at the end: Exa search (request shape unverified); phones in CSV exports; re-verify an existing finder email instead of a new lookup on an explicit re-run; Tavily country filter; finder for people without a company domain.

