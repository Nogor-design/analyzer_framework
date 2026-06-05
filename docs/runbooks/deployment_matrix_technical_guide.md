# Deployment Matrix — Technical Guide

*Written:* 2026-06-05 · *Companion:* [`deployment_matrix_user_guide.md`](deployment_matrix_user_guide.md)
*Worked example throughout:* live session **`opt_91711cf3671c`** ("Deployment
Matrix", `PantheonMasterBotV01TesterV2`, `NQ 06-26`, OOS 2026-05-01 → 2026-06-03).

This document traces the full pipeline in code and on disk: **what optimization
files get created, with which parameters, how the results are filtered into the
next round, and which critical settings are (or are not) set.**

---

## 0. Module map

| Concern | File |
|---|---|
| Launcher page | `web/templates/optimizer_deployment_matrix.html` |
| Routes (`/preview`, `/run`, `/coverage`, `/manifest`) | `web/app.py` (≈ lines 2649–2861) |
| Recipe builder + grid math | `web/optimizer_deployment_matrix.py` |
| Plan / template generation | `web/optimizer_recipe_plan.py`, `analysis/strategy_discovery/nt_template_generator.py` |
| **Candidate selection / filtering** | `web/optimizer_recipe_selection.py` |
| Stage orchestration (advance) | `web/optimizer_recipe_orchestrator.py` |
| Final 252-cell manifest + fallback | `web/optimizer_deployment_matrix_session.py`, `web/optimizer_deployment_matrix_manifest.py` |
| Grid definition (source of truth) | `D:\templateNaming\naming_rules.json` |

---

## 1. The grid and the recipe

`build_deployment_matrix_recipe()` (in `optimizer_deployment_matrix.py`) turns
`naming_rules.json` + the UI sweep ranges into a 3-stage **recipe**. The recipe
that ran for the example is saved at:

```
.ta_artifacts/web_optimizer/sessions/opt_91711cf3671c/recipe.json
```

Key top-level fields:

```jsonc
"mode": "matrix_sequence",
"target_final_candidates": 252,
"safety_caps": { "max_total_combinations": 250000, "max_templates_per_stage": 250 },
"keep_best_results": 1000,
"active_targets": ["MaxProfitFactor", "MaxNetProfit"],   // 2 fitness files per lane
"base_matrix": [ ... ],     // the fixed axes (below)
"stages": [ stage_1, refine_risk, final_backtest ]
```

**`base_matrix`** pins the structural axes that define a "lane":

| Param | Role | Values |
|---|---|---|
| `Session` (StartTimeH/M + DurationTimeH/M) | `matrix_bundle_axis` | 7 sessions from `session_windows` |
| `Reverse` | `matrix_axis` | `[false, true]` → god / monster |
| `averageSlow` | `matrix_axis` | 9 tier values `[40,100,150,200,250,300,350,400,450]` |
| `averageFast` | `fixed` | `5` |
| `UseTrend` / `UseTrendReverse` | `fixed` | **`false`** |

> **Why `UseTrend` is pinned false:** the Pantheon seed defaults `UseTrend=true`,
> which silently runs the whole grid trend-on. On the 2026-06-04 run that left
> 4/252 covered with everything else rejected. Pinning it off is deliberate and
> load-bearing — see the comment in `optimizer_deployment_matrix.py`.

Root lanes = 7 sessions × 2 reverse × 9 tiers = **126**. The single/multi axis
(×2 → 252) is *not* a stage-1 sweep; it emerges in `refine_risk` from the
risk-knob settings (§4).

---

## 2. Stage 1 — the optimization files that get created

**This is the "first it creates optimization files with these parameters" part
of your question.**

When you dispatch (or advance into) `stage_1`, the plan generator writes one NT
**StrategyTemplate `.xml` per lane per fitness target** into:

```
.ta_artifacts/web_optimizer/sessions/opt_91711cf3671c/generated_templates/stage_1/
```

126 lanes × 2 fitness targets (MaxProfitFactor, MaxNetProfit) = **252 files**,
plus a `recipe_template_manifest.json`.

### 👉 Example file to examine

```
generated_templates/stage_1/
  stage_1__starttimeh_00__starttimem_00__durationtimeh_04__durationtimem_00__reverse_false__averageslow_100__opt_maxprofitfactor.xml
```

Its structure (this is a real, verbatim breakdown of that file):

**Header** — tells NinjaTrader how to optimize:
```xml
<OptimizationFitness>...OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
<OptimizerType>...Optimizers.DefaultOptimizer</OptimizerType>
<StrategyType>...Strategies.PantheonMasterBotV01TesterV2</StrategyType>
<From>2026-05-01T00:00:00</From>   <To>2026-06-03T00:00:00</To>
<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
```

**`<OptimizationParameters>`** — the contract is: a param is **pinned** when
`Min == Max == ValueSerializable`, and **swept** when `Min != Max`.

*Pinned (this lane's identity + risk knobs OFF):*

| Param | Pinned value | Note |
|---|---|---|
| `StartTimeH/M`, `DurationTimeH/M` | 0,0,4,0 | London-Early 4h window |
| `averageFast` / `averageSlow` / `averageTrend` | 5 / 100 / 300 | tier-2 lane |
| `UseTrend` / `UseTrendReverse` | false / false | trend filter off |
| `ProfitStop` / `LossStop` | **10000 / 10000** | daily P&L caps effectively **disabled** |
| `MaxTrades` | **500** | effectively **unlimited** |
| `UseMaxStop` / `UseMaxTP` | true / true | per-trade bracket on |
| `Reverse` | false | god side |
| `Contracts` | 1 | |

*Swept (the structural search space):*

| Param | Min → Max (step) | Count |
|---|---|---|
| `MaxStop` | 50 → 350 (50) | 7 |
| `MaxTPRatio` | 0.5 → 2.0 (0.5) | 4 |
| `Long` | false → true | 2 |
| `Short` | false → true | 2 |

→ **112 combinations** per file, optimized by NT for MaxProfitFactor (the sibling
`opt_maxnetprofit.xml` is identical but optimizes MaxNetProfit).

**Takeaway:** stage 1 is a **pure structural search** — it finds the best
*timing + MA + direction + stop/target shape* per lane, with the money-management
knobs (`ProfitStop`/`LossStop`/`MaxTrades`) deliberately neutralized. Those are
tuned in stage 2.

NinjaTrader runs every file and writes results to
`nt_output/stage_1/BatchRunSummary.csv` (one summary row per file) plus per-run
subfolders. In the example: 252/252 `Completed`, e.g. first row
`$19,010 net profit, PF 1.80, 131 trades`.

---

## 3. Ingest + selection — how stage-1 results become stage-2 inputs

**This is the "next it creates optimizations using the results, maybe with
filtering" part.**

`RecipeRunOrchestrator.advance_once()` (driven by the recipe panel's **Advance**
button) does, for a finished optimizer stage:

1. **Ingest** — parse `BatchRunSummary.csv` + per-run files into rows. In the
   example this produced **`row_count: 2520`** across **252 batches** (NT keeps
   ~10 result rows per file).
2. **Select** — `select_recipe_stage_candidates()` in
   `optimizer_recipe_selection.py`. This is the filtering layer:

   ```
   ranked   = _add_scores(df)                  # adds portfolio_score
   filtered = _apply_hard_filters(ranked, …)   # min_trades / min_pf / max_dd / min_net
   selected = _select_rows(filtered, selection)
   ```

3. **Generate** the next stage's templates from `selected` rows and **dispatch**
   them to NinjaTrader.

Stage 1's `selection` block is **coverage-matrix mode**:

```jsonc
"selection": {
  "mode": "coverage_matrix_sequence",
  "group_by": ["StartTimeH", "StartTimeM", "Reverse", "averageSlow"],   // = a lane
  "coverage_grid": { ... full 7×2×9 grid ... },
  "keep_per_group": 1,
  "fitness_metrics": ["profit_factor", "total_net_profit"]
}
```

→ For each of the 126 lanes, keep the **best 1** row, ranked by **profit_factor,
then total_net_profit**. `_select_diverse_lane_rows()` also de-dupes by an
operational signature (direction shape + a coarse trade band) so a lane doesn't
promote two functionally identical rows.

Outputs written per stage (inspect these to audit selection):

```
<session>/parsed_results/stage_1/selected.csv      # winners
<session>/parsed_results/stage_1/rejected.csv      # with rejection_reason
<session>/parsed_results/stage_1/coverage_lanes.csv# per-lane full/thin/missing + gap reason
<session>/recipe_selection.csv                     # root copy of latest selection
```

`coverage_lanes.csv` is the best diagnostic: each lane gets `lane_status`
(`full`/`thin`/`missing`) and `lane_gap_reason` (e.g. `missing_failed_guardrails`,
`thin_only_one_passed`).

---

## 4. Stage 2 — `refine_risk` (the second round of optimization files)

`refine_risk` takes each stage-1 structural winner and, **pinning** the
structural params, sweeps the **money-management knobs**:

```jsonc
{
  "stage_id": "refine_risk",
  "from": "stage_1.selected_rows",
  "pin": ["StartTimeH","StartTimeM","DurationTimeH","DurationTimeM",
          "averageSlow","averageFast","MaxStop","MaxTPRatio","Long","Short","Reverse"],
  "optimize_inside_template": {
    "ProfitStop": {min:1, max:1001, step:500},   // → 1, 501, 1001
    "LossStop":   {min:1, max:1001, step:500},   // → 1, 501, 1001
    "MaxTrades":  {min:1, max:10,  step:2}        // from MaxTrades csv (1,3,5,10)
  },
  "selection": {
    "group_by": ["parent_candidate_id", "single_multi"],
    "keep_per_group": 1,
    "fitness_metrics": ["profit_factor", "total_net_profit"]
    // + hard_filters: {min_trades: N}  ONLY if refine_selection_min_trades > 0
  }
}
```

This is where the **single vs multi** axis is born: grouping by
`(parent_candidate_id, single_multi)` keeps the best of each, so one structural
winner can yield up to **2** refined templates → 126 × 2 ≈ **252**. New
optimization files land in `generated_templates/refine_risk/` and run results in
`nt_output/refine_risk/`. (In the example, advancing put the session here:
`recipe_refine_risk_20260605_143928`, 252 templates dispatched.)

`single_multi` itself is computed in `classify_single_multi()` using the
canonical `template_naming.compute_effective_trades()` — i.e. **effective**
trades after the ProfitStop/LossStop guardrails, not the raw `MaxTrades` cap.

---

## 5. Stage 3 — `final_backtest` and the 252-cell manifest

```jsonc
{ "stage_id": "final_backtest", "stage_type": "fixed_backtest",
  "from": "refine_risk.selected_rows", "finalists_per_bucket": 1 }
```

A plain fixed backtest (no sweep) of the refined winners to get clean,
deployment-equivalent metrics. Its renamed templates + evaluated metrics land in:

```
<session>/deployment_package/final_backtest_handoff/
    renamed_backtest_templates/renamed_template_index.json
    final_backtest_review/evaluated_candidates.json
```

`session_final_rows()` reads exactly those two files. **If that folder doesn't
exist yet, the coverage grid is empty** — which is why the coverage page shows
`missing 252` until `final_backtest` has run. (This was the state of the example
session before advancing.)

`build_deployment_manifest()` then assigns each final row to its
`(session, single_multi, tier_index, side)` cell and keeps the best per cell by
`("profit_factor", "total_net_profit")`. `apply_best_effort_fallback()` fills any
still-empty cell from the nearest **same-side** covered cell (never crosses
god/monster), tagging it `status="fallback"`. The **Download/write manifest**
button persists this to `<session>/deployment_matrix/` (json + csv) for the
predictor.

---

## 6. Critical settings — what to inspect before trusting a run

> Direct answer to *"there are critical settings that may not be set."* Yes —
> here are the ones that matter, with current defaults.

### 6.1 ⚠️ Minimum-trades floor (the PF-99-on-1-trade problem)

Every selection in this pipeline ranks by **`profit_factor` first** and applies
**no trade-count floor by default**:

- `build_deployment_matrix_recipe(..., refine_selection_min_trades=0)` — default **0**.
- `_apply_hard_filters()` only enforces `min_trades` when
  `selection.hard_filters.min_trades` is present; with the default it is absent.
- **The launcher UI has no input for it** — `optimizer_deployment_matrix.html`'s
  run payload (the `Sweep ranges` block) sends `max_stop_*`, `max_tp_ratio_*`,
  `profit_stop_*`, `loss_stop_*`, `max_trades_values` — but **not**
  `refine_selection_min_trades`. So a UI-launched run *always* uses floor = 0.

**Consequence:** exactly your scenario — a lane whose top-PF row has 1–2 trades
wins its cell, then dies in `final_backtest`/live → `missing`/`fallback` cells.

**How to set it until the UI exposes it** — POST to the run API with the field:

```bash
curl -X POST http://127.0.0.1:7739/api/optimizer/deployment-matrix/run \
  -H "Content-Type: application/json" \
  -d '{ "strategy_id": "PantheonMasterBotV01TesterV2",
        "seed_template_path": "...recipe_seed.xml",
        "instrument": "NQ 06-26", "market_suffix": "NQ",
        "start_date": "2026-05-01", "end_date": "2026-06-03",
        "refine_selection_min_trades": 25 }'
```

`api_optimizer_deployment_matrix_run()` already threads this into the recipe
(`app.py` ~line 2757). It adds `hard_filters.min_trades` to the `refine_risk`
selection, so any refined candidate below the floor is rejected with
`rejection_reason = below_min_trades` instead of being promoted.

> Note: as written, the floor is applied at **refine_risk** selection. Stage-1
> structural selection still ranks by PF without a floor; if you need the floor
> there too, that is a recipe edit (add `hard_filters` to the `stage_1`
> `selection` block) — flagged here as a gap.

### 6.2 Other settings worth a glance

| Setting | Where | Default | Why it matters |
|---|---|---|---|
| `active_targets` | recipe | `[MaxProfitFactor, MaxNetProfit]` | Two fitness files per lane. MaxProfitFactor alone over-rewards low-trade lanes. |
| `fitness_metrics` | each stage `selection` | `[profit_factor, total_net_profit]` | PF is the **primary** sort key. Pair with a trade floor (6.1). |
| `keep_best_results` | recipe | `1000` | How many rows NT retains per optimization. Too low can drop the genuinely-best-with-trades row. |
| `chunking.max_combinations_per_chunk` | session | `5000` | Splits big optimizations; lane combo count (112) is well under. |
| Session guardrails (`min_trades`, `min_percent_days_traded`, `min_profit_factor`, `max_drawdown_dollars`, …) | `session.json` | **all `null`** | These are the *operator* guardrails; null = no global gate. Worth setting for a real pool. |
| `ProfitStop`/`LossStop`/`MaxTrades` sweep ranges | UI `Sweep ranges` | 1/501/1001 ; 1/501/1001 ; 1,3,5,10 | These define the single↔multi spread. The `1` (=$1) endpoint is the de-facto "off" value. |
| `MaxStop` / `MaxTPRatio` | UI `Sweep ranges` | 50–350/50 ; 0.5–2.0/0.5 | The per-trade risk:reward grid searched in stage 1. |
| `safety_caps.max_total_combinations` | recipe | `250000` | Hard stop against a combinatorial explosion. |

### 6.3 Quick audit checklist before promoting a pool

1. Open `coverage_lanes.csv` for each stage — how many lanes are `missing` /
   `thin`, and why (`lane_gap_reason`)?
2. Open `selected.csv` — sort by `total_trades` ascending. Any winner with a
   tiny trade count is a red flag (6.1).
3. On the coverage page, confirm `covered` ≫ `fallback`. A high fallback count
   means many cells had no qualifying real winner.
4. Confirm `UseTrend=false` actually held in the generated templates (it is a
   known silent failure mode).

---

## 7. On-disk anatomy of a session (reference)

```
.ta_artifacts/web_optimizer/sessions/opt_91711cf3671c/
├── session.json                 # strategy, instrument, OOS dates, guardrails, chunking
├── recipe.json                  # the 3-stage recipe (base_matrix + stages)
├── recipe_plan.json             # expanded plan (per-lane template definitions)
├── recipe_state.json            # current_stage_id + state (running_stage, etc.)
├── recipe_run.json              # the active NT run handoff (source/dest folders, status file)
├── recipe_run_history.json      # every run dispatched
├── recipe_events.jsonl          # append-only event log (start/generate/ingest/select/…)
├── recipe_selection.csv/.json   # latest stage's winners
├── generated_templates/<stage>/ # the optimization .xml files SENT to NinjaTrader
├── nt_output/<stage>/           # BatchRunSummary.csv + per-run results FROM NinjaTrader
├── parsed_results/<stage>/      # selected.csv / rejected.csv / coverage_lanes.csv
└── deployment_package/final_backtest_handoff/   # feeds the 252-cell coverage manifest
```

IPC with NinjaTrader uses `C:\temp\nt8_command.json` (the RunBatch request) and
`C:\temp\nt8_status.json` (NT's heartbeat/progress; `state: finished` when done).
