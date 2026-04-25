# Claude Code Prompt — Align LargeCandleReversal Research ↔ Strategy

You are working in the `ta_foundation` Python repo that generates two artifacts:

1. The **Large Candle Excursion findings digest** (HTML report — Executive Summary, Strategy Cards, Regime Findings, etc.).
2. The **blueprint / strategy template exporter** that emits NinjaTrader XML templates for `LargeCandleReversal.cs`.

A patched `LargeCandleReversal.cs` has just been committed. It implements several fixes that change the contract between research and strategy. Your job is to update the Python side so the two stay in lockstep, and to add regression tests that catch future drift.

The patched `.cs` file is attached as context — read it first, especially:
- `LcrOnsetCondition` enum (only `FirstLargeAfterFailedContinuation` is implemented; others warn loudly at startup)
- `LcrPrimaryHoldRule` enum (now includes `OrderlyStart`, `Fav2BarOnly`, `Adv2BarOnly`)
- `ClassifySession` method (authoritative source of session labels)
- `IsSignalCandle` (now supports `MaxRangeTicks` / `MaxBodyTicks`)
- The new NinjaScriptProperty inputs in section B and H

---

## Tasks

### 1. Update the blueprint exporter to emit session BOOLEANS, not a CSV

The patched `.cs` has **replaced** `<AllowedSessionsCsv>` with ten individual boolean elements. A template's session filter now looks like this in XML:

```xml
<SessionMode>Allowlist</SessionMode>
<AllowSessionAsia>true</AllowSessionAsia>
<AllowSessionEarlyLondon>false</AllowSessionEarlyLondon>
<AllowSessionMidLondon>false</AllowSessionMidLondon>
<AllowSessionLondonNyOverlap>false</AllowSessionLondonNyOverlap>
<AllowSessionNyPreOpen>false</AllowSessionNyPreOpen>
<AllowSessionNyOpen>false</AllowSessionNyOpen>
<AllowSessionMidDay>false</AllowSessionMidDay>
<AllowSessionPowerHour>true</AllowSessionPowerHour>
<AllowSessionAfterHours>false</AllowSessionAfterHours>
<AllowSessionOvernight>false</AllowSessionOvernight>
```

The corresponding `<OptimizationParameters>` `<Parameter>` entries also need to be ten boolean parameters (one per flag), replacing the single `AllowedSessionsCsv` string parameter.

Misspelling is now structurally impossible — `AllowSessionFooBar` simply doesn't exist and NinjaTrader will fail to load the template, rather than silently rejecting every bar.

Tasks:

- In the exporter, replace the `allowed_sessions` → CSV serializer with a writer that emits all ten `<AllowSession*>` elements. Every session the research recommends gets `true`; every other session gets an explicit `false` (not omitted — NinjaTrader expects them all present).
- Create a constant `SESSION_LABELS_CANONICAL` mapping the Python research label → the matching C# property name:

  ```python
  SESSION_LABELS_CANONICAL = {
      "asia":              "AllowSessionAsia",
      "early_london":      "AllowSessionEarlyLondon",
      "mid_london":        "AllowSessionMidLondon",
      "london_ny_overlap": "AllowSessionLondonNyOverlap",
      "ny_pre_open":       "AllowSessionNyPreOpen",
      "ny_open":           "AllowSessionNyOpen",
      "mid_day":           "AllowSessionMidDay",
      "power_hour":        "AllowSessionPowerHour",
      "after_hours":       "AllowSessionAfterHours",
      "overnight":         "AllowSessionOvernight",
  }
  ```

- Validate the input: if the research produces a label not in this map (e.g. legacy `ny_pre` truncations), the exporter raises — do not silently drop it. Grep every place the Python side emits a session string and fix drift (`ny_pre` → `ny_pre_open`, etc.) so nothing gets filtered out.
- Update the findings digest so the "Best Sessions" badge on each Strategy Card uses the canonical label (no more `"power_hour, ny_pre"` with truncation).
- Regenerate every template. Any XML still containing `<AllowedSessionsCsv>` is stale and should be deleted (NinjaTrader will error on load).

### 2. Fix the blueprint exporter's onset whitelist

The C# strategy only implements `FirstLargeAfterFailedContinuation`. Templates for any other onset take zero trades.

In `strategy_blueprint_exporter` (or wherever the XML is assembled):

- Add a constant `IMPLEMENTED_ONSETS = {"first_large_after_failed_continuation"}`.
- Before writing an XML, assert the onset is in this set. If not, either:
  (a) skip the template with a warning and record it in a `skipped_blueprints.csv` output, or
  (b) fail the run if a `--strict` flag is passed.
- Default to (a) with a printed warning naming the onset.
- Update the findings digest so Strategy Cards and the Regime Findings section visibly mark unimplemented-onset rows with a `NOT IMPLEMENTED — cannot trade` badge, and exclude them from the "top candidates" ranking.

### 3. Wire up the new hold rules

`LcrPrimaryHoldRule` now includes:

| Enum value | Python research label | Semantics |
|---|---|---|
| `MidpointReclaimYes` | `midpoint_reclaim_yes` | price reclaims signal midpoint within N bars |
| `RebreakNo` | `rebreak_no` | signal extreme not rebroken within N bars |
| `ExplosiveStart` | `explosive_start` | fav ≥ 45% AND adv ≤ 20% |
| `OrderlyStart` | `orderly_start` | fav ≥ 25% AND adv ≤ 35% AND midpoint reclaimed |
| `Fav2BarOnly` | `fav2bar_ge_35pct` (and variants) | fav ≥ threshold |
| `Adv2BarOnly` | `adv2bar_lt_20pct` (and variants) | adv ≤ threshold |

- In the exporter, add a dictionary `HOLD_RULE_MAP` that translates the research label to the C# enum string and populates the matching threshold input(s):
  - `orderly_start` → set `OrderlyFav2BarPctMin=25`, `OrderlyAdv2BarPctMax=35`, primary rule `OrderlyStart`.
  - `fav2bar_ge_35pct` → set `Fav2BarOnlyPctMin=35`, primary rule `Fav2BarOnly`.
  - `adv2bar_lt_20pct` → set `Adv2BarOnlyPctMax=20`, primary rule `Adv2BarOnly`.
  - `adv2bar_20_40pct` is a compound label (20% ≤ adv ≤ 40%). The C# strategy currently only supports a ≤ cap, not a range. Either (a) extend the `.cs` to support a `[min, max]` band for adverse excursion, or (b) reject templates using this label. Prefer (a) if the label appears in top research findings.
- Any research label not in `HOLD_RULE_MAP` is rejected with a warning, same as unimplemented onsets.

### 4. Populate the candle-size bucket from Strategy Cards

Strategy Cards specify a `Candle Bucket: 50-75` (ticks). The patched `.cs` has optional `MaxBodyTicks` / `MaxRangeTicks` (0 = disabled).

- Parse the bucket string in the blueprint exporter (e.g. `"50-75"` → `(50, 75)`).
- For `CandleBasis=Range`, populate `MinRangeTicks=50, MaxRangeTicks=75` and leave `MinBodyTicks=2, MaxBodyTicks=0`.
- For `CandleBasis=Body`, populate `MinBodyTicks=50, MaxBodyTicks=75` and leave `MinRangeTicks=4, MaxRangeTicks=0`.
- If the bucket is open-ended (`"75+"`), set only the Min and leave Max=0.

### 5. Verify — and if necessary fix — the direction semantics

This is the one ambiguity I could not resolve from the strategy code alone and needs a human-in-the-loop decision before any code change.

In the research output, Strategy Cards describe `reverse | direction=-1` with prose like *"Fade (reverse) a 50-75-tick large candle on the 2m chart."* In conventional terms, fading a bullish large candle means going short.

In the C# strategy, `LcrDirectionPolicy.CounterToFailedContinuation` returns `signalDir` — i.e., it trades **in the direction of the signal candle**. The code's internal narrative is that the signal candle is itself the reversal of a *prior* failed continuation from the opposite direction.

These two stories can be reconciled, but they can also be silently inverted depending on how the exporter emits `DirectionPolicy`.

- Find the Python function that maps the research `trade_mode` (`reverse` / `continuation`) and `direction` (±1) to a `DirectionPolicy` enum value in the XML.
- Build a truth table. For every combination of `(signal_candle_direction, trade_mode, direction)` from the research, what direction does the code end up trading, and is that what the research intended?
- If `reverse` in the research means "fade the signal candle" (short a bullish candle / long a bearish candle), but `CounterToFailedContinuation` in the code returns `signalDir`, the exporter needs to emit a policy whose code maps to `-signalDir`. The current code doesn't have one. You have two options:
  1. Add a new `FadeSignalCandle` value to `LcrDirectionPolicy` in the `.cs` that returns `-signalDir`, and have the exporter emit it for `reverse` trades.
  2. Keep the existing policy and instead update the research label generator so that the exporter's existing `CounterToFailedContinuation` maps to the correct research "trade WITH the signal as a confirming reversal" interpretation, and rename the research label from `reverse` to something accurate like `confirmed_reversal`.

Write up a short design note (`docs/direction_policy_semantics.md`) with the truth table and the chosen option before writing code.

### 6. Fix `FailedContinuationLookbackSignals` in emitted templates

The patched `.cs` changes the default from 3 to 1 to match the research definition. But existing templates in the repo explicitly write `<FailedContinuationLookbackSignals>3</FailedContinuationLookbackSignals>`.

- Update the exporter to write `1` unless the research has specifically validated a higher count.
- Regenerate all templates under `templates/` (or wherever they live).
- If there's a version-pin or cache, bump it so downstream consumers pick up the new templates.

### 7. Add a round-trip integration test

This is the most valuable part of the task because it prevents regressions.

Create `tests/test_blueprint_roundtrip.py`:

- Take every Strategy Card and every Regime Finding emitted by a representative analytics run.
- For each, generate the corresponding XML template via the exporter.
- Parse the XML and assert:
  - `OnsetCondition` is in `IMPLEMENTED_ONSETS`.
  - `PrimaryHoldRule` is one of the C# enum values and its threshold inputs are set.
  - Exactly ten `<AllowSession*>` elements are present, each with a `true`/`false` value; every research label in the card maps to a `true` flag via `SESSION_LABELS_CANONICAL`; no `<AllowedSessionsCsv>` element exists anywhere.
  - In Allowlist mode, at least one `<AllowSession*>` is `true` (otherwise the strategy takes zero trades).
  - `MinRangeTicks`/`MaxRangeTicks` (or body variants) bracket the card's bucket.
  - `FailedContinuationLookbackSignals >= 1`.
  - `DirectionPolicy` matches the decision from task #5.
- Fail the test if any emitted template would silently take zero trades.

Add this test to CI. Do not merge until it passes for the full current output set.

### 8. Regenerate the findings digest

After all of the above:

- Re-run the analytics.
- Confirm the report shows no `ny_pre` (only `ny_pre_open`), no `asia` labels being dropped, and that any unimplemented-onset findings are visibly badged as non-tradeable.
- Compare the new "top candidates" list against the old one. Document any candidates that dropped off the list because their onset or hold rule is not tradeable — these are the ones the current strategy *could not have traded anyway* and the user was previously being misled about.

---

## Working style

- Do not guess session labels or enum values from training data. Read them from the actual `.cs` file in the repo.
- Make one commit per numbered task so the diff is reviewable.
- When you finish tasks 1–4 and 6, run task #7's test locally before starting task #5. Task #5 requires human review of the design note before any code change.
- Keep every warning message greppable (prefix with `[lcr-export]`).

## Acceptance criteria

- `pytest tests/test_blueprint_roundtrip.py -v` passes green against the full current output set.
- The `first_large_after_failed_continuation_x_rebreak_no_1m.xml` template, regenerated via the exporter, has `<AllowSessionAsia>true</AllowSessionAsia>` (or whatever sessions the card recommends) and takes at least one trade on a backtest against the last 30 days of data.
- `docs/direction_policy_semantics.md` exists and is signed off.
- The regenerated findings digest flags all unimplemented-onset rows and does not promote any of them into the "top candidates" ranking.
