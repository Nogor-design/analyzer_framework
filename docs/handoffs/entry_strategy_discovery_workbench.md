# Handoff: Entry-Strategy Discovery Workbench (capability workbench #4)

**Status:** spec ready to execute · **Created:** 2026-06-19 · **PM:** Claude
**Plan of record:** `docs/designs/capability_workbenches_plan.md` (Tier B #4)

## Goal

Build the **Entry-Strategy Discovery Workbench** at `D:\strategy-analysis\entry-discovery`
(port 7795) — the *generalization* of the existing `ma-cross` workbench to all 8
entry-strategy families. `ma-cross` already covers the `ma` family and is the
template; this workbench adds a family selector and routes to each family's real
sweep. ma-cross can later be retired into this (or kept as the MA-focused view).

## Pattern (non-negotiable)

Library-import workbench on `strategy-workbench-kit` (already `pip install -e`'d).
**Wrappers only — reimplement no analysis.** Copy structure from the 4 working
examples: `D:\strategy-analysis\{ma-cross, exit-policy-lab, prop-survival-lab, cross-asset-scout}`.
Kit API: `make_base_app`, `run_app`, `json_error`, `num`, `run_cross_instrument_proof`,
data loaders (`list_datasets`, `load_bars`, `load_1m_bars`). Frontend: `{% extends "kit/base.html" %}`
+ `tabs`/`panels`/`scripts` blocks; `/kit/kit.css`+`/kit/kit.js`; `WBKit` global
(`$,post,get,dataset,nums,md,renderVerdict`). Use the kit's dataset bar (bars-driven).

## The family → engine registry (all real, in `ta_foundation/analysis/entry_strategies/`)

| Family | Entry point | File |
|---|---|---|
| ma | `ma_sweep.run_ma_discovery(bars, cfg)` | `ma_sweep.py:360` |
| bb | `bb_sweep.run_bb_discovery(bars, cfg)` | `bb_sweep.py:351` |
| breakout | `breakout_sweep.run_breakout_discovery(bars, cfg)` | `breakout_sweep.py:95` |
| pullback | `pullback_sweep.run_pullback_discovery(bars, cfg)` | `pullback_sweep.py:104` |
| level | `level_sweep.run_level_discovery(bars, cfg)` | `level_sweep.py:190` |
| orb | `orb_sweep.run_orb_discovery(bars, cfg)` | `orb_sweep.py:287` |
| lcr | `lcr_sweep.run_lcr_discovery(bars, cfg)` | `lcr_sweep.py:47` |
| candle | `sweep.run_candle_discovery(bars, cfg)` | `sweep.py:376` |

Each returns the same shape ma-cross consumes: a dict with `sweep_results` (list of
combo dicts with `signal_id`, `tf`, `params_key`, `metrics`, `n_trades`,
`is_oos_degradation`, `fill_rate`, …) plus `n_combinations_run`/`n_results`/`trial_grid_size`.
Reuse ma-cross's `capabilities.discovery()` row-extraction + honest-results checklist
verbatim as the shared normalizer.

## Critical gotcha (from memory project-ma-cross-workbench — DO NOT relearn)

Each `run_*_discovery` **deep-merges** the caller cfg with its own DEFAULT, and the
defaults enable a broad grid (e.g. ma defaults enable BOTH ma_cross AND ma_pullback →
30 combos/155s). So:
1. Build a **minimal per-family cfg** that pins a small grid and EXPLICITLY disables
   unselected sub-signals (copy ma-cross's `_build_discovery_cfg` for ma; derive the
   analogue for each family by reading the top of its `run_*_discovery` for the cfg
   keys it deep-merges).
2. Always set the `outcome` block (atr target/stop mult, max_bars_timeout,
   commission_per_side, slippage, tick_size/tick_value from the instrument).
3. Exits resolve **intrabar on 1m bars** regardless of signal TF (max_bars is in 1m
   units) — label the UI field accordingly, exactly like ma-cross.

## Tabs

1. **Explore** — family selector + a quick signal preview on the chosen instrument/TF
   (reuse the family's signal detector to mark recent entries; for ma reuse
   `compute_ma_features`+`detect_ma_cross`). Optional — can be minimal.
2. **Discover** — per-family sweep via the registry; render the normalized rows table
   (sortable, honest-results ✓ column) — identical to ma-cross's discovery table.
3. **Prove** — cross-instrument pooled t-stat via `run_cross_instrument_proof`,
   supplying a family-appropriate bars→R extractor (emit entries via the family's
   signal + `simulate_atr_outcomes`, R = profit_net / risk). Reuse `WBKit.renderVerdict`.

## Build order to de-risk the config-per-family problem

Do families **one at a time, verifying each returns rows before adding the next**:
ma (already known-good) → breakout → bb → pullback → orb → level → lcr → candle.
If a family needs a config you can't quickly derive, ship it disabled in the selector
with an honest "config TODO" note rather than a broken/stub tab.

## Verification (REQUIRED)

Flask `app.test_client()` smoke test (PYTHONIOENCODING=utf-8): for EACH enabled
family, hit `/api/discover` on a real instrument with a SMALL grid and assert
`n_results > 0` and rows carry real metrics. Hit `/api/prove` for ≥1 family and print
the pooled t-stat. No `random`/stub data anywhere (that was the phase1-services
failure mode). Report each family's verified row count + a sample combo.

## Deliverable files

`app.py`, `entry_discovery/__init__.py`, `entry_discovery/capabilities.py`,
`templates/index.html`, `static/app.js`, `docs/{ENTRY_CAPABILITIES.md, FINDINGS.md}`,
`requirements.txt`, `README.md`. Only create files under the project dir; do not
modify ta_foundation, the kit, or other labs.
