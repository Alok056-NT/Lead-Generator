# Owner test checklist: Lead Generator v1.1

For: Alok. Plan about 2 to 3 hours. Tick each box as you go. If something does not look like the "You should see" line, do not worry: write it down (see "How to report a problem" at the end) and carry on with the next check.

Nothing here sends a real email or a LinkedIn message. Where a check needs a real account (Gmail, a real website, your LinkedIn file), it says so.

## Before you start (10 minutes)

1. **Make a backup.** Open your LeadGenerator folder. If you do not know where it is, open a terminal and type `leadgen paths`. Copy the whole folder somewhere safe (for example, paste it on the Desktop and name it `LeadGenerator backup`).
2. **Update the program.** Run the same install command you used the first time (it is in the README). Wait until it says it is finished.
   - Installed by hand from the source folder instead? Open a terminal in that folder and run `pip install -e .`
3. **Check the version.** In a terminal type `leadgen version`.
   - You should see: `1.1.0`.
4. **Quit and reopen Claude Desktop** completely (quit from the tray icon on Windows or the Claude menu on a Mac; closing the window is not enough).

---

## Part A: your old work is still there

**Check 1. The dashboard opens and your old campaigns are in a project.**
1. In a terminal type `leadgen dashboard --open`. A browser page opens.
2. Look at the top: you should see the menu **Projects, Campaigns, Results, Keys, Settings**.
3. You should see: the **Projects** page, with at least one project card. Your old campaigns are inside projects that were made for you (one per offer). Each card shows the name, how many campaigns, people ready and verified emails.
4. Click one project card, then one of its campaigns. Click **2 People**.
- You should see: the same people you had before. Counts look the same as before the upgrade.
- If anything is missing or the page shows an error, stop and report it (this is the most important check).

**Check 2. Claude still talks to Lead Generator.**
1. Open a new chat in Claude Desktop and type: `What is my Lead Generator status?`
- You should see: Claude uses the Lead Generator tool and answers in plain words, naming your project(s).

---

## Part B: projects (sprint items 1, 5, 9)

**Check 3. Create a project from one web page.**
1. In the dashboard, click **Projects**, then **New project**.
2. In **Project name** type `Test project one`.
3. Under **Attach your product or service** choose **A website address**. Type your real company website address.
4. Choose **Just this page**. Leave the days box at 90. Click **Create the project**.
- You should see: a message that it read **1 page** and a "Next: open your AI assistant and say: ..." line with a **Copy this sentence** button.
- The project page shows **What we read**, listing exactly one page.

**Check 4. Replace it with the whole site.**
1. On the project page click **Replace the source**.
2. Choose **A website address**, type the same address, choose **Whole website (up to 15 pages)** and click the button.
- You should see: a progress line while it reads ("8 pages read so far"), then a summary such as "Read 12 pages from ...". It may take a minute or two.
- **What we read** now lists several pages. (If your site really has one page, you will see one.)

**Check 5. "Campaign" means the whole plan.**
1. On the project page click **New campaign**.
- You should see: a form asking for countries, sectors, **Email** and **LinkedIn** boxes (with the note that a campaign plans both and sending is optional and comes later), job roles, lowest level, and **How big should it be?**.
- Nothing on this page should ask you to send anything.

---

## Part C: campaign size and splitting people (items 2, 4)

**Check 6. Make a small campaign with a size limit.**
1. On the New campaign form: name it `Wave 1`, choose one country, type a sector, tick **LinkedIn**, pick two job roles.
2. Under **How big should it be?** type `2` in **Most people per company (p)**. Leave the others empty. Click **Create the campaign**.
- You should see: the campaign page with a **Size plan** line that mentions 2 people per company.

**Check 7. Let the AI find people (this uses a few search credits or free mode).**
1. In Claude say: `Continue my Lead Generator campaign Wave 1`.
2. Let Claude add companies (suggest about 5 to 8 real companies you care about), approve them, and find people. It must tell you the cost and wait for your yes before searching. Say yes (or "use free mode").
- You should see: at most **2 confirmed people per company**. Claude tells you some good people were **saved for a later campaign**.
- In the dashboard, open **Wave 1** and look at **People saved for a later campaign**. It lists them.

**Check 8. A second campaign takes the saved people.**
1. Go to the project page, click **New campaign**, name it `Wave 2`.
2. Set **Most people per company (p)** to `2`. In **Take people saved by another campaign?** choose `Wave 1`. Click **Create the campaign**.
- You should see: the new campaign already has the people that were saved in Wave 1.
- Open **2 People** in both campaigns. **No person appears in both.**

---

## Part D: not contacting the same person twice (item 2)

**Check 9. Export, then try again.**
1. Ask Claude to write and check the messages for Wave 1, then export it as a CSV file (this is just a file; nothing is sent).
2. Make a third campaign `Wave 3` in the same project and ask Claude to find people again for the same companies.
- You should see: people from Wave 1's export are **not** offered again. In **2 People** they appear under **Needs a look**, with a reason saying they were already found or contacted. The project page shows them under **Who we will not contact again yet**, with a date.

**Check 10. Change the cooldown.**
1. On the project page find **Don't contact the same person again for this many days**. Change `90` to `30` and save.
- You should see: the page accepts it. (Setting `0` turns the memory off; do not test this on a real project.)

---

## Part E: your old contacts (item 7)

**Check 11. Download the template.**
1. On the project page choose **Contacts you already have**.
2. Click **Download the Excel template**, then **Download the CSV template**. Open the Excel file.
- You should see: columns such as first_name, last_name, email, linkedin_url, company, last_contacted, relationship, and a second sheet called **How to fill this in**.
- Type two made-up rows (for example "Test Person" at "Example Co") and save the file.

**Check 12. Upload and check your made-up file.**
1. Back on **Contacts you already have**, choose your file, choose **Don't contact these people again**, and click **Check the file**.
- You should see: how many people were found and how many rows could not be used. Nothing is saved yet.
2. Click **Add these contacts**.
- You should see: a confirmation. The project page now says how many contacts you added.

**Check 13. Your real LinkedIn contacts file (needs your LinkedIn account).**
1. Follow [Import your old contacts](user/CONTACTS_IMPORT.md), "Using your LinkedIn contacts". Request the archive (it can take up to 24 hours, so you can do this the day before).
2. Upload the `Connections.csv` file. Choose **Use them as warm leads**, click **Check the file** and read the preview.
- You should see: a sensible count of people and few problem rows. Only click **Add these contacts** if you want it saved in a test project. Do not worry if many rows have no email; LinkedIn leaves it out on purpose.
- In a campaign, those people show a **Known contact** badge, and **Only people I already know** filters the list to them.

---

## Part F: only verified emails (item 3)

**Check 14. Export with emails.**
1. Use a test campaign with the **Email** box ticked and your Hunter and Reoon keys saved on the **Keys** page. Ask Claude to find emails and export a CSV for lemlist.
2. Open the CSV in Excel.
- You should see: the **email** column only has addresses marked **verified** (look at the **emailStatus** column). Other found addresses are in **unverifiedEmail** and **unverifiedEmailStatus**.
- On the project page, **Which email addresses may be used** says "verified only".

---

## Part G: your mailbox (item 6; needs a real Gmail or Google Workspace account)

**Check 15. Connect your mailbox.**
1. In the dashboard click **Settings**, then follow the link **Connect your mailbox**. (Also try the link on the Home page.)
2. Read the page. Click **Turn on 2-Step Verification**.
- You should see: Google's 2-Step Verification page opens in a new tab (sign in if asked).
3. Back on the page, click the app password button, type `Lead Generator`, press **Create** on Google's page and copy the 16 letters.
4. Paste your address and the 16 letters, save, then click **Test it again (sends nothing)**.
- You should see: a success message. The page shows only the last 4 characters of the password.
- If Google offers no app password for your account, that is expected for some work accounts: the page should say so in plain words. Report which message you saw.

---

## Part H: plain language and the rest (items 5, 8, 9)

**Check 16. No technical words.**
1. Click through every menu item and open a project, a campaign (**Overview, 1 Companies, 2 People, 4 Messages, 6 Export or send, Results**), **Keys** and **Settings**.
- You should see: each page has a short **What's this?** line, and the words make sense to you. Note any word you do not understand (for example a code such as "L24", or "QA").

**Check 17. Sending is optional and needs your yes.**
1. Open a campaign, go to **6 Export or send**.
- You should see: the download section first, and a separate optional section for sending from your mailbox that says you do not have to use it.
- Nothing is sent unless you press a clear send button after a dry run. Do not send a real email in this test.

**Check 18. The free-tools report.**
1. Open the file `docs/ALTERNATIVES.md` in the Lead Generator folder (or ask Claude to summarise it).
- You should see: a pro and con list of free or open-source options for the tunnel, search and email finding or checking. Nothing was switched; you decide.

**Check 19. Close and reopen.**
1. Close the dashboard (press Ctrl+C in the terminal) and open it again with `leadgen dashboard --open`.
- You should see: all your projects, campaigns and people exactly as before.

**Check 20. Final look at the backup.**
1. Compare the old campaigns' people counts with your note from Check 1.
- You should see: the same numbers.

---

## How to report a problem

Write it down as you find it. For each problem, send:

1. **The check number** (for example "Check 8").
2. **What you did** in one or two sentences.
3. **What you expected** (the "You should see" line) and **what happened instead**.
4. **A screenshot** of the page, including any red message.
5. **The log**: the file `leadgen.log` in the `logs` folder inside your LeadGenerator folder (`leadgen paths` shows where). Attach it. It never contains your keys.
6. How serious it is: **blocks me** (cannot continue), **wrong** (works but the result is wrong) or **ugly** (works, looks or reads badly).

Please do not send screenshots or files that show real keys or other people's private details. If a screen shows a key, only the last 4 characters should be visible; tell us if you ever see more.

To go back to how things were: close everything and replace your LeadGenerator folder with your `LeadGenerator backup` copy.
