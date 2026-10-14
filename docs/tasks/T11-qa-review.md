# T11: QA engine and AI review

Depends on: T08, T09, T10. PLAN section 14 (implement every code in both tables), tools 25–28.

## Goal
A rigorous, explainable check of every lead and every message, plus a structured loop in which the AI reads every message with its evidence and records a verdict. Nothing with a `block` issue, and (in strict mode) nothing without an AI `pass`, can be exported or sent.

## Deliverables
### `services/qa_leads.py`
- `check_person(person, company, campaign, email_row=None, context) -> list[Issue]` implementing L01–L20 exactly as PLAN section 14. `Issue(code, severity, detail)`.
- Reuse `confirm.py` logic instead of duplicating it (call `parse_result` on stored evidence and translate reasons to codes). L12/L13/L15/L20 are new heuristics; define their patterns as module constants with comments.
- L02 duplicates across the whole campaign (URL canonical, email, name+company case-insensitive).

### `services/qa_messages.py`
- `check_message(message, person, company, campaign, offer, all_messages_index) -> list[Issue]` implementing M01–M16.
- M05: case-insensitive phrase match against `offer.forbidden_claims` (also match key noun phrases: split each forbidden claim into ≥ 3-word shingles) + a global list (`guarantee`, `guaranteed`, `100%`, `risk-free`, `no-brainer`, `act now`, `limited time`).
- M06: extract numbers/percentages/money (`\d[\d,.]*\s?(%|percent|x|times|k|m)?`, `S$`, `$`, `£`, `€`, `₹`); each must appear in some `proof_points[].claim`; ignore numbers that are part of the person's or company's name/title, and small counting words in CTAs ("15-minute", "2 minutes") if the offer `ctas` contain them.
- M07: capitalised multi-word names that are other campaign companies → block (M02); any other capitalised brand not in offer text → warn.
- M12: similarity ≥ 0.92 against other messages in the same channel (use `core.text.similarity` after replacing greeting name and company name with tokens); report the most similar message id.
- M13: locale spelling: small en-GB↔en-US list (organise/organize, practise/practice as verb, programme/program, colour/color, centre/center, behaviour/behavior, analyse/analyze); warn on the wrong variant for the campaign locale.

### `services/review.py`
- `run_qa(campaign_id) -> dict`: recompute all issues (delete old `qa_issues` for the campaign, insert new); set message status `qa_pass`/`qa_fail`; return counts per code/severity and the top 20 issues with person + company.
- `list_qa_issues(campaign_id, severity=None, code=None, page=1)`.
- `get_review_batch(campaign_id, batch_size=15, cursor=None) -> dict`: messages with status `qa_pass` and no AI verdict (or verdict `fix` with new text); each item includes full lead evidence (title, snippet, company, designation, email + status), the message text, its QA warnings, and the offer's claim lists; plus the **review checklist** for the AI:
  1. Every fact about the person/company is supported by the evidence shown.
  2. The offer line fits their role and sector.
  3. Every claim is in confirmed_claims/proof_points; nothing from forbidden_claims.
  4. Greeting name is right for this person.
  5. Tone is respectful, specific, not salesy; would a busy professional reply?
  6. Channel rules respected.
- `record_review(campaign_id, results: list[{message_id, verdict: "pass"|"fix"|"reject", notes, corrected_body?, corrected_subject?}]) -> dict`: `fix` requires corrected text, which is saved and re-checked (instant QA) and goes back into the review queue; `reject` marks the message rejected and the person `removed` with the note.
- `export_gate(campaign_id) -> dict`: counts of exportable messages/leads and why others are blocked (used by T12/T14).

## Tests
- One focused test per L and M code (pass and fail), using synthetic data.
- `run_qa` idempotent; statuses updated.
- Review loop: pass → approved; fix → re-QA → back in queue; reject → person removed.
- Export gate respects strict mode on/off.

## Acceptance
Tests pass; coverage ≥ 90% on qa_*.py and review.py. Reviewer re-runs the NXT-style scenario with synthetic data and checks the report reads clearly.
