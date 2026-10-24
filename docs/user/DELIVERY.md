# Delivery: getting your leads out

When your leads and messages are checked, you choose how they leave Lead Generator. There are three ways:

1. **A CSV file** (a spreadsheet file) that you import into lemlist or Instantly, or open in Excel.
2. **A direct push into a lemlist campaign**, using lemlist's API.
3. **Email sent from your own mailbox** by Lead Generator.

Tell Claude which one you want. All three only use leads and messages that passed the checks (see [Quality checks](QUALITY_CHECKS.md)). By default, a message also needs the AI's review pass before it can leave.

A campaign is the whole plan for finding and writing to people. **Delivery is the optional last step.** You can stop at a CSV file and use your own tools.

**Only verified email addresses leave Lead Generator.** The `email` column of an export, the lemlist push and the built-in sender all use verified addresses only. Addresses that were found but not verified are written to separate `unverifiedEmail` and `unverifiedEmailStatus` columns so you can see them, and guessed addresses are never used as the email. A project can be set to allow "risky" addresses too (the project page, "Which email addresses may be used"). That setting applies to campaigns you create afterwards.

**Nobody is delivered twice.** Everyone exported, pushed or emailed is remembered by the project. A later campaign in the same project does not offer them again until the cooldown (90 days unless you changed it) has passed.

## 1. CSV export

Ask Claude: "Export my campaign as a CSV for lemlist" (or "for Instantly", or "as a general CSV").

Lead Generator writes these files into the `exports` folder of your LeadGenerator folder, in a sub-folder named after the campaign (run `leadgen paths` to see where). The dashboard **Export** page has an "Open folder" button.

- `lemlist-<date-time>.csv`, `instantly-<date-time>.csv` or `generic-<date-time>.csv`: the leads and their messages.
- `qa_report.csv`: every person, whether they were exported, and the blocks, warnings and AI notes behind it.
- `removed.csv`: the people who were taken out, and why.

The files open correctly in Excel, including names with accents. They are saved with a byte-order mark (a hidden marker at the start). If Excel still shows odd characters, see [Troubleshooting](TROUBLESHOOTING.md).

Claude also tells you how many leads were exported, and what was held back (for example messages that failed checks, or addresses marked "guess").

### Importing into lemlist

1. In lemlist, create a new campaign, then add leads by importing a CSV file.
2. Choose the `lemlist-<date-time>.csv` file. lemlist shows a screen to match columns. Check that each column is matched. Keep the column names as they are, because lemlist variable names are case-sensitive.
3. Every row in lemlist's CSV import needs an email address. A person with only a LinkedIn profile cannot be imported this way. Find their email first, or add them by hand in lemlist from their LinkedIn profile.
4. Build the sequence with the variables from the file (see the recipe below).

Do not edit the text inside the `{{double braces}}` in your sequence. The messages were checked word for word.

#### The lemlist LinkedIn recipe

This is the sequence for LinkedIn outreach, with the message already written for each person:

1. **Step 1: LinkedIn invitation, left blank.** Many people send invites with no note. If you want a note, use `{{linkedinInvite}}`, which is limited to 200 characters. Choose this when you ask Claude to write invite notes.
2. **Step 2: a condition, "Accepted invite".** Wait at least 1 day.
3. **Step 3: a LinkedIn message** with the text `{{linkedinMessage}}`.

If you use email too, add email steps with the subject `{{emailSubject}}` and the body `{{emailBody}}`.

**Plan requirement:** lemlist's own pages say the "Accepted invite" condition needs one of its advanced plans (Multichannel Expert or Enterprise). That was the information when this guide was written. Check your plan and lemlist's pricing page before you build the sequence. If your plan does not have it, ask lemlist which options your plan offers for waiting until an invite is accepted.

### Importing into Instantly

1. In Instantly, upload `instantly-<date-time>.csv`.
2. Check the column matching. `email`, `first_name`, `last_name`, `company_name`, `job_title`, `linkedin_url`, `personalization` and `website` match Instantly's own fields.
3. Match the other columns as custom variables. A variable is named exactly like its column, with the same capital letters: `{{subject_line}}`, `{{message_body}}`, `{{follow_up_dm}}`, `{{invite_note}}`, `{{greeting}}` and `{{address_status}}`.
4. `personalization` holds the same text as `message_body`.
5. Instantly needs an email on every lead, so people without an email are left out of this file. They are listed in `qa_report.csv`.

Instantly's screens change. If a menu looks different, look for the closest match.

## 2. Push to lemlist (API)

This puts the leads straight into a lemlist campaign, without a CSV. It needs a lemlist API key (see [Providers](PROVIDERS.md)).

1. In lemlist, create the campaign first, and build the sequence from the recipe above. Lead Generator pushes into an existing campaign. It does not make one.
2. Ask Claude: "Push my leads to lemlist." Claude lists your lemlist campaigns and asks you to pick.
3. Claude does a **dry run** first. It shows what would be sent for three leads and the counts. Nothing is sent yet.
4. When you say yes, Claude pushes the leads in batches of 25.

Good to know:

- A lead already in that lemlist campaign is skipped. Pushing again is safe.
- Only verified email addresses are pushed. Leads with no verified email address are skipped for now, because we could not confirm that lemlist accepts them. They stay in Lead Generator. Add them in lemlist by hand, or find their email first.
- Leads that are missing a message you chose (for example an email message was not written) are held back, and Claude tells you. You can ask Claude to push them anyway.
- Lead Generator uses lemlist's rate limit of 20 requests every 2 seconds, so big pushes take a little while.

## 3. Sending email from your own mailbox

This is the most careful path. Lead Generator sends from your own email account, one email at a time, and reads your inbox to find replies, bounces and unsubscribes. Open and click tracking is off.

### Before you start: the readiness checklist

Ask Claude: "Check if my campaign is ready to send." Claude goes through this list with you:

| Item | What it means |
|---|---|
| Email channel | The campaign uses email. |
| Mailbox | Your mailbox is saved and the login test passes. |
| Sender | Your name, company and role are set on the dashboard Settings page (or in `settings.yaml`). They go in the email footer. |
| Postal address | A real postal address is set on the dashboard Settings page (or as `postal_address` in `settings.yaml`). Several countries require one in every commercial email. |
| Language | A language setting is set on the dashboard Settings page (or as `locale` in `settings.yaml`). |
| Sending settings | The `sending:` section of `settings.yaml` is valid. |
| Country rules | You have read and accepted the rule for each target country. See [Compliance](COMPLIANCE.md). For countries that need consent first, you must also write down your consent basis. |
| Messages | There are approved emails that passed every check. |
| Do-not-contact list | The list is loaded. |
| Not paused | The kill switch is off. |
| Bounce rate | Recent bounces are low. |
| Daily cap | You have room left today. |

### Set up your mailbox

In a terminal run:

```
leadgen mailbox set --preset gmail
```

(Use `--preset outlook` for Outlook or Microsoft 365. Without a preset, it asks for the server details.) It asks for your address and password, and hides the password as you type it. Then it tests the login.

The easier way for most people is the dashboard page **Connect your mailbox**, which walks you through Gmail in three steps. See [Connect your mailbox](CONNECT_MAILBOX.md).

- **Gmail and Google Workspace** need an **app password**, not your normal password. Turn on 2-Step Verification on your Google account first, then create the app password in your Google account's security settings. Google does not offer app passwords on some work or school accounts, or with Advanced Protection.
- **Outlook and Microsoft 365** may need your IT admin to switch on "SMTP AUTH" for your mailbox. Some accounts refuse passwords altogether. Lead Generator cannot use Microsoft's newer sign-in method yet.

Never type your mailbox password into the chat.

Run `leadgen mailbox test` any time to check the login again.

### Sender details and address

The easiest place is the dashboard **Settings** page (run `leadgen dashboard --open`). Fill in **Sender**, **Postal address** and **Language** there.

The other way is to edit `settings.yaml` in your LeadGenerator folder (run `leadgen paths`). Create the file if it is not there, then add these lines:

```
sender:
  name: Your Name
  company: Your Company
  role: Your Role
  website: https://www.example.com
postal_address: 1 Example Street, Singapore 000000
locale: en-GB
```

Use your real details. The text above is an example.

### Warm-up and caps

A new sending pattern can get your domain marked as spam. Lead Generator keeps the pace gentle:

- **Daily cap:** it starts at **20 emails a day** and rises by 5 each day, up to **50**. The cap counts across all campaigns, by your computer's calendar day.
- **Gaps:** it waits a random 1 to 3 minutes between emails.
- **Quiet hours:** it does not send between 20:00 and 08:00 in the recipient's local time.
- **It stops itself** if more than 5% of recent emails bounce (checked once there are at least 5 sent) or 3 sends in a row fail.

So a batch of 100 emails goes out over several days. This is normal and on purpose.

### Sending, step by step

1. Ask Claude to check readiness and fix each item.
2. Ask Claude to send. It does a **dry run** first. It shows each email exactly as it will be sent, with the footer and any subject label that a country requires, such as `<ADV>` for Singapore.
3. When you say yes, the emails are queued and a background program starts sending them. Most go out over the next hours or days.
4. Check progress with `leadgen send status`, or ask Claude.

Every email gets a footer with your name, company, website, postal address and a line such as "If you'd rather not hear from me, reply 'unsubscribe' and I won't email you again." It also carries an unsubscribe header that mail programs understand.

If the computer is off or asleep, sending waits. To start it again run `leadgen send-worker`, or ask Claude to send again.

### Stop sending

- `leadgen send pause` stops all sending at once (the kill switch).
- `leadgen send resume` starts it again. Look into why you paused first.

### Replies, bounces and unsubscribes

Ask Claude to "check my mailbox". Lead Generator reads your inbox **read-only**. It never deletes, moves, answers or marks anything as read. For emails it sent, it records:

- **Replies.** Claude tells you who replied. Replying is your job.
- **Out-of-office answers.** These are not counted as replies.
- **Bounces.** The address is marked invalid and put on the do-not-contact list. Waiting emails to it are cancelled.
- **Unsubscribes.** If someone writes something like "unsubscribe", "remove me", or "stop emailing", they go on the do-not-contact list at once, and their waiting emails are cancelled.

Ask Claude for the numbers ("how is my campaign doing?") or open the dashboard's **Analytics** page. Rates from fewer than 20 delivered emails are marked "too few to judge".

### The do-not-contact list

Anyone on the list is skipped in every check and by the sender, on every channel. To add someone ask Claude, or use the dashboard. To take someone off (only you can do this), run in a terminal:

```
leadgen suppression list
leadgen suppression remove email name@example.com
```

Use `domain` or `linkedin` in place of `email` for those kinds of entries.

## Which one should I pick?

| If you... | Use |
|---|---|
| already use lemlist or Instantly | CSV export |
| want LinkedIn steps with a "message after they accept" | lemlist (CSV or push) |
| want everything in one place and do your own email sending | CSV, then Excel or your email tool |
| are ready to do the sending setup and accept the care it needs | Sending from your mailbox |
