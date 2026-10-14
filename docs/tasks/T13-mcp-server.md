# T13: MCP server

Depends on: T11 (T12, T14, T15 tools can be stubbed returning `{"ok": false, "error": {"code": "not_available_yet"}}` until those tasks land; add them for real as each task finishes). PLAN sections: 2, 8, 9, 10.

## Goal
Expose every service as MCP tools so Claude Desktop, Claude Code and ChatGPT can drive the workflow; ship the workflow guide as both a prompt and a tool.

## Verify first (DECISIONS.md)
- Install `mcp` (official Python SDK, v2.x) and read its installed source to find: the high-level server class and decorator API (FastMCP was reportedly renamed in v2; confirm), how to run stdio, how to run Streamable HTTP with a custom path and host/port, how prompts are declared, sync vs async tool handlers, how tool annotations (read-only/destructive hints) are declared, and the in-memory client used for testing.
- Pin the dependency to the major version you verified (`mcp>=2,<3`).
- Claude Desktop per-call time limit (~240 s) and result size limit (~150k chars): keep tool results small (paginate; truncate text fields).

## Deliverables
### `src/leadgen/mcp_server.py`
- `build_server() -> Server` registering all tools in PLAN section 10 with:
  - clear names (exactly as PLAN), one-paragraph descriptions written for an AI (what it does, when to call it, what it costs, what to ask the user first);
  - typed parameters with descriptions; JSON-serialisable results;
  - annotations: read-only tools marked read-only; spending/sending tools marked as such (`find_people`, `find_emails`, `push_to_lemlist`, `send_emails`, `search_web`, `save_api_key`).
- Every tool body: call exactly one service function; catch `ProviderError`/validation errors and return the error shape from PLAN section 10; never return secrets; truncate long strings (> 2,000 chars) except in `ingest_offer_source` chunks and `get_workflow_guide`.
- Prompt `lead_generation` = `guides/workflow.md`. Tool `get_workflow_guide` returns the same text plus `messaging_style.md` and `offer_profile.md` on request (`section` param).
- `src/leadgen/guides/workflow.md`: the step-by-step script in PLAN section 8, written as instructions to the AI, including:
  - when to ask the user vs proceed; always show estimates before spending; confirmation wording;
  - how to present lists (tables of ≤ 20 rows; offer the dashboard link for more);
  - never fabricate; say plainly what was skipped and why;
  - recommend adding keys via the dashboard (keys pasted into chat are stored in chat history);
  - recovery: if a step fails, call `get_status` and continue from the suggested step.
- `services/status.py`: `get_status()` returning workspace, version, keys configured (masked), campaigns with counts, `next_step` (rule-based: no offer → ingest; no campaign → create; no approved companies → companies; …).
- Transports:
  - `leadgen mcp` → stdio (logging to stderr/file only).
  - `leadgen mcp --http [--host 127.0.0.1] [--port 8766]` → Streamable HTTP at `/mcp/<secret>`; secret from settings (generate if empty); requests to any other path → 404; bind to localhost only unless `--host` is given explicitly.
- `leadgen tools` CLI: prints the tool list (name + first line of description) for debugging.

## Tests
- Using the SDK's in-memory client (or direct handler calls if the SDK lacks one): list tools (names match PLAN), call `get_status`, run a mini flow with `FakeSearchProvider` (create offer → campaign → companies → find_people → drafts → run_qa → review → export).
- Error shape on missing key (`needs_key`), on validation error.
- No secrets in any tool output (set fake keys, scan all outputs).
- HTTP mode: wrong path 404; right path initialises (use the SDK's test client or httpx against a background server on a random port).

## Acceptance
- Tests pass.
- Manual: `claude mcp add lead-generator -- <venv>\Scripts\leadgen.exe mcp` in Claude Code; ask "what's my lead generator status?" and see `get_status` used.
