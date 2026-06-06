# Deployment Matrix — Optimization (lean plan)

*Written:* 2026-06-05 · *Author:* Claude (acting PM) · *Status:* lean, decision-made
*Reads-with:* `docs/runbooks/deployment_matrix_technical_guide.md`

> **Goal:** fill the 252-cell matrix with cells that are *real, trades-backed
> edges* — not in-sample artifacts — and make fake cells *visible* to the
> predictor. Then stop. This is plumbing for the daily pool; the edge work
> itself ("bigger fish": better strategies, exits, PantheonMaster) is elsewhere.
> Do the 2–3 things that matter, skip the rest.

---

## What was broken (and the evidence)

1. **Lineage collapse — ✅ FIXED 2026-06-05.** `refine_risk` selected only **4 of
   2520** rows because `parent_candidate_id` was null on every result row: NT
   truncates the per-run output-folder name so the parsed `batch_id` matched
   neither the manifest `template_id` nor `bucket_id`. Fix: bridge truncated
   folder → full template name via `BatchRunSummary.csv` in
   `optimizer_recipe_results.py` (`_load_batch_name_map` / `_meta_for`). Live
   re-validated: parent distinct **0→126**, refine selection **4→196**. Test:
   `tests/web/test_optimizer_recipe_results_lineage.py`. *This was the real reason
   the grid yielded ~4 cells — coverage, not quality.*

2. **Selection promotes the fragile ones.** On the finished stage_1 (`opt_91711cf3671c`,
   NQ, OOS 2026-05-01→06-03): **PF-vs-trades Spearman = −0.74**. High PF *is* low
   trade count, so PF-first selection ≈ a fewest-trades selector. 13% of winners
   rest on ≤10 trades; ~⅓ of lanes (34/108) have no candidate ≥20 trades.

3. **Fallback hides the gaps.** Empty cells are filled by the nearest same-side
   donor — a *different* strategy wearing this cell's name. The predictor can't
   tell a real cell from a borrowed one. That's a **correctness** bug, not a
   quality one.

---

## The lean fix list — only what's worth doing

**#1 — Min-trades floor = 10. ✅ DONE.** Hard filter on stage_1 + refine_risk
selection (`refine_selection_min_trades`, launcher default 10). Justified by a
no-NT floor sweep over the finished winners:

| floor | lanes covered | %cov | median winner PF |
|---:|---:|---:|---:|
| 0 | 126 | 100% | 2.06 |
| **10** | **115** | **91%** | **2.02** |
| 20 | 74 | 59% | 1.75 |

Floor 10 removes the ≤9-trade statistical garbage at ~9% coverage cost while PF
barely moves. The cliff is 10→15. Default 10; expose up to ~20 as an
"honest-edge" mode. *(Live-confirmed on the floored re-run: the floor drops
stage_1 lanes whose best candidate is sub-10-trade.)*

**#2 — Honest coverage accounting. ← the one real to-do.** Report three numbers
to the predictor manifest: `covered_real`, `covered_fallback`, `missing`, and
tag each fallback cell with its donor coords + a confidence flag so the daily
selector can down-weight or skip it. Cheap, mechanical, and it stops the matrix
from silently lying. Do this and the matrix is *trustworthy*.

**#3 — One validation gate, reusing what exists (optional / if-time).** Route the
matrix's final templates through the **walk-forward** card that already exists
(`optimizer_walkforward.py`, session-detail "Walk-forward validation"). It
re-runs each candidate across rolling windows skipping the IS/OOS window and
flags PF collapse — i.e. it *is* the holdout test, already built. The only work
is the output-path bridge: the matrix writes `renamed_backtest_templates` while
walk-forward reads the regular flow's `named_backtest_templates`/`F_xxx`. Bridge
that path; don't build a new validator. (Bootstrap-trade-sequence on the same
page is a free no-NT complement if wanted.)

That's it. Floor (done) + honest coverage (do) + walk-forward gate (optional) =
a matrix that is honest and trades-backed. Ship and move on.

---

## Explicitly NOT doing (parked, with the reason — so we don't re-litigate)

- **Separate TRAIN/VALIDATE/TEST window-split rebuild** — walk-forward (#3)
  already gives us a holdout without rewriting the recipe/dispatch plumbing.
  Building both is the over-engineering trap. Skip unless walk-forward proves
  insufficient.
- **Custom NT fitness control** — parked; see
  [`nt_fitness_parameter_control_plan.md`](nt_fitness_parameter_control_plan.md).
  The post-hoc Python floor already gives us the trade gate; an in-search NT
  `MinTrades` only helps if the optimizer's `KeepBestResults` discards
  trades-backed rows *before* we can select them — unproven, and not worth a
  C#/AddOn build until it is.
- **MA-cross pre-optimization scout** — appealing but a research rabbit hole.
  Only revive if a **no-NT** check (run anchor `discovery` on the stage_1 data we
  already own and ask "does its chop flag predict the thin lanes?") comes back
  positive. Until then it's unvalidated tooling. Note `D:\MarketData` is the data.
- **Parameter expansion / PantheonMaster exits** — this is *real strategy work*,
  not matrix plumbing. It belongs in the strategy/edge program
  (`ma_pool_enrichment_and_pantheonmaster_migration.md`), not here. That's the
  "bigger fish."

---

## Bottom line

The matrix was broken at the *coverage* layer (lineage — fixed) and dishonest at
the *reporting* layer (fallback — fix with #2). PF quality is handled
sufficiently by the floor. Don't gold-plate it: get coverage honest, optionally
add the existing walk-forward gate, and put the energy into better strategies.

> Lesson carried from `project_edge_discovery`: same-instrument IS/OOS makes fake
> edges. Walk-forward (#3) is the cheapest honest answer here; deeper validation
> machinery already exists — reuse, don't rebuild.
