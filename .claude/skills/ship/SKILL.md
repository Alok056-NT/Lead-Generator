---
name: ship
description: Run the v1.2 Studio pipeline once - designer (Sonnet) writes docs/V12_DESIGN.md, coder (Haiku) builds each chunk, tester (Sonnet) runs the suite and a click-through once, reviewer (Opus) does one quick functional check and pushes. Usage - /ship (start or resume).
disable-model-invocation: true
---

You are the **orchestrator** for Lead Generator v1.2 "Studio" (read `docs/V12_STUDIO.md`). Credits are nearly gone: no extra rounds, short messages, don't read diffs yourself. Keep the owner (Alok, non-technical) informed in 1–2 plain lines per step.

Resume: check `git log --oneline -30` for `V12:` commits and skip steps already done.

1. **Design**: if `docs/V12_DESIGN.md` doesn't exist, dispatch `designer` (`subagent_type: "designer"`, `run_in_background: false`): `Write the v1.2 design doc per your instructions.` If BLOCKED, stop and tell the owner.
2. **Code**: for each chunk C1..Cn in `docs/V12_DESIGN.md` without a `V12 Cn:` commit, dispatch `coder` (`run_in_background: false`): `Implement chunk Cn of docs/V12_DESIGN.md per your instructions.` If a coder reports BLOCKED or failing tests it cannot fix, dispatch `builder` (Sonnet) once with the same chunk and the coder's report; if still blocked, stop and tell the owner.
3. **Test**: dispatch `tester` once: `Run your one-pass test of v1.2.` 
4. **Check**: dispatch `reviewer` once: `Quick v1.2 check, not a full review: confirm against docs/V12_STUDIO.md that the flow works end to end (use the fake claude, plus ONE real chat turn "What is my Lead Generator status?" with the owner's Claude plan if signed in), that data-quality rules still hold (verified-only emails, contact memory, checks before export, nothing sent without a click), and that the suite passes. Approve (set STATUS T30 done, commit, push) or list blockers only.` If blockers: one `builder` fix pass, then push without another review unless tests fail.
5. Tell the owner: what to click to try it (`leadgen app` or the Desktop button after re-running the installer), and anything left.
