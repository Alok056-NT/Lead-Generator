# T01: Scaffold, config, workspace, secrets, `leadgen doctor`

Depends on: nothing. PLAN sections: 3, 4, 5.

## Goal
An installable Python package `lead-generator` (import `leadgen`, console script `leadgen`) with workspace handling, settings, secrets and a `doctor` command. Everything later builds on this.

## Deliverables
- `pyproject.toml` (setuptools, `src/` layout): name `lead-generator`, version `0.1.0`, `requires-python = ">=3.11"`, dependencies per PLAN section 4 (add `mcp` later in T13, `fastapi/uvicorn` in T16; keep this task lean: `httpx`, `pydantic>=2,<3`, `pyyaml`, `python-dotenv`, `dnspython`, `beautifulsoup4`), extras `dev = [pytest, pytest-cov, ruff, respx]`, script `leadgen = "leadgen.cli:main"`, package data for `leadgen/guides/*.md` and `leadgen/schema.sql`.
- `src/leadgen/__init__.py` (`__version__`), `__main__.py` (calls `cli.main`).
- `src/leadgen/core/paths.py`:
  - `home() -> Path`: `LEADGEN_HOME` env var, else `Path.home() / "LeadGenerator"`. Creates it and the subfolders `exports`, `logs`, `cache` on first call (`ensure_workspace()`).
  - `db_path()`, `secrets_path()`, `settings_path()`, `exports_dir(campaign_slug)`, `logs_dir()`, `cache_dir()`.
- `src/leadgen/core/secrets.py`:
  - Known keys registry (constant): `SERPER_API_KEY, BRAVE_API_KEY, HUNTER_API_KEY, REOON_API_KEY, ZEROBOUNCE_API_KEY, LEMLIST_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD`.
  - `get(name) -> str` (file first, then `os.environ`, stripped), `set(name, value)` (rewrite `secrets.env` atomically: write temp file then replace; preserve other keys and comments), `delete(name)`, `masked(name) -> str` (`"…" + last4` or `""`), `configured() -> dict[str, bool]`.
  - On POSIX set file mode `0o600`; on Windows skip silently.
  - Values must never appear in logs or exceptions.
- `src/leadgen/core/config.py`: `Settings` pydantic model loaded from `settings.yaml` with defaults:
  ```
  sender: {name: "", company: "", role: "", email: "", website: ""}
  postal_address: ""
  locale: "en-GB"
  defaults: {country: "", channels: ["linkedin"], seniority_min: "manager"}
  budgets: {search: 300, hunter: 25, verify: 200}
  confirm_threshold_credits: 20      # spending above this needs confirm=true
  strict_review: true                # AI review required before export/send
  allow_ddg_fallback: false          # last-resort DuckDuckGo scraping (unreliable)
  sending: {daily_cap: 20, max_daily_cap: 50, warmup_step: 5, min_gap_s: 60, max_gap_s: 180, quiet_start: "20:00", quiet_end: "08:00", max_bounce_rate: 0.05}
  dashboard: {host: "127.0.0.1", port: 8765}
  mcp_http: {host: "127.0.0.1", port: 8766, secret: ""}
  ```
  `load_settings()`, `save_settings(settings)`, unknown keys preserved on save.
- `src/leadgen/core/logging.py`: `setup_logging(level="INFO")`: rotating file handler to `logs/leadgen.log` (1 MB × 5) + stderr. **Stdout must stay clean** (MCP stdio uses stdout), so log only to stderr/file.
- `src/leadgen/core/text.py`: `slugify`, `normalize_whitespace`, `strip_html`, `truncate(s, n)`, `similarity(a, b) -> float` (difflib ratio on normalised text). Unit-tested.
- `src/leadgen/core/http.py`: `client()` returns an `httpx.Client` with sensible timeouts (connect 10 s, read 20 s), a real User-Agent string `LeadGenerator/<version>`, `follow_redirects=True`; `get_text(url, max_bytes=2_000_000)`.
- `src/leadgen/cli.py` (argparse, subcommands): `doctor`, `version`, `paths`. Later tasks add more. `doctor` prints: Python version, package version, workspace path (exists/writable), secrets configured (masked), settings valid, and for each later component a placeholder "not installed yet". Exit code 0 if healthy.
- `.github/workflows/tests.yml`: matrix `windows-latest`, `ubuntu-latest` × Python `3.11`, `3.13`; steps: checkout, setup-python, `pip install -e ".[dev]"`, `ruff check src tests`, `pytest -q --cov=leadgen --cov-report=term-missing`.
- `ruff` config in pyproject (line-length 100, target py311, rules E,F,W,I,B,UP).
- `.gitattributes`: `* text=auto eol=lf`, plus `*.ps1 text eol=crlf` and binary markers for images; then run `git add --renormalize .` so every file is stored with LF (avoids CRLF churn between Windows and CI).
- `README.md` stays as written by the planner (don't rewrite it).

## Tests (`tests/`)
- `conftest.py`: autouse fixture setting `LEADGEN_HOME` to `tmp_path` so no test touches the real workspace.
- paths: workspace created with subfolders; env override works.
- secrets: set/get/delete round-trip; other keys and comments preserved; `masked`; env fallback; value never in `repr` of any error.
- config: defaults load when file absent; save/load round-trip; unknown keys preserved.
- text: slugify unicode, similarity bounds.
- cli: `leadgen doctor` exit 0 in a fresh workspace (call `main(["doctor"])`, capture stdout).

## Acceptance
- `pip install -e ".[dev]"` works on Windows.
- `leadgen doctor` runs and exits 0.
- `pytest -q` and `ruff check src tests` pass.

## Out of scope
Database (T02), providers (T04), MCP (T13).
