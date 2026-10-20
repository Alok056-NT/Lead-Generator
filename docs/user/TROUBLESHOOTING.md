# Troubleshooting

Find your problem in the list. Each answer starts with the quickest thing to try.

- [The installer does not start or stops](#the-installer-does-not-start-or-stops)
- ["leadgen is not recognised"](#leadgen-is-not-recognised)
- [Claude does not show the Lead Generator tools](#claude-does-not-show-the-lead-generator-tools)
- [ChatGPT cannot connect](#chatgpt-cannot-connect)
- [The AI says "needs a key" (needs_key)](#the-ai-says-needs-a-key)
- [Out of credits](#out-of-credits)
- [A website cannot be read](#a-website-cannot-be-read)
- [Excel shows odd characters](#excel-shows-odd-characters)
- [Antivirus blocks cloudflared](#antivirus-blocks-cloudflared)
- ["Port 8766 is already in use"](#port-8766-is-already-in-use)
- [The dashboard does not open](#the-dashboard-does-not-open)
- [Email problems](#email-problems)
- [Where to find the log](#where-to-find-the-log)

## The installer does not start or stops

**Windows says scripts are blocked or "running scripts is disabled".**
Save `install.ps1` to a folder, open PowerShell in that folder, and run:

```
powershell -ExecutionPolicy Bypass -File install.ps1
```

This runs only this one script and does not change your computer's settings.

**It says Python is missing or too old.**
You need Python 3.11 or newer. The installer prints the exact command to install it:

- Windows: `winget install --id Python.Python.3.12 -e` (or install it from python.org)
- Mac: `brew install python@3.12`

Then run the install command again.

**The Windows one-line command (`irm ... | iex`) says it cannot find the file.**
That command downloads the installer from GitHub (`raw.githubusercontent.com`). Check these causes:

- No internet connection.
- A proxy, antivirus or company network is blocking `raw.githubusercontent.com`.
- The line was not copied whole. Copy all of it, in one go.

If you have the project files on disk, run `install.ps1` from that folder instead.

**You want a particular version, or a different folder.**
Set these before you run the installer. In PowerShell:

```
$env:LEADGEN_REF = "v1.0.0"
$env:LEADGEN_VENV = "D:\Tools\LeadGeneratorEnv"
```

On a Mac, put them before the command: `LEADGEN_REF=v1.0.0 bash install.sh`. `LEADGEN_REF` picks the version (a branch or tag). `LEADGEN_VENV` picks where the private Python environment goes.

## "leadgen is not recognised"

The installer does not put `leadgen` on your PATH (the list of places your computer looks for programs). Use the full path the installer printed at the end. On Windows it looks like this:

```
"%LOCALAPPDATA%\LeadGenerator\venv\Scripts\leadgen.exe" doctor
```

On a Mac or Linux: `~/.local/share/LeadGenerator/venv/bin/leadgen doctor`.

## Claude does not show the Lead Generator tools

1. **Quit Claude Desktop completely and open it again.** Closing the window is not enough. Windows: right-click the Claude icon in the system tray (bottom right, you may need the little arrow) and choose Quit. Mac: Claude menu, Quit Claude.
2. Run `leadgen doctor`. It should end with "All good."
3. Run `leadgen connect claude` again. It is safe to repeat. It tells you which settings file it changed.
4. In Claude Desktop open Settings, then Developer. Look at the log for `lead-generator`. The file `mcp-server-lead-generator.log` in `%APPDATA%\Claude\logs` (Windows) or `~/Library/Logs/Claude` (Mac) shows why the server did not start.
5. If Claude keeps its settings somewhere unusual (for example the Microsoft Store version of the app), go to Settings, Developer, Edit Config to see the real file, then run `leadgen connect claude --config "<that file>"`.
6. Still stuck? Try Claude Code instead: `leadgen connect claude-code`.

Full steps: [Connect Claude](CONNECT_CLAUDE.md).

## ChatGPT cannot connect

ChatGPT reaches your computer through a temporary secure link. Check these in order:

1. **The window running `leadgen connect chatgpt` must still be open.** Closing it switches the link off.
2. **Copy the whole address,** including the long secret at the end, and use the address printed in *this* run. It changes every time you start.
3. In ChatGPT, open your Lead Generator connection and press **Refresh**.
4. If you see no "Add custom MCP server" button, your ChatGPT plan or workspace does not allow it (yet). The rules and menu names change, so check on screen. On a Business, Enterprise or Education workspace an admin must allow custom connectors. Or use Claude.
5. Some ChatGPT plans do not allow actions that change data. If ChatGPT refuses to save a campaign, that is probably the plan, not a fault in Lead Generator.
6. If you think the address leaked, close the window and run `leadgen serve --rotate-secret`. The old address stops working.

If ChatGPT seems lost, say: "Call get_workflow_guide first and follow it."

More: [Connect ChatGPT](CONNECT_CHATGPT.md).

## The AI says "needs a key"

A "needs key" (`needs_key`) message means a step uses a service you have not set up. The AI should tell you which one, what it is for, its free allowance and where to sign up.

Choose one:

1. **Add the key.** Sign up (see [Providers](PROVIDERS.md)), then paste the key on the **API keys** page of the dashboard (`leadgen dashboard --open`), or run `leadgen keys set serper` (replace `serper` with the name the AI gave).
2. **Use free mode** for searches: say "use free mode". See [Free mode](FREE_MODE.md).

After adding a key, tell the AI to try again. You can run `leadgen keys list` to see which keys are set (only the last 4 characters are shown) and `leadgen keys test serper` to check one.

## Out of credits

When a service runs out, the AI tells you ("quota exhausted"). Nothing is lost: your leads stay in Lead Generator.

- **Serper** (search): buy more credits, add a Brave key, or use [free mode](FREE_MODE.md).
- **Hunter** (email patterns, monthly credits): wait for next month. Lead Generator uses the company's website and a standard pattern meanwhile, and marks unchecked addresses "guess".
- **Reoon** (email checks, 20 a day): continue tomorrow, or add a ZeroBounce key.
- **Budget reached:** each campaign has a budget for searches and checks so one run cannot use everything. The AI asks before going past it. Answer yes when it asks you to go over the budget, or ask Claude to raise this campaign's budget (the AI does this with `update_campaign`).

Details for each service: [Providers](PROVIDERS.md).

## A website cannot be read

When Lead Generator reads your offer's website (or a company's contact page) it follows the site's own rules: it respects the `robots.txt` file, goes slowly, and does not try to get around a block. Some sites refuse, or build their pages in a way that needs a full browser, so almost no text comes back.

What to do:

- **Paste the text** of your page into the chat.
- Or **give a document** (PDF, Word, text file) with the same information.
- For a Google Doc, set sharing to "anyone with the link can view", then give the link.

Lead Generator will not read private pages or pages that need a login.

## Excel shows odd characters

Names with accents (for example "José" showing as "JosÃ©") mean Excel did not read the file as UTF-8. Lead Generator saves its CSV files with a hidden marker (a "BOM") that makes Excel read them correctly when you double-click them. If you still see odd characters:

1. Open a blank workbook in Excel.
2. Go to **Data**, then **From Text/CSV**.
3. Choose the file. Set **File Origin** to **65001: Unicode (UTF-8)**.
4. Click **Load**.

If you saved the file again from another program, it may have lost the marker. Use the original file from the `exports` folder. Google Sheets reads these files correctly (File, Import, Upload).

## Antivirus blocks cloudflared

`cloudflared` is a small free program from Cloudflare that makes the secure link for ChatGPT. Some antivirus and firewall programs block it, or delete it after download.

1. Install it with the command `leadgen connect chatgpt` prints (Windows: `winget install --id Cloudflare.cloudflared`; Mac: `brew install cloudflared`).
2. If your antivirus quarantines it, restore it and add `cloudflared.exe` to the allowed programs list. Only do this if you installed it yourself from the command above.
3. If a company firewall blocks it, try another network (for example a phone hotspot), or ask your IT team.
4. When the link cannot start, the command prints the last messages from cloudflared. Read those.

Claude does not need `cloudflared` at all.

## "Port 8766 is already in use"

Something else on your computer uses the port that ChatGPT's link needs (8766). Pick another:

```
leadgen connect chatgpt --port 8777
```

You can choose any number between 1024 and 65535 that is free. You may also have a second window running the same command. Close it first.

## The dashboard does not open

- Run `leadgen dashboard --open`. It opens `http://127.0.0.1:8765` in your browser. The number 8765 is the setting `dashboard.port` in `settings.yaml`.
- If the port is taken, use another: `leadgen dashboard --open --port 8790`.
- Ask the AI to "open the Lead Generator dashboard". It starts the page in the background and gives you the address.
- The dashboard only works on your own computer. You cannot open it from your phone or another computer.
- A background dashboard keeps running until you restart your computer. That is safe, because it only listens to your own computer.

## Email problems

**The mailbox login fails.**
Run `leadgen mailbox test`. Gmail and Google Workspace need an **app password**, which needs 2-Step Verification. Outlook and Microsoft 365 may need your IT admin to switch on SMTP sending, and some accounts refuse passwords. See [Providers](PROVIDERS.md#your-mailbox-for-sending-email-optional).

**Emails are queued but nothing is sent.**
Run `leadgen send status`. Common reasons: sending is paused (`leadgen send resume`), it is the recipient's quiet hours, today's cap is used up, or the sending program is not running (`leadgen send-worker` starts it).

**Sending stopped by itself.**
It stops when more than 5% of recent emails bounce, or three sends fail in a row. Look at the reason with `leadgen send status`. Check your email addresses and your mailbox, then run `leadgen send resume`.

**Someone says they did not want to hear from you.**
Ask Claude to check your mailbox (it adds unsubscribes to the do-not-contact list), or add them yourself: `leadgen suppression add email name@example.com`.

## Where to find the log

Lead Generator keeps a log at `logs\leadgen.log` inside your LeadGenerator folder (run `leadgen paths` to see where). It never contains keys or full email text. If you ask for help, `leadgen doctor` output is the best thing to include.

Still stuck? Ask the AI: "Call get_status and tell me what to do next." It will check what is set up and suggest the next step.
