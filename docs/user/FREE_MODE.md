# Free mode: no search credits needed

Free mode lets you run a campaign without paying for a search service. Instead of using a search key, the AI runs the web searches itself with its own web search tool. It then hands the results to Lead Generator, which checks them with the same rules as a paid run.

## When to use it

Use free mode when:

- you have no search key and do not want to get one,
- your Serper credits have run out, or the campaign budget is used up,
- you want to try Lead Generator before you sign up for anything.

It is not the best choice for big jobs. Every search is a step in your chat, so free mode suits about **20 to 40 companies** in one session. For a bigger list, a Serper key is faster. (Serper gives 2,500 free searches. See [Providers](PROVIDERS.md).)

## Turn on web search in your AI chat

Free mode only works if the AI has a web search tool in the same chat as Lead Generator.

**Claude (claude.ai and Claude Desktop):**

1. Open a new chat.
2. Click the **+** menu at the bottom left of the chat box. On some plans it looks like a slider icon.
3. Switch on **Web search**. A tick shows it is on.
4. On Team and Enterprise plans, an owner may first need to allow web search in the organisation's admin settings, under Capabilities.

[Screenshot placeholder: the + menu with Web search switched on]

**ChatGPT:** make sure search is available in the same chat as the Lead Generator connector. We could not confirm from OpenAI's pages how ChatGPT handles search together with a custom connector, so check on screen. If it does not work, use Claude for free mode, or add a search key.

If you are not sure that Claude can use web search and Lead Generator in the same chat, test it first: ask a simple question that needs a web search, then ask "What is my Lead Generator status?" in the same chat.

## How to start

Tell the AI: **"Use free mode"** (or "Use AI search mode"). It will:

1. Check that it has a web search tool. If not, it tells you how to switch it on.
2. Set your campaign to AI search. (You can also choose "auto": Lead Generator uses the search key until it runs out, then asks you to switch.)
3. Fetch a batch of 10 searches from Lead Generator, run each one, and hand back what the search tool returned.
4. Tell you its progress after each batch, and continue until all the searches are done.

You do not need to do anything during the searches.

## What the AI must not do

The AI has to copy each search result **exactly** as the search tool shows it: the page title, the link and the short excerpt. It must not rewrite, shorten or "fix" a link, and it must not describe a person in its own words. If it hands in invented or tidied results, Lead Generator refuses them, or moves the person to the review list. A search that found nothing is handed in as empty.

## The spot check

Because the AI is copying the results, there is a bigger chance of a mistake than with a search key. Lead Generator protects you with a **spot check** before it lets you export.

1. Lead Generator picks a random sample of the people found: 5 people or 5% of them, whichever is more, up to 15.
2. The AI shows you the names, titles, companies and LinkedIn links.
3. You open each link and tell the AI **"ok"** or **"wrong"**: is it the right person, at the right company, in the right role?
4. A person you mark "wrong" is moved to the review list.
5. If two or more are wrong, every AI-search lead stays blocked until a second sample passes. This protects you from a bad batch.

Until the spot check is finished and passed, AI-search leads are blocked from export (quality check **L21**, see [Quality checks](QUALITY_CHECKS.md)). The AI does not open the links for you, and it should not.

## Emails in free mode

The AI searches for published email addresses at each company. Lead Generator also reads the company's own contact and about pages, which is free, and works out the company's email pattern from any real addresses it finds.

Without an email checker key (Reoon or ZeroBounce), every address stays marked **guess**, meaning nobody has checked that it works.

- Only verified emails go out. A guess is not put in the `email` column of a CSV (it is listed in a separate `unverifiedEmail` column with its status, so nobody imports it by mistake) and is not pushed to lemlist.
- The built-in email sender does not send to a guess either.
- For better results, add a free Reoon key. It is 20 checks a day. See [Providers](PROVIDERS.md).

## Limits to know about

- **Slower.** Each search is a step in the chat.
- **Fewer people may be confirmed.** Claude's search tool shows a title, a link and only a short quoted excerpt. A very short excerpt (under 40 characters) is not enough evidence, so that person goes to the review list. You can look at the profile and move the person across if you are happy.
- **You need the spot check.** It takes a few minutes.
- **Your chat can run out of room** on long jobs. Stop after a batch or two, and carry on in a new chat. Say "continue my Lead Generator campaign" and the AI picks up where it left off.
- Everywhere else, the checks are exactly the same as a paid run. Each lead shows where it came from (`evidenceOrigin` in the export is `ai_web_search`).
