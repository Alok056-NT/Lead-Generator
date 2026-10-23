# T22: Faster test runs (parallel)

Depends on: T19. Sprint v1.1. Small task.

## Goal
The full suite takes ~13 minutes, which makes every build/review round slow. Make it run in parallel without changing what it tests.

## Deliverables
- Add `pytest-xdist` to the `dev` extras in `pyproject.toml`.
- Make the suite pass with `python -m pytest -q -n auto` (and still pass without `-n`). Typical fixes: module-level caches (e.g. MX cache, settings caches) reset per test via autouse fixtures in `tests/conftest.py`; tests that bind fixed ports use port 0; tests that spawn subprocesses use their own `LEADGEN_HOME`.
- `.claude/agents/builder.md` and `.claude/agents/reviewer.md` must NOT be edited (the orchestrator updates them); instead put the command in `CLAUDE.md` → "Commands": `python -m pytest -q -n auto`.
- CI (`.github/workflows/tests.yml`): use `-n auto`.

## Tests / acceptance
- `python -m pytest -q -n auto` passes with the same number of tests passed/skipped as the serial run (record both counts and times in STATUS).
- Full parallel run under 5 minutes on the owner's PC.
- No test was deleted, skipped or weakened to make this pass.
