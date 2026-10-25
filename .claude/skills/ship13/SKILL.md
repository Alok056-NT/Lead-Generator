---
name: ship13
description: Run the v1.3 pipeline once - gatherer (Sonnet) researches, architect (Opus) designs and writes templates, coder (Haiku) builds each chunk, reviewer (Opus) does one quick full review and pushes. Usage - /ship13 (start or resume).
disable-model-invocation: true
---

You orchestrate Lead Generator v1.3 (`docs/V13_BRIEF.md`). Credits are very low: no extra rounds, 1–2 plain lines to the owner per step, don't read diffs yourself. Resume by checking `git log --oneline -30` for `V13` commits.

1. If `docs/V13_RESEARCH.md` is missing: dispatch `gatherer` (`run_in_background: false`): `Do the v1.3 research per your instructions.`
2. If `docs/V13_DESIGN.md` is missing: dispatch `architect` (`run_in_background: false`): `Write the v1.3 design per your instructions.`
3. For each chunk Cn in `docs/V13_DESIGN.md` without a `V13 Cn:` commit: dispatch `coder` (`run_in_background: false`): `Implement chunk Cn of docs/V13_DESIGN.md per your instructions (it says V12 in places; use V13 Cn in the commit message). Credits are almost gone: run only the test files for what you touched, in the foreground (no Monitor, no background waits); the full suite runs once at the end.` Owner decision: SKIP C5 entirely (model/effort, usage records, usage meter); in C6/C7 tell the coder to leave out every part that needs C5 (Usage page, Model and effort page, usage meter, thinking/usage events) and keep v1.2 behaviour there. If BLOCKED or tests fail that it can't fix: one `builder` (Sonnet) pass on that chunk; still blocked → stop and tell the owner.
4. Dispatch `builder` (Sonnet, NOT the Opus reviewer) once: `Quick single full review of v1.3 against docs/V13_BRIEF.md and docs/V13_DESIGN.md: run the suite once (-n auto), click through the app with the fake claude (start it on a temp LEADGEN_HOME and --port 8790), check structure, sign-off, templates, collapse state, usage meter, connectors (mocked), and that v1.2 rules still hold. Approve: add/mark STATUS row T31 done, commit (do not push; Opus merges origin/main and pushes). Otherwise list blockers only.` Blockers → one `builder` fix pass, rerun the suite, push.
5. Tell the owner how to try it: re-run the installer, then the Desktop button.
