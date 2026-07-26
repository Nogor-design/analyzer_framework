# Deployment Matrix (252 named templates) — Design

Status: proposed, 2026-06-04. Sibling capability to Weekly Coverage; **does not
change Weekly Coverage**. Drives a downstream daily-prediction tool that picks
from a fixed pool of named templates.

## Goal

Produce a fixed grid of **252 final named NinjaTrader templates** — the single
best template per cell — so the daily-prediction tool can pick the right
combination each day. Coverage of the grid is the product: the predictor needs
every cell populated.

## The 252-cell model

A cell is one combination of four axes. Every finished template classifies into
exactly one cell from its settings:

| Axis | Count | Derived from |
|---|---|---|
| Session | 7 | `StartTime` ∈ a `session_windows` range (`naming_rules.json`) |
| Single / Multi | 2 | `MaxTrades == 1` (or `ProfitStop == 1 && LossStop == 1`) → single, else multi |
| MA tier | 9 | `max(averageFast, averageSlow)` ∈ an `ma_tiers` range |
| God / Monster | 2 | `Reverse` = false / true |

7 × 2 × 9 × 2 = **252**. The **Descriptor** (max-loss × RR) is naming flavor
only — *not* a grid axis. It describes whatever wins each cell.

## Single source of truth: `naming_rules.json`

`D:\templateNaming\naming_rules.json` defines session windows, MA tiers, and
naming words. This capability **reads that file** to build both the optimizer
grid and the final names, so optimization and naming can never disagree. Change
a window there → regenerate → everything stays aligned.

### Session time-boxes (from current `naming_rules.json`, used as-is)

`start_minute`/`end_minute` are minutes from midnight (platform local time).
Each session becomes a fixed `StartTimeH/StartTimeM` + `DurationTimeH/DurationTimeM`.
(The seed already carries `StartTimeM`/`DurationTimeM`; Weekly Coverage simply
never used them.)

| Session | Window | StartTime | Duration |
|---|---|---|---|
| London Early | 000–239 | 00:00 | 4h00 |
| London Late | 240–419 | 04:00 | 3h00 |
| Pre-Market | 420–449 | 07:00 | 0h30 |
| NY Open | 450–479 | 07:30 | 0h30 |
| Midday | 480–719 | 08:00 | 4h00 |
| Power Hour | 720–959 | 12:00 | 4h00 |
| Asia | 960–1439 | 16:00 | 8h00 |

### MA-tier representative slowMA values (one per tier, tunable)

`averageFast` pinned small (e.g. 5) so `max(fast, slow)` = slow's tier.

| Tier | Range | slowMA | God / Monster |
|---|---|---|---|
| 1 | 2–75 | 40 | Hermes / Harpy |
| 2 | 76–125 | 100 | Artemis / Griffin |
| 3 | 126–175 | 150 | Poseidon / Medusa |
| 4 | 176–225 | 200 | Apollo / Hydra |
| 5 | 226–275 | 250 | Zeus / Chimera |
| 6 | 276–325 | 300 | Ares / Cerberus |
| 7 | 326–375 | 350 | Athena / Sphinx |
| 8 | 376–425 | 400 | Aphrodite / Siren |
| 9 | 426–500 | 450 | Dionysus / Typhon |

## Architecture — reuse the 3-stage auto-chain

The existing broad-search → risk-refine → final-validation chain is the right
shape. The Deployment Matrix is a re-parameterization plus a smarter selection.

```
base_matrix axes:
  Session       : 7 fixed time-boxes  (StartTimeH/M + DurationTimeH/M per session)
  Reverse       : [false, true]
  averageSlow   : 9 tier representatives
  averageFast   : fixed small (pin)

stage_1 (optimizer): sweep MaxStop, MaxTPRatio (descriptor variety)
  selection: coverage grid group_by (Session, Reverse, tier), keep 1
  -> 7 x 2 x 9 = 126 structural winners

refine_risk (optimizer): from stage_1.selected_rows, pin structure,
  sweep ProfitStop, LossStop, MaxTrades  (MaxTrades sweep MUST include 1)
  selection: group_by (parent_candidate_id, single_multi), keep 1 each
  -> best single + best multi per lane = 252

final_backtest (fixed_backtest): validate the 252 (1:1 risk-refine path)
```

This reuses the orchestrator, child-stage template generation, final validation,
and the package builder unchanged.

## What must be built

1. **`deployment_matrix` recipe builder** — like `_weekly_coverage_recipe_payload`,
   but emits the session time-boxes, 9 tier slowMAs, and the single/multi-aware
   refine selection. Reads `naming_rules.json`.
2. **Derived-key selection (the hard part).** The selection engine groups by
   literal param columns; it must group by **computed** keys: `session` (time →
   window), `ma_tier` (max(fast,slow) → tier), `single_multi` (MaxTrades /
   ProfitStop / LossStop). Approach: a small classifier that pre-computes these
   three columns onto each result row before selection, using `naming_rules.json`.
   This is the only genuinely new engine work.
3. **Forced single-trade coverage.** To guarantee a single-trade winner per cell,
   the `MaxTrades` sweep must explicitly include `1`, and selection keeps the best
   `single` and best `multi` per lane.
4. **Naming + predictor manifest.** Name the 252 via `naming_rules.json`
   (`[Phase][MAName][Descriptor][Direction]`) and emit a machine-readable
   **manifest** (`deployment_matrix_manifest.{json,csv}`: cell → template name →
   file path → key metrics). **This manifest is the interface to the
   daily-prediction tool.**
5. **252-cell coverage report + best-effort fallback.** Green / amber / red per
   cell. Cells with no guardrail-passing template get the best sub-threshold
   template, clearly flagged, so the predictor always sees a full grid.

## Deliverables

- 252 final named XML templates (best per cell) + best-effort fallbacks for gaps.
- `deployment_matrix_manifest.{json,csv}` — the predictor's pick list.
- A 252-cell coverage report (which cells are real winners vs fallback vs empty).
- A new web page (sibling to Weekly Coverage) to launch/track it. *(Phase 4.)*

## Phased build plan

- **Phase 1** — `deployment_matrix` recipe builder driven by `naming_rules.json`
  (grid only; reuse existing selection where possible). Unit-test the grid is
  exactly 126 lanes with correct time-boxes/tiers.
- **Phase 2** — derived-key classifier + single/multi-aware refine selection;
  test that one lane yields exactly best-single + best-multi.
- **Phase 3** — naming integration + predictor manifest + 252-cell coverage
  report + fallback.
- **Phase 4** — web launcher page (clone of Weekly Coverage UX) + tracking.

## Open detail decisions (defaults proposed; tune later)

- **slowMA tier representatives** — table above (mid-range, tunable).
- **`averageFast`** — pin at 5 (keeps tier governed by slowMA).
- **`MaxTrades` sweep** — include `1` plus a few > 1 (e.g. 1, 3, 5, 10).
- **Instrument scope** — NQ only first (matches Weekly Coverage); market suffix in
  the name. Multi-market multiplies the grid by #markets.
- **Per-cell count** — 1 best (the predictor wants a single pick per cell).

## Risks / notes

- **Combinatorics.** 126 lanes × stage-1 combos can exceed Weekly Coverage. Reuse
  the Fit-ranges budgeting idea for the risk pass; cap stage-1 sweep widths.
- **Short windows.** 30-min single-trade extreme-MA monster cells may legitimately
  have no profitable template — fallback + coverage reporting handle this.
- **Tier from `max(fast, slow)`.** Keep `averageFast` small/fixed so the tier is
  governed by `averageSlow`; otherwise a large swept fast could shift the tier.
- Keep `naming_rules.json` the only place windows/tiers/names are defined.

## Cross-references

- `docs/runbooks/weekly_optimization_and_reports_guide.md` — the sibling flow.
- `src/ta_foundation/web/optimizer_weekly_coverage_package.py` — naming/lane logic
  to generalize against `naming_rules.json`.
- `app.py:_weekly_coverage_recipe_payload` — the recipe-builder pattern to fork.
- `src/ta_foundation/web/optimizer_recipe_selection.py` — where derived-key
  grouping must be added.
