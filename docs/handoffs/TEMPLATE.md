# NNN — One-line task title

**Target AI:** `[codex|gemini|grok|claude]`
**Status:** Open / In-progress / Done
**Estimated effort:** ~Xh

---

## Goal

One paragraph, plain English. What does success look like? Who's
the user, what changes for them?

## Why

Context the executing AI can't infer from code alone. Past
incidents, business reasons, decisions already locked in elsewhere.
Keep to a few sentences — link to docs/designs/ if there's a long
backstory.

## Files to touch

Absolute paths. Approximate line numbers where helpful. Mark
`(new)` for files to create.

- `D:\Backup\projects\PythonProject\ta_foundation\src\...` — what changes / why
- ...

## Acceptance criteria

Testable bullets. Done means all checked.

- [ ] X happens when Y is clicked
- [ ] `python -m pytest path/to/test_xxx.py` passes
- [ ] No regressions in `python -m pytest src/ta_foundation/tests/web -q`

## Gotchas

Things the cold AI won't see by reading the code. Past bugs,
non-obvious constraints, naming conventions, tz-aware-only rules
(see CLAUDE.md), etc.

## Out of scope

Explicit list of things the AI should *not* touch this round, even
if they look related. Prevents gold-plating.

## How to verify

Exact commands. What output proves it worked.

```powershell
cd D:\Backup\projects\PythonProject\ta_foundation
python -m pytest <paths> -q
```

Plus any UI smoke-test the operator should do manually.

## Notes for the executing AI

- Read [CLAUDE.md](../../CLAUDE.md) before starting if you haven't.
  The architectural contracts there (4-layer model, tz-aware
  timestamps, no DataFrames in `pkg.metadata`) are load-bearing.
- If the spec turns out to be wrong, stop and report back — don't
  guess. The PM (Claude) wrote this; ambiguities are bugs in the
  spec, not in your interpretation.
