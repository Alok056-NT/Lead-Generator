# T19: End-to-end test and v1.0 release

Depends on: T01–T18.

## Goal
Prove the whole workflow works through the MCP tools exactly as an AI would drive it, then tag v1.0.

## Deliverables
- `tests/e2e/universe.py`: a synthetic world: 1 offer (fictional VR training studio), 2 countries (SG, GB), 12 companies (with aliases, domains on `example.com`/`example.org` style), and ~120 synthetic search results covering all confirmation shapes (reuse T08 fixtures + generators), synthetic Hunter/Reoon responses, a fake mailbox.
- `tests/e2e/test_full_flow.py`: drives the MCP tool functions in order (PLAN section 8): status → ingest text → save profile → create campaign (email + linkedin) → add/approve companies → estimate + find_people (with confirm and cursors) → estimate + find_emails → drafting batches (use a deterministic "fake AI" writer that fills a template from batch context; include deliberately bad drafts to exercise QA) → run_qa → review loop (fake AI marks some fix/reject) → export all three formats → lemlist dry run → readiness → send dry run → fake send via worker → sync fake mailbox → analytics.
  A second flow runs the same universe in **AI search mode** (fake AI submits the synthetic results through `get_search_tasks`/`submit_search_results`, including 3 fabricated results that must be blocked, and completes the spot check) and asserts the confirmed set equals the API flow's confirmed set.
  Assertions: counts at each stage, no blocked item exported, every exported message has an AI pass, CSV columns exact, no secrets in any output, total provider calls within budget.
- `tests/e2e/test_mcp_transport.py`: same mini flow over the real MCP stdio transport (spawn `python -m leadgen mcp` with `LEADGEN_HOME` temp and fake providers enabled via env `LEADGEN_FAKE_PROVIDERS=1`, which must only be honoured when `LEADGEN_ALLOW_FAKES=1` is also set, to avoid accidental use).
- `leadgen doctor --live`: real-provider smoke test spending ≤ 3 credits total (1 Serper search, 1 Reoon verify, Hunter account info call that costs 0 if available).
- Release: bump to `1.0.0`, update CHANGELOG, tag `v1.0.0` (reviewer pushes the tag).

## Worker prep (Haiku)
1. Write `tests/e2e/universe.py` (data only): `OFFER_TEXT` (a fictional VR training studio, ~400 words, with two lines marked `[CONFIRM ...]`), `COMPANIES` (12 invented companies: 7 in Singapore, 5 in the UK, each with `name`, `aliases`, `domain` on example.com/.org, `sector`), and `SEARCH_RESULTS: dict[str, list[dict]]` keyed by `"<company name>|<designation group label>"` with about 120 results in total reusing the shapes from `tests/fixtures/snippets.py` (a mix of confirmable and review cases, plus 3 results marked `"fabricated": True` with mismatched slugs for the AI-search flow), `HUNTER_RESPONSES` and `REOON_RESPONSES` (synthetic JSON matching the shapes recorded in DECISIONS.md). The builder writes the flows and verifies the data.

## Acceptance
- Full suite green on Windows + Ubuntu CI.
- Reviewer runs a real mini campaign (5 companies, LinkedIn only) end-to-end from Claude Desktop and compares quality with the NXT pilot.
