# Lead Generator: free / open-source alternatives report

Research date: 2026-10-24. Every figure below was read from the page linked next to it unless marked **UNVERIFIED** or **could not verify**. "Verified" means I read it on an official vendor/GitHub/docs page during this research. Where only blogs or review sites said something, I say so. Nothing was installed or run.

Note on method: pages were read through a summarising tool, so exact wording can be off. Anything that affects money or a sign-up should be re-checked on the live page before the owner commits.

---

## Executive summary (recommendation per area)

1. **Tunnel (ChatGPT to local MCP):** KEEP Cloudflare quick tunnel as default (free, no account, but no uptime guarantee and the URL changes every run). ADD ngrok free as a fallback option: it gives you one fixed address, so you do not have to re-paste a new URL into ChatGPT each time. Cap: 1 GB/month and 20,000 requests/month, which is plenty for this use.
2. **Tunnel, if a fixed address matters most:** Microsoft Dev Tunnels (fixed URL, 5 GB/month, needs a Microsoft or GitHub login) is the cleanest free alternative, but it is still "public preview, no SLA".
3. **Search:** KEEP Serper as primary while the 2,500 free searches last. DO NOT plan around Google Custom Search API: it is closed to new customers and shuts down 1 January 2027.
4. **Search, backup:** KEEP Brave as the backup but note it is now effectively "about 1,000 searches a month free credit, card required, and an attribution condition" (see section 2). ADD Tavily (1,000 free credits/month, no card) as a second free backup. Mojeek and Exa are cheap pay-as-you-go extras, not truly free.
5. **Search, self-hosted:** SearXNG is free and open source but is the highest-effort option (needs Docker/WSL, public engines may block it). Only worth it if the owner wants zero per-search cost and has a helper to set it up.
6. **Email finding:** KEEP Hunter free (50 credits/month). ADD Apollo free, Prospeo free (75/month, card required) and GetProspect free (50 valid emails/month) as extra free pools. Self-made pattern guessing plus verification is free and already possible in code. theHarvester is NOT a good fit (it finds emails by company domain, not by named person).
7. **Email verification:** KEEP Reoon (20/day plus 100 signup) and ZeroBounce (100/month). ADD Emailable (250 one-time), MillionVerifier (100-500 trial depending on page; see section 4). Reacher is free and open source but needs outbound port 25 and is unrealistic on a home PC for a non-technical user.
8. **Own contacts:** USE LinkedIn "Get a copy of your data" (Connections.csv). Expect most email cells to be EMPTY. Combine with Google Contacts, Outlook, HubSpot and lemlist exports. Details and steps in section 5.
9. **Sending:** KEEP own mailbox via SMTP plus lemlist for LinkedIn. Mautic / listmonk are free but are newsletter/marketing-automation tools meant for people who opted in. They add compliance risk for cold outreach and need a server and database. Not recommended.
10. **Biggest risk to flag:** Gmail personal accounts are capped at 500 emails/day and bulk senders (over 5,000/day) must follow strict rules; high bounce/spam rates damage the owner's mailbox. Verifying emails before sending matters more than which tool does it.

---

## 1. Public HTTPS tunnel (so ChatGPT can reach the local MCP server)

| Option | Free limit | Licence | Setup effort | Reliability | Risk | Pros | Cons |
|---|---|---|---|---|---|---|---|
| **Cloudflare quick tunnel** (current) | No account. 200 in-flight requests per tunnel (extra get 429). No Server-Sent Events. No uptime guarantee. [1] | cloudflared is open source (licence not checked this session: could not verify) | Low | Medium. Docs say "for testing and development". URL changes every run and dies when cloudflared stops. [1] | Low ToS risk, but Cloudflare says not for production | Free, no signup, already working | Random URL each time (must re-register in ChatGPT); SSE unsupported (matters if MCP uses SSE streaming: check); no SLA |
| **ngrok free** | 1 GB data out/month, 20,000 HTTP requests/month, 4,000 req/min, 3 online endpoints, 1 user. One assigned "development domain" (so a fixed address). No endpoint timeout. [2][3] | Proprietary service (agent is closed source) | Low-Med (account + install + auth token) | High for a free tier. | Low. Browser visitors see a warning page; API/programmatic traffic is not affected and the header `ngrok-skip-browser-warning` bypasses it. [3] | One stable URL; well-known; good inspector | Needs account; monthly quotas; the warning page could confuse a human tester; terms can change |
| **Microsoft Dev Tunnels** | 5 GB/user/month, 10 tunnels, 1,500 HTTP req/min per port, 16 MB max request body. Persistent URL. Unused tunnels deleted after 30 days. [4][5] | Proprietary (Microsoft) | Med (devtunnel CLI + Microsoft or GitHub login; anonymous access must be switched on for ChatGPT to reach it) | Medium. "Public preview", no SLA, "ad hoc testing, not production". [4] | Low | Stable URL; free; Microsoft-run; Windows supported [5] | Preview status; requires creating an anonymous-access rule; CLI is more technical |
| **Tailscale Funnel** | Free "Personal" plan reportedly OK; bandwidth limits exist but are not published. Ports 443/8443/10000 only; URL is `yourname.ts.net`. Funnel is **beta**. [6][7] | Tailscale client open source; service proprietary (licence not checked) | Med-High (install Tailscale, enable HTTPS and MagicDNS, edit access-policy file to allow `funnel`) [6] | Medium-High | Low | Stable URL; strong company | Beta; policy-file editing is awkward for non-technical user; page did not confirm Windows by name, so check |
| **zrok** (OpenZiti) | Free hosted tier: 5 GB/day, 50 shares, 25 environments (pricing page). Free accounts may see a first-visit interstitial. [8] | Apache-2.0 [9] | Med-High (account invite, `zrok enable`, CLI) | Medium (one forum post "public share stopped working" exists; community-run support) | Low | Open source, can self-host; Windows listed as supported [9] | Smaller community; extra concepts (environments, shares) |
| **Pinggy** | Free: 60-minute tunnel timeout, then a new URL. No signup for basic use. [10] | Not stated on site (could not verify) | Low (uses Windows' built-in SSH) | Low-Med because of the 60-minute cut-off | Low | Zero install | URL changes hourly: bad for an MCP connector |
| **Serveo** | Free: 3 tunnels, interstitial warning page. SSH-based. [11] | Not stated (could not verify) | Low-Med | Unknown: status page exists, I did not see uptime | Medium (small service, trust issue) | No install | Warning page; small operator |
| **bore** | bore.pub public server gives a TCP port only, no HTTPS URL. [12] | MIT [12] | Med | n/a | Low | Tiny and open source | **Not suitable**: no HTTPS, ChatGPT needs HTTPS |
| **frp** | Free software but you must run `frps` on **your own server with a public IP** (i.e. you rent a VPS). [13] | Apache-2.0 [13] | High | High if you run it well | Low | Very capable, 110k GitHub stars | Needs a server (cost) and sysadmin skills |
| **localtunnel** | Free public server; random URL; last commit seen Aug 2025; third-party reviews call the public server unreliable and lightly maintained. [14][15] | MIT [14] | Low (needs Node.js) | Low | Low-Med | Zero signup | Unreliable, little maintenance: avoid |

**Recommendation.** Keep Cloudflare quick tunnel as the default. The one real pain is the random URL: if the owner is tired of re-pasting it into ChatGPT, add **ngrok free** (fixed dev domain, no timeouts, quotas are far above what an MCP server uses) as the "stable address" option, with **Dev Tunnels** as the no-third-party-company alternative. A named Cloudflare tunnel gives a permanent address too, but I could not confirm the prerequisites on the official page this session; the page indicates you must add a website (domain) to Cloudflare to publish an app, which usually means buying a domain. [16] Avoid bore, localtunnel, Serveo.

---

## 2. Search (finding companies and public LinkedIn profiles)

| Option | Free limit | Licence | Setup effort | Reliability | Risk | Pros | Cons |
|---|---|---|---|---|---|---|---|
| **Serper.dev** (current) | 2,500 free queries, no card (vendor homepage). Expiry of free queries not stated on page. Third-party sites say paid credits expire after 6 months (UNVERIFIED). [17][18] | Proprietary | Low | High | Low-Med: it resells Google results; Google's terms are not Serper's problem to the user, but it is a grey area | Real Google results; cheap | One-time pot, then paid |
| **Google Programmable Search / Custom Search JSON API** | 100 free queries/day, then $5 per 1,000. **Closed to new customers; ends 1 Jan 2027.** Only searches a defined set of sites, not the whole web. [19] | Proprietary | n/a | n/a | n/a | n/a | **Do not use.** Not available to new users and being shut down |
| **Brave Search API** (current backup) | Free tier removed Feb 2026 per a news report; now $5 per 1,000 requests with $5 free credit monthly (~1,000 queries). Card is collected. A news report says the credit requires publicly attributing Brave on a website (UNVERIFIED: Brave's pricing page text was not readable). [20][21] | Proprietary | Low | High (own index) | Low | Own independent index; official API | Card required; possible attribution condition; own index sometimes finds fewer LinkedIn profiles than Google |
| **Tavily** | Researcher plan: free, 1,000 API credits/month, no card. [22] | Proprietary | Low | Medium-High | Low | Built for AI agents; returns summaries and links | Not raw Google results; LinkedIn-specific "site:" style queries may be weaker (not tested) |
| **Exa** | $10 free credits at signup (page says "no payment method required" but also contains a "card required" note: unclear). Search from $4-7 per 1,000. [23] | Proprietary | Low | Medium-High | Low | Semantic search; good for "companies like X" | Credits are one-off, then paid |
| **Mojeek Search API** | Pay-as-you-go: Startup £2 per 1,000 (5 queries/sec, 100,000/day); includes a free credit (size not stated); card required to open account. [24] | Proprietary | Low | Medium (small independent index) | Low | Independent, privacy-focused; allows caching 1 hour | Small index = weak coverage of LinkedIn |
| **SearXNG** (self-hosted metasearch) | Free software; no query limit of its own. [25][26] | AGPL-3.0 [26] | **High** for non-technical Windows user: needs Docker or Podman [25] (Windows typically means Docker Desktop/WSL2: my inference, page does not cover Windows); JSON output must be enabled in settings or the API returns 403 [27] | Low-Med: it just forwards to Google/Bing/etc., which may block or CAPTCHA a home IP (widely reported; not verified on official docs this session) | Low-Med (scraping engines against their terms) | No per-search cost, no key | Setup burden; engines may block; maintenance |
| **DuckDuckGo HTML scraping** (current last resort) | No official search API found; I could not confirm what the Acceptable Use Policy says. [28] | n/a | Low | Low | Medium (against typical terms; may block) | Free | Unreliable, can break any day |
| **Gemini API "Grounding with Google Search"** (optional idea) | Free tier: 500 grounded requests/day on Gemini 2.5 Flash / Flash-Lite; newer models: 5,000 per month but marked paid-only. [29] | Proprietary | Med | Medium | Low | Real Google grounding, free daily allowance | Returns an AI answer with citations, not a raw result list; free-tier terms may let Google use your prompts (not checked) |
| **Microsoft Bing Search API** | Retired 11 Aug 2025. [30] | n/a | n/a | n/a | n/a | n/a | **Gone.** Do not use |

**Recommendation.** Keep Serper first, then Brave. Add **Tavily** as a free second backup (no card). Do not rely on Google Custom Search (closed). SearXNG only if the owner wants a no-limit, no-vendor option and has IT help. The existing "free mode" (AI's own web search) remains a sensible zero-cost fallback.

---

## 3. Email pattern / email finder

| Option | Free limit | Licence | Setup effort | Reliability | Risk | Pros | Cons |
|---|---|---|---|---|---|---|---|
| **Hunter.io** (current) | 50 credits/month (finder, domain search, verifier shared). Find = 1 credit if found. Verify = 0.5 credit. [31] | Proprietary | Low | High | Low | Good pattern data per domain | Small allowance |
| **Apollo.io** | Free plan with no time limit. Credit figures conflict: 75/month (Apollo blog, per search summary), ~100/month and 10 export credits (third-party sites). Treat as **UNVERIFIED**. Free plan only connects Gmail; plans are "internal use only". [32][33] | Proprietary | Low-Med | High database size | Low-Med (data-privacy complaints about Apollo exist generally; GDPR consideration) | Large B2B database; also finds titles | Credit numbers unclear; export limits |
| **Prospeo** | Free plan: 75 credits/month per several third-party sources; official page says free plan exists with email finder + LinkedIn URL finder and **card required**. [34][35] | Proprietary | Low | Medium-High | Low | Finds email from a LinkedIn URL | Card required; allowance unverified on official page |
| **GetProspect** | Free: 50 valid emails and 100 verifications per month; credits roll over up to one month's worth; includes LinkedIn extension and API access. [36] | Proprietary | Low | Medium | Medium if using the LinkedIn browser extension (LinkedIn bans extensions that scrape [37]) | Includes verification; API on free plan | Same extension caveat |
| **Snov.io** | Free "Trial" plan renews monthly: 50 credits shared by finder and verifier, 100 recipients, **no API, no export, no bulk**. [38] | Proprietary | Low | Medium | Low | Cheap paid tiers | API and export blocked on free plan: poor fit for automation |
| **Skrapp** | Free: 50 credits (page does not say whether monthly), no API integration, no list export. [39] | Proprietary | Low | Medium | Low | Easy | No API/export on free tier |
| **theHarvester** (OSINT) | Free software; 60 sources, 33 need API keys. Works from a **domain**, not a named person. [40] | GPL-2.0 [40] | High (needs Python 3.14 + uv; Windows not confirmed on page) | Medium | Low-Med (built for security assessments) | Free; lists addresses that appear publicly for a company | Not a person-level finder; noisy; wrong tool for this job |
| **Own pattern generator + verification** | Free. Build candidate addresses (first.last@, flast@, etc.) then check with MX lookup and a verifier. I found no well-known maintained open-source Python permutator; the generation is simple string work. [41] | n/a (written in-house) | Low (already feasible in the existing Python tool) | Medium: guesses only | Low | No credits spent on finding; fits existing app | Accuracy depends on verification step |

**Recommendation.** Keep Hunter. Add Apollo, Prospeo and GetProspect as extra monthly free pools (the tool can fall through them in order). Use in-house pattern guessing plus verifier for the rest. Do not build around theHarvester, Snov or Skrapp free tiers (no API/export).

---

## 4. Email verification

| Option | Free limit | Licence | Setup effort | Reliability | Risk | Pros | Cons |
|---|---|---|---|---|---|---|---|
| **Reoon** (current) | 20 daily credits + 100 lifetime signup credits, no card (Capterra/Software Advice/Fitgap listings; official page returned 404, so **UNVERIFIED** on vendor site). One listing says free tier is capped at 600/month. Paid: about $12 per 10,000 one-off (listings). [42] | Proprietary | Low | Medium-High | Low | Daily refresh | Small free volume |
| **ZeroBounce** (current) | Freemium: 100 validation credits per month, refilled monthly; needs a business/premium-domain email to sign up; 10 email-finder credits. "Unknown" results cost no credits. Expiry rules may apply without a paid plan. [43] | Proprietary | Low | High | Low | Good accuracy reputation | Rejects free-mail signups (Gmail) per page |
| **Hunter verifier** (current) | 0.5 credit per email from the 50/month pool. [31] | Proprietary | Low | High | Low | Already integrated | Shares pool with finding |
| **Emailable** | 250 credits on every new account; credits never expire. Paid: 5,000 credits $38. [44] | Proprietary | Low | High | Low | Credits never expire; bigger starter pot | One-time |
| **MillionVerifier** | Conflicting: its bulk-verifier page says 100 free credits; its help page says 500 free credits for a business email (max 3 accounts per business domain), or 2,000 for $4.90, or integration credits up to 10,000. Free trial is once only. [45][46] | Proprietary | Low | Medium-High | Low | Very cheap paid (about $449 per 1 million per vendor claim) | Trial rules fiddly |
| **NeverBounce** | Third-party reviews say 1,000 free at signup; ~$8 per 1,000 paid; could not verify on official pages (official URL returned 404). [47] | Proprietary (owned by ZoomInfo per reviews) | Low | High | Low | Established | Free amount **UNVERIFIED** |
| **Reacher / check-if-email-exists** | Free software; no limit. Hosted SaaS exists at No2Bounce.com (limits not stated). [48][49] | **MIT** (README, docs and LICENSE all say MIT) [48][49] | **High**: needs Docker; "outbound port 25 must be open"; docs warn many ISPs and cloud providers block it, and anything beyond very small volumes needs paid SMTP proxies. [48][50] | Medium at best: SMTP probing is blocked or unreliable on big mail providers (general knowledge, UNVERIFIED on official docs) | Low-Med (probing mail servers from a home IP can get that IP blacklisted; the docs themselves discuss proxy reputation) [51] | Free, private, no credits | Home ISPs usually block port 25; not for non-technical users |
| **Truemail** (Ruby) | Free library; checks syntax, MX records, optional SMTP. [52] | MIT [52] | High (Ruby; Windows not covered on page) | Medium | Low | Free | Same port 25 limit; wrong language for this Python tool |

**Recommendation.** Keep the current three. Add **Emailable** (250 never-expire) and **MillionVerifier** as extra one-off pots. A cheap safety net: do free syntax + MX (DNS) checks in-house before spending any verifier credit. Skip Reacher and Truemail unless the owner has a server with open port 25.

---

## 5. Getting contacts the owner already has (free, ToS-safe)

These are all the owner exporting their **own** data through official features. This is the safest category in the whole report.

| Option | What you get | Free? | Steps | Effort | Notes |
|---|---|---|---|---|---|
| **LinkedIn "Get a copy of your data"** | `Connections.csv`: first name, last name, profile URL, company, position, connected-on date, email. **Email is only filled in for connections who allowed it in their privacy settings, so most email cells are empty.** [53][54][55] Also 1st-degree only. | Yes | Me > Settings & Privacy > Data privacy > Get a copy of your data > pick the larger archive/Connections > Request archive. LinkedIn emails a download link, valid 72 hours. Computer only, not mobile. [53][55] | Low | Timing: LinkedIn's help page says picked categories arrive "within 10 minutes" and the larger archive within 24 hours, with some categories up to 48 hours (the page's grouping put Connections.csv in the slower group, which other guides do not say). Plan on up to a day or two. Other files in the archive (invitations, messages) can also contain contact names; filenames vary by guide and **could not be verified** on LinkedIn's own pages. [55][56] |
| **Google Contacts export** | Name, email, phone, etc. | Yes | contacts.google.com > select contacts > More actions > Export > Google CSV or vCard > Export. [57] | Low | Gmail also saves "Other contacts" (people you emailed) which are not in the normal export (my inference from Google's help page; not verified) |
| **Outlook export** | CSV of contacts | Yes | New Outlook: People > Export contacts. Classic: File > Open & Export > Import/Export > Export to a file > CSV > Contacts. Outlook.com: People > Manage contacts > Export contacts. [58] | Low | Save as UTF-8 for non-English names |
| **HubSpot free CRM export** | Any contact properties, CSV/XLS/XLSX | Yes, "all products and plans". Needs export permission. Up to 300 exports/24 hours. Download link expires after 30 days. [59] | CRM > Contacts > Export | Low | Only useful if contacts already live in HubSpot |
| **lemlist export** | Lead list: first/last name, email, company, LinkedIn URL, phone, plus custom variables; or campaign results CSV. [60] | Part of paid lemlist | Open campaign > Lead list > select > Export as .CSV | Low | Official help-centre guides found via search; I did not open them directly |
| **Own sent/received mail (IMAP)** | Names and addresses of people the owner has already emailed | Yes | Already possible via the existing IMAP connection (design suggestion, not a researched product) | Med | Only the owner's own mailbox; consent/GDPR still applies to how contacts are used |

**ToS warning.** LinkedIn's User Agreement bans scraping, crawlers, bots and browser add-ons that copy profile data, and bans automated contact downloads and messages. LinkedIn also says accounts using such tools can be restricted. [61][37] So "LinkedIn contact scraper" extensions and unofficial APIs are NOT ToS-safe, even if cheap. The official data export is.

---

## 6. Sending (only if relevant to cold outreach)

| Option | Free limit | Licence | Setup effort | Reliability | Risk | Pros | Cons |
|---|---|---|---|---|---|---|---|
| **Own mailbox via SMTP/IMAP** (current) | Personal Gmail: 500 emails/day, 500 recipients per message; hitting the limit blocks sending for 1-24 hours; many bounces can also trigger blocks. [62] | n/a | Low | High | Medium: the mailbox is the owner's real identity | Real conversations, best deliverability for small volume | Daily cap; reputation damage hits the personal inbox |
| **Mautic** | Free self-hosted "free forever"; needs Composer/command line, PHP + database. [63] | GPL-3.0 (read from the repo licence file) [64] | **High** | Medium | **Higher**: built for opted-in marketing; hosting and DNS work | Full marketing automation | Heavy, not for cold outreach |
| **listmonk** | Free software; needs PostgreSQL; Docker or Go binary; Windows not mentioned. [65] | AGPL-3.0 [65] | High | High | **Higher**: newsletter tool for opt-in lists, no reply tracking for sales | Fast, simple, one binary | Wrong tool for 1-to-1 outreach |
| **Mailtrain** | Free; v2 rewrite; maintenance dates unclear. [66] | GPL-3.0 [66] | High | Unknown | Same as above | Free | Unclear maintenance |

**Compliance notes (not legal advice).** Google's rules (from 1 Feb 2024): everyone must use SPF or DKIM and keep spam complaints under 0.3 percent (staying under 0.10 percent is recommended); anyone sending over 5,000 a day to Gmail must also have DMARC and one-click unsubscribe; Google advises sending only to people who opted in and to increase volume gradually. [67] Laws such as CAN-SPAM, GDPR and PECR also apply to cold email: I did not research them for this report, so get proper advice if mailing EU/UK contacts.

**Recommendation.** Keep the own-mailbox + lemlist setup. Do not adopt Mautic or listmonk for cold outreach.

---

## Suggested adoption order (cheapest effort first)

1. Run the LinkedIn data export now (zero risk, 15 minutes of work, may take up to a day to arrive).
2. Add Tavily (search backup) and Emailable (verification) free accounts.
3. Decide whether the changing tunnel URL is annoying enough to add ngrok free.
4. Add Apollo / Prospeo / GetProspect free pools to the email-finder fallback chain.
5. Ignore: Google Custom Search, bore, localtunnel, Reacher, Mautic, listmonk, theHarvester.

---

## Things I could not verify (read before relying on this)

- Serper: whether the 2,500 free queries expire, and current paid plans (serper.dev/pricing returned 404).
- Brave: current plan text and whether a card or attribution is a condition (the official pricing page text was not readable; the claims come from a June 2026 news article and third-party sites).
- Apollo and Prospeo: exact monthly free credits (sources conflict).
- Reoon, NeverBounce: official pricing pages returned errors; figures come from review sites.
- Mojeek: size of free credit; the Personal tier shown in search snippets is not on the current page.
- Tailscale Funnel: bandwidth numbers and whether Windows is officially supported for Funnel.
- Cloudflare: licence of cloudflared and exact requirements/free status of a named tunnel.
- LinkedIn: exact archive timing for Connections.csv and exact filenames in the archive.
- DuckDuckGo: contents of its Acceptable Use Policy.
- zrok: release date year (page shows "v2.0.8, 8 October" without year).
- The owner should also check the MCP server over a quick tunnel does not use Server-Sent Events, which Cloudflare quick tunnels do not support.

---

## Sources

[1] Cloudflare, TryCloudflare quick tunnels: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
[2] ngrok pricing: https://ngrok.com/pricing
[3] ngrok free plan limits: https://ngrok.com/docs/pricing-limits/free-plan-limits
[4] Microsoft Dev Tunnels overview: https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/overview
[5] Dev Tunnels FAQ and limits: https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/faq and https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits (section "Dev tunnels limits")
[6] Tailscale Funnel: https://tailscale.com/kb/1223/funnel
[7] Tailscale Funnel use cases: https://tailscale.com/kb/1247/funnel-serve-use-cases ; Tailscale pricing: https://tailscale.com/pricing
[8] zrok pricing: https://zrok.io/pricing/ ; zrok limits docs: https://netfoundry.io/docs/zrok/myzrok/service-limits
[9] zrok GitHub: https://github.com/openziti/zrok ; releases: https://github.com/openziti/zrok/releases
[10] Pinggy: https://pinggy.io
[11] Serveo: https://serveo.net
[12] bore: https://github.com/ekzhang/bore ; releases https://github.com/ekzhang/bore/releases
[13] frp: https://github.com/fatedier/frp
[14] localtunnel: https://github.com/localtunnel/localtunnel ; commits https://github.com/localtunnel/localtunnel/commits/master
[15] localtunnel reliability (third-party): https://webhookrelay.com/blog/localtunnel-alternative/
[16] Cloudflare named tunnel setup: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/ ; free-tunnel comparison (third-party): https://merginit.com/blog/19062026-free-developer-tunnels-comparison
[17] Serper: https://serper.dev
[18] Serper pricing explained (third-party): https://apiserpent.com/blog/serper-pricing-credits-explained
[19] Google Custom Search JSON API: https://developers.google.com/custom-search/v1/overview
[20] Brave API pricing page: https://api-dashboard.search.brave.com/documentation/pricing
[21] Brave free tier removal (news): https://www.implicator.ai/brave-drops-free-search-api-tier-puts-all-developers-on-metered-billing/
[22] Tavily pricing: https://tavily.com/pricing
[23] Exa pricing: https://exa.ai/pricing
[24] Mojeek API: https://www.mojeek.com/services/api.html
[25] SearXNG Docker install: https://docs.searxng.org/admin/installation-docker.html
[26] SearXNG GitHub: https://github.com/searxng/searxng
[27] SearXNG search API: https://docs.searxng.org/dev/search_api.html
[28] DuckDuckGo terms: https://duckduckgo.com/terms
[29] Gemini API pricing (grounding): https://ai.google.dev/gemini-api/docs/pricing
[30] Bing Search API retirement: https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement
[31] Hunter pricing: https://hunter.io/pricing
[32] Apollo pricing: https://www.apollo.io/pricing
[33] Apollo free plan credits (third-party): https://www.zeliq.com/blog/apolloio-pricing ; https://docket.io/resources/research/apollo-pricing
[34] Prospeo pricing: https://prospeo.io/pricing
[35] Prospeo free credits (third-party): https://syncgtm.com/blog/prospeo-review
[36] GetProspect pricing: https://getprospect.com/pricing
[37] LinkedIn prohibited software policy: https://www.linkedin.com/help/linkedin/answer/a1341387
[38] Snov.io pricing: https://snov.io/pricing
[39] Skrapp pricing: https://skrapp.io/pricing
[40] theHarvester: https://github.com/laramies/theHarvester
[41] Email permutator explainers (third-party): https://amplemarket.com/blog/the-pros-and-cons-of-email-permutator-tools-explained
[42] Reoon listings (third-party): https://www.capterra.co.uk/software/1033680/reoon-email-verifier ; https://us.fitgap.com/products/reoon-email-verifier
[43] ZeroBounce pricing: https://www.zerobounce.net/pricing/
[44] Emailable pricing: https://emailable.com/pricing
[45] MillionVerifier trial credits: https://help.millionverifier.com/millionverifier/free-trial-credits
[46] MillionVerifier bulk verifier page: https://www.millionverifier.com/services/bulk-email-verifier/
[47] NeverBounce pricing (third-party): https://emelia.io/hub/neverbounce-pricing
[48] Reacher GitHub: https://github.com/reacherhq/check-if-email-exists
[49] Reacher licence: https://raw.githubusercontent.com/reacherhq/check-if-email-exists/master/LICENSE.md ; https://docs.reacher.email/self-hosting/licensing
[50] Reacher install docs: https://docs.reacher.email/self-hosting/install.md
[51] Reacher proxies docs: https://docs.reacher.email/self-hosting/proxies.md
[52] Truemail: https://github.com/truemail-rb/truemail
[53] LinkedIn help, download your data: https://www.linkedin.com/help/linkedin/answer/a1339364
[54] LinkedIn help, connections export: https://www.linkedin.com/help/linkedin/answer/a566336
[55] Connections.csv columns (third-party guide): https://linkedapi.io/guides/export-linkedin-connections.md
[56] Archive contents (third-party): https://www.dummies.com/social-media/linkedin/use-linkedin-archives-data-syncing/
[57] Google Contacts export: https://support.google.com/contacts/answer/7199294
[58] Outlook export: https://support.microsoft.com/en-us/office/export-contacts-from-outlook-10f09abd-643c-4495-bb80-543714eca73f
[59] HubSpot export: https://knowledge.hubspot.com/import-and-export/export-records
[60] lemlist export guides: https://help.lemlist.com/en/articles/4452746-export-a-list-of-leads
[61] LinkedIn User Agreement: https://www.linkedin.com/legal/user-agreement
[62] Gmail sending limits: https://support.google.com/mail/answer/22839
[63] Mautic: https://github.com/mautic/mautic
[64] Mautic licence file: https://raw.githubusercontent.com/mautic/mautic/7.x/LICENSE.txt
[65] listmonk: https://github.com/knadh/listmonk
[66] Mailtrain: https://github.com/Mailtrain-org/mailtrain
[67] Google email sender guidelines: https://support.google.com/a/answer/81126
