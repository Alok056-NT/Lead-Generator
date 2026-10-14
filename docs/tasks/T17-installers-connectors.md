# T17: Installers and AI connectors (Claude, ChatGPT)

Depends on: T13, T16. PLAN section 9.

## Goal
A non-technical user can install with one command and connect Claude Desktop, Claude Code or ChatGPT without editing files by hand.

## Verify first (DECISIONS.md)
- Claude Desktop config file location and JSON shape for local stdio servers on Windows and macOS (`mcpServers` → `{command, args, env}`), and whether Desktop must be restarted.
- `claude mcp add` syntax (scope flags) for Claude Code.
- ChatGPT: current official steps and requirements for adding a custom MCP connector (plan tiers, Developer Mode location, auth options "none"/OAuth, transport, read/write limits). Use OpenAI's help center / platform docs; record exact menu names and the date.
- `cloudflared` quick tunnel command and output format (`https://<random>.trycloudflare.com`), Windows install options (winget id).

## Deliverables
### `install.ps1` (Windows) and `install.sh` (macOS/Linux)
- Check Python ≥ 3.11 (`py -3.11`/`py -3`, `python3`); if missing, print the exact winget/brew command and stop (do not auto-install system software without the user's yes).
- Create venv at `%LOCALAPPDATA%\LeadGenerator\venv` (Windows) or `~/.local/share/LeadGenerator/venv`.
- `pip install "lead-generator @ git+https://github.com/Alok056-NT/Lead-Generator.git@<tag-or-main>"`.
- Run `leadgen doctor`, then ask (Y/n) to connect Claude Desktop (`leadgen connect claude`).
- Print next steps in plain English.
- Safe to re-run (upgrades in place).

### `services/connect.py` + CLI `leadgen connect <target>`
- `claude`: locate config; back up to `claude_desktop_config.backup-<timestamp>.json`; merge `mcpServers["lead-generator"] = {"command": "<venv>/Scripts/leadgen.exe" (or bin/leadgen), "args": ["mcp"]}`; preserve every other key; validate JSON; tell the user to fully quit and reopen Claude Desktop.
- `claude-code`: run `claude mcp add lead-generator --scope user -- <leadgen> mcp` if `claude` is on PATH, else print the command.
- `chatgpt`: alias for `leadgen serve --public` (below) plus printed instructions.
- `leadgen disconnect claude`: remove our entry only.

### `leadgen serve --public` (ChatGPT)
- Start MCP Streamable HTTP (T13) on localhost with the secret path.
- Find `cloudflared` (PATH, common install dirs). If missing, print the install command and stop.
- Start `cloudflared tunnel --url http://127.0.0.1:<port>`; parse the public URL from its output; print `https://<random>.trycloudflare.com/mcp/<secret>` with step-by-step ChatGPT instructions (as verified) and a clear warning: anyone with this URL can use your Lead Generator while this window is open; close the window to stop it.
- Clean shutdown of both processes on Ctrl+C.
- `--rotate-secret` regenerates the secret.

### Docs
- `docs/user/CONNECT_CLAUDE.md`, `docs/user/CONNECT_CHATGPT.md` with screenshots placeholders and the verified menu names.

## Tests
- Config merge: empty file, existing servers preserved, existing lead-generator entry updated, invalid JSON → backup + clear error (no overwrite).
- `disconnect` removes only our entry.
- cloudflared output parsing from a captured synthetic log.
- Installer scripts: `PSScriptAnalyzer` not required; reviewer runs them manually on Windows.

## Acceptance
Tests pass. Manual on the owner's Windows PC: run `install.ps1`, connect Claude Desktop, see the tools in Claude; run `leadgen serve --public` and add the URL in ChatGPT (owner does the ChatGPT clicks).
