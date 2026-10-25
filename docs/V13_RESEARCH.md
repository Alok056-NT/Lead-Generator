# V1.3 research (gathered 2026-10-25)

Every fact has a source URL; all pages were read on 2026-10-25 unless stated. **VERIFIED** = read on the provider's own site or in the provider's own repo. **UNVERIFIED** = third-party page, marketing page, or not found; do not build on it without a live check. Nothing here was tested with a real key or a paid call.

## 1. ElevenLabs UI (look to recreate)

**Licence and scope.** Repo `github.com/elevenlabs/ui`, MIT, "Copyright (c) 2025 Eleven Labs Inc." (https://raw.githubusercontent.com/elevenlabs/ui/main/LICENSE.md). Last commit on `main` 2026-10-16 (https://github.com/elevenlabs/ui/commits/main.atom). The code is React + Tailwind v4 + shadcn/ui ("new-york" style, `baseColor: neutral`, lucide icons) (https://raw.githubusercontent.com/elevenlabs/ui/main/apps/www/components.json). We do **not** copy code: we recreate the look in plain HTML/CSS/JS. MIT would permit copying with the notice, but the components need React, three.js and motion, so copying is moot. Do not use ElevenLabs logos or the "Waldenburg" brand font (it is a local font file in their repo, `apps/www/lib/fonts.ts`; its licence is not stated; use the system font stack). The docs site (ui.elevenlabs.io) blocks scripted fetches, so everything below comes from the GitHub raw files under `apps/www/` (registry.json, styles/globals.css, registry/elevenlabs-ui/ui/*.tsx), read 2026-10-25.

### 1.1 Components relevant to us

| Component (file in `registry/elevenlabs-ui/ui/`) | What it is | What we recreate |
|---|---|---|
| `conversation` | Scrolling chat log (`role="log"`), sticks to bottom with smooth scroll, content padding `p-4`, empty state (icon, title `text-sm font-medium`, muted description, centred, `gap-3 p-8`), round "scroll to bottom" button (white, `shadow-md`, arrow-down icon) shown only when not at bottom | Chat pane, empty state, scroll-to-bottom pill |
| `message` | `Message from="user"|"assistant"`, row `py-4 gap-2`; variant **contained**: bubble `max-w-[80%] px-4 py-3 rounded-lg text-sm`, user = primary (near-black) bg with white text, assistant = `secondary` (light grey) bg; variant **flat**: assistant text has no bubble, user bubble is grey; optional 32px avatar with 1px ring | Chat bubbles. Prefer the **flat** variant for the AI (calmer) |
| `response` | Streamed markdown (Streamdown), first child no top margin, last no bottom margin | Our existing markdown renderer |
| `shimmering-text` | Text with a light sweep moving across it while "working" (see 1.4) | "Thinking..." / current step label |
| `bar-visualizer` | Row of rounded bars in a muted box, states `connecting`, `initializing`, `listening`, `thinking`, `speaking`, `null` (see 1.4) | Live "AI is working" strip beside the chat |
| `orb` | WebGL sphere in soft blue-grey with flowing noise, agent states `null`, `thinking`, `listening`, `talking` (see 1.4) | CSS-only orb as the AI's status avatar |
| `live-waveform`, `waveform`, `scrub-bar`, `audio-player`, `mic-selector`, `speech-input`, `voice-*`, `transcript-viewer` | Audio/voice only | Skip (no voice) |
| `conversation-bar` | A `Card` with `shadow-lg`, row of waveform + mute + send/end buttons, textarea that expands (height transition 300ms ease-out) with a round up-arrow send button | Chat input bar: card with growing textarea and round send button |
| shadcn base used by them: `card`, `button`, `tabs`, `progress`, `collapsible` (Radix), `separator`, `avatar` | See 1.3 | Cards, buttons, tabs/segmented control, progress bar, collapse |

Source URLs: registry list https://raw.githubusercontent.com/elevenlabs/ui/main/apps/www/registry.json ; each file at `https://raw.githubusercontent.com/elevenlabs/ui/main/apps/www/registry/elevenlabs-ui/ui/<name>.tsx`.

### 1.2 Design tokens (from `apps/www/styles/globals.css`; oklch converted to sRGB hex by me)

The palette is pure neutral grey (zero chroma), no brand colour. Radius base `--radius: 0.625rem` (10px). The only chroma tokens are destructive red and chart blues.

| Token | Light | Dark |
|---|---|---|
| background | `#ffffff` | `#0a0a0a` |
| foreground | `#0a0a0a` | `#fafafa` |
| card / card-foreground | `#ffffff` / `#0a0a0a` | `#171717` / `#fafafa` |
| popover | `#ffffff` | `#262626` |
| primary / primary-foreground | `#171717` / `#fafafa` | `#e5e5e5` / `#171717` |
| secondary = muted = accent (light) | `#f5f5f5` | secondary/muted `#262626`, accent `#404040` |
| secondary-foreground | `#171717` | `#fafafa` |
| muted-foreground | `#737373` | `#a1a1a1` |
| border / input | `#e5e5e5` / `#e5e5e5` | `rgba(255,255,255,.10)` / `rgba(255,255,255,.15)` |
| ring (focus) | `#a1a1a1` (button focus: 3px ring at 50% alpha) | `#737373` |
| destructive | `#e7000b` | `#ff6467` |
| surface (code/boxes) | `#f8f8f8` | `#161616` |
| sidebar | `#fafafa`, border `#e5e5e5` | `#171717`, border `rgba(255,255,255,.10)` |
| selection | bg `#0a0a0a` / text `#ffffff` | bg `#e5e5e5` / text `#171717` |
| charts | Tailwind blue 300/500/600/700/800 | same |
| orb colours (default prop) | `#CADCFC`, `#A0B9D1` | same |

| Other token | Value (source) |
|---|---|
| Radii | `--radius` 10px; sm = 6px, md = 8px, lg = 10px, xl = 14px (globals.css `@theme`). Card = xl (14px); button/tab trigger = md (8px); tab list and message bubble = lg (10px); progress and round buttons = full |
| Spacing | Tailwind 4px unit. Card: `py-6` (24px) `px-6`, `gap-6`; card header `gap-1.5` (6px); message row `py-4` (16px), `gap-2` (8px), bubble `px-4 py-3` (16/12px); conversation `p-4`; empty state `p-8`, `gap-3` |
| Shadows (Tailwind defaults, not in repo) | card `shadow-sm` = `0 1px 3px 0 rgb(0 0 0/.1), 0 1px 2px -1px rgb(0 0 0/.1)`; outline button `shadow-xs` = `0 1px 2px 0 rgb(0 0 0/.05)`; scroll pill `shadow-md` = `0 4px 6px -1px rgb(0 0 0/.1), 0 2px 4px -2px rgb(0 0 0/.1)`; conversation-bar `shadow-lg` = `0 10px 15px -3px rgb(0 0 0/.1), 0 4px 6px -4px rgb(0 0 0/.1)` |
| Type | Body font Geist (Google font) with Geist Mono, `text-sm` (14px) for chat and buttons, buttons `font-medium` (500), card title `font-semibold`, `leading-none`; `text-rendering: optimizeLegibility`, `font-synthesis-weight: none`. We have no CDN, so use the system stack already in `studio.css` (SF Pro / Segoe UI Variable) |
| Motion keyframes (globals.css) | `fade-in` 0.6s `cubic-bezier(0.4,0,0.2,1)` from opacity 0, scale .95, translateY 10px; `fade-in-up` 0.5s from translateY 20px; `fade-in-scale` 0.4s from scale .9; `fade-in-fast` 0.3s; `fade-in-slow` 1s. Buttons `transition-all` (150ms default); bar `transition-all duration-150`; opacity fades 300ms; expanding bar 300ms `ease-out`; icon swap 200ms `cubic-bezier(0.22,1,0.36,1)` with a 50ms-delayed overshoot `cubic-bezier(0.34,1.56,0.64,1)`; active press on touch `opacity .6` |
| Focus | `outline: none`, border colour = ring, plus 3px ring `ring/50` |

### 1.3 How the base components look and behave

| Component | Look | Behaviour |
|---|---|---|
| Card | White (dark `#171717`), 1px border, 14px radius, `shadow-sm`, 24px vertical padding, 24px side padding, 24px gap between sections; header is a two-row grid with title (semibold) over muted description and an optional action cell on the right | Static. Header action is where a collapse chevron goes |
| Button | Height 36px (sm 32, lg 40, icon 36x36), 8px radius, 14px/500 text; default = near-black fill, white text, hover 90% opacity; outline = border + white fill + `shadow-xs`, hover grey fill; secondary = light-grey fill; ghost = transparent, grey on hover; destructive = red; disabled = 50% opacity, no pointer events | 3px focus ring |
| Tabs | Segmented control: muted track (36px high, 10px radius, 3px inner padding); active trigger = white pill with `shadow-sm` (dark: `input/30` fill + border); 14px/500 text | Cross-fades on switch |
| Progress | 8px high track `primary/20`, fully rounded; fill = primary, moved with `transform: translateX(-(100-v)%)`, `transition-all` | Smooth fill |
| Collapsible | Radix primitive: headless open/close wrapper (no styling of its own) | We add chevron rotation and a height transition |
| Message | See 1.1 | Streams in; list sticks to bottom |

### 1.4 The three "alive" visuals

| Visual | Look | States and numbers (from source) | Plain-CSS recreation |
|---|---|---|---|
| **Bar visualizer** (`bar-visualizer.tsx`) | A 128px-high box (`bg-muted`, 10px radius, 16px padding) holding 15 vertical rounded bars (6px gap, each 8 to 12px wide, `flex-1`), bars grow/shrink in height, highlighted bars are full colour, others faded | `thinking`: one highlight moves along the row every **150ms**, highlighted bar pulses (`animate-pulse` at 300ms); `listening`: step every **500ms**; `connecting`/`initializing`: sweep over **2000ms** total (2000/bars per step); `speaking`: all bars `bg-primary` and heights follow volume; otherwise step 1000ms. Bar height change `transition-all 150ms` | N `<i>` bars, CSS keyframes with `animation-delay: i*Xms`; state switched with `data-state` on the container. Use it as the "AI is working" strip |
| **Orb** (`orb.tsx`) | A soft pastel-blue sphere (colours `#CADCFC` and `#A0B9D1`) with drifting noise texture and a rim glow; calm when idle, ripples faster when "talking" | `agentState` `null` (idle, output .3), `thinking` (slow wander ~.38 in, .48 out), `listening` (in 0.55 oscillating at 3.2 rad/s), `talking` (out 0.75 oscillating at 3.6). Colours ease to the target with lerp .08, volumes with lerp .2; animation speed = `0.1 + (1-(out-1)^2)*0.9`, eased .12. Needs three.js and a noise PNG from a Google CDN | CSS only: a circle with two stacked `radial-gradient`s in those colours, `filter: blur(.5px)`, a slow `transform: rotate/scale` keyframe (idle 8s, thinking 3s, working 1.4s), soft `box-shadow: 0 0 40px` glow. `@media (prefers-reduced-motion)` = static |
| **Shimmering text** (`shimmering-text.tsx`) | Grey text with a bright band sweeping across it, left to right | Duration **2s**, linear, repeat with **0.5s** pause, fades in over 0.3s, band width = text length x 2px, `background-size: 250% 100%`, base colour = muted-foreground, band colour = foreground | `background: linear-gradient(90deg, transparent calc(50% - S), var(--fg), transparent calc(50% + S)) , linear-gradient(var(--muted), var(--muted)); background-clip: text; color: transparent;` animate `background-position 100% -> 0%` |

### 1.5 Mapping to what exists

`src/leadgen/dashboard/static/studio.css` (13 KB) already defines an Apple-like token set (`--bg #f5f5f7`, `--accent #0071e3`, `--r-sm/md/lg` 10/14/20px, `--ease cubic-bezier(.4,0,.2,1)`, `--t-fast 150ms`, `--t 220ms`, light and dark). ElevenLabs is **neutral grey with no accent colour and flat white cards**. Suggested split for the designer: keep our blue accent for the single primary action only; adopt their neutral surfaces (`#fff`/`#0a0a0a` backgrounds, `#f5f5f7`-ish becomes `#f5f5f5`), 14px card radius, 24px card padding, `shadow-sm`, hairline 1px borders, 14px base text, 36px controls, the 150/200/300ms motion set, fade-in-up entry animations (0.4 to 0.5s), and the three visuals above.

## 2. Flow patterns: onboarding, home, workspace

Sources read on 2026-10-25.

| # | Pattern | Evidence | Rule for us |
|---|---|---|---|
| 1 | **Account level vs workspace level vs personal**: Linear has a workspace ("home for all data in an organization"), teams under it, and personal preferences; each workspace has separate members and billing (https://linear.app/docs/workspaces). Notion: plans apply to one workspace, workspace switcher at the sidebar (https://www.notion.com/help/intro-to-workspaces). Vercel: global CLI/user settings "apply across all projects", project settings are per project (https://vercel.com/docs/projects/project-configuration, updated 2026-08-25) | Three scopes, never mixed on one screen | **App scope** (once per install): Connect your AI, Connectors (all keys), Usage. **Project scope**: product/content, sender identity, contact memory and cooldown, old contacts, campaigns, templates, results. A setting appears in exactly one scope |
| 2 | **Projects as self-contained workspaces**: a Claude project has "its own chat histories and knowledge bases" and its own instructions, applied only inside that project (https://support.claude.com/en/articles/9517075-what-are-projects) | Per-project context, account-level auth | Everything the AI needs to know about a campaign lives in the project; the chat is per project; Connect-your-AI is not repeated per project |
| 3 | **Progressive disclosure**: show the few options most users need, defer the rest behind a clearly labelled action; "designs that go beyond 2 disclosure levels typically have low usability"; staged wizards only for distinct, loosely linked steps (https://www.nngroup.com/articles/progressive-disclosure/, 2006-12-03) | Two levels max | Home: project cards + one button. Project page: chat + progress first; everything else in collapsed cards ("Advanced", old contacts, templates). No panel inside a collapsed panel inside a collapsed panel |
| 4 | **Do not front-load setup; ask in context**: Apple HIG onboarding guidance (page text was thin when fetched; the four principles listed in the page summary are fast, teach by doing, ask permissions in context, avoid front-loading setup) (https://developer.apple.com/design/human-interface-guidelines/onboarding). **UNVERIFIED wording**; treat as a principle, not a quote | One-time setup only when first needed | First run goes straight to a single "Connect your AI" step, nothing else is mandatory. Connectors are optional and prompted at the moment a task needs one ("To find emails, add a key for Apollo, Prospeo or GetProspect"), with a link, not a wizard |
| 5 | **Remember disclosure state per control**: collapsed by default for rarely needed content, remembered when users reopen the same section (Apple SwiftUI `DisclosureGroup` with persisted state, https://developer.apple.com/design/human-interface-guidelines/disclosure-controls; page text thin, **UNVERIFIED wording**) | Per-card state | Store open/closed per card in `localStorage` under a key like `lg.card.<screen>.<card>`; defaults: primary card open, everything else collapsed |

### 2.1 Concrete rules for our screens

1. **Routes**: `#/connect` (first run only, then reachable from the top bar), `#/home` (project list), `#/p/<id>` (workspace), `#/settings` (app scope: Connect your AI, Connectors, Usage as three collapsible cards or three tabs), `#/advanced`. No project data on app-scope screens; no keys on project screens.
2. **First run**: if no AI connection, land on `#/connect`; after success go to `#/home` with the "New project" card focused. If no connectors are configured, home shows a quiet one-line banner, not a blocker.
3. **Top bar** (frosted, existing) = project switcher (name, chevron) + Settings + Usage meter pill. The project switcher replaces a back button.
4. **Home**: grid of project cards (name, product one-liner, counts, last activity) plus "New project". Delete lives behind the card's menu, never on the card face.
5. **Project page**: one header row (name, sender identity chip, model/effort chip) and a layout switcher (Chat wide / Results wide / Side by side). Left = chat with the live process strip; right = stack of collapsible cards: Progress, Results, Campaigns, Templates, Sender identity, Product/content, Contact memory, Old contacts. First two open, rest collapsed.
6. **Empty states** explain the next action in one sentence and one button (ElevenLabs `ConversationEmptyState` pattern).
7. **Minimal scrolling**: page does not scroll; each pane scrolls inside itself; card bodies have a max height with their own scroll; collapse all is one click.
8. **Spend gates**: anything that costs connector credits shows the cost estimate and needs one explicit click (brief item 3).

## 3. Connectors

Legend: all endpoints are official docs unless flagged. "Phone" columns matter because the brief says phones are "found, not verified".

### 3.1 LinkedIn URL to email and phone

| Provider | Endpoint, auth | Input | Output fields | Credits | Free allowance and API on free | Rate limit | Status |
|---|---|---|---|---|---|---|---|
| **Apollo** people match / enrichment (https://docs.apollo.io/reference/people-enrichment) | `POST https://api.apollo.io/api/v1/people/match`, header `x-api-key: <key>`. Key: Settings > Integrations > API Keys > Create new key; scoped keys by default (a call to an unselected endpoint gives 403), optional "master key" (https://docs.apollo.io/docs/create-api-key) | Query params (not a JSON body): `linkedin_url`, or `first_name`+`last_name`+`organization_name`/`domain`, or `email`, or `id`. Flags: `reveal_personal_emails` (default false; not revealed for GDPR-region persons), `reveal_phone_number` (default false; **requires `webhook_url`**), `run_waterfall_email/phone` (default false), `poll_only` (default false; **400 `WEBHOOK_URL_WITH_POLL_ONLY` if combined with `webhook_url`**) | `person.email`, `person.email_status` (e.g. "verified"), `person.contact.contact_emails[]` (`email`, `email_status`), `person.contact.phone_numbers[]` (`raw_number`, `sanitized_number`, `status`, `type`, `position`, DNC fields), `person.match_confidence` (high/medium/low/none), `request_id` (integer) | People enrichment "1-9 credits per person, charged only if credit-consuming data is found": 1 for demographics or email, **+8 if a mobile phone is returned**, 0 if nothing found; no demographic credit when `match_confidence` is `none`; waterfall email typically 1-4 (can exceed 20), waterfall phone typically 8-25 (can exceed 45). Poll endpoint costs 0 (https://docs.apollo.io/docs/api-pricing, https://docs.apollo.io/reference/poll-webhook-result) | Apollo's marketing page says "Free to start. All plans include API access." (https://www.apollo.io/lp/api) **UNVERIFIED in the API docs**, which say "Access to Apollo API depends on your Apollo plan" and never name the free plan. Free-plan credit amounts are **UNVERIFIED** (third-party: 5 mobile and 10 export credits a month, plus email credits that depend on a verified corporate domain; a 2026 source says Apollo moved to a unified credit pool) | Free plan: enrichment endpoints **50/min, 200/hour, 600/day** (bulk: 20/min, 100/hour); paid plans 1,000/min, no hourly/daily cap. Headers `x-rate-limit-minute`, `x-minute-requests-left`, `x-24-hour-requests-left`, `retry-after` on 429 (https://docs.apollo.io/reference/rate-limits) | Email path: VERIFIED. Free-plan access: UNVERIFIED, test with the owner's key and show "Apollo said no" gracefully |
| **Apollo phone is asynchronous** | Synchronous reply carries the person and email; phones are "sent to the webhook asynchronously", "it can take several minutes". **A local app has no public URL, so use `poll_only=true` (omit `webhook_url`) and poll `GET https://api.apollo.io/api/v1/webhook_result/{request_id}`** (header `x-api-key`, 0 credits) | `request_id` is a signed 64-bit integer (negative is valid) | 200 ready: `webhook_result.people[].phone_numbers[]` with `raw_number`, `sanitized_number`, `status_cd` (e.g. `valid_number`, `no_status`), `type_cd`, `confidence_cd`, `direct_dial_source_cd`, `dnc_status_cd`. `webhook_status` in_progress/success/failed. 404 `result_pending` carries `retry_after_seconds` (retryable); 404 `request_id_unknown` and 410 `request_id_expired` (older than 30 days) are terminal; 400 `invalid_request_id` | 0 credits to poll | | | VERIFIED. Design consequence: phone reveal is a two-step "request now, collect later" job that must survive a restart; show it as "phone requested, waiting" and never block the chat |
| **Prospeo** Enrich Person (https://prospeo.io/api-docs/enrich-person) | `POST https://api.prospeo.io/enrich-person`, headers `X-KEY: <key>`, `Content-Type: application/json` | JSON `{"data": {"linkedin_url": "...", ...}, "only_verified_email": bool, "enrich_mobile": bool, "only_verified_mobile": bool}` (`only_verified_mobile` implies `enrich_mobile`). `linkedin_url` alone is a valid match. Other `data` keys: `first_name`, `last_name`, `full_name`, `email`, `company_name`, `company_website`, `company_linkedin_url`, `person_id` | `person.email`: `status`, `revealed`, `email`, `verification_method`, `email_mx_provider`. `person.mobile`: `status`, `revealed`, `mobile`, `mobile_national`, `mobile_international`, `mobile_country`, `mobile_country_code` (masked like `+1 415-3**-****` when `revealed` is false). `free_enrichment` flag says whether you were charged. **Synchronous** | **1 credit per email found, 10 per mobile found** (email included with a mobile); nothing if not found; re-enriching the same record within 90 days is free | "Free API access for all users" (https://prospeo.io/api-docs). Free-plan credit amount is **UNVERIFIED** (third-party sources say 75 or 100 credits a month, 25-row export cap). The pricing page is JavaScript-rendered and could not be read | Enrich endpoints (rate-limit page lists Starter/Growth/Pro only, not Free): 5/s and 300/min on Starter and Growth, 30/s and 1,800/min on Pro; search 1 to 5/s. 429 on excess (https://prospeo.io/api-docs/rate-limits) | Endpoint, auth, fields: VERIFIED. Free credits and free-plan limits: UNVERIFIED |
| **GetProspect** API v2 (https://getprospect.com/api-docs, `/people-enrich`, `/email-finder`, `/authentication`, `/credits-and-limits`) | Base `https://api.getprospect.com`, header `x-api-key: <key>`, `Content-Type: application/json`. `POST /v2/people/enrich`, `POST /v2/email/find`, `POST /v2/email/verify` | People enrich: `{"data": {"linkedin_url": "..."}, "enrich_email": true (default), "enrich_phone": false (default)}`. Match order: id, linkedin_id, linkedin_url, email, then name + company_domain/company_name. Email find: `{"data": {"first_name","last_name" or "full_name", "domain" or "company"}}`. Set client timeout **at least 95 seconds** (a first email search can take over a minute); a slow search may answer email `status: "searching"` with `retry_after` seconds, or HTTP 408 (not charged) | `data.email`: `status` (valid...), `address`, `verified_at`, `checked_at`, `revealed`; `data.mobile_phone`: `status` ("found"...), `number`, `revealed`; plus name, location, positions, `linkedin_url`. No match: HTTP 200 with `success:false` and `errors[0].name = NOT_FOUND`, free. Email find also returns `domain_status` (accept_all, disposable...) | Matching a person is free. **1 email credit per newly revealed email, 1 phone credit per phone lookup** (separate credit type); not-found and accept-all are free; re-reveal is free; 402 when out of credits with headers `X-Limit-Reached`, `X-Limit-Type` | Pricing page: Free = 50 valid emails and 100 verifications a month, no phone credits on Free (Starter and Growth list 5); "All plans include ... Connect via REST API" (https://getprospect.com/pricing). The pricing page does not name Free explicitly in that line, so API-on-Free is **inferred** | "Regular API keys are not rate-limited today", the limit is credit balance (demo account 1,500/hour) | VERIFIED docs. Cleanest API of the four for our use. Phone reveal appears synchronous but may 408 |
| **Snov.io** v2 (https://snov.io/api) | OAuth client credentials: `POST https://api.snov.io/v1/oauth/access_token` with `grant_type=client_credentials`, `client_id`, `client_secret` returns `access_token` (Bearer, `expires_in` 3600) | LinkedIn profile info: `POST /v2/li-profiles-by-urls/start` (`urls[]` up to 10, optional `webhook_url`), then `GET /v2/li-profiles-by-urls/result?task_hash=` (async). The part of the page I read has **no endpoint that returns an email from a LinkedIn URL** (the rest of the 259 KB page was not fully read) | name, positions, etc. (profile data, not email) | 1 credit per profile; email-by-name+domain 1 credit per email with valid/unknown status | "If you're on a free plan, you can request test access by booking a personal demo" (the API is **not** open on Free); third-party: 50 credits a month, no API on the free tier | 60 requests/minute | **Not usable on a free account. Exclude** |
| Anymail Finder (seen in search, not evaluated) | LinkedIn-URL-to-email endpoint exists, Bearer key, 2 credits per verified email (https://anymailfinder.com/email-finder-api/docs/find-linkedin-url-email) | | | | | | Paid; mention only as a fallback idea |

### 3.2 Search

| Provider | Endpoint, auth | Input | Output | Cost and free allowance | Rate limit | Status |
|---|---|---|---|---|---|---|
| **Tavily** (https://docs.tavily.com/documentation/api-reference/endpoint/search, https://docs.tavily.com/documentation/api-credits) | `POST https://api.tavily.com/search`, `Authorization: Bearer tvly-...` | JSON: `query` (required), `search_depth` (`basic` default, `advanced`, `fast`, `ultra-fast`), `max_results` 0-20 (default 10), `include_domains` (max 300), `exclude_domains` (max 150), `topic`, `time_range`, `country`, `include_answer`, `include_raw_content`, `include_usage` | `results[]` with `title`, `url`, `content` (snippet), `score`, optional `raw_content`, `published_date`; `answer` if requested; `usage.credits`; `response_time`; `request_id` | basic/fast/ultra-fast 1 credit, advanced 2; `auto_parameters` costs 2. **1,000 free credits every month, no card** (api-credits page). Extract: 1 credit per 5 URLs | Not stated on the pages read | VERIFIED. General web search, so it can use `include_domains: ["linkedin.com"]` for "site:" style queries, but result quality for LinkedIn profiles is untested |
| **Serper** (already in repo, DECISIONS rows T04) | `POST https://google.serper.dev/search`, `X-API-KEY` | `q`, `gl`, `num`, `page` | `organic[]` | Home page 2026-10-25: "Get 2,500 free queries", "No credit card required" (monthly vs one-time not stated) | | existing |
| **Brave** (already in repo) | `GET https://api.search.brave.com/res/v1/web/search`, `X-Subscription-Token` | `q`, `country`, `count` | | Plans page 2026-10-25: Search $5 per 1,000 requests, "Includes $5 in free credits every month" (home page says "up to $20", mismatch, confirm), 50 queries/s; a card is asked for ("to prevent abuse") | 50 q/s | existing; **not free for most owners** (card) |
| **Exa** (https://exa.ai/pricing) | `POST https://api.exa.ai/search` (endpoint from memory, **UNVERIFIED**; fetch https://docs.exa.ai before use) | | | Free tier: "$10 in credits (up to 2,500 Lite searches)", resets to $10 on the first of each month, no payment method. `/search` lite $4 per 1k, auto/fast $7 per 1k | not stated | Pricing VERIFIED; request shape UNVERIFIED. A candidate for "free search" |
| Google Custom Search JSON API | | | | 100 queries a day free, **closed to new customers**, ends 2027-01-01 (https://developers.google.com/custom-search/v1/overview) | | **Do not use** |

### 3.3 Email verification

| Provider | Endpoint, auth | Input | Output | Cost / free | Rate limit | Status |
|---|---|---|---|---|---|---|
| **Emailable** (https://emailable.com/docs/api/emails.md, `/authentication.md`, `/rate-limits.md`) | `GET https://api.emailable.com/v1/verify`, auth `Authorization: Bearer <key>` (preferred; a `api_key` query param also works but puts the key in URLs and logs). Test keys start `test_` and return simulated responses with no credits used | `email` (required); optional `smtp` (default true), `accept_all` (default false; slows the call), `timeout` 2-10s (default 5) | `state` (`deliverable`, `undeliverable`, `risky`, `unknown`), `reason`, `score` (0-100), `accept_all`, `disposable`, `free`, `role`, `no_reply`, `mailbox_full`, `did_you_mean`, `domain`, `user`, `mx_record`, `smtp_provider`, `duration`. HTTP **249** means "taking longer than normal, send again" (free to retry for 5 minutes); 429 "Rate Limit Exceeded" | 1 credit per verification. **250 free credits at sign-up, one-time, no card** and credits never expire per Capterra/G2 listings (**UNVERIFIED**, third-party) | `/verify` 25/second standard | Docs VERIFIED, free allowance UNVERIFIED. Map `deliverable` to verified; `risky`/`unknown`/`accept_all` to not verified |
| Reoon, ZeroBounce, Hunter | already in repo (Reoon ~20 daily credits + 100 at sign-up; Hunter 50/month; see DECISIONS T09) | | | | | existing |
| GetProspect `POST /v2/email/verify` | same key as above; 1 verifier credit per verification; free plan includes 100 verifications a month (pricing page) | `{"data": {"email": "..."}}` | | | | VERIFIED on the auth page example, response fields not read |

### 3.4 Segment summary for the Connectors page

| Segment | Offer | Needs card? | Notes |
|---|---|---|---|
| Search | Serper (2,500 free), Tavily (1,000/month), Exa ($10/month credits, UNVERIFIED request shape), Brave (card) | Serper, Tavily, Exa no | Add Tavily as the easiest new free key |
| Email and phone from a LinkedIn profile | Apollo (credits + async phone), Prospeo (sync, 10 credits for a mobile), GetProspect (sync, separate email/phone credits, ~95 s timeout). Snov excluded | Apollo, Prospeo, GetProspect sign-up without card (UNVERIFIED for Prospeo) | One internal interface `find_contact(linkedin_url, want_phone) -> {email?, phone?, source, status}`; phones always labelled "found, not verified" |
| Verification | Reoon, ZeroBounce, Emailable (new), GetProspect verifier | | Any email from the finders above still goes through verification before it counts (brief item 3) |
| Sending | mailbox, lemlist | | existing |

Cost-prompt rule: Apollo and Prospeo charge only when data is found, so show "up to N credits" (Apollo 1 or 9, Prospeo 1 or 10, GetProspect 1 email, +1 phone) and ask once per batch, with a hard cap the user can set.

## 4. Claude Code usage data (stream-json)

Read on 2026-10-25 from https://code.claude.com/docs/en/headless, https://code.claude.com/docs/en/agent-sdk/cost-tracking and https://code.claude.com/docs/en/agent-sdk/typescript.md (types `SDKResultMessage`, `SDKSystemMessage`, `ModelUsage`, `Usage`). Installed CLI: `claude --version` = 2.1.310. `claude --help` confirms `--output-format stream-json` (requires `--print`), `--verbose`, `--include-partial-messages`, `--effort <low|medium|high|xhigh|max>`, `--model`, `--max-budget-usd`, `--session-id`, `--no-session-persistence`. No paid turn was run. The existing `chat.py` already passes `--output-format stream-json --verbose --include-partial-messages`, and DECISIONS (2026-10-25 row) already lists `result` with `total_cost_usd`.

### 4.1 The `result` event (last line of the stream)

| Field | Type | Meaning |
|---|---|---|
| `type` | `"result"` | |
| `subtype` | `"success"` or `"error_max_turns"`, `"error_during_execution"`, `"error_max_budget_usd"`, `"error_max_structured_output_retries"` | |
| `is_error`, `api_error_status?` | bool, number/null | |
| `result` | string (success only) | final text |
| `errors` | string[] (error subtypes) | |
| `session_id`, `uuid` | string | |
| `duration_ms`, `duration_api_ms`, `num_turns` | number | |
| `stop_reason`, `terminal_reason?` | string/null | |
| **`total_cost_usd`** | number | **client-side estimate** from a bundled price table, "not authoritative billing data". Includes subagents. **On a resumed session it is the session's whole spend**, including earlier runs (since v2.1.277) |
| **`usage`** | object | `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `cache_creation{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}`, `server_tool_use`, `service_tier`, `speed`, `inference_geo`, `iterations`, `output_tokens_details{thinking_tokens}`, `fallback_credit`. **Excludes subagent tokens** (undercounts when the AI spawns subagents). On error results it can be lower than real spend (`error_max_budget_usd` leaves out the response that crossed the budget; after a crash it may be zeroed) |
| **`modelUsage`** | `{ "<model id>": ModelUsage }` | per model: `inputTokens`, `outputTokens` (already includes thinking), `thinkingTokens?`, `cacheReadInputTokens`, `cacheCreationInputTokens`, `webSearchRequests`, `costUSD`, `contextWindow`, `maxOutputTokens`, `canonicalModel?`, `provider?`, `costBasis?` (`list`/`managed`/`unknown`). Includes subagents; the docs recommend it for whole-tree accounting |
| `permission_denials` | array | |
| `structured_output?` | any | |

### 4.2 Other events that matter

| Event | Fields | Use |
|---|---|---|
| `system` / `init` | `session_id`, **`model`** (string), `claude_code_version`, `tools`, `mcp_servers[{name,status}]`, `permissionMode`, `apiKeySource`, `cwd`, `skills`, `plugins`, `effort?` (**but "Claude Code sets the field only on the init message it sends to Remote Control clients, and omits it from the init message your application reads"**) | Show the **model actually used** from `init.model`; show **effort from our own `--effort` argument**, not from the stream |
| `stream_event` with `event.type == "message_delta"` | `usage` with a growing real `output_tokens` | Live token counter while a response streams (needs `--include-partial-messages`) |
| `assistant` | `message.id`, `message.usage` | Per-step input and cache tokens are accurate once per unique `message.id` (parallel tool calls repeat the same id); **per-step `output_tokens` is a placeholder from `message_start`, do not use it** |
| `system` / `api_retry` | `attempt`, `max_retries`, `retry_delay_ms`, `error_status`, `error` | Already handled by Mapper per DECISIONS |
| `rate_limit_event` | `rate_limit_info.status` | Already in DECISIONS (seen in executable strings) |

### 4.3 Rules for the Usage meter

1. Tokens per chat turn: read `result.usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`); add `modelUsage` totals if subagents might be used (our chat runs the main agent only, so `usage` suffices; prefer `modelUsage` sums when they differ).
2. Cost per turn: **do not sum `total_cost_usd` across resumed turns** (it is cumulative for the session). Store the previous cumulative value per `session_id` and record the **difference**; if our first turn in a session starts at 0 this is simply the value. This is from the documentation ("Read the latest result for the session total; summing results double-counts"); the exact behaviour of `usage` (per turn or cumulative) on `--resume` is **UNVERIFIED**, so the tester should run one cheap real turn and a resumed turn and compare.
3. Label every cost "estimate". On a Claude **subscription** (not API key) the field may still be present but is not what the owner is billed; whether it is populated on subscription logins is **UNVERIFIED**; the UI must tolerate `null`/0 and show tokens only.
4. Per project and per day: store one row per turn `(project_id, session_id, ts, model, input, output, cache_read, cache_create, cost_delta_usd, is_error)` in the app database; "today" = local-date sum. Failed turns still carry cost, so record error results too.
5. Cache tokens are billed at different rates; show them separately in the detail view, collapsed.
6. Pass `--max-budget-usd` as an optional safety cap (counts only the call's own spend, not restored session totals).

## 5. Open items for the architect and tester

| # | Open item | Why | Suggested owner |
|---|---|---|---|
| 1 | Does a free Apollo account's API key work for `/people/match` and how many credits/mobile credits does it have? | Marketing says all plans, docs silent | Owner tries once with their key; show a clear message on 403 |
| 2 | Prospeo free-plan credits and whether `enrich-person` works on Free | Docs say "Free API access for all users", amount unknown | Owner key test |
| 3 | Exa request shape and auth | Not read from docs.exa.ai | Builder must read docs before coding, or skip Exa |
| 4 | `result.usage` semantics on `--resume`, and `total_cost_usd` on a subscription login | Needed for the per-turn meter | Tester: one real cheap turn + one resumed turn |
| 5 | GetProspect response field names for `email.status` values and the 95 s timeout vs our synchronous service rule | Must be chunked with a `cursor` (CLAUDE.md) | Architect: treat a finder call as a background job polled by the page |
| 6 | Snov.io LinkedIn-to-email endpoint | Not found in the 100 KB read; free tier has no API anyway | Dropped |
| 7 | Whether to use Geist (not available without CDN) | No CDN rule | Use the system stack |
