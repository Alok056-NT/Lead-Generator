# T20 (optional): Claude Desktop extension (.mcpb)

Depends on: T19.

## Goal
One-click install for Claude Desktop users: download a `.mcpb` file, double-click / "Install Extension", done, with keys entered in Claude's extension settings UI.

## Verify first (DECISIONS.md)
- The MCPB manifest format, CLI (`mcpb pack` or current tool name), how Python servers are packaged (bundled venv vs `uv` runtime vs requiring system Python), user_config fields for secrets, and platform support. Source: Anthropic's "Build a desktop extension with MCPB" docs linked from claude.com/docs/connectors/building.

## Deliverables
- `packaging/mcpb/manifest.json`, build script `packaging/build_mcpb.py`, CI job that builds the `.mcpb` on tag and attaches it to the GitHub release.
- user_config entries for SERPER/HUNTER/REOON/LEMLIST keys mapped to env vars; the server reads env vars as secrets fallback (already supported by `core/secrets.get`).
- Docs: add "Option A: one-click extension" to CONNECT_CLAUDE.md.

## Acceptance
The owner installs the `.mcpb` on Windows and sees the tools without running the installer.
