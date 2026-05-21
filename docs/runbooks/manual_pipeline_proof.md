# Phase 0 — Manual Pipeline Proof and Ledger Audit

This replaces the thin "Phase 0 — Ground Truth And Status Cleanup" in
`docs/designs/autonomous_research_to_paper_trade_loop_build_plan.md`.

Decision (2026-05-20): **do not build the autonomous conductor until the manual
pipeline has carried one candidate end to end and the ledger it would read is
trustworthy.** Automation multiplies throughput; if the manual process or its
evidence is unsound, automation just scales the unsoundness.

## Why this phase exists

A ledger snapshot shows the research half of the pipeline has genuinely run —
but with integrity gaps, and the NinjaTrader-validation seam has never run at
all. Phase 0 is an **audit plus a one-candidate proof**, not a cleanup pass.

## Current state — audit complete 2026-05-20

Ledger: `.ta_artifacts/research_ledger.db`
(backed up to `research_ledger.db.bak_2026-05-20_phase0audit`).

| Table | Count | Notes |
|---|---|---|
| hypotheses | 32 | 25 open, 7 retired |
| runs | 32 | 7 journaled (`r_h_auth_*`, all `fast_probe`); 25 backfilled (`r_backfill_*`, incl. all 6 hardened + 3 locked_holdout) |
| candidates | 427 | **after audit:** 327 pending, 99 rejected, **1 survivor** |
| shadow_signals | 146 | all for one candidate; outcomes resolved — shadow is live |
| tool_journal | 33 | author_probe ×7, inbox.accept ×7, run_probe ×7, operator.retire_no_trades ×7, enroll_shadow_trader ×1, shadow_pass ×2, decay_disable ×1, + this audit's reclassification |

**Root finding: the journaled tooling has only ever run the front half of the
pipeline, and it produced zero survivors. Every survivor is a backfill import.**

1. **The journaled pipeline's complete track record:** 7 hypotheses authored
   2026-05-14 → accepted → fast-probed → **all 7 retired for no trades**
   (`operator.retire_no_trades` ×7). The real tooling has produced zero
   survivors, zero hardening runs, zero holdout runs.

2. **Hardening and the one-shot holdout lock have never run.** All 6
   `hardened` and 3 `locked_holdout` runs are `r_backfill_*`.
   `promote_to_hardening` and `request_locked_holdout` have never been called.
   The lock the conductor is designed to depend on is completely unproven.

3. **All 21 "survivors" were backfilled.** 20 were mislabeled — from
   `r_backfill_999849779ea7e885`, `mode = fast_probe`, no OOS metrics, yet
   stamped `survivor`. **Reclassified to `pending` during this audit**
   (journaled as `phase0.reclassify_mislabeled_survivors`). The 1 remaining
   survivor, `c_1acc69ea578ff672_001` (`orb_failure_reclaim` NQ 1m), has
   complete metrics (dev `pf 3.88/n133`, oos `pf 5.25/n78`, holdout
   `pf 1.96/n30`, slippage passed) but `holdout_attempted = 0` — it bypassed
   the lock.

4. **Shadow works, for the one candidate it has.** 146 resolved signals for
   `c_1acc69ea578ff672_001` (sample outcome: a win, +$735). But that
   candidate's holdout provenance is a backfill.

5. **NinjaTrader validation has never run.** Zero NT runs, and **no ledger
   schema for NT evidence** — nowhere structured for it to live.

Bottom line: the **discovery → fast-probe → retire** front half runs through
real tooling. The **hardening → holdout → shadow** back half exists only as
backfilled data. No candidate has ever traversed the full pipeline through the
journaled tools.

## Data inputs

The manual pipeline has two separate data dependencies — both now documented:

| Input | Location | CLI flag | Status |
|---|---|---|---|
| NT backtest exports | `inputs/nt_exports/` | `--input` | **created 2026-05-20** (was undocumented / ad-hoc); see its `README.md` |
| Market data | `D:\MarketData` | `--market-data` | exists; NQ/ES/GC/MNQ/NG/RTY minute + tick series, kept current by `TaFoundationMinuteBarExporter` |

Pure discovery probes need only `--market-data` + the report YAML; `--input`
is still required by the CLI — point it at an `inputs/nt_exports/<batch>/`
subfolder (empty is acceptable).

## What Phase 0 must establish

1. **The ledger is trustworthy** — every `survivor` verdict is backed by real
   dev+OOS metrics, or is reclassified.
2. **The discovery-survivor → NT-validation seam works once, run by hand.**
3. **One candidate receives an honest, fully-evidenced manager decision.**

## Pipeline stage map (existing tools — nothing here is new)

| Stage | Command / tool | Ledger effect |
|---|---|---|
| Hypothesis intake | `research_intake` (LDR import) → `agent authoring-pass` → `agent inbox accept` | inserts `hypotheses` row, pre-registered params |
| Fast probe | `run_probe(mode="fast_probe")` → `cli.main --hypothesis-id ...`; or `agent operator-pass` to drain `runs/proposals_accepted/` | `runs` row, then `record_candidates_for_run` → `candidates` |
| Triage | `agent triage-pass` | sets `gate_verdict` |
| Promote | `promote_to_hardening` tool | `triage_state = hardening_queue` |
| Hardening | `run_probe(mode="hardened")` | dev/OOS metrics on `candidates` |
| Locked holdout | `request_locked_holdout` (one-shot lock) → `run_probe(mode="locked_holdout")` | `holdout_attempted = 1`, holdout metrics |
| NT validation | `nt_strategy_loop.cli full-loop` (needs `ensure-nt-ready` first) | **none yet — gap** |
| Shadow | `agent/tools/write/shadow.py`, `shadow/runner.py`; `agent shadow-scribe-pass` | `shadow_signals`, `triage_state = shadow` |
| Inspect | `python -m ta_foundation.research_ledger.cli_summary` | read-only |

## Runbook A — Ledger reconciliation + tooling proof

Goal: the ledger contains only verdicts the evidence supports, and the
journaled tooling is proven to work on one real candidate.

1. **[done 2026-05-20]** Confirmed `r_backfill_999849779ea7e885` is
   `mode = fast_probe`; its 20 `overnight_high_low_sweep_reclaim` survivors
   have no OOS metrics and are mislabeled.
2. **[done 2026-05-20]** Reclassified all 20 from `survivor` to `pending`;
   correction journaled as `phase0.reclassify_mislabeled_survivors`. One
   survivor (`c_1acc69ea578ff672_001`) now remains.
3. **Audit the backfilled shadow candidate** `c_1acc69ea578ff672_001`.
   Traced 2026-05-20: hypothesis `h_backfill_8c0b209fe54a0b95`
   (`registered_by = edge_program`), run yaml
   `discovery\04_nq_ny_open_orb_failure_reclaim_body_midpoint_locked_hardening.yaml`.
   **Open decision for the operator:** does a backfilled, lock-bypassed
   candidate (`holdout_attempted = 0`) stay in shadow, or must it re-acquire
   the lock via `request_locked_holdout` and re-run the holdout? Document the
   answer — it sets the precedent the conductor will inherit.
4. **[done 2026-05-21]** Exercised the journaled tooling end to end against a
   **sandbox copy** of the ledger (`scripts/phase0_journaled_tooling_proof.py`,
   run log `outputs/phase0_proof_run.log`). One fresh test hypothesis was driven
   through `author_probe` → `run_probe(fast_probe)` → `record_candidates_for_run`
   → `promote_to_hardening` → `run_probe(hardened)` → `request_locked_holdout` →
   `run_probe(locked_holdout)`. Every journaled tool fired and wrote a
   `tool_journal` row; the `not_a_survivor` precondition rejected a pending
   candidate; the one-shot lock returned `lock_acquired=True` then `False`.
   `agent triage-pass` was deliberately skipped — it needs Ollama and only sets
   `triage_state`, not `gate_verdict`, so it is not on the critical path. The
   chain only *completed* because the proof script supplied glue the production
   roles lack — see defects #9 / #10 / #11.
5. Write every breakage into the [defect log](#defect-log).

Exit for A: every `survivor` verdict is real or reclassified; the journaled
tooling has produced and locked at least one candidate correctly.

## Runbook B — NT-validation seam proof

Goal: prove, by hand, that a discovery survivor can become a NinjaTrader
strategy with Strategy Analyzer evidence. Use the **one trustworthy candidate**,
`c_1acc69ea578ff672_001` (`orb_failure_reclaim` NQ 1m).

1. **Extract the candidate.** Read `params_json` and the parent hypothesis
   (family, instrument, timeframe, session, direction) from the ledger.
2. **Choose a strategy-realization path and record the cost of each:**
   - **StrategyDiscoveryFilter** — fast, but a generic EMA entry + filters. It
     validates regime/filter value, *not* an ORB-reclaim entry. **Triage only:
     its result must not count as the candidate's NT evidence.**
   - **NinjatraderDocScrapper strategy factory** — deterministic `.cs` if the
     ORB family has a working generator. Confirm generator support first.
   - **Hand-written `.cs`** — last resort; record it as a Phase 0 finding that
     the family has no realization path.
3. **Bring NinjaTrader up:** `nt_strategy_loop.cli ensure-nt-ready`.
4. **Run the loop:** `nt_strategy_loop.cli full-loop --spec <spec.json>`
   (or `repair-loop` then `optimizer-bridge`). Capture the session folder.
5. **Collect NT evidence:** compile-clean status, optimizer CSV, guardrail
   verdict (`candidate` / `archive`).
6. **Record it back.** There is no NT schema yet — for Phase 0, attach the
   session path and verdict to the candidate via `notes_json`. Flag "design a
   first-class NT-evidence shape" as Phase 1 work.
7. **Manager decision** using the checklist in the build plan
   ("Manager Oversight Checks"). Be honest: does this candidate, given the
   holdout-lock bypass found in Runbook A, deserve to advance?

Exit for B: one candidate has gone survivor → realized `.cs` → compile-clean →
optimizer evidence → a written manager decision, with every manual step and
breakage logged.

## Defect log

Record every breakage. This list — not the conductor design — is the real
output of Phase 0; Phase 1+ tasks should be derived from it.

| # | Stage | What broke / was manual / was missing | Severity | Status / fix |
|---|---|---|---|---|
| 1 | audit | 20 survivors mislabeled (fast_probe, no OOS) | high | **resolved** 2026-05-20 — reclassified to `pending` |
| 2 | audit | shadow candidate bypassed the one-shot holdout lock (`holdout_attempted=0`) | high | open — Runbook A step 3 (operator decision). 2026-05-20: candidate re-hardened via the report-CLI path with a real holdout — passes (holdout PF 1.88) but the holdout month is materially softer than dev (PF 3.89→1.88, win 38%→23%): a decay signal, not a clean durable verdict. |
| 3 | tooling | hardening + holdout journaled tools (`promote_to_hardening`, `request_locked_holdout`, `run_probe` hardened/holdout) have never run | high | **resolved** 2026-05-21 — proven end to end on a sandbox ledger by `scripts/phase0_journaled_tooling_proof.py` (Runbook A step 4). All journaled tools fire and journal correctly; the `not_a_survivor` precondition and the one-shot holdout lock both behave. Earlier (2026-05-20) the report-CLI hardening path was also confirmed. NOTE: `run_probe`'s `mode` arg is a pure ledger label — it is *not* passed to the CLI, so a `hardened` and a `locked_holdout` run of the same YAML are compute-identical; the holdout is defined entirely by YAML content. |
| 4 | discovery | journaled tooling's full track record is 7 hypotheses → all retired no-trades → 0 survivors; the real pipeline has never produced a tradeable candidate | high | **re-framed 2026-05-21** — the 0-survivor record is **structural wiring, not signal selectivity**. The journaled path is broken in three places (defects #9 / #10 / #11): author_probe emits a config-less YAML, the Sweep Operator never ingests the sidecar, and the verdict mapper never returns `survivor` for a fast probe. The 2026-05-20 probes that "diagnosed signal selectivity" ran the **report-CLI path directly**, bypassing all three breaks — so they measured engine quality, not the journaled pipeline. Engine/signal quality is fine (`orb_failure_reclaim` reproduces the survivor, dev PF 3.89); the journaled pipeline simply never delivered a sidecar's candidates to the ledger with a promotable verdict. **Root cause resolved 2026-05-21:** #9 / #10 / #11 all fixed; `scripts/phase0_journaled_pipeline_check.py` confirms the journaled pipeline now produces a survivor **unaided** — the real Sweep Operator authored → ran `run_probe` (fast + hardened) → ingested both sidecars → 2 survivor candidates in the ledger (dev PF 3.89 / n146, OOS PF 5.24 / n87). Verified for `orb_failure_reclaim` only; the other 12 families still need templates (Phase 1). |
| 5 | inputs | `--input` NT-export folder was undocumented / ad-hoc | low | **resolved** 2026-05-20 — created `inputs/nt_exports/` + README |
| 6 | NT validation | no ledger schema for NT evidence; NT validation never run | med | open — Phase 1 (schema) + Runbook B (first run) |
| 7 | hardening | the hardening regime-scoping gate and the per-candidate regime breakdown were dormant — the CLI never passed `bars_with_regime` to any family sweep, so every regime join silently received `None` | med | **resolved** 2026-05-20 — `cli/main.py` now computes `compute_bar_regime` once and threads it through all discovery modules (incl. `lcr`) |
| 8 | reporting | discovery sidecar metric fields `expectancy_ticks` / `avg_win_ticks` / `avg_loss_ticks` / `max_drawdown_ticks` hold **dollar** values, not ticks (they are `avg_trade` / `avg_winner` / … from `compute_evaluation_metrics`, in `profit_net` dollars). The mislabel propagates into the research ledger via `sidecar_parser.py` (`expectancy_dev = metrics.get("expectancy_ticks")`) | med | open — misleads by a factor of `tick_value` (5×); no P&L or gate impact. Fix = rename keys or convert to true ticks; rippling change deferred. |
| 9 | tooling | `author_probe` emits `discovery/generated/<hypothesis_id>.yaml` containing **only** a `pre_registration:` block — no `orb_discovery:` / `candle_discovery:` / etc. sweep config. `resolve_yaml_path_via_author_probe` (the Sweep Operator's default resolver) points `run_probe` straight at that file, so the CLI runs with nothing to sweep and produces zero candidates. | high | **resolved** 2026-05-21 — per-family template + substitution. New `discovery/templates/orb_failure_reclaim.yaml` skeleton + `agent/tools/probe_config.py` (`build_probe_config`); `author_probe` now emits a full runnable single-combo discovery config for `orb_failure_reclaim` and returns `runnable: True`. Families without a template still register but return `runnable: False` + a `config_note` (explicit gap, not a silent one). Scoped to the one proven family; other families' templates are Phase 1. |
| 10 | tooling | the Sweep Operator never ingests the sidecar. `run_one_hypothesis` calls `run_probe` then immediately `repo.list_candidates(run_id=...)` — but nothing ever calls `record_candidates_for_run`, so the list is always empty and every hypothesis is retired as `no_trades`. `record_candidates_for_run` is an orphan tool: the runbook's stage map lists it, but no journaled role invokes it. | high | **resolved** 2026-05-21 — `ingest_run_candidates` added to `sweep_operator.py` (finds the sidecar, parses, mints `candidate_id`s via `sidecar_parser.candidate_dicts_for_run`, calls `record_candidates_for_run`) and wired into `run_one_hypothesis` after each `run_probe`. Also fixed a latent operator bug: `run_one_hypothesis` could not read a truncated `run_probe` payload — now unwrapped via `_unwrap_tool_payload`. |
| 11 | tooling | `sidecar_parser._derive_gate_verdict` maps tier ids `qualified`/`strong` → `survivor`, but the discovery pipeline actually emits tier id `high_quality` (fast probe) and `most_robust` (hardened). Every fast-probe candidate falls through to `pending`, so `promote_to_hardening` (precondition `gate_verdict=='survivor'`) can never fire from a fast probe. | high | **resolved** 2026-05-21 — `_derive_gate_verdict`'s allowlist aligned with the five live tier ids (`most_robust`/`high_quality` → survivor, `marginal`/`rejected` → rejected, `solid` → pending; legacy ids kept). |
| 12 | design | candidate identity is **per-run**: `candidate_id = c_<run_short>_<rank>`. A hypothesis hardened after a fast probe produces a *new* candidate row at the hardened stage — no single candidate persists across `fast_probe`/`hardened`/`locked_holdout`. The runbook's "promote a candidate then harden it" language implies a continuous identity that the schema does not provide. | med | open — discovered 2026-05-21. Not a bug, but the conductor must thread `hypothesis_id` (stable) and pick the right per-run candidate at each stage, not a single `candidate_id`. Phase 1 design input. |
| 13 | tooling | `research_ledger/backfill.py:131` calls `infer_family(parsed)` passing a `ParsedSidecar`, but `sidecar_parser.infer_family(yaml_filename: str, signal_name=None)` expects a filename string — `AttributeError: 'ParsedSidecar' object has no attribute 'lower'`. Backfill is currently broken; `test_backfill.py` fails 10/10. | med | open — discovered 2026-05-21 while fixing #9/#10/#11 (pre-existing on the `CandleDiscovery` branch, not introduced here). Fix = call `infer_family(sidecar_path.name, first_signal)` or add a `ParsedSidecar` overload. Untouched — outside the #9/#10/#11 scope. |

## Exit criteria for Phase 0

- Every `survivor` verdict is backed by real evidence or reclassified.
- One candidate has a complete, honest manager decision with NT evidence.
- The defect log is complete and Phase 1 scope is derived from it.
- A dry-run command exists that lists eligible candidates and the next legal
  transition for each — and triggers no runs.

## What Phase 0 explicitly does NOT do

- No conductor, state machine, or `autonomous_loop/` package.
- No new hypotheses registered **in the canonical ledger**, no new probes
  beyond re-running hardening to fix mislabeled survivors.
- No locked holdout spent on a new candidate **in the canonical ledger**.
- No Sim101 / paper / live execution.

> **Reconciliation note (2026-05-21):** Runbook A step 4 requires registering a
> test hypothesis and exercising the one-shot holdout lock — which the two
> bullets above otherwise forbid. The contradiction is resolved by running the
> step-4 tooling proof against a **throwaway sandbox copy** of the ledger
> (`research_ledger.sandbox.db`); the canonical ledger gains no hypothesis and
> spends no lock. The "does NOT do" bullets apply to the canonical ledger only.
