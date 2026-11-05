# v1.3.2 side task (Haiku): Mac + "update keeps my data" checks

A Sonnet session is editing `src/leadgen/dashboard/**` and `docs/V15_BRIEF.md` at the same time. **Do not touch those files.** You may only touch:
- new test files under `tests/`;
- `.github/workflows/tests.yml`;
- `install.sh` and `install.ps1`;
- small Mac fixes in `src/leadgen/` outside `dashboard/`.

Commit only your own paths (`git commit <paths> -m ...`), trailer `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>`. **Do not push**: Sonnet pushes everything at the end.

1. **Upgrade keeps data (test):**
   - Add `tests/test_upgrade_keeps_data.py`. Build a database at the oldest schema the migrations support, or at the v1.3.0 schema (6) if the oldest is impractical.
   - Fill it with a project, a campaign, companies, leads, emails, messages, contact memory and settings. Run the app's normal open/migrate path.
   - Assert that every row and value is still there and the schema is at the latest version.
   - Reuse the existing migration helpers and fixtures. Grep the tests for "schema" first; if an equivalent test already exists, extend it instead.
2. **Upgrade keeps data (real copy, no commit):**
   - Copy the owner's real data folder `C:\Users\hp\LeadGenerator` to a temp folder. Never open or write the original.
   - Open the copy with the repo's code (`LEADGEN_HOME=<temp>`). Compare the row count of every table before and after the migration.
   - Delete the temp copy. Report the numbers.
3. **Installers never delete data:**
   - Read `install.ps1` and `install.sh`. Confirm that re-running them on an existing install upgrades the program only and never removes or overwrites the data folder (`~/LeadGenerator`), and that the version bump makes pip reinstall.
   - Fix it if not.
4. **Mac compatibility:**
   - Grep `src/` for Windows-only things: `os.startfile`, `pythonw`, `.exe`, `creationflags`, `\\` in paths, `msvcrt`, `winreg`, `shell=True` with Windows commands, hard-coded `C:`. Check that each is guarded for macOS.
   - Fix small ones outside `dashboard/`. List the rest.
5. **Mac CI:** add `macos-latest` to the `os` matrix in `.github/workflows/tests.yml`. If a step is Windows-only, keep it to Windows with `if:`.
6. Run only your new test file and `.venv/Scripts/ruff check tests`. Final message: 8 lines max, plain English.
