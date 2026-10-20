# Getting started: a full example

This guide follows one made-up campaign from the first install to a finished CSV file. Everything in it is invented: the company, the people and the websites. Your own screens will show your own details, and the AI's exact words will differ.

**The story.** You work at *Brightpath Safety Training*, a small company that runs workplace safety courses (website: `https://www.brightpath.example`). You want to reach learning and safety managers at logistics companies in Singapore.

Time needed: about 30 minutes for the first run, most of it waiting for searches.

## Before you begin

You need:

- A Windows, Mac or Linux computer that you can leave on.
- **Claude Desktop** (the app), or Claude Code. ChatGPT works too, with more steps (see [Connect ChatGPT](CONNECT_CHATGPT.md)).
- About 10 minutes to sign up for one free key (Serper). Or none at all, if you use [free mode](FREE_MODE.md).

## Step 1: Install

Open PowerShell (Windows) or Terminal (Mac) and paste the install command from the [README](../../README.md). When it asks "Connect Claude Desktop now?", answer **Y**.

At the end it prints a few lines of next steps and the full path of the `leadgen` program. If your computer later says it does not know `leadgen`, type that full path instead.

Check the installation:

```
leadgen doctor
```

It ends with "All good." If not, see [Troubleshooting](TROUBLESHOOTING.md).

## Step 2: Connect Claude

If you said yes in step 1, quit Claude Desktop completely and open it again. (Closing the window is not enough: use Quit from the tray icon on Windows, or the Claude menu on a Mac.) If you said no, follow [Connect Claude](CONNECT_CLAUDE.md).

Start a new chat and ask:

> What is my Lead Generator status?

Claude should use a tool called `get_status`, and answer with something like:

> Lead Generator is set up. You have no campaigns yet and no API keys. The next step is to tell me what you want leads for.

Claude may ask your permission the first time it uses a tool. Choose allow.

## Step 3: Tell Claude what you sell

> **You:** Find me leads for https://www.brightpath.example in Singapore.

Claude reads your website. If the site has little text or blocks reading, Claude will ask you to paste the text or give it a document instead.

> **Claude:** I've read the Brightpath site. Here is what I plan to say about you, so please correct it.
>
> **Facts I can state:**
> - Brightpath runs workplace safety courses for companies.
> - Courses are taught on site or online.
> - Brightpath is based in Singapore.
>
> **Things I will NOT say** (not confirmed, or marked "to confirm" on your page):
> - "Over 10,000 people trained" (the page says "to confirm")
> - Any client names
>
> Services I'll mention: "Forklift safety course", "Manager safety briefing". Calls to action: "a 15-minute call".
> Is this right? Anything to add or remove?

This step matters. Everything Claude writes later is limited to the facts you approve here.

> **You:** Add that we're MOM-approved. The 10,000 number is actually correct, you can use it, and the source is our 2025 annual report.

> **Claude:** Done. I've moved "over 10,000 people trained" into the facts I can use, with your annual report as its source. Saved.

(MOM is the Singapore Ministry of Manpower, used here only as an example. Only confirm a claim like this if it is true.)

## Step 4: Choose who to reach

> **Claude:** Who do you want to reach? I suggest:
> - **Country:** Singapore. (Add others?)
> - **Sectors:** logistics and warehousing.
> - **Job roles:** (1) Learning and development, (2) Safety and operations, (3) HR. I'd skip owners and MDs for this offer.
> - **Level:** manager and above.
> - **Channels:** LinkedIn, email, or both?

> **You:** Singapore only. Use all three job groups, manager and above. LinkedIn and email.

> **Claude:** I've created the campaign "Brightpath SG logistics". Since you want email, note that email needs free keys for Hunter and Reoon. We'll add them before the email step.

## Step 5: The company list

> **Claude:** I've drafted 12 logistics companies in Singapore that I know of, and checked their websites. Two had problems:
> - "Harbourline" matches several businesses, so I need an exact website for it.
> - `kestrelcargo.example` did not load.
>
> The others look fine. Shall I remove those two, or do you have the right websites?

> **You:** Remove Kestrel, use harbourline-freight.example for Harbourline.

> **Claude:** Done. 11 companies. Approve them all?

> **You:** Yes. And exclude "Westgate Haulage", they're a customer.

Only approved companies are searched. This list is where you control the cost of the search, so it is worth a minute.

## Step 6: Add a search key (or use free mode)

> **Claude:** To find people I need a search key. Serper gives 2,500 free searches and needs no card. Sign up at serper.dev, copy your key, then in a terminal run `leadgen dashboard --open`, go to the **API keys** page and paste it there. Please don't paste it into this chat.

Do exactly that. The dashboard opens in your browser and only ever shows the last 4 characters of a key. Click **Save and test**. It should say the key works.

(No key? Say **"use free mode"** and skip ahead. See [Free mode](FREE_MODE.md).)

## Step 7: Find the people

> **Claude:** The search needs about 33 Serper searches (11 companies × 3 job groups). None are cached yet, and you have about 2,500 free. Shall I go ahead?

> **You:** Yes.

Claude searches in chunks and reports progress ("20 of 33 done").

> **Claude:** Done. I found **14 confirmed people** and **9 more for review**. Confirmed means the search result showed the right company, country, role and level. For example:
>
> | Name | Title | Company |
> |---|---|---|
> | Aria Tan | Head of Learning and Development | Northwind Logistics |
> | Marcus Lim | Safety Manager | Harbourline Freight |
>
> The 9 for review are people the search was not sure about, for example "former" job titles or a different employer in their headline. I've left them out.

You can ask to see the review list ("show me the review people and why"). Do not promote anyone unless you have opened their profile and it is clearly right.

## Step 8: Find the emails

> **Claude:** For email I need two free keys: Hunter (company email patterns) and Reoon (checks that an address works). Both are free, no card. Paste them on the dashboard **API keys** page as before.

After you do:

> **Claude:** This will use about 6 Hunter credits and 14 Reoon checks (your numbers will differ). Go ahead?

> **You:** Yes.

> **Claude:** Done. 9 verified, 2 risky, 3 guess. Verified means Reoon confirmed the mailbox exists. Risky addresses are not used unless you allow them. A guess is an address built from the company's pattern that nobody could check. Guess addresses will not be sent by the built-in sender.

## Step 9: The messages

Claude writes one message per person per channel, in batches, using only the facts you approved and the evidence about each person. You do not need to do anything. When it saves a message, Lead Generator runs instant checks and Claude fixes any that fail.

Example of a LinkedIn message (after the person accepts your invite):

> Hi Aria, I saw you lead Learning and Development at Northwind Logistics. Brightpath runs a forklift safety course for warehouse teams, and over 10,000 people have trained with us. Would a 15-minute call to see if it fits Northwind be useful?

Every statement in it comes from your approved facts or Aria's search result. The "10,000" appears because you approved it with a source. It would have been blocked otherwise.

## Step 10: Quality check and review

> **You:** Run the quality check.

> **Claude:** 14 leads and 23 messages checked. 21 messages are clean. 2 messages are blocked: one has a number that is not in your proof points, one mentions "Harbourline" in a message to someone at Northwind. I've rewritten both.
>
> Now I'll read every message against the person's evidence.

Claude reads each message and records pass, fix or reject. If it cannot pass a message, it explains why. What each code means: [Quality checks](QUALITY_CHECKS.md).

## Step 11: Export

> **You:** Export for lemlist.

> **Claude:** Exported 14 leads (23 messages) to `exports\brightpath-sg-logistics\lemlist-20261021-0930.csv` in your LeadGenerator folder. I also wrote `qa_report.csv` (every person, with the reasons) and `removed.csv`. 3 emails are labelled "guess". Next steps for lemlist are below.

Open the CSV in Excel and look at it. Then follow [Delivery](DELIVERY.md) to import it into lemlist or Instantly, push it with the lemlist API, or send the emails from your own mailbox.

## You're done

You can stop here and come back later. Start a new chat and say "continue my Lead Generator campaign". Claude looks at the status and picks up where you stopped.

Things worth knowing before the next run:

- Look at five or ten profiles yourself. The tool is careful but not perfect.
- Searching again for the same company costs nothing for 30 days (results are remembered).
- Email sending has its own safety steps. Read [Delivery](DELIVERY.md) and [Compliance](COMPLIANCE.md) first.
- If something goes wrong, see [Troubleshooting](TROUBLESHOOTING.md).
