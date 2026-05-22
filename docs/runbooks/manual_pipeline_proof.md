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
| Dry-run | `python -m ta_foundation.research_ledger.cli_next_actions` | read-only — next legal transition per candidate |

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

### Runbook B run — 2026-05-21

**Step 2 outcome — realization path.** Confirmed all three paths:
`StrategyDiscoveryFilter` (`nt_template_generator.py`) emits a parameter
template for a generic EMA-entry strategy — triage-only, not ORB evidence;
`NinjatraderDocScrapper` is design-intent only (`authoring.render_source`
raises for any family lacking an in-tree renderer; the doc-scrapper is not
wired); the only in-tree renderer is `sma_cross`. **Finding: the
`orb_failure_reclaim` family has no faithful automated realization path** —
exactly the gap step 2 anticipates. Operator chose the **seam-proof** path:
run the loop with the generic `sma_cross` strategy to prove the seam, and
record the realization-path gap.

**Steps 3–5 — seam proof.** `ensure-nt-ready` → ready. `full-loop` on a
generic `sma_cross` spec (`.ta_artifacts/phase0_seam_probe.spec.json`):

- ✅ author → install → **compile-clean** (`error_count: 0`).
- ✅ seed-template generation.
- ❌ optimizer leg broke (defects #14, #15) — NinjaTrader rejected the run:
  *"unknown category 'NinjaScript'"*.

**Optimizer leg — fixed and verified (2026-05-21, follow-up).** Root cause:
`seed_template.py` emitted a synthetic `<NinjaTrader>`-root template that was
not NinjaTrader's real format. Rewrote `render_seed_template` to emit the
proven `<StrategyTemplate>` format (transcribed from a real NT-saved
optimizer template). Verified by running three generic `sma_cross` strategies
through `full-loop` end to end — author → compile-clean → **optimizer
finished, 162 rows each**, CSV + guardrail verdict — with **no category
error**. See defects #14 / #15.

**Bottom line:** the discovery→NT seam now runs **end-to-end for a generic
strategy** — compile-clean → optimizer evidence → guardrail verdict. What
remains is a faithful realization path for `orb_failure_reclaim` itself.

**Step 2 follow-up — realization path built (2026-05-21).** The gap step 2
surfaced is now closed in code: `authoring.py` registers an
`orb_failure_reclaim` family renderer — a faithful NinjaScript realization of
candidate `c_1acc69ea578ff672_001`'s rule (per-day opening range, sweep +
within-`MaxReclaimBars` reclaim detection, body-midpoint limit entry with a
`FillTimeoutBars` cancel, TP/SL/bar-timeout exits). Numeric knobs are exposed
as `[NinjaScriptProperty]`; only `TargetTicks`/`StopTicks` carry `[Range]`, so
`seed_template.extract_strategy_parameters` produces a focused 3×3 optimizer
grid around the discovered point with no template-side changes. Verified:
render → `generate_seed_template_from_source` emits a valid `<StrategyTemplate>`
carrying `<Category>Optimize</Category>`; regression-locked by
`test_authoring.py` (`test_orb_failure_reclaim_*`). Spec for the loop:
`discovery/_phase0_proof/orb_failure_reclaim_candidate.spec.json`.
**Fidelity caveat:** the discovery engine groups bars in `America/Denver`
local time; the renderer gates the session window by the bar timestamp's
wall-clock minute-of-day, so the NQ 1m series must be loaded in the same
timezone — a documented seam, not an exact match. **Remaining live step:**
`ensure-nt-ready` → `full-loop --spec <that spec>` against a running
NinjaTrader to actually produce the candidate's NT evidence (not yet run).

**Step 6/7 — manager decision: superseded by the 2026-05-22 run below.**

### Runbook B — NT validation run + reconciliation (2026-05-22)

The candidate's realization (the `orb_failure_reclaim` renderer, Step 2
follow-up) was driven through `full-loop` end to end against a running
NinjaTrader: author → **compile-clean** → seed template → optimizer (9-row
3×3 grid) → guardrail verdict. The seam runs for the real candidate.

**The first verdict was wrong — and catching that is the point.** The
optimizer returned `archive` (best row −$885, PF 0.575). A controlled
comparison — the *same* params and window (NQ, 2026-04-14…05-14) through the
Python discovery engine — disagreed flatly: the engine showed **+$3,260**,
same ~30 trades, same session. The verdict was not trustworthy, so it was
investigated, not recorded.

**Diagnosis (trade-by-trade vs the NT Strategy Analyzer grid).** Not a logic
bug, not a timezone bug — NT runs the correct 07:30 MT session (grid entries
are 07:37–08:54). The divergence is entirely in **exit modelling**:

- *NT side:* `OrderFillResolution=Standard` cannot sequence a limit fill and
  its managed stop inside one 1-minute bar. On a deep-sweep bar the stop
  fills catastrophically — 10 of 30 trades lost far more than the 21-tick
  stop (−92, −71, −68 ticks…), ~$1,160 of pure artifact loss.
- *Discovery-engine side:* the outcome simulator was systematically
  optimistic — it skipped the bar a limit fills on (`_resolve_outcome`
  scanned from `entry_bar_idx + 1`), and `_fill_limit_order` applied
  breakout/stop fill semantics to `body_midpoint` limit orders. Both inflate
  every limit-entry candidate.

All three are fixed (defects #16 / #17 / #18). The discovery-engine fixes
(#17/#18) cut its window result from a fantasy +$4,485 to a conservative
+$395. **The renderer fix (#16) was then verified live (2026-05-22):**
re-running `full-loop` with `OrderFillResolution=High` flipped all nine
optimizer rows from losers to winners — the best row (T151/S21) now nets
**+$1,105, PF 1.41** (was −$885, PF 0.575); the exact-params row T150/S20
nets **+$355, PF 1.13**. The catastrophic same-bar stop fills are gone.

**Corrected manager decision.** Candidate `c_1acc69ea578ff672_001` **does not
advance** — but it is a *modest real edge*, not the disaster the first run
showed nor the PF 3.88 the ledger claimed. Modelled with correct fills
(NT tick-resolution) it is **PF ~1.1–1.4** on the validation window; NT
archives it only because PF 1.41 misses the 1.5 guardrail — an honest
near-miss. The original `archive` verdict (−$885) is **retracted** as a
fill-resolution artifact; the negative verdict now stands for the correct
reason. Phase 0 / Runbook B did exactly its job — a "survivor" with a
headline PF 3.88 is, modelled honestly, a marginal edge that does not clear
the bar. No NT evidence is written to the canonical ledger pending the
operator's call.

**Ledger re-score — orb family (2026-05-22).** With the engine fixed, the
109 orb `body_midpoint`/`break_extreme` ledger candidates were re-scored:
detect + emit run once, then `simulate_outcomes` run with the pre-fix
simulator (git HEAD) vs the fixed one on identical pending entries over the
same window — so the old/new ratio isolates the bug. Result: **median PF
inflation 4.7×**; **all 109 fail the 1.5 guardrail** honestly scored (median
fixed PF 0.38) and **99 of 109 are outright losing** (PF < 1.0). The
old-recompute median (1.30) tracks the stored `pf_dev` median (1.57),
confirming the reconstruction is faithful. The fixed engine is a
*conservative* lower bound (the fill-bar check assumes adverse-first), but
a 4.7× inflation across 109/109 candidates is unambiguous. **Conclusion:
the orb family's ledger "edges" are almost entirely fill-modelling
artifacts — there is no real orb edge in the ledger, including the former
survivor `c_1acc69ea578ff672_001` (stored 3.88 → fixed 0.58).** Report:
`.ta_artifacts/orb_rescore_report.csv`.

The bug is in the **shared** `simulate_outcomes` — every family's sweep
(`orb_sweep`, `bb_sweep`, `_sweep_base` for level/pullback) calls it
identically — so it is engine-wide, not orb-specific; the 25 bb/level/pullback
`body_midpoint` candidates are inflated by the same mechanism. A faithful
per-candidate re-score of those is blocked by defect #19. **All 429 ledger
candidates were flagged 2026-05-22** (`phase0.flag_stale_metrics`, journaled;
backup `research_ledger.db.bak_2026-05-22_rescore_flag`): 136 carry
`rescore_flag.affected_by_fill_bug = true`, 293 are marked predates-audit.
Nothing in the ledger should be promoted on its stored metrics.

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
| 6 | NT validation | no ledger schema for NT evidence; NT validation never run | med | **partially addressed** 2026-05-21 — Runbook B run for the first time (see "Runbook B run" above). The discovery→NT seam now runs **end-to-end for a generic strategy** (author → compile-clean → optimizer → guardrail verdict; #14/#15 fixed). The `orb_failure_reclaim` realization-path gap is now closed in code (Step 2 follow-up: `authoring.py` registers an `orb_failure_reclaim` renderer; spec at `discovery/_phase0_proof/orb_failure_reclaim_candidate.spec.json`). Still open: a first-class ledger schema for NT evidence (Phase 1), and the **live `full-loop` run** of the candidate spec against a running NinjaTrader to actually produce its NT evidence. |
| 7 | hardening | the hardening regime-scoping gate and the per-candidate regime breakdown were dormant — the CLI never passed `bars_with_regime` to any family sweep, so every regime join silently received `None` | med | **resolved** 2026-05-20 — `cli/main.py` now computes `compute_bar_regime` once and threads it through all discovery modules (incl. `lcr`) |
| 8 | reporting | discovery sidecar metric fields `expectancy_ticks` / `avg_win_ticks` / `avg_loss_ticks` / `max_drawdown_ticks` hold **dollar** values, not ticks (they are `avg_trade` / `avg_winner` / … from `compute_evaluation_metrics`, in `profit_net` dollars). The mislabel propagates into the research ledger via `sidecar_parser.py` (`expectancy_dev = metrics.get("expectancy_ticks")`) | med | open — misleads by a factor of `tick_value` (5×); no P&L or gate impact. Fix = rename keys or convert to true ticks; rippling change deferred. |
| 9 | tooling | `author_probe` emits `discovery/generated/<hypothesis_id>.yaml` containing **only** a `pre_registration:` block — no `orb_discovery:` / `candle_discovery:` / etc. sweep config. `resolve_yaml_path_via_author_probe` (the Sweep Operator's default resolver) points `run_probe` straight at that file, so the CLI runs with nothing to sweep and produces zero candidates. | high | **resolved** 2026-05-21 — per-family template + substitution. New `discovery/templates/orb_failure_reclaim.yaml` skeleton + `agent/tools/probe_config.py` (`build_probe_config`); `author_probe` now emits a full runnable single-combo discovery config for `orb_failure_reclaim` and returns `runnable: True`. Families without a template still register but return `runnable: False` + a `config_note` (explicit gap, not a silent one). Scoped to the one proven family; other families' templates are Phase 1. |
| 10 | tooling | the Sweep Operator never ingests the sidecar. `run_one_hypothesis` calls `run_probe` then immediately `repo.list_candidates(run_id=...)` — but nothing ever calls `record_candidates_for_run`, so the list is always empty and every hypothesis is retired as `no_trades`. `record_candidates_for_run` is an orphan tool: the runbook's stage map lists it, but no journaled role invokes it. | high | **resolved** 2026-05-21 — `ingest_run_candidates` added to `sweep_operator.py` (finds the sidecar, parses, mints `candidate_id`s via `sidecar_parser.candidate_dicts_for_run`, calls `record_candidates_for_run`) and wired into `run_one_hypothesis` after each `run_probe`. Also fixed a latent operator bug: `run_one_hypothesis` could not read a truncated `run_probe` payload — now unwrapped via `_unwrap_tool_payload`. |
| 11 | tooling | `sidecar_parser._derive_gate_verdict` maps tier ids `qualified`/`strong` → `survivor`, but the discovery pipeline actually emits tier id `high_quality` (fast probe) and `most_robust` (hardened). Every fast-probe candidate falls through to `pending`, so `promote_to_hardening` (precondition `gate_verdict=='survivor'`) can never fire from a fast probe. | high | **resolved** 2026-05-21 — `_derive_gate_verdict`'s allowlist aligned with the five live tier ids (`most_robust`/`high_quality` → survivor, `marginal`/`rejected` → rejected, `solid` → pending; legacy ids kept). |
| 12 | design | candidate identity is **per-run**: `candidate_id = c_<run_short>_<rank>`. A hypothesis hardened after a fast probe produces a *new* candidate row at the hardened stage — no single candidate persists across `fast_probe`/`hardened`/`locked_holdout`. The runbook's "promote a candidate then harden it" language implies a continuous identity that the schema does not provide. | med | open — discovered 2026-05-21. Not a bug, but the conductor must thread `hypothesis_id` (stable) and pick the right per-run candidate at each stage, not a single `candidate_id`. Phase 1 design input. |
| 13 | tooling | `research_ledger/backfill.py:131` calls `infer_family(parsed)` passing a `ParsedSidecar`, but `sidecar_parser.infer_family(yaml_filename: str, signal_name=None)` expects a filename string — `AttributeError: 'ParsedSidecar' object has no attribute 'lower'`. Backfill is currently broken; `test_backfill.py` fails 10/10. | med | open — discovered 2026-05-21 while fixing #9/#10/#11 (pre-existing on the `CandleDiscovery` branch, not introduced here). Fix = call `infer_family(sidecar_path.name, first_signal)` or add a `ParsedSidecar` overload. Untouched — outside the #9/#10/#11 scope. |
| 14 | NT validation | the optimizer **chunk-template writer** lost the analyzer category: `optimizer_template_writer.py` removes `<BacktestType>` then calls `_replace_tag_text(text, "Category", ...)` — a no-op when the seed has no `<Category>` — so chunks reached preflight with neither tag (`Optimizer preflight failed: chunk_001.xml … category is '<missing>'`). | high | **resolved** 2026-05-21 — root cause was the malformed seed (#15), not the chunk writer. Once `seed_template.py` emits the proven format the seed already carries `<Category>Optimize</Category>` inside `<Strategy>`, which `_replace_tag_text` finds and the preflight's `.//Category` finds. The interim one-line change to `optimizer_template_writer.py` was **reverted** — that file is unchanged, the whole fix lives in `seed_template.py`. |
| 15 | NT validation | the seam reached NinjaTrader's Strategy Analyzer, which rejected the run: *"Tried to run strategy analyzer on strategy from unknown category 'NinjaScript'"*. Root cause: `nt_strategy_loop/seed_template.py` emitted a **synthetic `<NinjaTrader>`-root** optimizer template that bore no resemblance to NT's real saved format — wrong root element, a stub `<Strategy>` with ~5 fields, and no `<Category>` strategy-property — so NT defaulted the category to 'NinjaScript' and rejected it. (This is the recurring bug: the synthetic format was never NT's format.) | high | **resolved** 2026-05-21 — `render_seed_template` rewritten to emit the **proven `<StrategyTemplate>` format**, transcribed field-for-field from a real NinjaTrader-saved optimizer template (`opt_5bab6a5ee1ea`'s working chunks): `<StrategyTemplate>` root, `<OptimizerParameters><ArrayOfParameterWrapper>`, `<OptimizationParameters><ArrayOfParameter>` with full `xsi:type`/assembly-qualified types, and a complete `<Strategy>` serialization carrying `<Category>Optimize</Category>`. Regression-locked by `test_seed_template.py`. **Verified live**: `SeamProbeOne` ran the full loop — compile-clean → optimizer **finished, 162 rows**, CSV written, guardrail verdict — no category error. |
| 16 | NT validation | `OrderFillResolution=Standard` (the seed-template default) cannot sequence a limit fill and its managed stop within one 1-minute bar. On a deep-sweep bar NT fills the stop far past its level — 10/30 trades in the `orb_failure_reclaim` run lost −92/−71/−68… ticks against a 21-tick stop, ~$1,160 of artifact loss, flipping the candidate's NT verdict to a false `archive`. | high | **resolved 2026-05-22** — the `orb_failure_reclaim` renderer (`authoring.py`) and the shared `seed_template.py` boilerplate now set `OrderFillResolution=High` / `OrderFillResolutionType=Tick` / value 1 so NT resolves intrabar fills with tick data. Dependency: NT must have tick data for the instrument/period. **Verified live 2026-05-22** — re-running `full-loop` flipped all 9 optimizer rows from losers (−$885…−$1,675) to winners (+$345…+$1,105); best row T151/S21 PF 0.575 → 1.41. |
| 17 | discovery | the tick outcome simulator (`_simulate_tick_outcomes_slow`) scanned TP/SL from `entry_bar_idx + 1`, **skipping the bar the limit fills on**. A limit entry placed after a sweep routinely fills into a bar still carrying a large adverse excursion — silently ignored — so every limit-entry (`body_midpoint`/`break_extreme`) candidate was inflated. | high | **resolved 2026-05-22** — a conservative fill-bar stop check added before `_resolve_outcome`; regression-locked by `TestFillBarExposure`. The ATR outcome path (`simulate_atr_outcomes`) had the identical fill-bar skip — initially missed, caught during the orb re-score, and given the same fix. The vectorized `next_open` fast path (`_simulate_tick_outcomes` / `_build_forward_windows`) was checked 2026-05-22 and is **correct** — `_build_forward_windows` receives the *signal* bar index, so its `+1` scan start lands on the next_open entry bar (which the entry fills at the open of); the entry bar's range is already included. Regression-locked by `test_next_open_entry_bar_is_scanned`. The misleading "entry bar positions" docstring was corrected. |
| 18 | discovery | `_fill_limit_order` applied break_extreme stop-fill semantics (long: `bar_high>=P`) to `body_midpoint` limit orders, which must fill on a pullback (long: `bar_low<=P`). For a midpoint below market this fabricated an instant fill at a price better than market. | high | **resolved 2026-05-22** — `timing_mode` threaded into `_fill_limit_order`; `body_midpoint` now fills on the pullback. Combined effect of #17+#18: the candidate's window P&L fell from a fantasy +$4,485 to an honest +$395, reconciling with the corrected NT run (≈ +$275). |
| 19 | tooling / data | `backfill.py` did not record `signal_id` / `pattern_id` into candidate `notes_json` — every backfilled bb/level/pullback candidate carries `signal_id: null`. Each of those families has 3–7 detectors, so the detector that produced a candidate is unrecoverable from the ledger alone (only inferable from param-key sets), which blocks faithful per-candidate re-scoring. | med | open — discovered 2026-05-22 during the re-score. orb was unaffected (single detector path, re-scored fine). Fix = have backfill capture `signal_id`; until then, re-score bb/level/pullback by re-running their source discovery configs (which carry `signal_id`), not by ledger reconstruction. |

## Exit criteria for Phase 0

- Every `survivor` verdict is backed by real evidence or reclassified.
- **[met 2026-05-22]** One candidate has gone survivor → realized `.cs` →
  compile-clean → optimizer evidence → a written, evidenced manager decision,
  with every step and breakage logged — see "Runbook B — NT validation run +
  reconciliation". The decision for `c_1acc69ea578ff672_001` is **negative
  and honest**: modelled with correct fills (verified live, NT
  tick-resolution) the candidate is a modest edge — PF ~1.1–1.4, archived
  for missing the 1.5 guardrail — not the PF 3.88 the ledger claimed. Three
  fill-modelling defects (#16/#17/#18) were found and fixed in the process —
  the real output of the exercise.
- The defect log is complete and Phase 1 scope is derived from it.
- **[done 2026-05-21]** A dry-run command exists that lists eligible
  candidates and the next legal transition for each — and triggers no runs:
  `python -m ta_foundation.research_ledger.cli_next_actions`. Read-only
  (only `list_candidates`); transition rules mirror the journaled tools'
  preconditions. On the canonical ledger today: 327 → `triage-pass`,
  99 → `set_triage_state(graveyard)`, 1 → terminal `shadow`.

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
