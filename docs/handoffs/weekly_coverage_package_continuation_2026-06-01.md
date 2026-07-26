# Continuation Prompt: Weekly Coverage Package And Coverage Recipe

Use this prompt to continue the weekly Pantheon coverage work in a fresh Codex
thread.

Latest report QA addendum:

```text
docs\handoffs\weekly_coverage_report_repair_handoff_2026-06-03.md
```

Read that addendum after this file when continuing report/card/debug work for
the `opt_f2ac9fefeb44` weekly package.

---

You are working in:

```text
D:\Backup\projects\PythonProject\ta_foundation
```

Current date context: 2026-06-01.

The user is building a weekly workflow for a NinjaTrader / Pantheon optimizer
recipe. They want to run an optimization each week and produce a deployable
package of final named XML templates with reports.

## Business Goal

The desired weekly grid is:

```text
StartTimeH buckets: 0-4, 4-8, 8-12, 12-16, 16-20, 20-24
Reverse false: Gods
Reverse true: Monsters
slowMA anchors: 100, 200, 300, 400
target: top 2 useful templates per lane
```

Naming guide:

```text
D:\templateNaming\NamingUserGuide.md
```

Important naming semantics:

- Reverse false = Gods
- Reverse true = Monsters
- slowMA 100 = Artemis / Griffin
- slowMA 200 = Apollo / Hydra
- slowMA 300 = Ares / Cerberus
- slowMA 400 = Aphrodite / Siren

The user specifically said that two templates with almost identical parameters
are not useful. Key distinctions that should count as meaningful:

- Long vs Short flags
- Long-only vs short-only vs both
- one/few trades vs many trades
- materially different risk and timing parameters

## Current Reference Session

The run used while building this capability was:

```text
.ta_artifacts\web_optimizer\sessions\opt_627e6fa4eb51
```

Important final package artifacts:

```text
.ta_artifacts\web_optimizer\sessions\opt_627e6fa4eb51\deployment_package\weekly_coverage_package
.ta_artifacts\web_optimizer\sessions\opt_627e6fa4eb51\deployment_package\weekly_coverage_package.zip
.ta_artifacts\web_optimizer\sessions\opt_627e6fa4eb51\deployment_package\weekly_coverage_package\reports\operationally_diverse_weekly_coverage_package_report.html
```

Current result from durable builder on that session:

- 36 operationally diverse deployable templates
- 86 review-only top-two templates
- 17 duplicate operational shapes dropped
- 48 lanes total
- 6 full lanes
- 24 thin lanes
- 18 missing lanes

## What Was Implemented

Minimal durable capability:

```text
src/ta_foundation/web/optimizer_weekly_coverage_package.py
```

It reads final backtest review outputs:

```text
deployment_package/final_backtest_handoff/final_backtest_review/result_intake.csv
deployment_package/final_backtest_handoff/final_backtest_review/evaluated_candidates.csv
deployment_package/final_backtest_handoff/final_backtest_review/recommendations.csv
```

It copies named XML templates from:

```text
deployment_package/final_backtest_handoff/named_backtest_templates/
deployment_package/final_backtest_handoff/renamed_backtest_templates/
```

It creates:

```text
deployment_package/weekly_coverage_package/
deployment_package/weekly_coverage_package.zip
```

Decision Dashboard button:

```text
Build weekly package
```

Routes:

```text
POST /api/optimizer/sessions/<session_id>/weekly-coverage-package
GET  /optimizer/sessions/<session_id>/weekly-coverage-package/report
GET  /optimizer/sessions/<session_id>/weekly-coverage-package.zip
```

Focused test:

```text
src/ta_foundation/tests/web/test_optimizer_weekly_coverage_package.py
```

The test proves that an exact duplicate is dropped but a Long/Short operational
difference survives.

Docs added:

```text
docs/runbooks/weekly_coverage_package_user_guide.md
docs/handoffs/weekly_coverage_package_continuation_2026-06-01.md
```

Indexes updated:

```text
docs/DOCS_INDEX.md
docs/handoffs/README.md
```

## Verification Already Run

```powershell
python -m pytest src\ta_foundation\tests\web\test_optimizer_weekly_coverage_package.py -q
python -m compileall -q src\ta_foundation\web\optimizer_weekly_coverage_package.py src\ta_foundation\tests\web\test_optimizer_weekly_coverage_package.py
```

Both passed when the minimal durable capability was added.

## Important Repo State Warning

The worktree may contain many unrelated modified and untracked files. Do not
revert or clean them. Only touch files needed for the user's current request.

Known files touched by this workstream include:

```text
src/ta_foundation/web/optimizer_weekly_coverage_package.py
src/ta_foundation/web/app.py
src/ta_foundation/web/templates/optimizer_decision_dashboard.html
src/ta_foundation/tests/web/test_optimizer_weekly_coverage_package.py
docs/runbooks/weekly_coverage_package_user_guide.md
docs/handoffs/weekly_coverage_package_continuation_2026-06-01.md
docs/DOCS_INDEX.md
docs/handoffs/README.md
```

There is prior related work in the repo for:

- final report section picker
- selected-row final reports from the Decision Dashboard
- `final_template_bundle_basket` report section

Do not assume those are cleanly committed.

## Next Recommended Work

The user wants the weekly package to become a true optimizer recipe, not only a
post-run package builder.

Recommended next phase:

1. Add a `coverage_matrix_sequence` recipe preset or mode.
2. Make lane identity first-class:

```text
StartTimeH + Reverse + averageSlow
```

3. In stage selection, keep top PF and top Net Profit per lane.
4. Add operational diversity during selection so duplicates do not consume lane
   slots.
5. Ensure final backtest reads from fully refined selected rows, not broad
   stage-1 selected rows.
6. Add a coverage status table to the Recipe UI:

```text
filled / thin / missing by time bucket, God/Monster side, slowMA family
```

7. Reuse the existing weekly package builder as the final export step.

## Design Notes For The Next Implementation

Current weekly package defaults:

```json
{
  "start_hours": [0, 4, 8, 12, 16, 20],
  "slow_ma_values": [100, 200, 300, 400],
  "target_per_lane": 2,
  "require_final_status": "pass",
  "package_dirname": "weekly_coverage_package"
}
```

Operational shape currently includes:

- duration hours
- averageFast
- MaxStop
- MaxTPRatio
- ProfitStop
- LossStop
- MaxTrades
- Long/Short direction shape
- realized trade-count band

For upstream recipe selection, consider adding:

- minimum parameter distance score
- dedupe exact raw parameter signature first
- keep one PF winner and one Net winner when distinct
- if PF and Net winner are the same shape, fill the second slot from the next
  best operationally different candidate
- label lane gaps as `missing_no_results`, `missing_failed_validation`,
  `thin_duplicate_only`, or `thin_only_one_passed`

## User Preference

The user wants pragmatic, minimal durable improvements. Prefer small working
features over a large redesign. Be careful not to hinder NinjaTrader jobs that
may be running; read existing artifacts freely, but do not start/rerun NT work
unless explicitly asked.
