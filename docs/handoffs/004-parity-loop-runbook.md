# 004 — Write `docs/runbooks/parity_loop.md` (operator runbook for the C#↔Python parity gate)

**Target AI:** `[gemini]` · **Status:** Open · **Repo:** `D:\Backup\projects\PythonProject\ta_foundation`

## Goal / Why

Phase 1 of the automated C#↔Python parity loop shipped and passed live on
2026-06-12 (100% stop-trajectory match, 29/29 trades). The knowledge currently
lives across commit messages, a handoff doc, and a design doc. Write ONE
operator runbook so anyone (human or AI) can run the gate and read its verdicts
without archaeology.

## Files to touch

- CREATE `docs/runbooks/parity_loop.md` (the only deliverable).
- EDIT `docs/DOCS_INDEX.md` — add one line for the new runbook in the runbooks
  section (match the existing list style).

## Source material (all in-repo; the runbook consolidates, does NOT invent)

- `docs/handoffs/parity_automation_phase1_handoff_2026-06-12.md` — phases, design invariants.
- `docs/designs/parity_phase2_live_leg_design.md` — Phase-2 status + the AddOn IPC commands.
- `scripts/run_parity_backtest.py` module docstring — the one-command gate.
- `scripts/stop_audit_parity.py` module docstring — the grading CLI (incl. `--ticks` live leg).
- Git log on branch `CandleDiscovery`, commits `58ccb2b`, `99aa1d8`, `509e5c4` — the
  live-validation fixes (UseTimeFilter pin, empty-enum-tag patching, anchor matching,
  exit bounding, live-leg replica).
- `docs/REGENERATE_SEED_GUIDE.md` — the empty-enum-tag section (link, don't duplicate).

## Required runbook sections

1. **What the gate proves** (one paragraph: the Python replica of PantheonMaster's
   AtrTrail matches the real C# per stop event; Wilder ATR confirmed; why this
   protects exit pre-selection).
2. **One-command backtest-leg run** — the `run_parity_backtest.py` invocation with
   `--no-dispatch` dry-run first, prerequisites (NT up + logged in, AddOn authorized;
   point to CLAUDE.md's NT restart/login section).
3. **Grading an existing audit** — `stop_audit_parity.py` for both legs
   (`--atr-mode wilder|sma`, `--ticks` for the live leg), and how to read the
   per-trade table: `stop_match_rate`, `first_divergence_dt`, `atr_match_rate`,
   `non_trail_exit`.
4. **Troubleshooting table** — at minimum: audit file never written (stale-assembly
   AddOn bug, fixed `b6bba0e` in `D:\ninjatraderOptimizer` — restart NT if params
   "don't take"); zero trades (UseTimeFilter / window without signals); empty enum
   tags (link to seed guide); FAIL with diff=0 events (trade-anchor or exit-bounding
   symptoms — should not recur, but say what they looked like).
5. **Phase status** — P1 done/passed; P2 in progress (live-tick leg; AddOn
   `ConnectPlayback`/`EnableStrategy` commands); P3 = strategy-agnostic gate.

## Acceptance criteria

- Runbook is self-contained for RUNNING the gate (commands copy-paste ready,
  ASCII-only) but LINKS for background instead of duplicating design docs.
- Every command in it actually exists in the repo at the stated path.
- No new claims not present in the source material.
- `docs/DOCS_INDEX.md` updated.

## Gotchas

- Windows cp1252 console: keep all example output ASCII.
- Do NOT edit any Python or C# — docs only.
- `docs/runbooks/atr_trail_parity.md` already covers the older exit-only Parity A;
  reference it, don't merge or rewrite it.

## Out of scope

- Any code changes; any rewrite of existing runbooks; Phase-2 speculation beyond
  what `parity_phase2_live_leg_design.md` states.

## How to verify

- `python -m pytest src/ta_foundation/tests/analysis/exits/ -q` still passes
  (proves you didn't touch code).
- Every file path referenced in the runbook exists (`Test-Path` each one).
