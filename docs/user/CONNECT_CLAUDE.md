# Connect Lead Generator to Claude

Lead Generator runs on your own computer. "Connecting" means telling Claude where to find it. You only do this once.

Pick the Claude app you use:

- [Claude Desktop](#claude-desktop) (the app for Windows and Mac). Easiest.
- [Claude Code](#claude-code) (the coding assistant in a terminal).

Before you start, install Lead Generator (see the main [README](../../README.md)). The installer asks "Connect Claude Desktop now?" and does the Claude Desktop part for you. If you said yes, skip to step 3 below.

## Claude Desktop

### 1. Run one command

Open PowerShell (Windows) or Terminal (Mac) and run:

```
leadgen connect claude
```

If your computer says it does not know `leadgen`, use the full path the installer printed at the end. On Windows it looks like this:

```
"%LOCALAPPDATA%\LeadGenerator\venv\Scripts\leadgen.exe" connect claude
```

What this does:

- It finds Claude Desktop's settings file:
  - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
  - Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`
- It saves a copy of the old file next to it, named `claude_desktop_config.backup-<date-time>.json`.
- It adds one entry called `lead-generator`. It does not touch anything else in the file.
- If the file is damaged (not valid JSON), it stops, leaves the file alone and tells you.

You can run the command again at any time. If nothing needs changing, it says so.

### 2. Fully quit and reopen Claude Desktop

Closing the window is not enough.

- Windows: right-click the Claude icon in the system tray (bottom right, you may need the little arrow), choose Quit, then open Claude again.
- Mac: in the menu bar choose Claude, then Quit Claude, then open it again.

[Screenshot placeholder: the Quit option in the system tray]

### 3. Check that it works

Start a new chat and ask:

> What is my Lead Generator status?

Claude should use a tool called `get_status` and tell you what is set up and what to do next. Claude may ask your permission the first time it uses a tool. Choose allow.

You can also look at the list of tools. In the chat box choose the "Add files, connectors, and more" button (the plus or slider icon at the bottom left), then Connectors. `lead-generator` should be listed.

[Screenshot placeholder: lead-generator shown in the connectors list]

### If the tools do not appear

1. Make sure you fully quit and reopened Claude (step 2).
2. Run `leadgen doctor`. It should end with "All good."
3. Open Claude Desktop's settings, go to Developer, and look at the logs button. The file `mcp-server-lead-generator.log` (in `%APPDATA%\Claude\logs` on Windows, `~/Library/Logs/Claude` on Mac) shows why the server did not start.
4. Run `leadgen connect claude` again and confirm it prints the path you expect.
5. If the settings file is somewhere unusual, open Claude Desktop, go to Settings, Developer, press "Edit Config" to see the real location, then run `leadgen connect claude --config "<that file>"`.

### To disconnect

```
leadgen disconnect claude
```

This removes only the `lead-generator` entry (after saving a backup). Quit and reopen Claude Desktop afterwards.

## Claude Code

Run:

```
leadgen connect claude-code
```

If the `claude` program is installed, this runs the right command for you:

```
claude mcp add --scope user lead-generator -- <path to leadgen> mcp
```

"User scope" means Lead Generator is available in every project you open with Claude Code. If `claude` is not installed, the command prints the line above so you can run it yourself later. Start a new Claude Code session and ask "What is my Lead Generator status?".

To remove it again: `claude mcp remove lead-generator --scope user`.

## Add your free keys

Claude can search for companies and people once you add a search key. The easiest way is the local web page:

```
leadgen dashboard --open
```

It opens at `http://127.0.0.1:8765` (the number is `dashboard.port` in your `settings.yaml`). Paste keys on the "API keys" page. They stay on your computer, and the page only ever shows the last 4 characters. You can also just ask Claude: "Open the Lead Generator dashboard".

## Using ChatGPT instead

See [CONNECT_CHATGPT.md](CONNECT_CHATGPT.md).
