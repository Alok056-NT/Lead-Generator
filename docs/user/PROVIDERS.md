# Providers: the services Lead Generator uses

Lead Generator uses outside services to search the web and to check email addresses. Each service gives you a **key**. A key works like a password for your account with that service. You paste it into Lead Generator once.

Most services have a free allowance. You only need the ones for the steps you use.

A note on the numbers: the free allowances below were read from the providers' pages or from articles about them in October 2026. Providers change their plans without warning, and their sign-up screens change too. If a screen looks different from what is described here, trust the screen and look for the closest match. Check the provider's own pricing page before you rely on a number.

## What do I need?

| I want to... | I need |
|---|---|
| Find companies and LinkedIn profiles | Serper (or use [free mode](FREE_MODE.md), which needs no key) |
| Also find and check work email addresses | Hunter and Reoon (both free) |
| Export a CSV file | Nothing more |
| Push leads into lemlist | A paid lemlist plan and its key |
| Send emails from my own address | My own mailbox |

## How to add a key (all providers)

1. Sign up with the provider. Find your API key in your account. It is a long string of letters and numbers. Copy it.
2. Paste the key into the dashboard. In a terminal, run `leadgen dashboard --open`. Open the **API keys** page, paste the key and save it.
3. Test it. In a terminal, run `leadgen keys test <provider>`, for example `leadgen keys test serper`. A test uses at most 1 credit. Some providers (Hunter, Reoon, ZeroBounce) let Lead Generator check a key for free.

You can also run `leadgen keys set <provider>` in a terminal. It asks for the key and does not show it while you type.

You can paste a key into the chat too, if you insist. The dashboard is safer, because anything you type in a chat is kept in the chat history.

Lead Generator stores keys in a file called `secrets.env` in your LeadGenerator folder (run `leadgen paths` to see where). It never shows a whole key. It shows only the last 4 characters.

Lead Generator always tells you the cost before it spends credits, and waits for your yes.

## Serper.dev (Google search)

- **What it is for:** Finding people and companies. Lead Generator asks Google, through Serper, for public LinkedIn profile links. It never logs in to LinkedIn.
- **Free allowance:** 2,500 searches, no credit card. One search uses one credit.
- **Is it needed?** It is the default search service. Without it, use [free mode](FREE_MODE.md).
- **One-time or monthly?** Serper's home page says "2,500 free queries" and does not say whether the allowance comes back. Lead Generator plans on 2,500 in total. If Serper gives you more later, that is a bonus.
- **How to sign up:** Go to [serper.dev](https://serper.dev) and create an account. Your API key is in your account area.
- **Where to paste the key:** The **API keys** page in the dashboard, or `leadgen keys set serper`. The key's name inside the file is `SERPER_API_KEY`.
- **When credits run out:** Searches stop and the AI tells you Serper has no credits left. You can buy more credits from Serper, add a backup search key (Brave), or switch to free mode.

## Brave Search API (backup search, optional)

- **What it is for:** A backup web search for when you have no Serper key or it has run out.
- **Free allowance:** Brave gives a monthly credit of about $5, which is roughly 1,000 searches. Brave asks for a credit card even for the free credit. Brave says it will not charge the card for the free credit.
- **Is it needed?** No.
- **How to sign up:** Go to [api-dashboard.search.brave.com/register](https://api-dashboard.search.brave.com/register), create an account, then find the option to create a key for the Search API.
- **Where to paste the key:** The **API keys** page, or `leadgen keys set brave`. The key's name inside the file is `BRAVE_API_KEY`.
- **When credits run out:** Brave charges for searches beyond the free credit (about $5 for 1,000), because a card is on file. Check Brave's pricing page and your spending limit before a big run.

## DuckDuckGo (last resort, no key)

- **What it is for:** A free web search with no key. It is the last resort.
- **Free allowance:** Free, but unreliable. It often blocks automated use, so searches can fail.
- **Is it needed?** No. It is off unless you turn it on.
- **How to turn it on:** Open `settings.yaml` in your LeadGenerator folder (`leadgen paths` shows where). Create the file if it is not there. Add the line `allow_ddg_fallback: true`. (If the line is already there and says `false`, change it to `true`.)
- **Where to paste the key:** Nothing to paste.
- **When it stops:** Searches fail or are refused for a while. Use [free mode](FREE_MODE.md) instead.

## Hunter.io (email patterns and email finder)

- **What it is for:** Learning the usual email pattern at a company, for example `first.last@company.example.com`. It can also find some published addresses. You only need it for the email channel.
- **Free allowance:** 50 credits each month on the free plan, no card. Learning a company's pattern costs 1 credit, a found address costs 1 credit, and checking an address costs 0.5 credit. Credits refresh every month.
- **How to sign up:** Go to [hunter.io/users/sign_up](https://hunter.io/users/sign_up) and create an account. Your API key is in your account area.
- **Where to paste the key:** The **API keys** page, or `leadgen keys set hunter`. The key's name inside the file is `HUNTER_API_KEY`.
- **When credits run out:** Lead Generator skips Hunter. It reads the company's own website for addresses and falls back to a standard pattern such as `first.last`. An address it cannot check stays marked **guess**. The built-in sender never sends a guess unless you allow it for that campaign.

## Reoon (email checker)

- **What it is for:** Checking that an email address exists before you use it. It is the first email checker Lead Generator tries.
- **Free allowance:** 20 checks a day (about 600 a month), and 100 extra checks when you sign up. The extra 100 do not expire. No card.
- **Is it needed?** Strongly recommended for the email channel. Without any checker, every address is a **guess**.
- **How to sign up:** Go to [emailverifier.reoon.com/register](https://emailverifier.reoon.com/register/) and create an account. Your API key is in your account area.
- **Where to paste the key:** The **API keys** page, or `leadgen keys set reoon`. The key's name inside the file is `REOON_API_KEY`.
- **When credits run out:** Checks stop until the daily allowance refills. Lead Generator then tries ZeroBounce, then Hunter's checker (0.5 Hunter credit each), if you have set them up. An address nobody could check stays marked **guess**.

## ZeroBounce (second email checker, optional)

- **What it is for:** A second email checker, tried after Reoon.
- **Free allowance:** 100 checks each month. ZeroBounce suggests signing up with a business email address, not a free webmail one.
- **Is it needed?** No.
- **How to sign up:** Go to [zerobounce.net/members/signin/register](https://www.zerobounce.net/members/signin/register).
- **Where to paste the key:** The **API keys** page, or `leadgen keys set zerobounce`. The key's name inside the file is `ZEROBOUNCE_API_KEY`.
- **When credits run out:** Checks stop until the monthly allowance refills. Addresses nobody could check stay marked **guess**. ZeroBounce gives the same error for a wrong key and for no credits left, so check your balance on their site if it stops working.

## lemlist (sends leads into LinkedIn campaigns, optional, paid)

- **What it is for:** Taking your leads into a lemlist campaign. lemlist does the LinkedIn sending. Lead Generator does not send LinkedIn messages.
- **Free allowance:** None. You need a paid lemlist plan. lemlist's pages say the "Accepted invite" step used in the LinkedIn recipe needs one of its advanced plans. See [DELIVERY.md](DELIVERY.md) and check lemlist's pricing page. The plan needed for API access is not stated on the pages we read.
- **Is it needed?** No. You can export a CSV file and import it yourself.
- **How to sign up:** Go to [lemlist.com](https://www.lemlist.com/) and choose a plan.
- **Where to paste the key:** Find your API key in your lemlist account settings (look for an API or Integrations section). Paste it on the **API keys** page, or run `leadgen keys set lemlist`. The key's name inside the file is `LEMLIST_API_KEY`.
- **When it stops:** If your plan ends or the key stops working, pushes fail. Your leads stay in Lead Generator. Export a CSV file instead.

## Your mailbox (for sending email, optional)

- **What it is for:** Sending email from your own address and reading replies. You do not need it for CSV exports.
- **Free allowance:** None from Lead Generator. It uses your own email account, so your email provider sets your sending limits. Lead Generator starts at 20 emails a day and rises by 5 a day to a top of 50.
- **Is it needed?** No.
- **Gmail and Google Workspace:** You need an **app password**, a special password for one program. Turn on 2-Step Verification on your Google account first. Google may not offer app passwords on some work or school accounts, or if you use Advanced Protection or security keys only. Then you need another mailbox.
- **Outlook and Microsoft 365:** Your IT admin may have to switch on "SMTP AUTH". Some accounts refuse passwords altogether, and Lead Generator cannot use the newer sign-in method yet.
- **Where to set it up:** Not on the **API keys** page. In a terminal, run `leadgen mailbox set --preset gmail` (or `--preset outlook`, or no preset for another provider). It asks for your address and password and does not show the password. Then run `leadgen mailbox test` to check the login.
- **When it stops:** If your email provider stops sending, Lead Generator stops too. Read [COMPLIANCE.md](COMPLIANCE.md) and [DELIVERY.md](DELIVERY.md) before you send any email.

## No credits? Use free mode

When Serper or your email checkers run out, or you would rather not pay, the AI can run the web searches itself. It uses its own web search tool. You need no key for this.

- Lead Generator checks the results with the same rules as a paid run.
- It is slower. It suits about 20 to 40 companies in one chat. For bigger jobs, add a search key.
- Switch on web search in your AI chat first. In Claude, use the **+** menu under the chat box.
- Lead Generator asks you to spot-check a few LinkedIn links before it lets you export.
- Emails found this way stay **guess** unless you add a checker key.

Read the full guide: [Free mode](FREE_MODE.md).
