# Quality checks

Lead Generator checks every lead and every message before anything is exported or sent. Each check has a code.

- Codes that start with **L** check **leads** (the people, their companies and their email addresses).
- Codes that start with **M** check **messages**.

Each check gives one of two results:

- **Block:** the lead or message is held back from the export and from sending until the problem is gone. Nothing is deleted.
- **Warn:** the issue is shown to you but does not stop anything. Read it and decide.

A few checks have two results depending on the case. The tables say so.

## How to see the checks

1. Ask Claude: "Run the quality check on my campaign."
2. Claude tells you how many leads and messages are clean, and which codes come up most.
3. You can see every issue on the **Leads** and **Messages** pages of the dashboard (`leadgen dashboard --open`). When you export, Lead Generator also writes `qa_report.csv` and `removed.csv` in the campaign's exports folder. They show every person, the blocks, the warnings and the reasons.

The checks are run again each time, so a problem clears once it is fixed. Run the check again after you fix something.

## How to fix things

You normally fix things by telling Claude what you want. These are the things Claude can do:

- **Take a person out** (status "rejected" or "removed"), or **put a person back** that you have looked at and accept. Claude must give a reason when it puts someone back.
- **Change the campaign**: countries, job groups, minimum seniority, companies to exclude. After that, Claude runs a re-check on the people already found.
- **Rewrite a message** and save it. Claude then checks it again.
- **Add to the do-not-contact list.**
- **Find or check emails again.**

You cannot edit a person's name, title or country by hand. If the stored details are wrong, take the lead out.

Most of the L codes are also the reasons a person lands on the **review list** instead of the confirmed list when Lead Generator searches. The review list is people it found but could not fully confirm. Nobody on the review list is exported or messaged. You can open a profile, and if you are happy, ask Claude to move the person to confirmed ("put back" below). After that the person is checked again, and for that person these blocks become warnings: L04 to L10 and L14. These never change: L01, L02, L03, L16 and the email blocks.

## Lead checks (L)

| Code | What it means | Result | What to do |
|---|---|---|---|
| L01 | The LinkedIn link is not a personal profile. A personal profile looks like `linkedin.com/in/name`. | Block | Take the lead out. It is probably a company page or a post. |
| L02 | The same person appears twice: the same LinkedIn link, the same email address, or the same name at the same company. The first one found is kept. | Block (on the copy) | Nothing. The copy stays out. |
| L03 | The company is not approved, or it is on your excluded list, or it is a competitor. | Block | Approve the company if it is a good fit. Otherwise leave the person out. |
| L04 | The person's current employer is not confirmed by the search result. The company is either not mentioned as their current job, or only mentioned in a way that is not clear. | Block | Open the profile and check. If they work there now, ask Claude to put them back with that reason. Otherwise leave them out. |
| L05 | The search result does not show the person is based in your campaign's country. | Block | Open the profile and check where they live. If it is right, ask Claude to put them back. Or add the country to the campaign. |
| L06 | The job title does not fit any of the campaign's job groups. | Block | Leave them out. Or ask Claude to change the campaign's job groups, then re-check. |
| L07 | The person's level is below the campaign's minimum (the default minimum is manager). | Block | Leave them out. Or ask Claude to lower the minimum level for the campaign. |
| L08 | The headline describes a former role ("former", "ex-", "retired", "previously"). | Block | Leave them out. They have moved on. |
| L09 | The headline names a different employer. They probably work somewhere else now. | Block | Leave them out, unless you checked the profile and the company is right. |
| L10 | The campaign has an "owners" group, but the title is only a rank such as "Managing Director" or "Principal". At a big company that does not mean the person runs the company. | Block | Open the profile. A real head of the company (CEO, founder, owner, chairman, managing partner, general manager) can be put back by hand. Otherwise leave them out. |
| L11 | The name is awkward for a greeting: it is not a first and last name, it has brackets, credentials after a comma, a part in ALL CAPS or only an initial, or the greeting name is empty or an initial. | Warn | The message check M01 will make sure the greeting uses the greeting name. If the name looks wrong in the lead list, leave the lead out. |
| L12 | The job title is empty, cut off ("..." or ending in "at"), or has website text in it such as "View mutual connections". | Warn | Check the profile. If you cannot trust the title, leave the lead out. |
| L13 | A weak fit: an IT, finance, sales or similar "operations" role in a safety or operations group. It only shows up together with L06. | Warn | Leave them out. The title is not the function you are targeting. |
| L14 | The title has a junior word such as "junior", "jr", "intern", "trainee" or "apprentice". Words like "associate", "executive", "specialist" and "coordinator" also count when there is no rank word with them ("Associate Director" is fine). | Block | Leave them out. |
| L15 | The profile text says the person is looking for work ("looking for", "open to work", "seeking", "job seeker"). | Warn | Think about whether to contact them. They may be leaving the job. |
| L16 | On your do-not-contact list: the person's LinkedIn link or name, their email address or its domain, or the company's domain. This stops every channel, LinkedIn too. | Block | Leave them out. Do not add them back. |
| L17 | Email channel only: the person has no email address, or the address is not usable (not verified, or "risky" when you did not allow risky addresses, or invalid). | Block (email only) | Ask Claude to find and check emails. Or use LinkedIn for this person. |
| L18 | Email channel only: the email's domain is not the company's domain or a known alias of it. | Warn | Check the address belongs to this company. If not, leave it out. |
| L19 | Email channel only: a role address such as `info@` or `hr@`, not a person's own address. | Block (email only) | Find the person's own address, or use LinkedIn. |
| L20 | The profile looks like it is in another language (Spanish, Portuguese, French, German or Italian job words), or its text places the person in a different country from the campaign. | Warn | Check the profile fits your country and language. Leave the lead out if not. |
| L21 | The lead was found by the AI's own web search ([free mode](FREE_MODE.md)), so the evidence is less certain. | Warn if the spot check passed. **Block** if the spot check is not finished or failed, or if you marked this person "wrong". | Finish the spot check: open the sample LinkedIn links and tell Claude "ok" or "wrong" for each. |
| L22 | The LinkedIn link does not contain any part of the person's name. The link may be made up or mixed up. A link with a random ID number cannot be compared and is flagged with a warning. | **Block** for leads found by AI search. Warn for leads found by a search key, and for ID-only links. | Open the link. If it is the right person and role, ask Claude to put them back. Otherwise leave them out. |
| L23 | Email channel only: the email address is a **guess**: nobody has verified it. | A CSV export is allowed and labels it "guess". **Blocks sending** unless the campaign allows guessed emails. If it allows them, it is a warning. | Add a checker key (see [PROVIDERS.md](PROVIDERS.md)) and run the email search again. Only allow guessed emails if you accept that some will bounce. |

"Email only" blocks stop the email message but not a LinkedIn message to the same person.

## Message checks (M)

| Code | What it means | Result | What to do |
|---|---|---|---|
| M01 | The first line does not greet the person by their greeting name. | Block | Ask Claude to rewrite the opening line. |
| M02 | The message does not mention the person's own company: **warn**. It mentions another company from this campaign: **block**. (A word only counts as a company name when it is written like the name, for example "Northwind Logistics", not "a total of".) | Warn or block, as described | Ask Claude to name this person's company and no other target company. |
| M03 | A placeholder is left in the text, such as `{{name}}`, `[Name]`, `<company>`, `TODO`, `TBC` or `lorem ipsum`. | Block | Ask Claude to fill it in or remove it. |
| M04 | The length is outside the limit for the channel. LinkedIn invite: at most 200 characters. LinkedIn message: 150 to 700 characters. Email body: 40 to 160 words. | Block | Ask Claude to shorten (or lengthen) the message. |
| M05 | The message states something your offer says it must not (a forbidden claim), or uses a banned phrase: "guarantee", "100%", "risk-free", "no-brainer", "act now", "limited time". | Block | Ask Claude to remove it. |
| M06 | The message has a number, percentage or price that is not in your offer's proof points. A number from a call-to-action ("15-minute call"), or from the person's name or title, is allowed only if it is the same kind of number. A price always needs a proof point. | Block | Ask Claude to take the number out. Add it to the offer's proof points only if it is true and you can back it up. |
| M07 | The message has a name or brand (two or more capitalised words in a row) that is not in your offer or in the person's evidence. It may be a made-up client name. | Warn | Remove it, or add it to your confirmed claims if you may use it. |
| M08 | Email only: words that spam filters dislike, such as "free", "discount", "urgent", "click here", "winner", "no obligation". ("Feel free to reply" is fine.) | Warn | Ask Claude to use plain words. |
| M09 | The email subject is missing, longer than 60 characters, in ALL CAPS, or starts with "Re:", "Fwd:" or "Fw:" when it is not a real reply. | Block | Ask Claude to write a new subject. |
| M10 | The message does not end with a question or a clear next step. | Warn | Ask Claude to end with a simple question, such as whether a short call would help. |
| M11 | A LinkedIn invite or message has a link in it. | Warn | Ask Claude to remove the link. Email messages may have links. |
| M12 | The message is almost the same as another message in the campaign (92% alike or more, once names are ignored). | Warn | Ask Claude to rewrite the middle so it fits this person. |
| M13 | The spelling does not match the campaign's language setting. A British setting (for example `en-GB`) warns about "organize" and "color". An American setting (`en-US`) warns about "organise" and "colour". It only applies to English settings. | Warn | Ask Claude to fix the spelling. Or change the campaign's language setting. |
| M14 | The message names a role (for example "Head of Operations") that is different from the person's own title. | Warn | Ask Claude to use the person's real title, or none. |
| M15 | Too much excitement: two or more exclamation marks, an emoji, or two or more words in ALL CAPS. | Warn | Ask Claude to calm it down. |
| M16 | The AI has not read and passed this message. | **Block** when strict review is on (it is on by default). Warn when it is off. | Ask Claude to review the messages. Claude reads each one against the person's evidence and records pass, fix or reject. |

## When something is blocked

1. Ask Claude: "Run the quality check on my campaign." Note which codes are blocking.
2. For each blocked lead, decide: leave them out, or look at the profile and ask Claude to put them back. For each blocked message, ask Claude to rewrite it.
3. Run the check again until the blocks are gone.
4. Export or send. Warnings appear in the report but do not stop either.
