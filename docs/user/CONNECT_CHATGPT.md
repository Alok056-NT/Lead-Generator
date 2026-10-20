# Connect Lead Generator to ChatGPT

ChatGPT runs on the internet, so it cannot reach a program on your computer by itself. Lead Generator solves this with a temporary secure link (a "tunnel") that only exists while a window on your computer stays open.

Claude is simpler and does not need any of this (see [CONNECT_CLAUDE.md](CONNECT_CLAUDE.md)). Use ChatGPT if that is what your team already has.

## What you need

- A ChatGPT plan that allows custom connectors. OpenAI's pages do not list the plans clearly and the rules change, so check on screen: the "Add custom MCP server" button must be available to you. Reports from other sites say Plus and Pro accounts work (possibly read-only) and Business, Enterprise and Education need a workspace admin to allow custom connectors. If you do not see the button, ask your ChatGPT workspace admin.
- Lead Generator installed (see the main [README](../../README.md)).
- A free tool called `cloudflared`, which makes the secure link. If you do not have it, the first command below tells you what to type:
  - Windows: `winget install --id Cloudflare.cloudflared`
  - Mac: `brew install cloudflared`
- A computer that stays on and connected while you work.

## Steps

### 1. Start the link

Open PowerShell (Windows) or Terminal (Mac) and run:

```
leadgen connect chatgpt
```

(`leadgen serve --public` does the same thing.) After a few seconds it prints a web address that looks like this:

```
https://some-random-words.trycloudflare.com/mcp/AbCdEf...
```

The long part at the end is a secret. Copy the whole address. Leave this window open.

Important:

- Anyone who has this address can use your Lead Generator, see your leads and spend your search credits while the window is open. Do not post it or send it around.
- Closing the window (or pressing Ctrl+C) switches the link off immediately. The random words change every time you start it.
- If you think the address leaked, close the window and run `leadgen serve --rotate-secret`. The old secret stops working forever.

### 2. Add it in ChatGPT

Do this in ChatGPT on the web, on a computer. The menu names below are from OpenAI's current instructions (checked 2026-10-20). OpenAI changes them often, so if a name is slightly different on your screen, look for the closest match.

1. Go to `https://chatgpt.com/plugins` and click the **+** (plus) button.
2. Choose **Add custom MCP server**.
3. Give it a name such as "Lead Generator" and a short description.
4. Paste your address as the server URL.
5. For authentication choose **No authentication**. (The secret in the address is the protection.)
6. Read the warning, then tick **I understand and want to continue**.
7. Click **Create as a plugin**. ChatGPT lists the tools it found. There are about 45.
8. Start a new chat, type **@** and pick **Lead Generator**.

[Screenshot placeholder: the Add custom MCP server form]
[Screenshot placeholder: the tools list after creating]

If you cannot find step 1 or 2, an older version of ChatGPT put the same thing under **Settings**, then **Connectors** (or **Apps**), then **Advanced**, where you switch on **Developer mode** and then press **Create**. Business and Enterprise workspaces need an admin to allow this.

### 3. Try it

In the new chat ask:

> What is my Lead Generator status?

ChatGPT should call the `get_status` tool. Approve the tool call if it asks. Then say what product or service you want leads for.

## Things to know

- ChatGPT may ignore the built-in "prompt" that tells the AI how to work. That is fine: the AI can fetch the same instructions with the `get_workflow_guide` tool. If it seems lost, say: "Call get_workflow_guide first and follow it."
- Some ChatGPT plans limit tools that change data ("write" actions). If ChatGPT refuses to save a campaign or key, that is the plan limit, not a Lead Generator fault. Claude has no such limit.
- Paste API keys on the local dashboard page (`leadgen dashboard --open`), not into the chat. A chat is stored by ChatGPT.
- The link needs the window open. If you restart your computer or close the window, run the command again. The address changes each time, so update the server URL in ChatGPT (open the connection on `chatgpt.com/plugins`, edit the URL, and press Refresh).
- Cloudflare's free "quick tunnels" are meant for testing: no uptime promise and a limit of 200 requests in flight at once. That is plenty for one person chatting.
- Free AI-search mode (when you have no search credits) needs ChatGPT's own web search to be on in the same chat. We have not confirmed that it works together with a custom connector, so Claude is the safer choice for that mode.

## If it does not work

| Problem | What to try |
|---|---|
| "cloudflared is not installed" | Run the install command it prints, then start again. |
| Antivirus or firewall blocks it | Allow `cloudflared.exe`, or try another network. The command prints the last messages when the link cannot start. |
| "Port 8766 is already in use" | Run `leadgen connect chatgpt --port 8777`. |
| ChatGPT says it cannot connect | Check the window is still open, the address was copied completely (including the secret), and that you used the address printed in this run. Press Refresh on the connection. |
| No "Add custom MCP server" button | Your plan or workspace does not allow it yet. Ask your admin, or use Claude. |

More help: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
