# Local Deep Research Hypothesis Backlog - 2026-05-14

Source run:

- Local Deep Research run: `06bd51f1-939f-4997-b8f0-d3c25fa5d41a`
- Interrupted predecessor: `af2af4d7-d668-4e20-b77c-8b7ab51fc2a4`
- Downloaded report: `C:\Users\Owner\Downloads\research_06bd51f1-939f-4997-b8f0-d3c25fa5d41a.pdf`
- Extracted text: `D:\local-deep-research\continuation\research_06bd51f1_text.txt`

This note converts the LDR output into a conservative `ta_foundation` backlog seed. It is not a ledger mutation and it is not evidence of edge. Each item below still needs normal Hypothesis Author review, duplicate/graveyard checks, deterministic pre-registration, fast probe, hardened run, slippage/delay stress, and forward shadow observation.

## Read On The LDR Report

Useful:

- The report correctly converged on `orb_failure_reclaim`, `initial_balance_reversal`, `overnight_high_low_sweep_reclaim`, `prior_high_low_failed_breakout`, VWAP reclaim/reject variants, and compression/expansion as the highest-fit families.
- Most of those families are already in the registry, so the work can enter the existing agentic loop without inventing strategy classes.
- The filter/risk ideas are best treated as analysis dimensions or shared hardening overlays, not independent edge claims.

Needs correction before registration:

- Performance claims such as `>55% win rate`, `>60% win rate`, and `positive after friction` must be rewritten as falsifiable hypotheses. They are not established facts.
- Several proposed slugs were off-registry: `orb_initial_balance_extension`, `sweep_retest`, `sweep_vwap`, `vwap_pullback`, `vwap_reversal`, `vwap_mean_reversion`, and `orb_vwap_filter`.
- Source quality is mixed. The best source anchor is the IEEE Access ORB paper. Much of the sweep/VWAP material is retail/blog/TradingView/Reddit quality and should be used only for mechanism vocabulary.
- `large_candle_origin_retest` should remain low priority because prior local hardening found slippage fragility.
- Adaptive live gates such as "require 70% win rate over the last 20 trades" are likely overfit. Keep them as diagnostics, not live gating.

## Source Confidence

High confidence as research/process anchors:

- `Assessing the Profitability of Timely Opening Range Breakout on Index Futures Markets`, DOI `10.1109/access.2019.2899177`.
- Existing `ta_foundation` local evidence in `docs/designs/real_edge_discovery_program.md`, especially prior ORB short and large-candle-origin findings.

Medium confidence as definitions or implementation vocabulary:

- Investopedia opening range and VWAP definitions.
- Schwab/Capital.com/Warrior Trading/Tradervue VWAP explanations.
- Steady Turtle / Strata / Trade That Swing ORB and initial-balance guides.

Low confidence as edge evidence:

- TradingView scripts, Reddit threads, YouTube videos, Facebook posts, and generic retail "liquidity sweep" articles.
- The LDR-cited arXiv preprints dated 2026 should be verified before being used as anything more than cautionary leads.

## Registry Mapping

| LDR idea | Registry family | Action |
| --- | --- | --- |
| ORB-IB Extension | `initial_balance_extension` or `orb_breakout` | Map to `initial_balance_extension` only if using 30-120 minute IB; otherwise use `orb_breakout`. |
| ORB-Failure Reclaim | `orb_failure_reclaim` | Strong fit. |
| ORB-VWAP Filter | `orb_breakout` | Treat VWAP/volume as analysis filters; registry params cannot encode them directly. |
| Sweep Fakeout Retest | `prior_high_low_failed_breakout`, `overnight_high_low_sweep_reclaim`, or `orb_failure_reclaim` | Select family by reference level. |
| Sweep + VWAP | Same as above | VWAP is a filter/stratification variable. |
| VWAP Pullback/Reversal | `vwap_reclaim_continuation` | Strong fit if framed as reclaim-within-N-bars. |
| VWAP Mean Reversion | `vwap_reject_fade` | Strong fit if entry is rejection from extended VWAP distance. |
| Compression then Expansion | `compression_then_expansion` | Strong fit. |
| Trend Pullback Continuation | `trend_pullback_continuation` | Strong fit. |
| Exhaustion into Reference | `exhaustion_into_reference` | Strong fit. |
| Overnight High/Low Sweep Reclaim | `overnight_high_low_sweep_reclaim` | Strong fit. |
| Prior High/Low Failed Breakout | `prior_high_low_failed_breakout` | Strong fit. |
| Initial Balance Reversal | `initial_balance_reversal` | Strong fit. |

## Recommended Pre-Registration Queue

These are draft inputs for the Hypothesis Author. Parameters are intentionally modest, whitelist-compatible, and not tuned from the downloaded result.

### 1. ORB Failure Reclaim, Body-Midpoint Fill

Family: `orb_failure_reclaim`

Instrument/timeframe/session: `NQ`, `5m`, `ny_open_0730_0830_denver`

Direction: `both`

Params:

```json
{
  "orb_minutes": 5,
  "sweep_min_ticks": 4,
  "reclaim_within_bars": 2,
  "fill_mode": "body_midpoint",
  "stop_ticks": 24,
  "target_ticks": 80
}
```

Mechanism: An opening range breakout that cannot hold beyond the range traps early breakout participants. Reclaim into the range forces those late entries to exit, and a body-midpoint pullback tries to avoid chasing the immediate reversal while still participating in the trapped-flow unwind.

Falsifiable claim: This family variant should produce a non-negative expectancy after realistic NQ friction and delay when tested as a fixed rule over multiple market regimes. It fails if edge concentrates in one short calendar segment, one side, or disappears under modest entry delay/slippage.

Primary adverse tests: side split, first 30 minutes vs full first hour, 1-bar entry delay, +1 to +4 tick slippage, 2022-style high-volatility regime, 2024-2026 holdout.

### 2. Overnight High/Low Sweep Reclaim

Family: `overnight_high_low_sweep_reclaim`

Instrument/timeframe/session: `NQ`, `5m`, `ny_open_0730_0830_denver`

Direction: `both`

Params:

```json
{
  "level_type": "both",
  "sweep_min_ticks": 6,
  "reclaim_within_bars": 3,
  "stop_ticks": 28,
  "target_ticks": 90
}
```

Mechanism: Overnight highs and lows concentrate resting liquidity before the RTH open. A sweep followed by fast reclaim suggests the breakout side did not attract continuation flow; trapped breakout participants and stop-outs can fuel a reversal back into the overnight range.

Falsifiable claim: Reclaim after an overnight high/low sweep during the early RTH window should outperform an unconditional reference-level fade after transaction cost, especially when evaluated separately for ONH and ONL.

Primary adverse tests: ONH vs ONL, gap-size stratification, exclude FOMC/CPI days, entry delay, sweep threshold grid, no-VWAP-filter baseline.

### 3. Prior High/Low Failed Breakout

Family: `prior_high_low_failed_breakout`

Instrument/timeframe/session: `NQ`, `5m`, `rth_open_0730_0900_denver`

Direction: `both`

Params:

```json
{
  "level_type": "both",
  "break_buffer_ticks": 4,
  "max_failure_bars": 3,
  "stop_ticks": 28,
  "target_ticks": 90
}
```

Mechanism: Prior session extremes are visible breakout levels. When price clears the level but quickly closes back inside the prior range, breakout chasers are trapped and the reversal can be reinforced by stop-loss exits and mean-reversion participants.

Falsifiable claim: Failed breakout entries at prior-session high/low should show better post-cost expectancy than simple breakouts at the same levels. It fails if results vanish when separated by level type, session time, or delay.

Primary adverse tests: prior high vs prior low, first hour vs later RTH, target/stop asymmetry, slippage ladder, compare against random reference-level fades.

### 4. Initial Balance Reversal

Family: `initial_balance_reversal`

Instrument/timeframe/session: `NQ`, `5m`, `rth_morning_0730_0930_denver`

Direction: `both`

Params:

```json
{
  "ib_minutes": 60,
  "extension_attempt_ticks": 8,
  "stop_ticks": 30,
  "target_ticks": 100
}
```

Mechanism: A failed extension beyond the first-hour initial balance traps participants who entered late in the direction of the apparent trend day. Reversal back into the balance can be driven by failed continuation exits and inventory rebalancing.

Falsifiable claim: Failed initial-balance extension should produce more robust post-cost outcomes than a same-window unconditional IB fade. It fails if the result depends on one IB size or if stop/target widening alone explains all performance.

Primary adverse tests: 30/60/90-minute IB, NQ vs ES, extension-attempt threshold, day-of-week and volatility regime, delayed entry.

### 5. VWAP Reclaim Continuation

Family: `vwap_reclaim_continuation`

Instrument/timeframe/session: `NQ`, `5m`, `rth_open_0730_1000_denver`

Direction: `both`

Params:

```json
{
  "reclaim_max_bars": 3,
  "stop_ticks": 24,
  "target_ticks": 80
}
```

Mechanism: A failed move through VWAP followed by reclaim can trap short-horizon reversal traders. Continuation after reclaim uses VWAP as a volume-weighted reference without relying on order-flow data unavailable to the deterministic pipeline.

Falsifiable claim: VWAP reclaim continuation should outperform a generic VWAP cross when tested with fixed entry, stop, target, and no adaptive tuning. It fails if edge appears only before slippage or only in a single side/session slice.

Primary adverse tests: reclaim_max_bars grid, opening hour vs late morning, long vs short, distance from VWAP at reclaim, entry delay, compare against naive VWAP cross.

### 6. VWAP Reject Fade

Family: `vwap_reject_fade`

Instrument/timeframe/session: `NQ`, `5m`, `rth_morning_0730_1030_denver`

Direction: `both`

Params:

```json
{
  "min_distance_ticks": 12,
  "max_distance_ticks": 80,
  "stop_ticks": 24,
  "target_ticks": 70
}
```

Mechanism: When price stretches away from VWAP and fails to continue, late momentum participants become the counterparty for a reversion attempt toward the volume-weighted reference. The hypothesis is only plausible when the distance is large enough to compensate for friction but not so large that it signals a trend day.

Falsifiable claim: VWAP rejection fades inside a bounded distance window should outperform unbounded VWAP mean reversion after cost. It fails if performance is dominated by low-volatility days or is destroyed by a small slippage ladder.

Primary adverse tests: distance bucket monotonicity, trend-day exclusion vs inclusion, long/short split, NQ vs ES, 1-bar delay, ATR-normalized distance.

### 7. Compression Then Expansion

Family: `compression_then_expansion`

Instrument/timeframe/session: `NQ`, `5m`, `rth_morning_0730_1100_denver`

Direction: `both`

Params:

```json
{
  "compression_bars": 4,
  "compression_range_ticks_max": 40,
  "breakout_buffer_ticks": 4,
  "stop_ticks": 24,
  "target_ticks": 80
}
```

Mechanism: A short compression window can represent coiled liquidity and reduced realized volatility. A breakout with a small buffer attempts to capture directional expansion before the move is crowded, while avoiding broad parameter searches over pattern definitions.

Falsifiable claim: A fixed compression-window expansion rule should show better post-cost behavior than random breakout entries with the same time-of-day distribution. It fails if only one volatility regime or one calendar segment contributes the result.

Primary adverse tests: random-time matched baseline, compression_bars 3/4/5, buffer sensitivity, volatility regime split, no-trade first 10 minutes overlay.

### 8. ORB Breakout With Volume/VWAP Audit

Family: `orb_breakout`

Instrument/timeframe/session: `NQ`, `5m`, `ny_open_0730_0830_denver`

Direction: `both`

Params:

```json
{
  "orb_minutes": 5,
  "signal_type": "break_close",
  "stop_ticks": 28,
  "target_ticks": 100
}
```

Mechanism: Early directional participation can extend when the opening auction breaks the first range and fade participants are forced to cover. The LDR source trail supports ORB as a research object, but the ta_foundation version must explicitly audit volume/VWAP as post-registration stratification rather than claiming they are encoded in the registry params.

Falsifiable claim: A fixed 5-minute close-break ORB should survive realistic friction better in high participation/VWAP-aligned strata than in low participation or VWAP-opposed strata. It fails if VWAP/volume stratification is not monotonic or if performance is only one-side.

Primary adverse tests: volume/VWAP strata, first 10 minutes excluded vs included, NQ vs ES, 1-bar delay, 2-4 tick extra friction.

### 9. Trend Pullback Continuation To VWAP

Family: `trend_pullback_continuation`

Instrument/timeframe/session: `NQ`, `5m`, `rth_morning_0730_1100_denver`

Direction: `both`

Params:

```json
{
  "pullback_target": "vwap",
  "pullback_max_ticks": 40,
  "stop_ticks": 28,
  "target_ticks": 100
}
```

Mechanism: In a confirmed directional morning, a pullback to VWAP can attract participants who missed the first impulse, while counter-trend traders become trapped if the pullback fails to break the trend. This is related to VWAP reclaim but should be tested as a trend-regime family.

Falsifiable claim: VWAP pullback continuation should outperform unfiltered VWAP reclaim only in trend-labeled regimes and should underperform or flatten in range-labeled regimes. It fails if the regime label adds no separation.

Primary adverse tests: trend/range classifier ablation, target/stop widening, late morning decay, direction split, compare to `vwap_reclaim_continuation`.

### 10. Exhaustion Into Prior High/Low Reference

Family: `exhaustion_into_reference`

Instrument/timeframe/session: `NQ`, `5m`, `rth_morning_0730_1100_denver`

Direction: `both`

Params:

```json
{
  "n_bars": 5,
  "reference_type": "prior_high",
  "exhaustion_threshold_ticks": 40,
  "stop_ticks": 28,
  "target_ticks": 80
}
```

Mechanism: A multi-bar one-way move into a widely observed reference can deplete short-horizon momentum. Late chasers at the reference become the counterparty for a mean-reversion entry if follow-through fails.

Falsifiable claim: Exhaustion into prior high should show post-cost reversion behavior distinct from random five-bar impulse fades. It fails if the reference adds no value beyond generic momentum exhaustion.

Primary adverse tests: prior_high vs prior_low as separate hypotheses, random impulse fade baseline, n_bars sensitivity, high-volatility days, entry delay.

Note: This draft lists `prior_high` only because the current whitelist encodes one `reference_type` per hypothesis. A mirrored `prior_low` hypothesis should be authored separately if the first pass is worth expanding.

## Lower Priority / Hold

- `large_candle_origin_retest`: keep in the backlog only as a negative-control or revival candidate with a written difference from the previous failed/tight-geometry tests.
- Rolling `>=70%` recent-win-rate gate: do not use as live gating. Use only as a diagnostic stability slice after preregistered tests.
- Generic liquidity sweep not tied to a registered level: do not author until a registry family exists for generic swing sweep/reclaim, or map it explicitly to prior/overnight/ORB reference families.

## Suggested Next Step

Run the Hypothesis Author against this note with a small session quota, or manually author the first five items through `author_probe` after checking for ledger duplicates. Do not register all ten in one batch unless the weekly hypothesis counter and family coverage cap are intentionally budgeted for it.
