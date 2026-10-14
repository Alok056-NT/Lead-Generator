# Status

Statuses: `todo` → `in progress` → `review` (builder done) → `done` (reviewer approved) / `changes` (reviewer asked for fixes).

| Task | Title | Status | Notes |
|---|---|---|---|
| T01 | Scaffold, config, workspace, secrets, `leadgen doctor` | changes | |
| T02 | Database layer and schema | todo | |
| T03 | Countries and compliance tables | todo | |
| T04 | Provider framework and search providers | todo | |
| T05 | Offer intake (URL, text, file) and OfferProfile | todo | |
| T06 | Campaigns and designations | todo | |
| T07 | Companies: add, verify, approve | todo | |
| T08 | People finder and confirmation engine | todo | |
| T09 | Email finder and verification | todo | |
| T10 | Drafting workflow | todo | |
| T11 | QA engine and AI review | todo | |
| T12 | Export (CSV) and lemlist push | todo | |
| T13 | MCP server | todo | |
| T14 | Email sending | todo | |
| T15 | Mailbox sync and analytics | todo | |
| T16 | Local dashboard | todo | |
| T17 | Installers and AI connectors (Claude, ChatGPT) | todo | |
| T18 | User documentation | todo | |
| T19 | End-to-end test and v1.0 release | todo | |
| T20 | Claude Desktop extension (.mcpb), optional | todo | |
| T21 | AI search fallback (free mode), **build right after T11** | todo | |

## Task summaries
(Builder adds a short summary per task here.)

### T01 (review)
Installable `lead-generator` package (`src/leadgen`, console script `leadgen`) with workspace paths, atomic `secrets.env` handling (comments preserved, masked display), pydantic `Settings` in `settings.yaml` (unknown keys kept), rotating-file + stderr logging (stdout stays clean), text and HTTP helpers, and `leadgen doctor | version | paths`. 41 tests pass (1 POSIX-only skip), ruff clean, 97% coverage. CI workflow runs Windows/Ubuntu x Python 3.11/3.13. `.gitattributes` added and files renormalised to LF.
Files: pyproject.toml, .gitattributes, .github/workflows/tests.yml, src/leadgen/{__init__,__main__,cli}.py, src/leadgen/core/{__init__,paths,secrets,config,logging,text,http,fsutil}.py, tests/{conftest,test_paths,test_secrets,test_config,test_text,test_http,test_logging,test_cli,test_fsutil}.py, docs/DECISIONS.md, docs/STATUS.md.
Extra beyond card: `core/fsutil.py` (shared atomic write helper), `shutdown_logging()`, `ConfigError`.

## Follow-ups
(Ideas or issues found during tasks that are out of scope for the current card.)
- T01: `guides/*.md` and `schema.sql` are listed as package data but do not exist yet (T02/T18 create them).
- T01: settings fields are type-checked only (e.g. `quiet_start` format, `daily_cap <= max_daily_cap` are not validated); add when the sending task (T14) uses them.
- T01: CLI output on Windows: when stdout is piped it uses the ANSI code page (e.g. cp1252), so an unencodable character (e.g. in a workspace path) would raise UnicodeEncodeError. Consider `sys.stdout.reconfigure(errors="replace")` in T17.
- T01: the hand-rolled secrets.env parser keeps inline `# comments` as part of an unquoted value.
- T13: the MCP set-key tool should accept only known/provider keys, because `secrets.set` accepts any UPPER_CASE name.
- T06: campaign slugs must be unique, because `exports_dir()` slugifies and two names can map to the same folder.
