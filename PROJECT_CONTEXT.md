# ta_foundation — Project Context

## Purpose
Reusable foundation library for parsing NinjaTrader CSV exports and producing self-contained HTML reports for single-run and multi-run (comparison) analysis.

## Supported file types (current)
- *_Trades.csv
- *_Analysis.csv
- *_Summery.csv (also supports *_Summary.csv)

## Folder ingest behavior
- The framework ingests a folder containing many runs.
- Multiple files of the same type are supported.
- Files are grouped into runs using run_id.

## run_id derivation (default)
- run_id is derived from filename by stripping known suffixes:
  - _Trades.csv
  - _Analysis.csv
  - _Summery.csv / _Summary.csv
Example:
  bot1_Trades.csv   -> run_id = bot1
  bot1_Analysis.csv -> run_id = bot1
  bot1_Summery.csv  -> run_id = bot1

## Optional run_id regex override
- CLI supports --run-id-regex
- If provided, the first capture group is used as run_id.
- Use this if filenames include dates/variants and you want stable grouping.

## Timestamp policy (authoritative)
- Source timestamps are NinjaTrader local PC time (account/local).
- All datetimes are localized on ingest to:
  - America/Denver
- Internal tables use tz-aware datetimes only.

Metadata recorded:
- timezone: America/Denver
- timestamp_source: ninjatrader_local_pc_time
- datetime_policy: localized_on_ingest

## Data model
- One AnalysisPackage per run_id:
  - trades: DataFrame (one row per trade)
  - daily: DataFrame (one row per day/period)
  - summary: SummaryBlock (kpis_all/long/short + start_dt/end_dt)
  - warnings: list[dict]
  - metadata: dict

## KPI keys
- Summary KPIs use normalized keys (case/punctuation/spacing tolerant).
- Reports should read KPIs via .get("total net profit"), etc.

## Reports (current)
- HTML comparison report
  - single self-contained HTML file
  - all images embedded as base64 data URIs
  - sections:
    - Comparison Overview
    - Equity Curve Comparison
    - Run Metadata Cards
    - Run KPI Cards

### trades_intraday_pnl_by_day
**Default title:** Intraday Trade PnL by Day (MFE Overlay)  
**File:** `reports/html/sections/trades_intraday_pnl_by_day.py`  
**Purpose:** For each run, render a chart per trading day showing direction-adjusted realized PnL bars at entry times, with a blue MFE (potential) overlay.  
**Data sources:**
- `pkg.trades` (entry time, profit, direction, mfe)

**Options:**
- `max_days_per_run` (default 10)
- `max_trades_per_day` (default 250)
- `mfe_alpha` (default 0.22)
- `show_run_card` (default True)
- `show_legend_hint` (default True)

## How to run (CLI)
Example:
python -m ta_foundation.cli.main --input "C:/path/to/folder" --output ./outputs

Optional:
--recursive
--run-id-regex "(...)"  (first capture group used for run_id)
