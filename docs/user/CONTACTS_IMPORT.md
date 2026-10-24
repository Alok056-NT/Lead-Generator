# Import your old contacts

If you already know or have contacted people (old LinkedIn connections, past email lists, customers, people who asked you to stop), you can tell Lead Generator about them. It then does not offer them again, or marks them as people you already know.

Contacts belong to one **project**. A person you uploaded to one project is not held back in another project.

## What the upload can do

You choose one of three things:

| Choice | What happens |
|---|---|
| **Don't contact these people again** (`exclude`) | These people are not offered in a campaign of this project until the project's cooldown has passed (90 days unless you changed it). |
| **Use them as warm leads** (`signal`) | They are kept as "people I already know". Messages may say you have spoken before, but only for people you marked as replied, customer or partner. Their companies show as warm. They are **not** held back. |
| **Both** (`both`) | Leave them out and also mark them as known contacts. |

Anyone you mark **do not contact** goes on the do-not-contact list for good, whatever you choose. That list is checked for every campaign, on every channel. (A row with only a name and no email, website or LinkedIn link cannot go on that list; it only holds the person back for the cooldown.)

## Step 1: get the template

Any of these:

- In the dashboard, open your project, choose **Contacts you already have** and download the **Excel template** or the **CSV template**.
- Ask your AI assistant: "Give me the contacts template." It tells you where it saved the files.
- In a terminal: `leadgen contacts template` (CSV) or `leadgen contacts template --xlsx` (Excel).

The Excel file has a second sheet, "How to fill this in", with one line per column.

## Step 2: fill it in

One row per person. A row needs **an email address, or a LinkedIn link, or a name together with a company**. The more it has, the better the matching.

| Column | What to put |
|---|---|
| `first_name`, `last_name` | The name. Or fill `full_name` instead. |
| `email` | Work or personal email address. |
| `linkedin_url` | The link to their LinkedIn profile (linkedin.com/in/...). |
| `company`, `company_domain` | The company name and website. |
| `title`, `country` | Optional. |
| `last_contacted` | The last time you contacted them, for example `2026-09-30` or `30/09/2026`. Empty means today. The cooldown counts from this date. |
| `channel` | How: `email`, `linkedin` or `both`. |
| `relationship` | `contacted` (the default), `replied`, `customer`, `partner` or `do_not_contact`. |
| `notes` | Anything you want to remember. |

Column order does not matter, and extra columns are ignored. Files can be `.csv` or `.xlsx`, up to 20 MB and 50,000 rows. Accents and other languages are fine.

## Using your LinkedIn contacts

LinkedIn lets you download your own connections list. Lead Generator reads that file as it is; you do not need to change it.

1. In LinkedIn, open the **Me** menu, then **Settings & Privacy**.
2. Choose **Data privacy**, then **Get a copy of your data**.
3. Choose the option that includes connections ("Download larger data archive, including connections") and click **Request archive**.
4. LinkedIn emails a download link to your main email address. LinkedIn's help page says the larger archive arrives within 24 hours and the link works for 72 hours. LinkedIn's screens change, so look for the closest match if a menu looks different.
5. Download and unzip the file. Inside is your connections list (`Connections.csv`).
6. Upload that file in the dashboard (**Contacts you already have**), or give its path to your AI assistant.

Good to know:

- LinkedIn only includes an email address when the connection allowed it. Many rows have none, and that is fine; the LinkedIn link is used.
- LinkedIn's file has the date you connected, not the date you last wrote to someone. Lead Generator keeps that date only as a note, so with "Don't contact these people again" everyone is held back for a full cooldown from the day you upload. This is on purpose (the careful choice).
- This is a file **you** download from your own account. Lead Generator never logs in to LinkedIn.

## Step 3: check first, then add

Always check before adding:

1. **Dashboard:** open the project, choose **Contacts you already have**, pick the file and what to do, and press **Check the file**. You see how many people were found, which rows could not be used and why, and how many would be held back. Nothing is saved yet. If it looks right, press **Add these contacts**.
2. **With your AI assistant:** say "Import my contacts file". It checks first, shows you the counts and the first problems, and only adds the contacts after your clear yes.
3. **Terminal:** `leadgen contacts import <file> --project <number> --mode exclude` only previews. Add `--apply` to save.

The preview is exact. Adding the same file again does not create duplicates, and a newer `last_contacted` for the same person moves the date forward.

A row that cannot be understood is listed and skipped; it never stops the rest. A row is skipped when it has an unknown relationship or a date that cannot be read (so nobody looks safer than you meant), or when nothing identifies the person.

## Where you see the result

- In a campaign, a person who is held back appears under **Needs a look** with the reason. Nobody is deleted.
- People you marked as known show a **Known contact** badge in the people list, and you can filter to "Only people I already know". Companies with known people show a **Warm** badge.
- The project page shows who is held back and until when.

## Problems

- **Nothing identifies this person:** add an email, a LinkedIn link, or a name with a company.
- **A date is not understood:** use `2026-09-30`, or an Excel date cell.
- **Strange characters:** save the file as CSV UTF-8, or use the Excel template.
- More: [Troubleshooting](TROUBLESHOOTING.md).
