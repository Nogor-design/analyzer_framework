# Recipe Optimizer Final Review Workflow

This README explains the end-user path after a recipe optimizer run has reached final fixed backtests.

Use this flow when the goal is to pick candidate strategies for different parts of the day, compare the final candidates with Python reports, decide whether to refine or deploy, and get the NinjaTrader template XML files.

## Quick Path

1. Open the recipe optimizer page.
2. Go to `Candidate Results`.
3. Select `Final Fixed Backtests (final_backtest)` from `Execution Stage`.
4. Review the passing winners and rejected rows.
5. Use `Decision dashboard` for the final decision workspace.
6. Use `Rename final templates`.
7. Use `Build all-template report`.
8. Use `Build per-candidate reports` when individual candidate reports are useful.
9. Use `Open final templates` or `Download templates` to get the NinjaTrader XML files.

## What The Final Results Page Shows

The final result table is the first review point.

It should show:

- The final candidate ID, such as `F_007`.
- Whether the row is a selected winner.
- Profit factor, net profit, max drawdown, trades, and score.
- The important parameter values that produced the candidate.
- Rejection reasons for rows that did not pass the final filters.

For a time-bucket recipe, the final selector should preserve the original Stage 1 bucket idea. If Stage 1 was built as 12 time buckets with 2 final winners per bucket, the target is 24 final templates. A bucket may produce fewer than 2 if its candidates fail the filters, but that should be visible in the bucket report rather than silently disappearing.

## Decision Dashboard

Open the decision dashboard from either place:

- Recipe page: `Candidate Results` -> final stage -> `Decision dashboard`
- Session page: `Decision dashboard`

The decision dashboard is the main final review page. It is where the user should decide whether to deploy, refine, or keep investigating.

The dashboard provides:

- Recommended final candidates.
- Rejected candidates and reasons.
- Links to each candidate report.
- A link to the all-template comparison report.
- Final NinjaTrader template links.
- Buttons to build reports and rename templates.

For the example session used during validation:

- Session: `opt_32eddad58acc`
- Reviewed final candidates: 27
- Passing recommendations: 4
- Rejected candidates: 23
- All-template report: `.ta_artifacts/web_optimizer/sessions/opt_32eddad58acc/deployment_package/session_candidate_report.html`

Passing recommendations in that run:

| Candidate | Profit Factor | Net Profit | Max Drawdown | Trades | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `F_007` | 1.89 | 4780 | 1050 | 38 | Best available regression candidate for London Early. |
| `F_013` | 1.58 | 1205 | 950 | 32 | Best available breakout candidate for London Early. |
| `F_008` | 1.72 | 3760 | 1165 | 35 | Passed filters, but had recent trade fading note. |
| `F_010` | 1.62 | 4025 | 1755 | 35 | Passed filters, but had recent trade fading note. |

## Reports

There are two useful report types.

### All-Template Comparison Report

Use `Build all-template report`.

This generates one HTML report that compares all final candidates together. Use this when deciding which winners are worth running live, refining, or discarding.

For the example session, the generated file is:

`D:/Backup/projects/PythonProject/ta_foundation/.ta_artifacts/web_optimizer/sessions/opt_32eddad58acc/deployment_package/session_candidate_report.html`

The generated comparison report included:

- `comparison_overview`
- `equity_curve_comparison`
- `run_kpi_cards`
- `run_metadata_cards`
- `daily_scoreboard`
- `daily_winner_spotlight`
- `daily_leaderboard_cards`
- `run_settings_table`

Open it from the UI with `View all-template report`, or directly at:

`/optimizer/sessions/<session_id>/candidate-report`

### Per-Candidate Reports

Use `Build per-candidate reports`.

This generates one HTML report per final candidate. Use these when a single candidate needs deeper inspection.

For the example session, reports were generated under:

`D:/Backup/projects/PythonProject/ta_foundation/.ta_artifacts/web_optimizer/sessions/opt_32eddad58acc/deployment_package/per_candidate_reports/`

Example:

`D:/Backup/projects/PythonProject/ta_foundation/.ta_artifacts/web_optimizer/sessions/opt_32eddad58acc/deployment_package/per_candidate_reports/F_007.html`

Open a candidate report from the decision dashboard row link, or directly at:

`/optimizer/sessions/<session_id>/candidates/<run_id>/report`

Example:

`/optimizer/sessions/opt_32eddad58acc/candidates/F_007/report`

## NinjaTrader Templates

The final templates are fixed Backtest XML templates. These are the files the user takes to NinjaTrader.

From the final page or decision dashboard:

- `Open final templates` shows the active final template file list.
- `Download templates` downloads the active final templates as a ZIP.
- `Rename final templates` copies the final templates into the renamed active template folder.

For the example session, the active template folder after rename is:

`D:/Backup/projects/PythonProject/ta_foundation/.ta_artifacts/web_optimizer/sessions/opt_32eddad58acc/deployment_package/final_backtest_handoff/renamed_backtest_templates/`

The downloadable ZIP endpoint is:

`/optimizer/sessions/opt_32eddad58acc/templates/final.zip`

The file list endpoint is:

`/optimizer/sessions/opt_32eddad58acc/templates/final`

Note: in this environment, `template_naming` was not importable, so the rename step used the fallback names that preserve the run ID and original recipe stem. The files are still usable NinjaTrader XML templates.

## Refine Decision

Use refinement when the final results show promise but the candidate needs a narrower follow-up search.

Good refine signals:

- A time bucket has one strong candidate but not enough depth.
- A candidate passes filters but has a warning, such as recent trade fading.
- A rejected candidate has high net profit but failed drawdown.
- Neighboring parameter values suggest the winning region has not been fully explored.

Good deploy or paper-trade signals:

- Candidate passed the hard filters.
- Drawdown is within the configured limit.
- Profit factor is above the threshold.
- Trade count is not too thin.
- The all-template comparison report does not show an obvious instability.

The decision dashboard is the right page for this choice because it keeps the recommendations, rejection reasons, report links, and template links together.

## Recommended End-User Mental Model

Think of the workflow as four gates:

1. `Final Results`: Did any final candidate pass?
2. `Reports`: Do the comparison and candidate reports support the pass?
3. `Decision`: Deploy, paper trade, refine, or reject?
4. `Templates`: Download or open the final NinjaTrader XML files.

The user should not need to hunt through artifact folders for normal operation. The UI buttons should carry them from final candidates to reports, decision review, and templates.
