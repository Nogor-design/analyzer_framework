# ta_foundation — Project Context

## Purpose
`ta_foundation` is a reusable analytics/reporting framework for NinjaTrader exports.
It supports both run-attached analysis and market-data-driven research workflows, producing self-contained HTML reports.

---

## Ingest model (current)

### Run-scoped inputs (CSV)
Typical parser coverage includes:
- `*_Trades.csv`
- `*_Analysis.csv`
- `*_Summery.csv` / `*_Summary.csv`
- `*_Settings.csv`
- `*_Optimization.csv`

### Shared market inputs
- minute bars: `*.Last*.txt` (minute format)
- ticks: `*.Last*.txt` (tick format)
- optional tick parquet cache files under `.ta_cache/`

Shared artifacts are ingested with `run_id=None` and attached to `MarketDataStore`.

---

## run_id behavior

Default `run_id` derivation strips known suffixes (for CSV run files), e.g.:
- `bot1_Trades.csv` → `bot1`
- `bot1_Analysis.csv` → `bot1`
- `bot1_Summery.csv` → `bot1`

Optional override:
- `--run-id-regex "(...)"` uses capture group 1 as run_id.

---

## Time policy (authoritative)

- Canonical timezone: `America/Denver`.
- All canonical datetimes are tz-aware.
- NinjaTrader run timestamps are localized on ingest.
- Market minute/tick feeds may come from UTC-style exports and are normalized during ingest/parsing.

Metadata commonly records:
- `timezone: America/Denver`
- `timestamp_source`
- `datetime_policy`

---

## Data model

### AnalysisPackage (run-scoped)
One package per run_id with:
- `trades`
- `daily`
- `summary`
- `settings`
- `metadata`
- `assets`
- `warnings`

### MarketDataStore (shared)
Stores shared market series and derived cache:
- minute bars
- ticks
- resampled bars cache

Consumers typically access minute bars via:
- `market.get(instrument, contract)`
or timeframe bars via:
- `market.get_bars(instrument, contract, timeframe="5m", source="auto")`

---

## Report system

Config/build flow:

```text
report.yaml (or multi-report YAML)
  ↓ load_report_config(s)
build_report_from_config(packages, cfg, market, optimization_store)
  ↓ HtmlReportBuilder.build(context)
section.render_fn(section_ctx)
```

Sections are registry-driven and rendered as pure HTML using context.

---

## CLI usage (typical)

```bash
python -m ta_foundation.cli.main \
  --input /path/to/run_folder \
  --output ./outputs \
  --report-config ./report.yaml
```

Common options:
- `--recursive`
- `--run-id-regex "(...)"`
- `--market-data /path/to/market_data`
- `--no-tick-data`
- `--include-run-images`

---

## Extension mindset

The framework is intentionally stable.
New functionality should fit one of:
- parser,
- analysis helper,
- report section,
- minor pipeline enhancement.

When in doubt, prefer extending existing modules over introducing new architecture.
