# Deployment Matrix — User Guide

*Page:* **Optimizer → Deployment Matrix** (`/optimizer/deployment-matrix`)
*Written:* 2026-06-05 · *Companion:* [`deployment_matrix_technical_guide.md`](deployment_matrix_technical_guide.md)

---

## 1. What this page is for

The Deployment Matrix builds the **fixed 252-cell strategy pool** that the
daily-prediction tool draws from. The grid is defined by
`D:\templateNaming\naming_rules.json`:

```
7 sessions  ×  2 single/multi  ×  9 MA tiers  ×  2 god/monster  =  252 cells
```

The job of the page is to find **the single best strategy template for each of
those 252 cells**, for one strategy + instrument, over an out-of-sample (OOS)
date range. "Best per cell" is decided by a multi-stage NinjaTrader
optimization the page runs for you.

It is a **sibling** of Weekly Coverage — it does not change or read Weekly
Coverage runs.

> **Important mental model:** this is a *launcher*, not a results page. It
> kicks off a multi-stage run and hands you off to the **session** page to drive
> it and to the **coverage** page to see results. The grid at the bottom of the
> launcher is only a *target* picture; it never fills in.

---

## 2. The screen, top to bottom

| Control | What it does |
|---|---|
| **Strategy** | The NinjaScript strategy to build the pool from (e.g. `PantheonMasterBotV01TesterV2`). The "(N seeds)" count is how many seed templates already exist for it. |
| **Seed template** + **Generate seed** | The base `.xml` every generated optimization file is cloned from. If none exists, click **Generate seed** (no NinjaTrader UI download needed). |
| **Instrument / Market suffix** | e.g. `NQ 06-26` / `NQ`. The suffix is appended to the final template names. |
| **Start date / End date (OOS)** | The backtest window used for every run in the pipeline. |
| **Sweep ranges (structural + risk knobs)** | *(collapsed by default)* The parameter ranges the optimizer searches, including **Min trades (selection floor)**. See §4 and the technical guide. |
| **Build & preview (no NT)** | **Safe.** Creates the session, recipe, and plan on disk. Writes **nothing** to NinjaTrader. Use this to inspect what *would* run. |
| **Build & dispatch to NinjaTrader** | Builds **and** immediately sends stage 1 to NinjaTrader. Do **not** click this while another NT optimization is running. |
| **Coverage target grid** (bottom) | A static preview of the 7×9 layout from `naming_rules.json`. The dots are placeholders. **This never fills with results** — it only shows the shape of the target. |

---

## 3. The normal workflow

The pipeline runs in **three NinjaTrader stages**. After each NT stage finishes,
the pipeline must be **advanced** to ingest results and launch the next stage.

```
                    you click            NT runs          you Advance
 Build & dispatch ─────────────▶ stage_1 (structural) ──────────────┐
                                                                     ▼
                                  refine_risk (risk knobs) ◀── ingest+select
                                       │ NT runs                     │
                                       ▼                             │
                                  Advance ─▶ ingest+select ──────────┘
                                       │
                                       ▼
                                 final_backtest (validation) ─▶ Advance
                                       │
                                       ▼
                              252 coverage grid fills
```

**Step by step:**

1. **Pick** strategy, seed, instrument, OOS dates. Open **Sweep ranges** and
   confirm **Min trades (selection floor)**. The launcher default is 10; raise
   it for stricter evidence, lower it only for exploration.
2. Click **Build & preview** first. Confirm the plan looks right (stage list,
   combination estimate).
3. Click **Build & dispatch to NinjaTrader**. Stage 1 (the broad structural
   search) starts. Make sure NinjaTrader is up — it cold-starts in 1–2 minutes.
4. **Open the session page** (the link appears after dispatch, or go to
   `/optimizer/sessions/<session_id>`). This page has the **recipe panel**.
5. When a stage finishes in NinjaTrader, the recipe panel's **Advance once**
   button (or the **Auto advance** checkbox) moves the pipeline forward:
   it ingests the finished results, picks the per-cell winners, generates the
   next stage's templates, and dispatches them back to NinjaTrader.
6. Repeat advance through `stage_1 → refine_risk → final_backtest`.
7. When `final_backtest` is done and reviewed, open the
   **252 coverage view** (`/optimizer/sessions/<session_id>/deployment-matrix/coverage`).
   Cells now show **covered / fallback / missing**. Click
   **Download/write manifest (json + csv)** to produce the predictor-facing pool
   file.

> **Why the grid was empty for you:** your run (`opt_91711cf3671c`) had finished
> stage 1 in NinjaTrader, but the pipeline had never been *advanced* to ingest
> it, so it never reached the stage that fills the grid. Clicking **Advance**
> (which we did) moved it from stage 1 into `refine_risk`. The grid fills only
> after `final_backtest` completes.

---

## 4. The setting people forget — minimum trades

**Current state, 2026-06-05:** the launcher now exposes **Min trades
(selection floor)** inside **Sweep ranges** and defaults it to **10**. The
recipe builder still accepts `0` for API/backward compatibility, but a normal
UI-launched run should not use zero unless the operator explicitly changes it.
The floor is threaded into structural and risk-refine selection so high-PF,
low-trade candidates are rejected before promotion.

Use 10 for broad coverage, 15-20 for stricter evidence, and 20-30 when you
would rather see fewer real cells plus visible fallbacks than promote thin
winners.

> This is the exact trap you described: *"if you optimize and ask for the top 10
> by PF you could get 10 results with PF 99 and 1 trade each."*

Without this floor, the pipeline would pick each cell's winner by **Profit
Factor first, then Net Profit**. A lane whose best-PF result came from 1 or 2
lucky trades could win its cell and then collapse in final validation or live
trading.

As a rule of thumb, require enough trades over the OOS window that the Profit
Factor is meaningful, not 1 or 2.

When you inspect results, always read **Profit Factor next to the trade count**.
A PF of 3.0 on 4 trades is noise; a PF of 1.6 on 120 trades is an edge.

---

## 5. Where to look afterward

| You want… | Go to |
|---|---|
| Drive the pipeline (advance/pause/stop) | `/optimizer/sessions/<session_id>` (recipe panel) |
| See the filled 252 grid + coverage counts | `/optimizer/sessions/<session_id>/deployment-matrix/coverage` |
| The raw per-cell winners chosen | `recipe_selection.csv` / `selected.json` in the session folder |
| Why a candidate was dropped | `rejected.csv` / `coverage_lanes.csv` per stage |
| The predictor-facing pool export | **Download/write manifest** on the coverage page → `deployment_matrix/` |

Session folders live under
`.ta_artifacts/web_optimizer/sessions/<session_id>/`.
