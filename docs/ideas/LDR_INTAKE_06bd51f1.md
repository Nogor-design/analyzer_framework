# External Research Intake - Local Deep Research

This file is a review artifact. It does not register hypotheses or modify the research ledger.

## Source

- Tool: `local_deep_research`
- Run ID: `06bd51f1-939f-4997-b8f0-d3c25fa5d41a`
- Predecessor run ID: `af2af4d7-d668-4e20-b77c-8b7ab51fc2a4`
- Report path: `C:\Users\Owner\Downloads\research_06bd51f1-939f-4997-b8f0-d3c25fa5d41a.pdf`
- Extracted text path: `output\research_intake\LDR_INTAKE_06bd51f1.txt`
- Prompt/report hash: `7fafe6677d15512f64e8e18da7a1dd667b03573d517c268cf6b8c45885a918bf`
- Imported at: `2026-05-14T13:34:07Z`

## Warnings

- Report contains performance language; rewrite all such language as hypotheses before ledger registration.
- Report cites low-confidence retail/social sources; use them only for mechanism vocabulary or implementation examples.
- large_candle_origin_retest appeared in the report; prior local hardening found slippage fragility, so require a revival reason.

## Registry Validation

- `ORB Failure Reclaim, Body-Midpoint Fill` (orb_failure_reclaim): `ready`
- `Overnight High/Low Sweep Reclaim` (overnight_high_low_sweep_reclaim): `ready`
- `Prior High/Low Failed Breakout` (prior_high_low_failed_breakout): `ready`
- `Initial Balance Reversal` (initial_balance_reversal): `ready`
- `VWAP Reclaim Continuation` (vwap_reclaim_continuation): `ready`
  - warning: low source confidence; require stronger local evidence before accepting
- `VWAP Reject Fade` (vwap_reject_fade): `ready`
  - warning: low source confidence; require stronger local evidence before accepting
- `Compression Then Expansion` (compression_then_expansion): `ready`

## Candidate Queue

Each candidate is still subject to family whitelist validation, duplicate/graveyard checks, quota controls, deterministic testing, and human review.

### 1. ORB Failure Reclaim, Body-Midpoint Fill

- Status: `draft`
- Family: `orb_failure_reclaim`
- Instrument/timeframe/session: `NQ`, `5m`, `ny_open_0730_0830_denver`
- Direction: `both`
- Source confidence: `medium`

Params:

```json
{
  "fill_mode": "body_midpoint",
  "orb_minutes": 5,
  "reclaim_within_bars": 2,
  "stop_ticks": 24,
  "sweep_min_ticks": 4,
  "target_ticks": 80
}
```

Mechanism:

An opening range breakout that cannot hold beyond the range traps early breakout participants. Reclaim into the range forces those late entries to exit, and a body-midpoint pullback attempts to avoid chasing the immediate reversal.

Falsifiable claim:

This fixed ORB failure-reclaim variant should produce non-negative expectancy after realistic NQ friction and entry delay across multiple market regimes.

Primary adverse tests:

- Long/short side split.
- First 30 minutes versus full first hour.
- One-bar entry delay.
- +1 to +4 tick slippage ladder.
- 2024-2026 holdout.

Source notes:

- ORB has the strongest source trail in the LDR report.
- Failure-reclaim mechanics are plausible but require local validation.

### 2. Overnight High/Low Sweep Reclaim

- Status: `draft`
- Family: `overnight_high_low_sweep_reclaim`
- Instrument/timeframe/session: `NQ`, `5m`, `ny_open_0730_0830_denver`
- Direction: `both`
- Source confidence: `medium`

Params:

```json
{
  "level_type": "both",
  "reclaim_within_bars": 3,
  "stop_ticks": 28,
  "sweep_min_ticks": 6,
  "target_ticks": 90
}
```

Mechanism:

Overnight highs and lows concentrate resting liquidity before the RTH open. A sweep followed by fast reclaim suggests the breakout side did not attract continuation flow, trapping participants back inside the overnight range.

Falsifiable claim:

Early RTH reclaim after an overnight high/low sweep should outperform an unconditional reference-level fade after cost.

Primary adverse tests:

- ONH and ONL split.
- Gap-size stratification.
- Exclude scheduled macro-news days.
- Sweep threshold grid.
- Compare to no-VWAP-filter baseline.

Source notes:

- Sweep sources are mostly retail quality.
- The reference level itself is deterministic and registry-backed.

### 3. Prior High/Low Failed Breakout

- Status: `draft`
- Family: `prior_high_low_failed_breakout`
- Instrument/timeframe/session: `NQ`, `5m`, `rth_open_0730_0900_denver`
- Direction: `both`
- Source confidence: `medium`

Params:

```json
{
  "break_buffer_ticks": 4,
  "level_type": "both",
  "max_failure_bars": 3,
  "stop_ticks": 28,
  "target_ticks": 90
}
```

Mechanism:

Prior session extremes are visible breakout levels. When price clears the level but quickly closes back inside the prior range, breakout chasers are trapped and their exits may reinforce a reversal.

Falsifiable claim:

Failed breakout entries at prior-session high/low should outperform simple breakouts at the same levels after cost.

Primary adverse tests:

- Prior high versus prior low.
- First hour versus later RTH.
- Stop/target asymmetry.
- Slippage ladder.
- Random reference-level fade baseline.

Source notes:

- Source trail supports reference-level behavior more than edge.

### 4. Initial Balance Reversal

- Status: `draft`
- Family: `initial_balance_reversal`
- Instrument/timeframe/session: `NQ`, `5m`, `rth_morning_0730_0930_denver`
- Direction: `both`
- Source confidence: `medium`

Params:

```json
{
  "extension_attempt_ticks": 8,
  "ib_minutes": 60,
  "stop_ticks": 30,
  "target_ticks": 100
}
```

Mechanism:

A failed extension beyond the first-hour initial balance traps participants who entered late in the direction of the apparent trend day. Reversal back into balance can be driven by failed continuation exits and inventory rebalancing.

Falsifiable claim:

Failed initial-balance extension should produce more robust post-cost behavior than an unconditional IB fade.

Primary adverse tests:

- 30/60/90-minute IB split.
- NQ versus ES.
- Extension-attempt threshold grid.
- Day-of-week and volatility regimes.
- Delayed entry.

Source notes:

- IB is a standard market-profile concept, but edge claims need local proof.

### 5. VWAP Reclaim Continuation

- Status: `draft`
- Family: `vwap_reclaim_continuation`
- Instrument/timeframe/session: `NQ`, `5m`, `rth_open_0730_1000_denver`
- Direction: `both`
- Source confidence: `low`

Params:

```json
{
  "reclaim_max_bars": 3,
  "stop_ticks": 24,
  "target_ticks": 80
}
```

Mechanism:

A failed move through VWAP followed by reclaim can trap short-horizon reversal traders. Continuation after reclaim uses VWAP as a volume-weighted reference without relying on order-flow data outside the deterministic pipeline.

Falsifiable claim:

VWAP reclaim continuation should outperform a generic VWAP cross when tested with fixed entry, stop, target, and no adaptive tuning.

Primary adverse tests:

- Opening hour versus late morning.
- Long versus short split.
- Distance from VWAP at reclaim.
- Entry delay.
- Naive VWAP cross baseline.

Source notes:

- VWAP definitions are well sourced; signal edge sources are weaker.

### 6. VWAP Reject Fade

- Status: `draft`
- Family: `vwap_reject_fade`
- Instrument/timeframe/session: `NQ`, `5m`, `rth_morning_0730_1030_denver`
- Direction: `both`
- Source confidence: `low`

Params:

```json
{
  "max_distance_ticks": 80,
  "min_distance_ticks": 12,
  "stop_ticks": 24,
  "target_ticks": 70
}
```

Mechanism:

When price stretches away from VWAP and fails to continue, late momentum participants become the counterparty for a reversion attempt toward the volume-weighted reference.

Falsifiable claim:

VWAP rejection fades inside a bounded distance window should outperform unbounded VWAP mean reversion after cost.

Primary adverse tests:

- Distance bucket monotonicity.
- Trend-day exclusion versus inclusion.
- Long/short split.
- NQ versus ES.
- ATR-normalized distance.

Source notes:

- Mostly retail VWAP strategy sources; use as a test prompt, not evidence.

### 7. Compression Then Expansion

- Status: `draft`
- Family: `compression_then_expansion`
- Instrument/timeframe/session: `NQ`, `5m`, `rth_morning_0730_1100_denver`
- Direction: `both`
- Source confidence: `medium`

Params:

```json
{
  "breakout_buffer_ticks": 4,
  "compression_bars": 4,
  "compression_range_ticks_max": 40,
  "stop_ticks": 24,
  "target_ticks": 80
}
```

Mechanism:

A short compression window can represent coiled liquidity and reduced realized volatility. A buffered breakout attempts to capture directional expansion before the move is crowded.

Falsifiable claim:

A fixed compression-window expansion rule should beat random breakout entries with the same time-of-day distribution after cost.

Primary adverse tests:

- Random-time matched baseline.
- Compression bars 3/4/5.
- Breakout buffer sensitivity.
- Volatility regime split.
- No-trade first 10 minutes overlay.

Source notes:

- Mechanism is generic but registry-compatible and easy to falsify.
