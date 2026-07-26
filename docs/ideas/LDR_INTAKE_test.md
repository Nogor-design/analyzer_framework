# External Research Intake - Local Deep Research

This file is a review artifact. It does not register hypotheses or modify the research ledger.

## Source

- Tool: `local_deep_research`
- Run ID: `test_ldr_run_001`
- Predecessor run ID: ``
- Report path: `d:\Backup\projects\PythonProject\ta_foundation\docs\ideas\test_ldr_report.md`
- Extracted text path: ``
- Prompt/report hash: `fdad978462473a5b1ae716e1fb3788d73293d0a1ae75d786c0bb455a5a7c91ef`
- Imported at: `2026-05-22T12:56:12Z`

## Warnings

- Report contains performance language; rewrite all such language as hypotheses before ledger registration.
- Report cites low-confidence retail/social sources; use them only for mechanism vocabulary or implementation examples.

## Registry Validation

- `ORB Failure Reclaim, Body-Midpoint Fill` (orb_failure_reclaim): `ready`
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

### 2. Compression Then Expansion

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
