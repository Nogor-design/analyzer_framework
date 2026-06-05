# Regime/Exit-Aware Pool + PantheonMaster Migration

**Status:** Proposed (2026-06-04)
**Owner:** PM (Claude) · drives the weekly/deployment pool that feeds the daily-prediction tool
**Related:** [`deployment_matrix_252_capability.md`](deployment_matrix_252_capability.md),
`docs/runbooks/weekly_optimization_and_reports_guide.md`,
`analysis/exits/`, `analysis/regime_recommender/`, `strategies/PantheonMaster/PantheonMaster.cs`

---

## 1. Problem & goal

The team runs an MA-cross pool. A weekly package selects templates per lane (by PF/net), and a
downstream AI does **daily predictions** to pick which to trade. Two stated goals:

1. Make the MA-cross pool the **best it can be** so the predictor gets good input ("bad list →
   bad predictions").
2. Move the team to **more advanced strategies** for a more robust system and greater edge.

The MA cross is a known-weak *entry*. The leverage is therefore not in the entry but in **exit
quality, regime fit, and giving the predictor real features** — and in adopting the strategy that
makes regime + exit first-class.

## 2. Key finding: the horsepower already exists, disconnected

This system already contains almost everything needed; it is not wired together.

| Capability | Where | State |
|---|---|---|
| Exit-policy tick simulator (AtrTrail, BreakEven+ATR, Chandelier, FixedThenTrail, giveback-after-MFE, time-stop) | `analysis/exits/policies.py`, `simulate.py` (`simulate_exit_policies_for_run`, `ExitSimConfig`) | Real; `outputs/full_ExitStrat.html` already compared policies over ~16.8M NQ ticks |
| Regime classifier (ADX trend + ATR-percentile volatility) | `analysis/regime_recommender/classifier.py` | Mirrors the strategy's regime logic |
| Parameter recommender w/ explainable confidence | `analysis/regime_recommender/recommender.py` (`recommend_parameters`) | Real |
| Regime → NT template export | `analysis/regime_recommender/template_export.py` (`generate_session_templates`) | Real |
| Walk-forward IS/OOS, MAE/MFE, ranking | `analysis/strategy_discovery/` (`strategy_discovery_full`) | Real, in report catalog |
| 252-cell predictor interface | `web/optimizer_deployment_matrix*.py` | Built + live-validated; manifest is the predictor's pick list |

**The advanced strategy is a near-drop-in upgrade.** `strategies/PantheonMaster/PantheonMaster.cs`
is the same MA-cross core with the **identical risk param names** the whole pipeline keys on —
`MaxStop`, `MaxTPRatio`, `MaxTrades`, `ProfitStop`, `LossStop` — plus exactly two new dimensions:

- **Regime filter:** `PantheonRegimeMode` (TrendingOnly / RangingOnly / TrendingUp / TrendingDown /
  NoHighVol / LowVolOnly / HighVolOnly) + `UseTrendAlignment`, from ADX + ATR-percentile.
- **Selectable exit policy:** `PantheonExitPolicy` (AtrTrail, BreakEvenOnly/-plus, Chandelier) via
  `UseDiscoveryExitPolicy` / `DiscoveryExitPolicy`, with `AtrTrailMultiple`,
  `BreakEvenTriggerTicks`, `BreakEvenPlusTicks`.

Because the risk params match, the naming rules (true-max-loss, effective-trades single/multi),
the optimizer, weekly coverage, and the deployment-matrix manifest **work on PantheonMaster
unchanged**. The only naming gap: PantheonMaster uses `FastPeriod`/`SlowPeriod`/`TrendPeriod` vs
`averageFast`/`averageSlow`/`averageTrend`; the MA-tier classifier needs an alias (§6).

## 3. The three moves

1. **Pre-select the exit policy on local ticks, then pin it — do not sweep it in NT.** The exit is
   where an MA cross lives or dies. `simulate_exit_policies_for_run` ranks policies per
   (session × regime × MA-tier) over the tick cache; pin the winner into the template. NT runs get
   cheaper *and* better, and the exit is chosen by 16M-tick evidence, not a coarse grid.
2. **Regime-specialize templates.** Today the pool is regime-blind. `RegimeMode` lets a template
   trade only its favorable regime — far more robust with no new entry logic.
3. **Feed the predictor features, not just PF/net — on the manifest.** The deployment-matrix
   manifest is the predictor interface; extend each cell with diagnostics already computed
   (walk-forward IS/OOS degradation, MAE/MFE shape, exit robustness margin, regime/session fit,
   daily consistency, prop survival).

## 4. Phase A (first concrete step): exit-sim → manifest features

Strategy-agnostic, additive, no NT change. Makes the **current MA-cross pool** immediately better
input for the predictor and builds the feature plumbing PantheonMaster will reuse.

### 4.1 New module `web/optimizer_template_quality_features.py`

```python
def exit_robustness_for_template(
    trades: pd.DataFrame,            # the template's executed trades (from its session results)
    ticks: pd.DataFrame,             # tick cache window for the instrument/contract
    *, config: ExitSimConfig,
) -> dict:
    """Rank exit policies for this template's trades via
    analysis.exits.simulate.simulate_exit_policies_for_run and return:
      best_policy, best_policy_net, current_policy_net,
      exit_robustness_margin   = best_net - current_net  (head-room left on the table)
      exit_rank_stability       = spread across top-k policies (low spread = robust choice)
    """

def template_quality_features(session, cell_row, *, market) -> dict:
    """Assemble the predictor feature block for one covered manifest cell:
      - exit_*               (from exit_robustness_for_template)
      - mae_mfe_shape        (median MAE/MFE ratio, MFE-give-back) from trade enrichment
      - walkforward_degradation (IS->OOS PF/net drop) from strategy_discovery validation
      - daily_consistency    (% green days, worst-day) from daily_outcomes
      - regime_fit           (share of trades in favorable ADX/vol regime) from regime classifier
      - prop_survival        (max daily drawdown vs guardrail headroom)
    Every field optional; None when its source artifact is absent."""
```

### 4.2 Wire into the manifest

In `optimizer_deployment_matrix_session.build_session_deployment_manifest`, after the cell is
covered, attach `entry["features"] = template_quality_features(...)`. Keep it **optional and
non-breaking**: when source artifacts are missing, `features` is omitted or its fields are `None`
(the manifest already tolerates heterogeneous cell keys; `write_manifest` flattens). The predictor
reads `cells[].features.*` alongside `name`/`profit_factor`.

### 4.3 Data sources (already produced per session)

- trades / metrics: `deployment_package/final_backtest_handoff/.../evaluated_candidates.json`
- tick cache: `MarketDataStore` / `.ta_artifacts` tick parquet
- walk-forward + MAE/MFE: `strategy_discovery` derived outputs
- daily consistency: `core/daily_outcomes.py`

### 4.4 Deliverable & tests

- `optimizer_template_quality_features.py` + unit tests with a synthetic trades+ticks fixture
  asserting `exit_robustness_margin` and a populated `features` block.
- Extend `test_optimizer_deployment_matrix_coverage.py`: a covered cell carries `features` when
  trades+ticks exist; absent → no crash, `features` fields None.
- CSV manifest gains the flattened `features.*` columns; round-trip test.

**Done = the predictor can read per-template exit-robustness + degradation features from the
manifest, with the MA-cross pool unchanged.**

## 5. Phase B/C: PantheonMaster migration

- **B (parallel):** register PantheonMaster in the deployment-matrix pipeline. Add the
  `FastPeriod/SlowPeriod`↔`averageFast/averageSlow` tier-naming alias (§6). **Pin `RegimeMode` and
  `DiscoveryExitPolicy` from the Phase-A analysis** (exit sim + regime recommender) rather than
  sweeping them. Run the same lanes head-to-head vs MA-cross; compare OOS robustness + the Phase-A
  features.
- **C (switch):** when PantheonMaster wins on OOS, repoint the pool. Predictor interface unchanged
  (same risk params, same manifest schema) → nothing downstream breaks.

## 6. Open items / risks

- **Naming alias:** add `FastPeriod→averageFast`, `SlowPeriod→averageSlow`, `TrendPeriod→
  averageTrend` to the tier-name extraction (`template_naming` facts and/or
  `optimizer_deployment_matrix.classify_tier` inputs) so PantheonMaster templates tier-name
  correctly. Small, localized.
- **Parity (highest risk):** PantheonMaster backtests with managed SL/TP but **live uses explicit
  stop orders + ChangeOrder trailing** — trailing exits are where backtest/live diverge. Validate
  trail-policy parity before any trail-based template goes live (ties into the existing parity
  workstream).
- **ATR definition:** regime + exit math use ATR; the Python sim's ATR must match NT's ATR
  (Wilder vs SMA) or pre-selection is wrong. Reconcile before trusting exit pre-selection.
- **Combinatorics:** PantheonMaster has ~59 params. Regime + exit knobs must be **pinned from
  analysis, never swept in NT**, or Stage 1 explodes.

## 7. One-line thesis

We don't need new analysis. We need to connect the analysis we already have to (a) exit selection
and (b) the manifest the predictor reads — and PantheonMaster is the vehicle that makes regime +
exit first-class without breaking the predictor interface.
