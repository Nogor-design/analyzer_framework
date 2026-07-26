# Weekly Coverage Package User Guide

Status: current, updated 2026-06-02.

This guide documents the weekly Pantheon coverage workflow: a one-off launcher
page that creates the coverage recipe, drives the optimizer recipe stages, and
builds the final team package.

## Purpose

The weekly coverage workflow builds a deployable set of named NinjaTrader XML
templates from a repeatable lane grid. It now starts upstream at recipe
creation, not only after an already completed final backtest run.

It is designed for the Pantheon weekly workflow where the operator wants a
repeatable grid:

```text
StartTimeH bucket x Reverse side x slowMA family

StartTimeH: 0, 4, 8, 12, 16, 20
Reverse: false, true
slowMA: 20, 50, 100, 200, 300, 400
target: up to 2 operationally distinct validated templates per lane
```

That default grid is 72 lanes. With the default target of 2 templates per lane,
the maximum deployable target is 144 templates.

The default Stage 1 estimate is 72 templates x 112 optimizer combinations =
8,064 backtests. The 112 combinations come from averageFast fixed at 5, seven
MaxStop values, four MaxTPRatio values, and the Long/Short enable sweep.

The final package builder itself does not rerun NinjaTrader. It reads the final
backtest review CSVs and copies the best available named XML templates into a
new package folder. The weekly launcher page is what creates and starts the
NinjaTrader recipe run.

## Where To Run It

Open the one-off weekly launcher:

```text
/optimizer/weekly-coverage
```

The sessions page also links to it:

```text
/optimizer/sessions
```

The page should auto-select `PantheonMasterBotV01TesterV2` when that strategy
is available. It checks the project weekly seed store first:

```text
.ta_artifacts/web_optimizer/recipe_seeds/
```

If no seed is available, click `Generate weekly seed`. This writes a seed into
the project seed store rather than the normal NinjaTrader template folder, so
clearing or changing that template folder does not remove the weekly seed.

Click:

```text
Run weekly coverage
```

The button calls:

```text
POST /api/optimizer/weekly-coverage/run
```

That endpoint creates an optimizer session, saves the weekly coverage recipe,
builds the recipe plan, and starts the recipe orchestrator.

After final backtests are ingested, click:

```text
Build package & reports
```

That button calls:

```text
POST /api/optimizer/sessions/<session_id>/weekly-coverage-package
```

The API returns links for the standard report, coverage report, category-prune
page, refinement page, and ZIP download.

## Inputs

The builder requires final backtest review artifacts under:

```text
.ta_artifacts/web_optimizer/sessions/<session_id>/deployment_package/final_backtest_handoff/final_backtest_review/
```

Required:

- `result_intake.csv`
- `evaluated_candidates.csv`

Optional:

- `recommendations.csv`

Template sources are read from:

```text
deployment_package/final_backtest_handoff/named_backtest_templates/
deployment_package/final_backtest_handoff/renamed_backtest_templates/
```

If `renamed_template_index.json` exists, the builder uses it to map `F_NNN`
run IDs to semantic template names. Otherwise, it falls back to XML parameter
signature matching.

The weekly launcher also requires:

- Strategy: defaults to `PantheonMasterBotV01TesterV2` when found.
- Seed template: project weekly seed store first, normal NinjaTrader templates
  second.
- Instrument and market suffix.
- Optional final backtest start/end dates.
- Validation guardrails.
- Lane grid values.
- Stage 1 search ranges.

## Outputs

The durable output folder is:

```text
deployment_package/weekly_coverage_package/
```

Key contents:

| Path | Purpose |
|---|---|
| `operationally_diverse_validated_named_templates/` | Deployable named XML templates. Use this folder for the weekly package. |
| `operationally_diverse_validated_named_templates_by_run/` | Same deployable XMLs, prefixed by `F_NNN` for traceability. |
| `best_effort_fallback_named_templates/` | Clearly separated fallback XMLs for lanes with no validated pass. Review before deploying. |
| `best_effort_fallback_named_templates_by_run/` | Same fallback XMLs, prefixed by `F_NNN` for traceability. |
| `review_all_top2_named_templates/` | Review-only top-two templates before final validation filtering. Do not deploy blindly. |
| `review_all_top2_named_templates_by_run/` | Review-only XMLs, prefixed by `F_NNN`. |
| `data/operationally_diverse_validated_selection.csv` | Manifest for deployable templates. |
| `data/best_effort_fallback_selection.csv` | Manifest for fallback templates. |
| `data/operationally_diverse_lane_coverage.csv` | Coverage counts by bucket, side, and slowMA. |
| `data/operational_diversity_duplicate_audit.csv` | Candidates dropped as duplicate operational shapes. |
| `data/review_all_top2_selection.csv` | Manifest for review-only top-two picks. |
| `reports/operationally_diverse_weekly_coverage_package_report.html` | Human-readable package report. |
| `package_manifest.json` | Machine-readable package summary. |

The ZIP is written beside the folder:

```text
deployment_package/weekly_coverage_package.zip
```

The report and ZIP are also served from:

```text
GET /optimizer/sessions/<session_id>/weekly-coverage-package/report
GET /optimizer/sessions/<session_id>/weekly-coverage-package.zip
```

## Selection Logic

The package is lane-aware. A lane is:

```text
StartTimeH + Reverse + average_slow
```

For each lane, the builder considers final backtest rows that passed final
validation (`status == pass`) and then ranks by:

1. Profit factor
2. Net profit
3. Final review score fallback

It keeps up to `target_per_lane` candidates, default `2`, but only if they are
operationally distinct.

If a lane has no validated pass, the builder adds the best available
sub-threshold candidate as a best-effort fallback. Fallbacks are copied into a
separate folder and reported separately so the team can see exactly which lanes
did not meet guardrails.

## Operational Diversity Rule

Two candidates are not both useful if they are effectively the same bot with a
different filename. The builder therefore compares each candidate's operational
shape before adding it as a second lane representative.

Operational shape includes:

- Duration hours
- `averageFast`
- `MaxStop`
- `MaxTPRatio`
- `ProfitStop`
- `LossStop`
- `MaxTrades`
- Long/Short direction shape:
  - `long_only`
  - `short_only`
  - `both`
  - `disabled`
- Realized trade-count band:
  - `single_trade`
  - `few_trades_2_5`
  - `moderate_6_15`
  - `active_16_40`
  - `high_activity_41_plus`

This means a long-only candidate and a short-only candidate can both survive
even if their risk settings are similar. A one-trade candidate is also treated
as different from a more active multi-trade candidate.

Exact parameter duplicates are dropped first. Candidates with the same
operational shape are also dropped.

Dropped candidates are recorded in:

```text
data/operational_diversity_duplicate_audit.csv
```

## Current Defaults

The current durable defaults are intentionally Pantheon-friendly:

```json
{
  "start_hours": [0, 4, 8, 12, 16, 20],
  "slow_ma_values": [20, 50, 100, 200, 300, 400],
  "target_per_lane": 2,
  "duration_hours": 4,
  "require_final_status": "pass",
  "package_dirname": "weekly_coverage_package"
}
```

The launcher exposes those values as editable controls. The package API reads
the lane grid from the session recipe by default so the package cannot drift
from what the optimizer actually ran.

## Current Implementation

Main package module:

```text
src/ta_foundation/web/optimizer_weekly_coverage_package.py
```

Launcher page:

```text
src/ta_foundation/web/templates/optimizer_weekly_coverage.html
```

Coverage selection mode:

```text
src/ta_foundation/web/optimizer_recipe_selection.py
```

Routes:

```text
GET  /optimizer/weekly-coverage
POST /api/optimizer/weekly-coverage/run
GET  /api/optimizer/weekly-coverage/recent
POST /api/optimizer/sessions/<session_id>/weekly-coverage-package
GET  /optimizer/sessions/<session_id>/weekly-coverage-package/report
GET  /optimizer/sessions/<session_id>/weekly-coverage-package.zip
```

Related review pages:

```text
GET /optimizer/sessions/<session_id>/refine
GET /optimizer/sessions/<session_id>/category-bundle
```

Focused tests:

```text
src/ta_foundation/tests/web/test_optimizer_weekly_coverage_package.py
src/ta_foundation/tests/web/test_optimizer_routes.py
src/ta_foundation/tests/web/test_optimizer_category_bundle.py
src/ta_foundation/tests/web/test_optimizer_refinement.py
```

Verification:

```powershell
python -m pytest src\ta_foundation\tests\web\test_optimizer_weekly_coverage_package.py src\ta_foundation\tests\web\test_optimizer_routes.py src\ta_foundation\tests\web\test_optimizer_category_bundle.py src\ta_foundation\tests\web\test_optimizer_refinement.py -q
```

## Known Limitations

- The weekly page auto-advances the recipe while it is open by polling
  `/api/optimizer/sessions/<session_id>/recipe/advance`. That is intentional
  for simplicity, but operators should know the page is active, not passive.
- Lanes with no validated final pass are filled only with clearly separated
  best-effort fallbacks.
- It does not yet compare against previous weekly packages.
- It does not yet build portfolio bundles across time buckets.

## Recommended Next Step

The next durable phase should make the weekly page more operator-safe:

1. Add a compact status checklist for seed, NinjaTrader readiness, Stage 1,
   final backtest, package, refinement, and category pruning.
2. Make auto-advance behavior explicit in the UI.
3. Add a previous-week comparison report.
4. Add portfolio-bundle summaries across time buckets.
