# Continuation Prompt: Weekly Coverage Package Report Repair

Use this prompt to continue the weekly Pantheon coverage report work in a fresh
Codex thread.

---

You are working in:

```text
D:\Backup\projects\PythonProject\ta_foundation
```

Current date context for this handoff: 2026-06-03.

The user is refining the team-facing weekly package report for a completed
optimizer session. The focus of this pass was not to rerun NinjaTrader. The
work was to inspect the finished weekly package, reconnect the executive card
report, fix misleading report labels/counts, regenerate the report artifacts,
and document what changed.

## Active Reference Session

The current weekly package session is:

```text
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44
```

Important package/report paths:

```text
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package.zip
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\operationally_diverse_weekly_coverage_package_report.html
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\executive_cards_deduped\final_template_cards_report_deduped.html
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\executive_cards_deduped\deduped_report_manifest.json
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\executive_cards_deduped\cards
```

The deduped executive report is the user's current team-facing report. It is
filtered to one representative per semantic base name and includes:

- `daily_winner_spotlight`
- `run_executive_profile_cards`
- `weekly_leaderboard_cards`

The deduped manifest currently lists 14 run ids:

```text
F_001, F_005, F_007, F_011, F_013, F_019, F_023,
F_025, F_029, F_030, F_031, F_033, F_039, F_045
```

## Package Stats From This Session

The weekly package for `opt_f2ac9fefeb44` was generated from the completed
weekly run and reported:

- 22 validated deployable templates
- 1 fallback template
- 38 review-only templates
- 1 duplicate operational shape dropped
- 12 of 48 lanes covered

## Report Repairs Completed

### 1. Reconnected the final/executive card report

The final executive card report was regenerated under the weekly package
instead of only under the broad deployment package. The team-facing output is:

```text
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\executive_cards_deduped\final_template_cards_report_deduped.html
```

The report was regenerated with 14 exported PNG cards in:

```text
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\executive_cards_deduped\cards
```

The weekly package ZIP was refreshed after regeneration:

```text
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package.zip
```

### 2. Naming bucket cleanup and user guide clarification

External naming files are outside this repo:

```text
D:\templateNaming\naming_rules.json
D:\templateNaming\NamingUserGuide.md
```

The current session buckets in `naming_rules.json` are:

| Session | Start minute | End minute | Single | Multi |
|---|---:|---:|---|---|
| London Early | 0 | 239 | Rise | Rising |
| London Late | 240 | 419 | Prime | Priming |
| Pre-Market | 420 | 449 | Coil | Coiling |
| NY Open | 450 | 479 | Rage | Raging |
| Midday | 480 | 719 | Drift | Drifting |
| Power Hour | 720 | 959 | Close | Closing |
| Asia | 960 | 1439 | Dawn | Dawning |

Important naming rule: `-ing` is based on configured session trade style, not
total backtest trades. A template with `MaxTrades = 1` should use the
single-trade phase word even when it traded many times across many days.
`MaxTrades > 1` uses the `-ing` phase word unless both `ProfitStop` and
`LossStop` are set to `1`, which also forces a single-trade style.

### 3. Fixed executive card MaxTrades display

User-visible symptom:

```text
ClosingArtemisFireB was named correctly, but its card showed
"Max Trades per Session: 1".
```

Root cause:

```text
src\ta_foundation\reports\html\sections\run_executive_profile_cards.py
```

The card read `MaxTrades` from settings, then later overwrote it with
`derived["effective_max_trades_per_session"]`. That derived value represented
observed/derived behavior and was not the template's configured max-trades
setting.

Fix:

- Keep the displayed "Max Trades per Session" value from template settings.
- Do not override it with the derived effective max-trades value.

Verification:

- `F_013` / `ClosingArtemisFireB` now displays `Max Trades per Session: 5`.

### 4. Fixed Daily Winner Insight strategy names

User-visible symptom:

```text
Daily Winner Insight showed F_001, F_005, etc. instead of readable strategy
names.
```

Changed file:

```text
src\ta_foundation\reports\html\sections\daily_winner_spotlight.py
```

Fix:

- Added a display-name helper that prefers
  `pkg.metadata["derived"]["display_name"]`, then `display_name_spaced`, then
  run id.
- Winner card, runner-up text, strongest support card, and the main table now
  use the readable strategy name.
- The table subtext keeps the run id as a secondary reference, for example
  `... | F_001`.

### 5. Fixed win/loss strip date anchoring

User-visible symptom:

```text
The daily winner strip and executive card strip did not agree for recent days.
Some strips included future/current calendar days after the backtest ended.
```

Changed file:

```text
src\ta_foundation\reports\html\sections\_wlr_strip.py
```

Root cause:

- `compute_shared_trading_days()` anchored to the wall-clock current date.
- Historical regenerated reports could show extra no-trade boxes for days
  after the report data ended.

Fix:

- Anchor the shared strip to the latest date present in
  `pkg.metadata["derived"]["daily_outcomes"]["by_date"]`.
- Return recent weekdays ending on the latest data date.
- For `days_back <= 0`, return weekdays between min and max data dates.

Verified example for `F_001` / `DriftAresInfernoB`:

```text
2026-05-25 LOSS
2026-05-26 LOSS
2026-05-27 WIN
2026-05-28 NO_TRADE
2026-05-29 FLAT
2026-06-01 WIN
```

### 6. Fixed Weekly Prop Dashboard future-day window

User-visible symptom:

```text
DriftApolloEmberB showed 5 wins in the last 6 days on the top chart and card,
but the Weekly Prop Dashboard showed only 1 win day.
```

Changed file:

```text
src\ta_foundation\reports\html\sections\weekly_leaderboard_cards.py
```

Root cause:

- The Weekly Prop Dashboard auto window could expand to a Sun-Fri calendar
  window after the data ended.
- For this report, the actual data ended on `2026-06-01`, but the dashboard
  card was evaluating `2026-05-31` through `2026-06-05`.
- That produced mostly future no-trade days and only one visible win day.

Fix:

- In auto mode, resolve the dashboard to a rolling data-ended window instead of
  a future-expanded calendar week.
- Align the top W/L strip with the resolved dashboard window when the requested
  recent-day count equals the window length.
- Display readable strategy names in the Weekly Prop Dashboard card identity,
  with the run id preserved in the title attribute.

Verified example for `F_005` / `DriftApolloEmberB`:

```text
Rolling 6-day window ending 2026-06-01
2026-05-25 WIN
2026-05-26 WIN
2026-05-27 WIN
2026-05-28 NO_TRADE
2026-05-29 WIN
2026-06-01 WIN
Dashboard text: W 5 L 0 N 1
```

## Commands Used For Verification

The following targeted compile check was run during the report repair pass:

```powershell
python -m py_compile src/ta_foundation/reports/html/sections/weekly_leaderboard_cards.py
```

The report was regenerated programmatically using
`build_session_candidate_report()` with these sections:

```text
daily_winner_spotlight
run_executive_profile_cards
weekly_leaderboard_cards
```

After regeneration:

- 14 deduped cards exported
- deduped manifest updated
- weekly package ZIP refreshed
- HTML inspection confirmed `DriftApolloEmberB` shows `W 5 L 0 N 1`
- direct data check confirmed the resolved window ends on `2026-06-01`

## Browser Note

The user had the in-app browser open to:

```text
file:///D:/Backup/projects/PythonProject/ta_foundation/.ta_artifacts/web_optimizer/sessions/opt_f2ac9fefeb44/deployment_package/weekly_coverage_package/reports/executive_cards_deduped/final_template_cards_report_deduped.html
```

The browser plugin refused a programmatic reload of that `file://` URL because
of its URL policy. The file on disk was regenerated and verified. If the user
still sees stale values, ask them to manually refresh the open tab.

## Current Touched Source Files In This Report Pass

```text
src/ta_foundation/reports/html/sections/run_executive_profile_cards.py
src/ta_foundation/reports/html/sections/daily_winner_spotlight.py
src/ta_foundation/reports/html/sections/_wlr_strip.py
src/ta_foundation/reports/html/sections/weekly_leaderboard_cards.py
```

External naming docs touched:

```text
D:\templateNaming\naming_rules.json
D:\templateNaming\NamingUserGuide.md
```

Generated artifacts touched:

```text
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\executive_cards_deduped\final_template_cards_report_deduped.html
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\executive_cards_deduped\deduped_report_manifest.json
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package\reports\executive_cards_deduped\cards\*.png
.ta_artifacts\web_optimizer\sessions\opt_f2ac9fefeb44\deployment_package\weekly_coverage_package.zip
```

## Worktree Warning

The worktree is very dirty and includes many unrelated modified and untracked
files. Do not revert, clean, or overwrite unrelated files. Continue to isolate
changes to the files needed for the user's current request.

## Recommended Next Checks

If a follow-on conversation continues the report QA, start here:

1. Open the deduped HTML report path above.
2. Check the Daily Winner Insight names are readable, with run ids only as
   secondary detail.
3. Check `ClosingArtemisFireB` shows `Max Trades per Session: 5`.
4. Check `DriftAresInfernoB` recent strip ends on `2026-06-01`.
5. Check `DriftApolloEmberB` in Weekly Prop Dashboard shows `W 5 L 0 N 1`.
6. If more report mismatches appear, first compare the section's date window
   against the latest actual outcome date before changing strategy metrics.

