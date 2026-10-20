# Lead Generator

Find, check and contact the right people for any product or service, by chatting with your AI assistant.

You tell Claude (or ChatGPT) what you sell and who you want to reach. Lead Generator does the searching, checking and exporting on your own computer. Your leads and your keys stay with you.

## What it does
- **Learns your offer** from a web page, a document or text you paste.
- **Finds real people** at your target companies, with their LinkedIn profile and, if you want email, their work email. It uses free services where it can and asks before it spends any credits.
- **Writes one personal message per person** for LinkedIn, email or both, using only facts you have confirmed about your offer.
- **Checks everything.** Every lead is confirmed against evidence, and every message is checked by the tool and then read by the AI before anything goes out.
- **Delivers how you like:** a CSV file for lemlist or Instantly, a direct push into a lemlist campaign, or emails sent from your own mailbox with reply, bounce and unsubscribe tracking.

It never logs in to LinkedIn and never scrapes LinkedIn. People are found from public search results.

It has a **free mode** for when you have no search credits: the AI runs the web searches itself. See [Free mode](docs/user/FREE_MODE.md).

## Install

You need Python 3.11 or newer. The installer tells you the exact command to get it if it is missing.

**Windows.** Open PowerShell and paste:

```
irm https://raw.githubusercontent.com/Alok056-NT/Lead-Generator/main/install.ps1 | iex
```

**Mac (and Linux).** Open Terminal and paste:

```
curl -fsSL https://raw.githubusercontent.com/Alok056-NT/Lead-Generator/main/install.sh | bash
```

The installer makes a private Python environment, installs Lead Generator, checks it works, and offers to connect Claude Desktop. Run the same command again later to upgrade.

If Windows says scripts are blocked, run `powershell -ExecutionPolicy Bypass -File install.ps1` from the folder where you saved the file. Other problems: [Troubleshooting](docs/user/TROUBLESHOOTING.md).

## Connect your AI
- **Claude Desktop or Claude Code:** [Connect Claude](docs/user/CONNECT_CLAUDE.md). This is the easiest way.
- **ChatGPT:** [Connect ChatGPT](docs/user/CONNECT_CHATGPT.md). It needs a temporary secure link and a ChatGPT plan that allows custom connectors.

## Your first campaign in 6 steps
1. **Open a new chat** with Claude. Ask: "What is my Lead Generator status?" It should answer using the Lead Generator tools.
2. **Say what you want.** For example: *"Find me leads for https://www.example.com in Singapore."* Claude reads your page and shows you the facts it will use and the claims it will avoid. Correct them.
3. **Choose who to reach.** Claude asks which countries, sectors, job roles and channels (LinkedIn, email or both).
4. **Check the company list.** Claude proposes companies. Remove the ones you do not want and approve the rest.
5. **Add a key, or use free mode.** Claude asks for a free search key (see the table below) and tells you the cost before it searches. No key? Say "use free mode".
6. **Review and export.** Claude finds and checks the people, writes the messages, checks them, and exports a CSV file (or pushes to lemlist, or sends email). It tells you where the file is.

A full worked example with what Claude asks and what you answer is in [Getting started](docs/user/GETTING_STARTED.md).

## What it costs

You can run the whole thing on free allowances. Free allowances change, so check each provider's page before a big run. Details are in [Providers](docs/user/PROVIDERS.md).

| Service | Used for | Free allowance | Needed? |
|---|---|---|---|
| Serper.dev | Google search for companies and people | 2,500 searches, no card | Recommended |
| Free mode | The AI searches for you | Free | Instead of Serper |
| Hunter.io | Email patterns | 50 credits a month, no card | Email only |
| Reoon | Email checking | 20 a day plus 100 on sign-up, no card | Email only |
| ZeroBounce | Second email checker | 100 a month | Optional |
| Brave Search | Backup search | About $5 credit a month, card required | Optional |
| lemlist | LinkedIn sending | None, paid plan | Optional |
| Your own mailbox | Sending email | Your email provider's limits | Optional |

Lead Generator always tells you the cost first and waits for your yes.

## Safety notes
- **Nothing is sent without your yes.** Exports are just files. Pushing to lemlist and sending email each start as a dry run and need your clear confirmation.
- **Email sending is opt-in and careful.** It starts at 20 emails a day and grows slowly, waits 1 to 3 minutes between emails, skips quiet hours, honours unsubscribes and stops itself if too many bounce.
- **Check the rules for each country** before you email. Some countries need the recipient's consent first. See [Compliance](docs/user/COMPLIANCE.md). It is a summary, not legal advice.
- **LinkedIn:** sending LinkedIn messages is done in your own tool (such as lemlist) and must follow LinkedIn's terms.
- **Keys are private.** Put them in the local dashboard (`leadgen dashboard --open`), not in the chat. They are stored on your computer and only the last 4 characters are ever shown.
- **Lead Generator never makes things up on purpose,** but the AI can still make mistakes. The checks catch most of them. Read [Quality checks](docs/user/QUALITY_CHECKS.md) and look at a few profiles yourself.

## Guides
- [Getting started](docs/user/GETTING_STARTED.md): a full example, step by step
- [Connect Claude](docs/user/CONNECT_CLAUDE.md) and [Connect ChatGPT](docs/user/CONNECT_CHATGPT.md)
- [Providers](docs/user/PROVIDERS.md): keys, free allowances, what happens when credits run out
- [Free mode](docs/user/FREE_MODE.md): no credits needed
- [Quality checks](docs/user/QUALITY_CHECKS.md): what each L and M code means
- [Delivery](docs/user/DELIVERY.md): CSV, lemlist, Instantly and sending email
- [Compliance](docs/user/COMPLIANCE.md): email rules by country
- [Troubleshooting](docs/user/TROUBLESHOOTING.md)
- [Changelog](CHANGELOG.md)

## For contributors and AI builders
Start with [`CLAUDE.md`](CLAUDE.md), then [`docs/PLAN.md`](docs/PLAN.md), then the next open task in [`docs/STATUS.md`](docs/STATUS.md). Task cards are in [`docs/tasks/`](docs/tasks/).

## Licence and credits
MIT, see [`LICENSE`](LICENSE). Parts are adapted from [Harvey](https://github.com/ethanplusai/harvey) (MIT, Ethan Rogers); see [`NOTICE.md`](NOTICE.md).
