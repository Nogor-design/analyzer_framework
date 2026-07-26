# Discovery → NT Entry Parameterization (Path A: enum-driven entry)

**Status:** Phase 1 implemented (2026-06-03) — pending NT compile + live parity run
**Author:** Claude (PM + executor)
**Date:** 2026-06-03
**Decision locked:** Path A — one enum-driven NT strategy. Hybrid A→B later only
for families that earn it.

## Implementation status (2026-06-03)

Done (Python-side fully tested; C# pending NT compile):
- **C# `StrategyDiscoveryFilter.cs`**: `SdfEntrySignal` enum (EMA cross + 9
  candle patterns + N-bar breakout), `SdfTimingMode` enum, `SdfCandleFeatureEngine`
  (parity-faithful anatomy / rolling-avg-with-shift(1) / SMA-of-TR ATR — explicitly
  NOT NinjaTrader's Wilder ATR), entry dispatch, NextOpen + stop/limit timing,
  and a `[SDF-SIGNAL]` debug probe that logs raw pattern fires before gating.
  D2: Entry Pattern parameter group exposes all thresholds (pinned, not swept).
- **`nt_template_generator.py`**: maps discovered `structure` → `EntrySignal`,
  writes all entry params, derives `BarsPeriod` from the timeframe (no longer
  hard-coded to 1m), accepts `options["entry_params"]` / `["timeframe_minutes"]`
  / `["entry_signal"]`. Pattern defaults equal patterns.py defaults, so a
  default-threshold discovery matches with nothing supplied.
- **`parity_harness.py`** + **`scripts/parity_signal_export.py`**: export the
  Python signal-bar list and diff it against an NT `[SDF-SIGNAL]` dump.
- **Offline algorithm validation** — `candle_signal_stream.py`: a bar-by-bar
  streaming Python twin of the C# engine. `test_candle_signal_stream_parity.py`
  proves it fires on exactly the same bars as the vectorized `detect_pattern`
  ground truth across all 9 candle patterns and multiple random seeds. This
  validates the entry MATH (shift(1) rolling means, simple-mean ATR, NaN
  semantics) without NinjaTrader. Residual risk after this is only the
  C#→NT transcription/wiring, which the live `[SDF-SIGNAL]` diff catches.
- **Tests**: `test_nt_template_entry_params.py` (18), `test_parity_harness.py` (7),
  `test_candle_signal_stream_parity.py` (3); full strategy_discovery suite green (428).

Phase 3 done (Python, tested; no NT launch):
- **`analysis/strategy_discovery/edge_spec.py`**: `EdgeSpec` (structure / entry
  signal / tf / timing / direction / stop / target / observed PF·WR·N),
  `edge_spec_from_discovery`, and `compare_to_discovery` →
  confirmed / decayed / diverged / underpowered verdict (the acceptance gate;
  `diverged` explicitly says "check parity first").
- **`web/optimizer_recipe_from_edge.py`**: `build_confirmation_seed_xml` (seed
  baseline with the entry pinned) + `build_confirmation_recipe` →
  `OptimizerRecipeDocument` targeting StrategyDiscoveryFilter, direction/regime
  fixed, a tight StopTicks/TargetTicks sweep around the discovered values, and a
  final backtest. Round-trips through save/load_recipe.
- **Tests**: `test_edge_spec.py`, `test_optimizer_recipe_from_edge.py`; discovery
  + web suites green (1014 passed, 15 skipped).

Wire-up done (Python, tested; no NT dispatch):
- **`web/optimizer_recipe_from_edge.py::prepare_confirmation_session`** + route
  **`POST /api/optimizer/edge-confirm/run`** (app.py). Accepts an `edge` (EdgeSpec)
  or a `discovery` dict; regenerates the StrategyDiscoveryFilter seed, patches the
  discovered timeframe into it, creates a session, saves the confirmation recipe
  (entry pinned in base_matrix, stop/target swept), and builds the plan.
  **SAFE BY DEFAULT**: `start=false` assembles + returns the plan for review and
  dispatches NOTHING; `start=true` is required to launch RecipeRunOrchestrator
  (writes the AddOn command file — never do this during another NT run).
- Recipe builder now pins the FULL entry (enum + bools + pattern thresholds) as
  base_matrix `fixed`, so `_patch_fixed` reproduces the discovered entry regardless
  of seed defaults; only the data-series timeframe is patched into the seed.
- **Tests**: `test_optimizer_edge_confirm_route.py` runs the whole prepare flow
  against the real `.cs` in a fake NT install. web+discovery suites green
  (1018 passed, 15 skipped).

Remaining:
- **NT compile + live parity run** (user machine): compile the strategy, run with
  `EnableDebugPrint=true`, diff with the harness. THIS is the gate before trusting
  any recipe result. Then a real `start=true` confirmation run + read back the PF
  and feed `compare_to_discovery` for the verdict.
- **Phase 2**: verify BreakExtreme/BodyMidpoint fills against the Python outcome
  simulator (order logic is written but unproven).
- **UI affordance**: a button on the discovery NT-template section that POSTs to
  the route (the API is ready; only the front-end button is missing).
- **Deferred families**: bb / pullback / level / lcr / MA-pullback / ORB.

---

## Problem

The Strategy Discovery engine finds *entry triggers* (candle patterns, ORB
breaks, BB reversals, MA pullbacks, N-bar breakouts, level/LCR touches), each
crossed with a timeframe, an entry **timing mode**, TP/SL, and
regime/session/direction filters. But the NinjaTrader harness that's supposed to
confirm those edges — `StrategyDiscoveryFilter.cs` — has a **single hard-coded
entry**:

```csharp
bool longSignal  = CrossAbove(entryEma, regimeEma, 1);   // EMA(5) x EMA(200)
bool shortSignal = CrossBelow(entryEma, regimeEma, 1);
```

Everything else it exposes (regime, session, direction, exit, daily risk) is a
*filter on top of that one entry*. The template generator
(`nt_template_generator.py`) reads the discovered rule's `family`/`structure`
(`_extract_signal_insights`, line 616) but writes them only into an XML
**comment** — the rule's `conditions` are mined solely for `adx`, `regime`,
`session_label`, `direction`. **The discovered entry pattern is discarded.**

Result: a discovery of "bullish engulfing on 5m, NY morning, PF 1.4" is
backtested in NT as "EMA(5)×EMA(200) on 1m with a trending filter." Unrelated
system; the confirmation PF is meaningless. This is the bug the user observed.

Three secondary mismatches ride along:

1. **Timeframe** — generated XML hard-codes a 1-minute `BarsPeriod`
   (`nt_template_generator.py:747`). A 5m/15m discovery can never match.
2. **Timing mode** — the harness only does market entries on bar close
   (≈ `next_open`). `break_extreme` (stop beyond the bar) and `body_midpoint`
   (limit retrace into the body) — both simulated and reported by discovery —
   are not modeled.
3. **Parity** — nothing verifies the C# detector fires on the *same bars* as the
   Python detector. Without that, "same edge?" is unanswerable.

## Goal

A discovery result can be carried into a NinjaTrader recipe optimization that
**actually trades the discovered entry**, so that the NT profit factor /
win-rate / trade count can be compared against what discovery reported. When they
diverge, the cause is a real edge decay or a parity bug — never an
apples-to-oranges strategy mismatch.

Success looks like: pick a top discovery row → generate a seed whose entry is
pinned to that discovery → run the recipe optimizer → see an NT PF within a
stated tolerance of the discovered PF (or a clear parity-failure signal).

## Why this shape

The recipe optimizer is already strategy-agnostic: `regenerate_recipe_seed`
(`optimizer_strategy_catalog.py`) extracts `[NinjaScriptProperty]` params from
*any* `.cs`, pins them, and lets the recipe planner own the sweeps. So the only
missing piece is an NT strategy whose entries match discovery. Making the entry a
**parameter** (Path A) means:

- one strategy, one template format, one generator code path;
- the entry family becomes just another swept dimension — the recipe optimizer
  can compare entry families for free;
- smallest change to the generator (set one enum + a few pattern params).

Per-family strategies (Path B) are deferred to whichever 2–3 families survive
validation and need richer params than an enum row can carry.

---

## Architecture

```
Discovery result (signal_entry_discovery / cross-family leaderboard)
        │
        │  emit EdgeSpec (structured, JSON-safe)
        ▼
EdgeSpec { family, structure, timeframe, timing_mode, direction,
           pattern_params, regime/session filters, stop_ticks, target_ticks,
           observed_pf, observed_wr, observed_n }
        │
        │  nt_template_generator → write entry params (NOT just filters)
        ▼
StrategyDiscoveryFilter seed XML
   - EntrySignal enum pinned to structure
   - Timeframe / TimingMode pinned
   - pattern params pinned
   - stop/target/filters pinned (recipe sweeps a tight band around them)
        │
        │  regenerate_recipe_seed + recipe planner
        ▼
Recipe optimization run in NT
        │
        ▼
Compare NT PF/WR/N  ──►  EdgeSpec.observed_*   (acceptance gate / parity check)
```

### Component 1 — `EntrySignal` enum in `StrategyDiscoveryFilter.cs`

Replace the hard-coded EMA cross with a switch over an `SdfEntrySignal` enum.
Phase 1 set (chosen for fidelity + simplicity of C# reproduction):

| Enum value            | Python source (`patterns.py` / family)      | Notes |
|-----------------------|---------------------------------------------|-------|
| `EmaCross` (0)        | existing                                    | keep as baseline |
| `EngulfingBullish`/`Bearish` | `detect_engulfing_*`                 | bar-2 body engulfs bar-1 |
| `PinBarBullish`/`Bearish`    | `detect_pin_bar_*`                   | wick/body ratio |
| `LargeBody`           | `detect_large_body`                         | body > N× rolling avg body |
| `CleanBreakoutBar`    | `detect_clean_breakout_bar`                 | |
| `InsideBar`/`OutsideBar` | `detect_inside_bar`/`detect_outside_bar` | |
| `NbarBreakout`        | `breakout/` family                          | N-bar high/low channel |
| `OrbBreak`            | `orb/` family                               | opening-range break |

Defer to Path B (stateful zone logic, poor enum fit): `bb`, `pullback`,
`level`, `lcr`, MA pullback.

New optimizable params (all `[NinjaScriptProperty]` so the catalog/recipe sees
them):

- `EntrySignal` (enum) — **but enums are NOT optimizable by NT** (see Gotchas);
  expose as a plain pinned property, and add an *int* `EntrySignalId` mirror if
  we ever want the recipe to sweep across families.
- `BodyMultiplier` (double) — for large-body / engulfing size gates.
- `WickToBodyMax` (double) — for pin bars.
- `BreakoutLookback` (int) — for N-bar breakout.
- `OrbRangeMinutes` (int) — for ORB.
- `EntryTimeframeMinutes` (int) — see Component 3.
- `TimingMode` (enum/int) — `NextOpen` | `BreakExtreme` | `BodyMidpoint`.
- `BufferTicks` (double) — for break_extreme.
- `FillTimeoutBars` (int) — for break_extreme / body_midpoint.

The existing regime/session/direction/exit/risk machinery stays as-is; entry is
now layered cleanly in front of it.

### Component 2 — `EdgeSpec` + generator writes entry params

Add a small JSON-safe `EdgeSpec` dataclass (likely alongside
`nt_template_generator.py`) carrying everything needed to reproduce a discovered
edge. Populate it from `signal_entry_discovery.top_signal_rules` /
`cross_family_optimizer.CandidateRank` (which already has
`family`, `signal_id`, `params`, `tf`, `pf`, `win_rate`, `n_trades`,
`is_oos_degradation`).

`generate_nt_template` / `generate_per_rule_templates` must then **write the
entry params** (`EntrySignal`, `EntryTimeframeMinutes`, `TimingMode`, pattern
params) from the EdgeSpec — not drop them into a comment. Stop hard-coding the
1-minute `BarsPeriod`; derive it from `EntryTimeframeMinutes`.

### Component 3 — timeframe handling

Two viable options, pick during build:

- **(a) Native bar period** — set the seed's `BarsPeriodSerializable` to the
  discovery timeframe (5 → 5-minute bars). Simplest; entry logic reads `Close[0]`
  etc. directly. Constraint: ORB/session timing must still reference wall-clock.
- **(b) 1-minute base + internal resample** — add a secondary `BarsArray`. More
  faithful to the Python resample boundaries but more C# complexity.

Recommend (a) for Phase 1; revisit if parity (Component 4) shows resample-edge
drift.

### Component 4 — C#↔Python parity harness (non-negotiable)

Before any recipe result is trusted: export the discovery's exact signal bar
timestamps for one rule, run `StrategyDiscoveryFilter` with `EnableDebugPrint`,
and assert NT entries fire on the **same bars** (within ±1 bar for fill modes).
This is the make-or-break step — the project has already been burned by C#/Python
divergence (ORB PF 3.88→1.4 fill-model bugs; Pantheon parity loop). Reuse that
discipline. A parity mismatch invalidates everything downstream.

### Component 5 — discovery → recipe routing

With the entry now real, route an EdgeSpec into a recipe run:

1. Generate the seed (entry + filters pinned).
2. `regenerate_recipe_seed` for `StrategyDiscoveryFilter`.
3. Recipe planner places a **tight sweep** around `stop_ticks`/`target_ticks`
   (and optionally regime threshold), entry params stay pinned.
4. Run; capture NT PF/WR/N.
5. Compare to `EdgeSpec.observed_*`; flag `|ΔPF|` beyond tolerance as decay or
   parity failure.

Surface this as a "Send to recipe optimizer" action on the discovery NT-template
section (`reports/html/sections/strategy_discovery_nt_template.py`) and/or a CLI
shim.

---

## Phasing

- **Phase 1 — entry parameterization + parity.** EntrySignal enum (candle set +
  EMA + N-bar + ORB), generator writes entry params, timeframe option (a),
  parity harness green for ≥2 families. *No recipe wiring yet.*
- **Phase 2 — timing modes.** `BreakExtreme` / `BodyMidpoint` order logic +
  parity for those modes.
- **Phase 3 — recipe routing.** EdgeSpec → recipe seed → run → PF comparison +
  UI/CLI action.
- **Phase 4 (optional, Hybrid A→B).** Promote 2–3 surviving families to
  dedicated strategies if the enum row can't carry their params.

## Acceptance criteria

- [ ] `StrategyDiscoveryFilter` takes an entry-signal parameter; EMA cross is one
      of several options, not the only path.
- [ ] Generated seed for an engulfing/pin-bar/large-body discovery sets the entry
      to that pattern and the correct timeframe — verified by reading the XML.
- [ ] Parity harness: NT entry bars match Python signal bars for ≥2 families
      (documented run, not just asserted in prose).
- [ ] A discovery row can be pushed into a recipe run and produces an NT PF that
      is compared, in-product, against the discovered PF.
- [ ] `python -m pytest src/ta_foundation/tests/ -q` green; new tests for the
      generator's entry-param emission and EdgeSpec extraction.

## Gotchas

- **NT optimizer cannot enumerate `enum`/`String`/`DateTime`/`Brush` params** —
  it `.GetType()`s the value and throws `NullReferenceException`, taking down
  Strategy Analyzer (see `_is_optimizable_type` / `_pin_all_optimization_parameters`
  in `optimizer_strategy_catalog.py`). The `EntrySignal` enum must be **pinned
  and excluded** from `<OptimizationParameters>`. If we ever want to sweep entry
  families, use an **int** `EntrySignalId` instead.
- **Seed invariant** (CLAUDE.md / `REGENERATE_SEED_GUIDE.md`): the recipe seed
  must pin every param (`Min==Max==ValueSerializable`) and carry exactly one
  `__recipe_placeholder__` swept row. Adding entry params must not let the seed
  contribute sweep dimensions, or Stage 1 combinations explode.
- **tz-aware America/Denver** everywhere on the Python side; NT bar `Time[]` is
  account-local. Session/ORB windows must line up — a TZ offset will silently
  break parity.
- **C# must reproduce Python detector math exactly**: body = |close-open|,
  rolling-avg-body lookback, ATR period, wick/body ratios. Copy the constants
  from `candle/features.py` and `patterns.py`; don't re-derive by eye.
- Don't trust IL from `NinjaTrader.Gui.dll` (obfuscated). NT cold-start is 1–2
  min before IPC dispatches.

## Out of scope (this round)

- BB / pullback / level / LCR / MA-pullback entries (Path B, later phase).
- Changing the recipe planner / standalone optimizer under `optimization/`.
- Any new CLI flags for report rendering (belongs in `report.yaml`).
- Multi-instrument cross-validation (separate `cross_instrument.py` workstream).

## Key files

- `src/ta_foundation/strategies/StrategyDiscoveryFilter/StrategyDiscoveryFilter.cs`
  — add EntrySignal/Timeframe/TimingMode params + entry switch.
- `src/ta_foundation/analysis/strategy_discovery/nt_template_generator.py`
  — EdgeSpec, write entry params, derive BarsPeriod from timeframe.
- `src/ta_foundation/analysis/entry_strategies/candle/{patterns,features}.py`
  — source of truth for C# detector parity (read-only reference).
- `src/ta_foundation/analysis/entry_strategies/cross_family_optimizer.py`
  — `CandidateRank` is the natural EdgeSpec source.
- `src/ta_foundation/web/optimizer_strategy_catalog.py`
  — recipe seed generation; respect `_is_optimizable_type` / pinning invariants.
- `src/ta_foundation/reports/html/sections/strategy_discovery_nt_template.py`
  — surface "send to recipe optimizer".
- `docs/REGENERATE_SEED_GUIDE.md` — seed invariants (read before touching seeds).
```
