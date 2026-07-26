# Real Edge Discovery Program

This is the operating plan for turning `ta_foundation` from a signal sweeper
into a serious intraday strategy research platform. The goal is not to find a
pretty backtest. The goal is to discover multiple small, repeatable edges that
can survive costs, market adaptation, and forward observation.

## Research Premise

Professional day traders are not all trading one universal pattern. They are
usually exploiting narrow, conditional behaviors:

- Time-of-day microstructure: open auctions, lunch compression, power-hour
  imbalance, overnight inventory cleanup.
- Reference-level reactions: VWAP, prior RTH high/low, overnight high/low,
  opening range, settlement, large candle origins.
- Failed auctions: liquidity sweeps, failed breakouts, trapped participants.
- Regime-specific continuation: momentum only when volatility and participation
  are high enough.
- Mean reversion only when a move is overextended relative to current session
  volatility and location.

The system must therefore search conditional hypotheses, not just broad signal
families. A raw signal with PF 0.8 can contain a real tradable pocket if the
context filter is economically meaningful and not overfit.

## Current State

Already useful:

- Multi-family discovery funnel exists.
- Market-data-only sweeps now produce sidecar summaries.
- Level discovery now includes VWAP, prior/overnight levels, and liquidity
  sweep failure signals.
- Entry discovery can find conditions over session, level type, level distance,
  timing mode, and generic numeric features.
- Conditional-rule promotion now converts supported nested entry-discovery rules
  into focused YAML probes under `discovery/generated/`, with parent rank, rule,
  params, and sidecar provenance preserved in each generated file.
- Hardening infrastructure exists for walk-forward, slippage stress, and OOS
  evaluation, though not every fast probe enables it.

Recent candidate audit update:

- `discovery/03_vwap_london_reject_fade.yaml`
- Intended as NQ, 00:00-06:00 America/Denver, inverted VWAP reject, long.
- A CLI instrument-selection bug caused earlier multi-instrument market-data
  runs to use the first merged root in `D:\MarketData` instead of the YAML's
  `discovery.instrument`; in this folder that meant ES data could be reported
  under an NQ label.
- After fixing the selector and rerunning on true NQ, the VWAP London
  reject-fade candidate is rejected: best explicit audit PF is about 0.74 with
  hundreds of trades, and hardening fails t-test, fold consistency, and
  slippage stress.
- Treat the earlier PF about 2.80 / 21-trade pocket as invalid for NQ research.

Recent graveyard entries:

- `discovery/03_nq_prior_overnight_fast_probe.yaml`: prior RTH high/low,
  overnight high/low, and prior close reactions on true NQ all ranked below
  PF 1.0; nested conditional rules also stayed below PF 1.0.
- `discovery/03_nq_london_liquidity_sweep_fast_probe.yaml`: London liquidity
  sweep failures on true NQ all ranked below PF 1.0; nested conditional rules
  stayed negative.
- `discovery/04_nq_ny_open_orb_fast_probe.yaml`: NY-open ORB produced one
  small research pocket (15-minute range, short, break-extreme entry, TP/SL
  near 24/8), but `discovery/04_nq_ny_open_orb_short_hardened.yaml` failed the
  adjusted t-test and slippage/latency stress. Treat as marginal/noise, not a
  forward candidate.
- `discovery/04_nq_ny_open_orb_failure_reclaim_locked_hardening.yaml`: locked
  a failed next-open scout row for hardening-quality diagnostics. Full sample:
  150 trades, PF 0.917, net -1627.00. Development/OOS diagnostics also failed:
  evaluation OOS was 98 trades, PF 0.857, net -1889.64. Slippage/latency
  stress at 2 ticks and 1-bar delay failed with PF 0.759 and net -3896.52 on
  114 development trades. Locked holdout from 2026-04-09 through 2026-05-07
  was essentially flat-negative but still below gate: 36 trades, PF 0.998,
  net -10.48, win rate 52.8%. This rejects immediate next-open entry after OR
  reclaim; it is a graveyard result, not a candidate.

Current live research candidate:

- `discovery/04_nq_ny_open_orb_large_move_probe.yaml` tested the operator
  hypothesis that NQ may need 100-150 tick targets rather than scalp exits.
- `discovery/04_nq_ny_open_orb_large_move_hardened.yaml`: 15-minute ORB short,
  break-extreme entry, 80/100/150 tick targets and 30/50 tick stops. Stress
  stayed profitable and rolling folds were consistently positive, but the
  multiple-comparison-adjusted t-test failed on 47 trades. Keep as a research
  candidate only.
- `discovery/04_nq_ny_open_orb_5m_large_move_hardened.yaml`: broader 5-minute
  ORB both-direction runner produced 122 trades. Top variants stayed profitable
  under slippage/latency stress and all OOS folds were positive, but the
  adjusted t-test still failed. This is the best current hypothesis to refine,
  preferably by splitting long vs short and validating recent holdout/forward
  shadow rather than expanding the parameter grid.
- Direction split follow-up:
  `discovery/04_nq_ny_open_orb_5m_large_move_long_hardened.yaml` and
  `discovery/04_nq_ny_open_orb_5m_large_move_short_hardened.yaml` keep the same
  5-minute ORB large-move thesis but isolate trade direction. Long-only remained
  profitable but degraded badly OOS (top: 100/30, 65 trades, PF about 1.62,
  stress PF about 1.39, degradation about 50%, t-test failed). Short-only is the
  better side-specific research candidate (top: 150/30, 57 trades, PF about
  1.75, OOS PF about 2.24, stress PF about 1.52, degradation passed), but still
  fails the adjusted t-test and lacks sufficient rolling fold coverage. Treat it
  as a serious research candidate, not a trading candidate.
- Locked holdout follow-up:
  `discovery/04_nq_ny_open_orb_5m_short_150_30_locked_holdout.yaml` freezes the
  best side-specific hypothesis to one configuration: 5-minute ORB short,
  close-beyond required, break-extreme entry, 150 tick target, 30 tick stop. The
  entry-strategy hardening adapter now supports an optional chronological
  holdout split, so validation/stress see only development trades while the
  final slice is scored separately. Development validation remained strong
  (46 dev trades, OOS PF about 2.49, stress PF about 1.70), but the locked
  holdout failed: 11 trades from 2026-04-10 through 2026-05-07, PF about 0.996,
  net about -6, win rate about 18%. This demotes the short ORB runner back to
  research-only/no-forward status until there is a stronger structural filter
  or more post-lock data.
- New structural OR reclaim follow-up:
  `discovery/04_nq_ny_open_orb_failure_reclaim_probe.yaml` added an ORB
  `signal_type: failure_reclaim` detector and tested true NQ from 2025-12-15
  through 2026-05-07 using the repository's UTC-to-Denver Ninja timestamp
  convention. The broad scout ran 2,592 rows. The leading family is not the
  immediate reclaim close; it is a retrace entry at the reclaim candle body
  midpoint. The locked top row in
  `discovery/04_nq_ny_open_orb_failure_reclaim_body_midpoint_locked_hardening.yaml`
  is: 5-minute ORB failure/reclaim, both directions, sweep >= 4 ticks, reclaim
  same bar, body-midpoint fill within 5 bars, 150 tick target / 20 tick stop.
  Full sample: 133 trades, PF 3.88, net 27314.06, win rate 37.6%.
  Development validation: 103 dev trades, t-stat 5.80 after 2,592-hypothesis
  correction, evaluation OOS 89 trades, PF 4.80, net 22137.98. Stress at
  2 ticks and 1-bar delay stayed profitable: 103 trades, PF 3.82, net 22729.46,
  expectancy loss 8.3%. Locked holdout from 2026-04-09 through 2026-05-07
  passed but is small: 30 trades, PF 1.96, net 2524.60, win rate 23.3%.
  Important caveat: six-fold rolling validation did not retain sufficient
  trades per fold, so this is a serious research candidate for follow-up
  hardening/forward observation, not deployable.
- Rolling-fold gap follow-up (2026-05-12):
  Root cause traced. Rolling WF splits the dev set into `n_folds + 1`
  contiguous blocks; with 103 dev trades (post-holdout) and `min_is_trades:
  20`, the original `n_folds: 6` produced `block_size = 14` and zero
  qualifying folds. Because `compute_validation_v3` auto-wires
  `fold_sign_consistency` from rolling only when rolling returns a fraction,
  a no-folds rolling result silently *skips* the sign-consistency gate
  rather than failing it — so the previous "passed" hardening verdict had
  fold consistency disabled. `discovery/04_nq_ny_open_orb_failure_reclaim_body_midpoint_locked_hardening.yaml`
  has been updated to `n_folds: 3` (block_size ~25), which is sized to the
  dev sample without lowering the per-fold floor. Next hardening pass on
  this YAML will activate the fold-sign-consistency gate (default minimum
  0.6, i.e. at least 2 of 3 fold OOS slices positive). Two outcomes possible:
  the candidate confirms (graduates beyond "research only") or it fails the
  consistency gate (graveyard demotion).

- Silent-skip guard (2026-05-12, follow-on code change):
  The "rolling produced no fraction → gate silently skipped" footgun has
  now been closed at the source. `run_validation` in
  `src/ta_foundation/analysis/strategy_discovery/validation.py` now emits an
  explicit failed `fold_sign_consistency` gate whenever `use_rolling_wf=True`
  but rolling returned no fraction (no qualifying folds). The gate reason
  surfaces the rolling diagnostics ("trade count too small for N
  non-overlapping folds", etc.) so the misconfiguration is visible in the
  hardening verdict instead of being hidden. Caller-supplied
  `fold_sign_consistency` overrides still take precedence (the override
  path is unchanged). Regression covered by
  `TestValidation.test_rolling_no_qualifying_folds_fails_fold_consistency_gate`
  and `TestValidation.test_explicit_fold_sign_consistency_overrides_rolling_fallback`.
  Implication for prior verdicts: any "passed" hardening result that ran
  with `use_rolling_wf=True` and no rolling folds qualifying must be re-run
  before being treated as valid; the prior pass was a silent skip, not a
  real pass.

- Body-midpoint rerun with active fold gate (2026-05-12):
  `discovery/04_nq_ny_open_orb_failure_reclaim_body_midpoint_locked_hardening.yaml`
  re-run at `n_folds: 3`. All 5 hardening gates now pass with the gate
  *active* rather than silently skipped: min_counts IS=72/OOS=31, degradation
  -0.47 (OOS better than IS), t-stat 5.80 vs 2592-hypothesis adjusted
  threshold 4.48, monte_carlo actual_dd 685 vs p95 1256, and
  **fold_sign_consistency 1.0** — all three rolling folds OOS-positive
  (per-fold OOS PFs 5.06, 5.06, 5.59 on 25/25/28 trades). Evaluation OOS:
  78 trades, PF 5.25, net 20844. Locked holdout (2026-04-09 → 2026-05-07):
  30 trades, PF 1.96, net 2525, win rate 23.3% — small but positive.
  Slippage stress: 2t / 1-bar delay PF 3.82, expectancy loss 8.3%; worst
  cell (3t / 2-bar) PF 3.23, loss 16.6%, both far below the 60% cap. The
  candidate **graduates from "research only" to forward-shadow** status:
  next step is `triage_state='shadow'` enrollment so the runner picks it
  up on its next pass. It is still not deployable — the holdout is small
  (30 trades over ~4 weeks) and the Page-CUSUM decay gate exists precisely
  for this kind of in-flight monitoring.

- Audit of prior "passed" verdicts (2026-05-12):
  Scanned every `output/**/*_summary.json` for the silent-skip signature
  (`hardening.passed: True` ∧ `wf_type: rolling` ∧ missing/null
  `fold_sign_consistency`). Only hit was the prior body-midpoint summary
  (now superseded by the rerun above). Every other "passed" hardening
  result in the tree already carries a populated `fold_sign_consistency`
  gate, so no further reruns are required by this audit. Other hardened
  YAMLs (`*_large_move_*`, `*_short_hardened`, `*_failure_reclaim_locked_*`)
  had `hardening_passed: False` and are unaffected.

- New-family probe sweep (2026-05-12):
  - `discovery/03_nq_failed_reference_breakout_probe.yaml`: **graveyard**.
    2,160 combos × prior-RTH/overnight reference levels, both directions.
    Top 25 rows all PF 0.87–0.90 on 511–571 trades, win rate ~15%. The
    confirmed-close-then-fail thesis does not survive in broad form. Six
    conditional rules were auto-promoted to `discovery/generated/` for
    pocket mining; given the broad PF deficit, treat those as speculative
    follow-ups, not priority candidates.
  - `discovery/03_nq_vwap_continuation_probe.yaml`: **marginal**.
    960 combos. Top result PF 1.12 on 98 trades (Solid tier); only 2 Solid
    and 3 Marginal entries cleared min_trades. Seven conditional rules
    auto-promoted. Worth investigating one or two of the higher-rank
    promoted rules, but the broad signal does not warrant immediate
    hardening as-is.
  - `discovery/03_nq_large_candle_origin_retest_probe.yaml`: **strong
    research candidate**. 8,640 combos; top 25 all positive (3 high_quality,
    22 solid). Rank 1 broad: 5m, both directions, `large_body_mult=2.5`,
    `min_body_ticks=8`, `max_retest_bars=6`, `touch_ticks=8`,
    `min_close_ticks=2`, target 40t / stop 8t → PF 1.36, 740 trades,
    28.5% win rate. The notable result is auto-promoted conditional
    pockets inside rank 1: `signal_atr >= 14.08 AND level_dist_ticks <= 30`
    → PF 3.59 on 225 trades, win rate 51.1%, net 15,410; and
    `level_dist_ticks <= 13.0` → PF 3.19 on 186 trades, win rate 48.4%.
    Thirteen generated probes are queued in `discovery/generated/`. The
    top conditional was promoted to a locked hardening YAML
    `discovery/03_nq_large_candle_origin_retest_high_atr_locked_hardening.yaml`
    (rank-1 params + `max_dist_ticks=30` + `min_atr_ticks=57` to translate
    `signal_atr >= 14.08` price units, single 40t/8t outcome). **Rejected
    by hardening (2026-05-13):** full sample 514 trades PF 1.56; t-test
    fails after the 8,640-hypothesis penalty (t=3.67 vs required 4.60);
    slippage stress fails catastrophically — at the 2t / 1-bar stress cell,
    PF collapses to 1.008 and expectancy loss is 98.0%. Per the
    "system that dies with one extra tick is not an edge" standard, this
    is a graveyard result. Caveat: the signal-level translation
    (`min_atr_ticks=57` on the qualifying-large-candle bar) is not
    identical to the rule-mining filter (`signal_atr >= 14.08` on the
    retest bar), so the hardened sample is broader and dilutes the scout
    pocket; refining the filter is unlikely to fix the 1-tick fragility,
    so the family is shelved unless a different conditional dimension
    (e.g. time-of-day, level type) materially changes execution cost
    sensitivity.

- Large-candle-origin family graveyard close-out (2026-05-13):
  Audit of the 13 auto-promoted probes under `discovery/generated/` and
  re-mining of the broad probe's full conditional-rule list found that:
  - All 13 generated probes encode variations on the same two-dimensional
    pocket (`signal_atr` × `level_dist_ticks`). None explore a different
    conditional dimension. Running them as separate hardening passes is
    busywork — they share the rank-1 candidate's slippage fragility.
  - Across the broad probe's 25 ranked rows, only 2 conditional rules
    touched a context dimension other than the two numeric features.
    Both used `level_type == large_candle_origin_short AND level_dist_ticks
    <= 12.0` (PF 3.00 / 3.54 on 229 / 181 trades, parent ranks 17 and 25).
    The auto-promoter's top-10 cutoff filtered them out, so they were
    never converted to follow-up YAMLs.
  - Locked hardening of the short-only direction split:
    `discovery/03_nq_large_candle_origin_retest_short_locked_hardening.yaml`
    — direction=-1, max_dist_ticks=12, rank-1 params, 40t/8t outcome.
    Full sample 398 trades PF 1.60, expectancy 22.3 ticks. Evaluation
    OOS 232 trades PF 1.64; locked holdout 89 trades PF 1.74 net 2378
    (passes its own gate). Rolling fold sign consistency 1.0 (all 3
    folds OOS-positive). But the 8,640-hypothesis adjusted t-test
    failed (t=3.32 vs required 4.61) and slippage stress failed: at
    the 2t / 1-bar required cell, PF 1.02 and expectancy loss 95.2%.
    Per-session holdout breakdown was the genuinely informative result:
    London PF 2.74 (18 trades), NyMid PF 2.45 (12), PowerHr PF 1.96
    (11) vs Asia PF 0.49, NyOpen PF 0.73, NyPre PF 0.86 — clean
    differentiation despite the overall slippage failure.
  - Locked hardening of the London-restricted refinement:
    `discovery/03_nq_large_candle_origin_retest_short_london_locked_hardening.yaml`
    — same params + `session_filter` 00:00-05:59 Denver (London only).
    Full sample 123 trades PF 1.42, expectancy 16 ticks. Holdout 29
    trades PF 1.31. t-test still fails (t=1.52 vs required 4.81) and
    slippage stress still fails (2t/1-bar PF 0.948, loss 115.5%).
    Even at the strongest single session, the structural problem
    holds.
  - **Family graveyard verdict:** the entire `large_candle_origin_retest`
    signal as currently parameterised is execution-fragile by
    geometry, not by conditional pocket. The 8t stop / 40t target R:R
    that produced the high in-sample PF leaves no room for friction —
    a single tick of slippage plus a one-bar fill delay consumes ~50%
    of expectancy, two ticks plus one bar consumes 95-115%. No
    conditional dimension we have surfaced (signal_atr, level_dist,
    direction, session) materially changes that elasticity. The
    family is shelved unless re-probed with a different *outcome*
    geometry (e.g. wider stops 16-20t with proportionally wider
    targets), which is a *new* hypothesis, not a refinement of this
    one. The 13 queued generated probes under
    `discovery/generated/03_nq_large_candle_origin_retest_probe__*`
    can be left in place but should not be promoted to hardening
    runs — they carry the same fragility by construction.

- Large-candle-origin family wide-stop re-probe (2026-05-13):
  Tested the "separate hypothesis" called out in the 2026-05-13 close-out
  — same signal, different outcome geometry (16/20t stops × 40-150t
  targets) to see if wider stops absorb the slippage that killed the
  40t/8t variant. Two runs:
  - `discovery/03_nq_large_candle_origin_retest_wide_stop_probe.yaml`:
    1,280 combos, 5m + 1m, narrowed signal grid around the parent's
    rank-1 cluster. **Broad signal got worse, not better.** Top
    broad row PF 0.97 (rejected), all 25 ranked rows below PF 1.0.
    Conditional rule mining still surfaces the same
    `signal_atr × level_dist_ticks` pocket (e.g. rank-2 →
    `signal_atr >= 14.08 AND level_dist_ticks <= 30` at PF 2.65 on
    225 trades) plus a pure `level_dist_ticks <= 13` single-condition
    pocket at PF 2.55 on 186 trades. No new conditional dimension
    appeared.
  - `discovery/03_nq_large_candle_origin_retest_wide_stop_high_atr_locked_hardening.yaml`:
    direct A/B against the graveyarded 40t/8t high-ATR hardening —
    same min_atr_ticks=57 + max_dist_ticks=30 signal at 40t/16t
    geometry. Full sample 514 trades PF 1.04 expectancy 2.24t.
    Dev split: 399 trades (IS 279 / OOS 120), OOS PF 1.06,
    expectancy 3.82t, all 3 rolling folds OOS-positive
    (sign_consistency 1.0). **Hardening fails on three gates**:
    t-test t=0.50 vs required 4.16 (1,280-hypothesis penalty,
    huge miss); slippage stress catastrophic at every cell
    (1t/1-bar expectancy -6.6t = 297% loss; 2t/1-bar expectancy
    -16.6t = 597% loss); locked holdout 115 trades PF 0.97
    net -190.7.
  - **Diagnosis:** the wider stop did *not* fix slippage
    fragility because the expectancy cushion shrank
    proportionally. The 8t-stop pocket had PF 3.59 with a high
    per-trade expectancy (~68 ticks); the 16t-stop pocket has
    PF 1.06 with per-trade expectancy of 3.36t. Each tick of
    friction is now ~30% of the entire expectancy — worse, not
    better. The hypothesis "wider stops absorb friction" was
    wrong: friction is denominated in ticks per trade, and
    cushion shrank faster than stop widened.
  - **Family final verdict (2026-05-13):** the entire
    `large_candle_origin_retest` family at the parameter ranges
    surveyed is structurally dead. The signal does discriminate
    (5-7% win-rate lift at the high-ATR/tight-distance pocket),
    but it cannot produce enough expectancy per trade to survive
    realistic execution cost at any outcome geometry tested
    (8t→16t stop sweep, both directions, session restrictions).
    The auto-promoted conditional probes under
    `discovery/generated/03_nq_large_candle_origin_retest_*` and
    `discovery/generated/03_nq_large_candle_origin_retest_wide_stop_probe__*`
    are left in place for archival but should not be promoted.
    Re-opening this family requires a *different* signal
    geometry (e.g. body-midpoint or pullback-confirm entry on
    the retest, instead of next-open), not a different outcome
    geometry.

- Body-midpoint enrolled in shadow (2026-05-13):
  Candidate `c_1acc69ea578ff672_001` was backfilled into the research
  ledger and enrolled via `enroll_shadow_trader` (`triage_state='shadow'`,
  `enrolled_by='edge_program'`). First shadow pass against `D:\MarketData`
  emitted 146 signals across 2025-12-15 → 2026-05-07 (cursor now at
  2026-05-08), of which 140 resolved and 6 no-fill. Sum profit_ticks
  3660, mean 26.14 ticks/trade — below the dev-derived μ₀ of 37.98 but
  positive. Page-CUSUM state after 140 trades: S=318.36 vs threshold
  H=402.96 (5σ, σ=80.59 ticks), **not triggered**. The candidate is
  active under forward observation; next bars to arrive will be its
  first true forward signals.
  Two pre-existing bugs surfaced and were fixed while wiring this up:
  - `src/ta_foundation/research_ledger/sidecar_parser.py` and
    `backfill.py` did not extract `evaluation_holdout` fields, so backfilled
    candidates always had `pf_holdout=NULL` and could not satisfy the
    `enroll_shadow_trader` precondition. Parser now reads
    `hardening.evaluation_holdout.{n_trades, profit_factor, expectancy}`
    and backfill passes them through to `record_candidate`.
  - `src/ta_foundation/shadow/decay.py` could not find TP/SL ticks for
    entry-strategy candidates because the discovery sidecar records
    `outcome: {"mode": "ticks_<TP>_<SL>"}` without structured fields, so
    `_derive_mu0_sigma` fell back to σ=1.0 tick and CUSUM tripped on the
    first realistic trade (150-tick winner). Added a last-resort parser
    that splits `mode` strings of the form `ticks_<TP>_<SL>` to recover
    the binary tp/sl identity for μ₀/σ derivation.

## What Is Missing

### 1. Hypothesis Factory

The current discovery configs still test too few market behaviors. We need a
larger library of entry families built from real day-trader concepts:

- VWAP distance fade / reclaim / continuation.
- Prior high/low failed breakout and retest.
- Overnight high/low sweep and reclaim.
- Opening range breakout, failed ORB, and ORB retest.
- Previous day close / settlement reaction.
- Session initial balance extension/reversal.
- Large candle origin retest.
- Compression then expansion after volatility contraction.
- Trend pullback to VWAP/EMA only under directional regime.
- Exhaustion after N-bar one-way movement into a reference level.

Each family should expose a small, interpretable parameter set and emit
context-rich columns so entry discovery can find filters.

### 2. Candidate Promotion From Conditional Rules

Entry discovery can find pockets inside weak broad results, but those pockets
must become first-class strategies automatically.

Required behavior:

- Read nested `entry_discovery.top_rules`.
- Convert rules like `session_label == London` and `level_dist_ticks >= 8`
  into a concrete follow-up YAML probe.
- Re-run that promoted subset as a normal sweep row.
- Preserve the rule provenance in the sidecar.

This prevents us from manually noticing edge after the fact.

### 3. Fund-Grade Validation For Entry Sweeps

Fast probes are useful for search, but any candidate promoted from them needs
the same hardening gates:

- Minimum trades by stage: low for search, higher for promotion.
- Rolling walk-forward fold distribution.
- Locked final holdout if enough current-period trades exist.
- Slippage and one-bar delay stress.
- Parameter neighborhood stability.
- Permutation/null test against shuffled entry times or signs.
- Multiple-comparison penalty based on number of hypotheses tested.

No single metric should promote a candidate. A candidate must show edge,
stability, and explainability.

### 4. True Forward Observation Loop

More old data is not the answer, but forward observation is mandatory.

Needed output:

- A signal log for every candidate: timestamp, instrument, signal ID, params,
  direction, planned entry, planned stop/target, context features.
- A shadow-trade ledger that simulates the trade from live/current bars without
  modifying the strategy after the signal.
- Daily report: signals fired, fills, expected vs realized slippage, net PnL,
  missed fills, and rule drift.

This is how we separate "fit to the recent sample" from "still working now."

### 5. Regime and Portfolio Selection

We should not expect one system to trade every day. The final product should be
a small book of systems with low correlation:

- London VWAP fade.
- NY open breakout or failed breakout.
- Large-candle continuation/retrace.
- Prior high/low sweep failure.
- Trend pullback continuation.

The selector should choose which systems are allowed based on session, realized
volatility, trend/range label, and recent candidate health.

## Research Loop

1. Generate hypotheses from market microstructure, not random indicators.
2. Run broad probes with loose thresholds and low minimum-trade floors.
3. Mine conditional rules inside weak broad signals.
4. Promote conditional rules into concrete strategy YAMLs.
5. Re-run promoted candidates with slightly wider parameter neighborhoods.
6. Harden survivors: walk-forward, holdout, slippage, delay, null tests.
7. Build a basket only from candidates with different entry logic or sessions.
8. Run shadow forward for one to two weeks of current-market observation.
9. Only then consider live/paper deployment sizing.

## Immediate Build Queue

P0 - Promote conditional rules automatically: **Done for level/VWAP rule
promotion; keep extending conversions as new structural families are added.**

- Add a conditional-rule promoter for entry-sweep results.
- Support rule-to-YAML conversion for `session_label`, `level_type`,
  `level_dist_ticks`, `direction`, `timing_mode`, and `outcome_mode`.
- Add a generated probe file per top conditional rule.

P0 - Harden the VWAP London reject-fade candidate:

- Enable hardening in `03_vwap_london_reject_fade.yaml`.
- Run rolling walk-forward and slippage stress.
- Add a null/permutation test before treating PF as meaningful.
- Test parameter neighborhoods around min distance, max distance, stop/target,
  and session boundaries.

P1 - Add more professional entry families:

- Failed prior high/low breakout. **Added (2026-05-12):** level signal
  `failed_reference_breakout` requires a *confirmed* close beyond prior
  RTH or overnight high/low (`confirmation_ticks`), then a close back
  through the level within `max_fail_bars` by at least `fail_close_ticks`.
  Distinct from `reference_sweep_reclaim` (which is a single-bar
  sweep-and-reclaim wick pattern). Disabled in
  `DEFAULT_LEVEL_DISCOVERY_CONFIG` — opt in per probe. First probe at
  `discovery/03_nq_failed_reference_breakout_probe.yaml` ran on
  2026-05-12 and is **rejected as a broad signal** (top PF 0.90,
  WR ~15%, see candidate-audit entry above).
- Overnight high/low sweep reclaim. **Partially added:** `reference_sweep_reclaim`
  now detects failed sweeps of prior RTH and overnight reference levels. The
  first broad full-corpus probe needs performance tightening before it can be
  treated as market evidence.
- Opening range failure/retest. **Partially added:** ORB `signal_type:
  failure_reclaim` now covers sweep-then-reclaim failed opening-range auctions;
  the first NQ NY-open probe is rejected and logged above.
- Large candle origin retest. **Added (2026-05-12):** level signal
  `large_candle_origin_retest` qualifies a large candle by body
  vs. rolling-avg-body and absolute tick floor, then on a subsequent bar
  that touches the candle's open (origin) and closes away by
  `min_close_ticks`, emits a continuation signal in the large candle's
  direction. This is the entry-signal counterpart to the existing
  `large_candle_excursion` analysis (which is event-statistics only).
  Disabled in defaults; first probe at
  `discovery/03_nq_large_candle_origin_retest_probe.yaml` ran on
  2026-05-12 and is the **strongest new-family result so far** —
  top broad PF 1.36 on 740 trades; auto-promoted conditional pocket
  `signal_atr >= 14.08 AND level_dist_ticks <= 30` reached PF 3.59
  on 225 trades, WR 51.1%. Thirteen generated follow-ups queued under
  `discovery/generated/` for hardening. **Family closed out as
  graveyard 2026-05-13** after high-ATR, short-only, and
  London-restricted hardening passes all failed slippage stress on
  the 40t/8t outcome geometry. Re-probing with wider stops (16-20t)
  is a separate hypothesis, not a refinement of this one — see the
  candidate-audit close-out entry. **Wide-stop re-probe also failed
  (2026-05-13):** broad PF dropped to 0.97; the 40t/16t high-ATR
  hardening failed t-test, slippage stress (-6.6t at 1t/1-bar),
  and locked holdout (PF 0.97). Per-trade expectancy cushion shrank
  proportionally with the stop, so friction got worse in
  percentage terms. Family is now permanently shelved at the
  parameter ranges surveyed; re-opening requires a different
  *signal* geometry (e.g. body-midpoint or pullback-confirm entry
  on the retest), not a different outcome geometry.
- VWAP continuation after reclaim. **Added (2026-05-12):** level signal
  `vwap_continuation` arms after a close-side cross of VWAP and fires
  when a subsequent bar (within `max_age_bars`, after `min_hold_bars`)
  closes further away from VWAP in the cross direction by at least
  `min_continuation_ticks`. Distinct from `vwap_reclaim_reject`, which
  fires *on* the reclaim bar itself. Disabled in defaults; first probe at
  `discovery/03_nq_vwap_continuation_probe.yaml` ran on 2026-05-12 and
  is **marginal** — top broad PF 1.12 on 98 trades, only 5 entries
  above the noise floor. Seven conditional rules auto-promoted; broad
  signal does not justify dedicated hardening yet.

P1 - Build a candidate ledger: **SHIPPED 2026-05-14**

- Persist every run's top candidates to a structured local store (SQLite research ledger).
- Track run date, config hash, instrument, sample window, metrics, hardening verdict, and promotion lineage.
- Enhanced sidecar parser to capture granular regime/session breakdowns in candidate notes.
- Added `vwap_continuation` and other new families to the ledger registry.
- Added `python -m ta_foundation.research_ledger.cli_summary` for cross-run comparison and leaderboards.


P1 - Add forward shadow runner: **Skeleton + daily health report landed.**

- `src/ta_foundation/shadow/` now hosts a data source, signal/outcome
  simulator wrapper, and runner. Each pass picks up every candidate in
  `triage_state='shadow'`, pulls 1-minute bars from the data source
  (defaulting to `D:\MarketData`-style NinjaTrader exports), runs the
  family-appropriate detector + entry timing on the new window, and
  inserts `shadow_signals` rows idempotently via the migration-0004
  unique index `(candidate_id, ts, direction)`. Open positions
  (`status: pending` / `open`) are resolved on each subsequent pass; the
  `shadow_cursor_ts` column makes restart safe.
- CLI: `python -m ta_foundation.cli.main --shadow-pass --market-data
  <folder> [--ledger-db <path>] [--candidate-id <id>] [--until <iso>]`.
  Cron-driven scheduling (Windows Task Scheduler) is the intended
  invocation pattern — the pass is idempotent and short-lived.
- Coverage so far is only the `orb_breakout` and `orb_failure_reclaim`
  families (which is what the locked body-midpoint candidate needs).
  Other families raise `ShadowNotSupported`.
- Daily health: `python -m ta_foundation.cli.main --shadow-health
  [--for-date YYYY-MM-DD] [--trailing-window N] [--out <path>]` writes a
  deterministic markdown report (`runs/<date>/shadow_health.md` by
  default). Numbers are pulled directly from `shadow_signals` +
  `candidates`; trailing PF / win-rate / expectancy come from the most
  recent N resolved trades by exit ts. Stale open positions are flagged
  as anomalies.
- Sequential edge-decay test (Phase D plan §D.3): Page CUSUM lives at
  `src/ta_foundation/shadow/decay.py`. The shadow runner now consumes
  every newly-resolved trade through `update_decay_state` after the
  resolution phase. μ₀ and σ are derived per-candidate from `pf_dev`
  plus target/stop ticks (binary tp/sl identity), keeping the reference
  in the same unit as `profit_ticks` from `realized_outcome_json`.
  Defaults: `k = 0.5σ`, `H = 5σ` (ARL₀ ≈ 465; above the 250-trade floor
  the spec requires). State persists at `candidates.decay_state_json`
  via migration 0005. On threshold crossing the runner sets
  `triage_state='decayed'`, journals a `decay_disable` tool row (in
  addition to the surrounding `shadow_pass` row), and stops the math
  while the watermark keeps advancing so re-runs are no-ops. Tests:
  `tests/shadow/test_decay.py` (9 tests on the state machine) and
  `tests/shadow/test_runner.py::test_runner_auto_disables_decayed_candidate`
  (+ idempotency follow-up). Known follow-up: decayed candidates' open
  positions are not tracked to resolution because the runner filters on
  `triage_state='shadow'`; an extension to `('shadow','decayed')` with
  signal-generation suppressed for `'decayed'` is the next iteration.
- LLM Scribe prose layer (Phase D.3): `python -m ta_foundation.agent.cli
  shadow-scribe-pass [--for-date YYYY-MM-DD] [--trailing-window N]
  [--open-age-warn-hours H] [--max-retries N] [--model llama3.1]
  [--ledger-db <path>]` runs the D.2 aggregator, then feeds the
  resulting `ShadowHealthReport` into the Scribe. The Scribe builds a
  data block of every cite-allowed number (per-candidate counts and
  PnL, trailing PF / win-rate / win-rate-percent / expectancy, anomaly
  list), generates a body via the injected LLM, wraps it in YAML
  frontmatter with a `cites:` list of every candidate referenced, and
  runs the B.3 numerical-claim linter (widened with the data block's
  allowed floats/ints). Drafts land at
  `runs/inbox/shadow_health/<YYYY-MM-DD>.md`; lint failures after
  retries leave a `_LINT_FAIL.md` placeholder for HITL review. Empty
  days (no candidates in `triage_state='shadow'`) write a deterministic
  stub without calling the LLM.

## Roadmap — Next Build Queue (2026-05-13)

This block carries the items adopted from the external `IMPROVEMENT_IDEAS.md`
critique plus follow-ups surfaced during the large-candle close-out. Each
item lists *why*, *scope*, and *non-goals* so a fresh session does not
expand the work beyond what was justified.

### P0-HASH — Probe-hash graveyard refusal — **SHIPPED 2026-05-13**

Lives under `src/ta_foundation/discovery_registry/`:
- `hashing.py` — `compute_probe_identity()` extracts a structural fingerprint
  (instrument, signal-family set, sorted param ranges, outcome geometry,
  entry-timing modes, session filter) from a parsed probe YAML.
  `probe_hash()` returns a stable SHA-256 invariant to YAML formatting,
  list ordering, and incidental metadata.
- `registry.py` — `ProbeRegistry` + `GraveyardRegistry` are JSON-backed
  append-only ledgers (`output/_probe_registry.json`,
  `output/_graveyard_registry.json`). Both are idempotent on hash.
  `check_graveyard()` returns either an exact-hash hit or a near-match
  (same family set + outcome mode + Jaccard ≥ 0.80 on param ranges and
  TP/SL sets).
- `backfill.py` — one-shot scan of `output/*_summary.json` populates both
  registries (`python -m ta_foundation.discovery_registry.backfill
  --output ./output`). As of 2026-05-13 backfill against the repo,
  6 graveyard entries + 10 probe records were captured.
- `refusal.py` — CLI-side resolver and post-run hook.
- CLI integration in `cli/main.py`: `--override-graveyard "<reason>"`
  required to proceed on a hit; refusal returns exit code 4. The hit
  reason is logged in `_graveyard_registry.json` under
  `override_history[]`. Post-run, the just-written sidecar is registered
  idempotently.

Tested by 23 unit tests + 4 integration tests against real backfilled
sidecars (`tests/discovery_registry/`). Verified end-to-end: the
wide-stop probe YAML now refuses to re-run; the wide-stop high-ATR
hardening YAML now refuses to re-run; the body-midpoint hardening (live
shadow candidate) is not in the graveyard and proceeds normally.

### P0-HASH (original scope — for reference)

**Why.** Multiple manifestations of the same hypothesis have already slipped
through under different YAML names: the `large_candle_origin_retest` family
produced four hardened-candidate YAMLs (high-ATR, short-only, London-restricted,
wide-stop high-ATR) that all encode variations on the same
`signal_atr × level_dist_ticks` pocket with the same execution-cost
fragility. The system happily reran each one as if it were a new
hypothesis. The graveyard captured the failures, but did not prevent
re-running them.

**Scope.**

1. Define a deterministic probe hash:
   `hash = sha256(signal_family, sorted_param_ranges, outcome_grid, session_filter, instrument)`.
2. Persist `output/_graveyard_registry.json` with one entry per
   graveyarded probe/hardening: `{hash, yaml_path, verdict_date, reason,
   stress_failure_cell?}`.
3. Backfill the registry from the existing sidecars in `output/` — every
   sidecar with `hardening_passed: False` or `tier_breakdown: rejected`-only
   gets an entry.
4. At CLI start (before `Running Level Discovery`), compute the proposed
   probe's hash and check against the registry. **Near-matches** (same
   signal_family + ≥80% param-range overlap + same outcome geometry)
   refuse with a registry hit. Override via
   `--override-graveyard "hypothesis-differs-because-<text>"`; the override
   string is logged to the sidecar so subsequent audits can re-evaluate.
5. Hardening (06_validate stage) does the same check before launching:
   a hardening run whose locked rule matches a graveyarded one refuses
   without override.

**Non-goals.** No embedding-based similarity. No LLM-in-the-loop. No
auto-modification of probe YAMLs. The mechanism is a structural hash +
local JSON registry; no new dependencies.

**Test plan.** Unit: hash stability across YAML formatting differences,
near-match thresholding, override path. Integration: backfill registry
from existing graveyard sidecars and verify the four large-candle
hardening YAMLs all refuse without override.

---

### P0-CUMULATIVE — Cross-probe cumulative hypothesis counter — **SHIPPED 2026-05-13**

Lives in the same `discovery_registry/` module:
- `ProbeRegistry.cumulative_hypotheses(family_filter=...)` returns
  family-filtered or global cumulative combinations across all runs.
- `refusal.compute_effective_n_hypotheses()` returns
  `{cumulative_family, cumulative_global, decay_factor, families}` for
  a proposed probe identity.
- `refusal.inject_effective_penalty()` mutates each enabled
  `<block>.hardening.n_hypotheses_tested` to
  `max(yaml_value, cumulative_family // decay_factor)`. Default
  `decay_factor=10`. Preserves the YAML original under
  `yaml_n_hypotheses_tested` for audit; surfaces it in the validation
  t_test sidecar block alongside the active value.
- CLI integration: applied right after the graveyard check, before any
  discovery runs. Console log emits any per-block promotion.

As of backfill 2026-05-13, the `large_candle_origin_retest` family
shows a cumulative of ~9,924 hypotheses across all probes, giving a
family floor of ~992. Neither the body-midpoint candidate (yaml_n=2592)
nor the wide-stop hardening (yaml_n=1280) gets promoted by that floor —
both were already self-corrected. The mechanism bites only when an
individual probe's yaml_n is below the family-cumulative floor, which
is the intended behaviour.

Tested by 5 unit tests in `tests/discovery_registry/test_refusal.py`.

### P0-CUMULATIVE (original scope — for reference)

**Why.** Each probe currently applies its own Bonferroni penalty
(`n_hypotheses_tested` = its own combo count) in the t-test gate. But
across the project's history we have actually tested several thousand
hypotheses, not just the per-probe slice. A candidate that passes its
own probe's 1,280-penalty may still be a Type-I error against the
project-cumulative count.

**Scope.**

1. Persist `output/_probe_registry.json`: one row per probe run with
   `{run_date, yaml_path, sidecar_path, n_combinations_run, instrument,
   family}`. Backfill from existing sidecars.
2. Hardening uses
   `effective_n_hypotheses = max(probe_n_hypotheses, project_cumulative / decay_factor)`
   where `decay_factor` (default 10) discounts long-ago tests to prevent
   the threshold from rising monotonically forever. Discount only by
   "tests in the same family" if family attribution is available; fall
   back to global count.
3. The hardening verdict surfaces both the probe-local and effective
   penalties so the operator can see what bar the candidate cleared.
4. The shadow-enrollment precondition (`enroll_shadow_trader`) reads
   the effective penalty so it cannot enroll a candidate that only
   cleared the probe-local penalty.

**Non-goals.** No frequentist/Bayesian reframe of the entire validation
stack. No real-time correlation control between hypotheses. No
attempt to estimate "effective number of tests after correlations" —
that's a separate, much larger piece of work (idea #1 in the original
critique). This is a pragmatic counter, not a Romano-Wolf
implementation.

**Test plan.** Unit: registry append idempotency, decay calculation,
backfill correctness. Integration: re-evaluate the body-midpoint
shadow candidate's hardening t-test against the new effective
penalty; document whether it still clears.

---

### P1-REGIME — Volatility regime as a first-class YAML dimension — **SHIPPED 2026-05-14**

**Summary.** Volatility and trend regimes are now integrated into the discovery pipeline.
- `regime.py` supports `vol_mode: realized_vol` and granular bins (`vol_regime_tertile`, `vol_regime_quartile`).
- Probes can filter by regime via `regime_filter: {vol_regime_tertile: ["high"]}`.
- Rule miner automatically discovers regime-based pockets by including these as categorical features.
- Hardening sidecars and evaluation blocks now include a `by_regime` breakdown with multiple dimensions (`by_vol_regime`, `by_trend_direction`, etc.).

Tested via unit tests on `regime.py` and `evaluation.py`. Verified that rule mining now surfaces "high_vol" and "trending" pockets in NQ discovery runs.

---

### P1-DOCS — Documentation hygiene (this section's neighbor)

**Why.** A 2026-05-13 docs audit found three categories of
misalignment: (a) `docs/architecture/UNIFIED_*.md` describe a
generic LLM reward-engine pattern that is not built and not on
the roadmap; (b) `docs/designs/ai_integration_architecture.md`
is superseded by `_v2.md`; (c) the repo root has ~30 legacy
`.md` files (`BUILDPLAN_AGENTIC.md`, `EDGE_DISCOVERY.md`,
`PATTERN_ENGINE_HANDOFF.md`, etc.) that are explicitly or
implicitly superseded by docs in `docs/`.

**Scope.**

1. ✅ Architecture/UNIFIED_* and ai_integration_architecture.md now
   carry explicit STATUS banners — done 2026-05-13.
2. ✅ `IMPROVEMENT_IDEAS.md` now leads with an Assessment table
   identifying which suggestions were adopted as P0-HASH /
   P0-CUMULATIVE / P1-REGIME — done 2026-05-13.
3. ✅ `docs/DOCS_INDEX.md` exists as the single authoritative
   "what's current vs archived" map — done 2026-05-13.
4. **Open:** the ~30 legacy top-level `.md` files need disposition
   (move to `docs/_archive/` or delete). Operator decision
   required; do not bulk-move without per-file review since some
   (`README.md`, `CLAUDE.md`, `USER_MANUAL.md`, `CONTRIBUTING.md`,
   `ARCHITECTURE.md`) are still current at the repo root.

**Non-goals.** No autoregeneration of `AI_REPO_INDEX.md` (that's
already script-driven; see `scripts/build_ai_index.py`). No
rewriting of CLAUDE.md.

---

## What I Need From The Operator

To do this properly I need:

- A consistent current-market minute-bar update workflow in `D:\MarketData`.
- Confirmation of the primary instruments to prioritize. Start with NQ/MNQ and
  optionally ES, because tick value, liquidity, and behavior are cleanest.
- Realistic execution assumptions for your platform: commission per side,
  expected slippage by instrument/session, and whether entries are market,
  stop-market, stop-limit, or limit.
- Any hard constraints: max trades per day, max loss per day, preferred session,
  and whether overnight/Globex trading is allowed.

## Non-Negotiable Standards

- Do not deploy a strategy because one backtest has PF greater than 2.
- Do not trust a result with fewer than 30 trades except as a hypothesis.
- Do not combine multiple filters unless each has market logic.
- Do not use old data to justify a current system without recent validation.
- Do not hide losing variants. The system must show the graveyard.
- Do not optimize exits on the same slice used to choose entries.
- Do not ignore slippage. A system that dies with one extra tick is not an edge.

## Definition Of Real Edge

A candidate becomes a real edge only when it satisfies all of this:

- Clear market thesis.
- Positive net expectancy after realistic costs.
- At least one independent validation slice.
- Acceptable walk-forward degradation.
- Survives slippage and one-bar delay stress.
- Parameter neighborhood is not a cliff.
- Forward shadow results do not immediately contradict the backtest.
- Fits into a diversified intraday playbook.
