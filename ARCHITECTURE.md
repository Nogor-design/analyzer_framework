# ta_foundation — Architecture

## Overview
`ta_foundation` is a reusable library for:
1) ingesting NinjaTrader CSV exports from a folder containing multiple runs,
2) normalizing data into a stable internal model,
3) generating self-contained HTML reports with embedded images,
4) emitting a reproducible manifest containing file hashes, run mappings, and warnings.

The system is designed to scale by adding:
- new parsers (new file types),
- new report sections (new charts/tables),
without rewriting the pipeline.

---

## Core non-negotiable contracts

### Time handling
- Source timestamps are NinjaTrader local PC time (account/local).
- All datetimes are localized on ingest to `America/Denver`.
- Internal dataframes use timezone-aware datetimes only.
- This applies to:
  - Trades: entry_time, exit_time
  - Daily: date (Period) anchored at midnight America/Denver
  - Summary: start_dt, end_dt

### Multi-run ingest
- A folder may contain many runs, with multiple files of each supported type.
- Files are grouped into runs using `run_id`.
- Default run_id: strip known suffixes from filename:
  - `_Trades.csv`, `_Analysis.csv`, `_Summery.csv`, `_Summary.csv`
- Optional override: CLI `--run-id-regex` (first capture group becomes run_id).

### KPI key normalization
- Summary KPI dictionaries are stored as normalized-key dicts.
- Consumers should use tolerant lookups (e.g. `k.get("total net profit")`).
- Normalization removes casing/punctuation/spacing differences.

### Reporting
- HTML reports are self-contained: all images are embedded as base64 PNG data URIs.
- Reports are composed of reusable “sections”.
- Enabled sections and titles are controlled by `report.yaml`.

---

## Repository layout (src-based packaging)
ta_foundation/
pyproject.toml
PROJECT_CONTEXT.md
ARCHITECTURE.md
REPORT_SECTIONS.md
report.yaml

src/ta_foundation/
core/
model.py # AnalysisPackage, SummaryBlock
registry.py # ParserRegistry + header sampling
pipeline.py # ingest_folder + derive_run_id
manifest.py # sha256 hashing + manifest.json writer
combine.py # (optional) helpers to combine frames across runs
utils/
parsing.py # parse_money, parse_percent, parse_local_dt
kpi.py # normalize_kpi_key + NormalizedKpiDict
parsers/
base.py # Parser protocol + ParsedArtifact
ninjatrader/
trades_csv.py
analysis_by_day_csv.py
summary_csv.py
reports/
html/
builder.py # HtmlReportBuilder + HtmlSection
theme.py # default_css
embed.py # fig_to_base64_png
registry.py # SECTION_REGISTRY (id -> render_fn)
config.py # load_report_config + build_report_from_config
sections/
comparison_overview.py
equity_curve.py
run_metadata.py
run_kpis.py
cli/
main.py # CLI entrypoint (ingest + report + manifest)


---

## Runtime data flow

### 1) Discovery & parser selection
- `core.pipeline.ingest_folder(...)` enumerates CSV files in the input folder.
- For each file, it reads a header sample and asks `core.registry.ParserRegistry` for a matching parser.
- Unrecognized CSV files are collected in `unparsed_files`.

### 2) run_id grouping
- Each parsed file is assigned a `run_id` via `derive_run_id(...)`.
- Artifacts are assembled into one `AnalysisPackage` per run_id.

### 3) Normalization
- Each parser produces canonical columns and typed values:
  - money/percent fields → numeric
  - dates/times → tz-aware America/Denver
- Summary KPIs are stored in normalized-key dictionaries.

### 4) Reporting
- `reports.html.config.load_report_config(...)` loads `report.yaml` (or defaults).
- `build_report_from_config(packages, cfg)` builds a section list from the registry.
- The HTML builder renders each section into cards.
- Charts are rendered via matplotlib and embedded as base64 PNGs.

### 5) Manifest output
- `core.manifest.write_manifest(...)` writes:
  - parsed file hashes + sizes + run_id mapping + parser name/kind
  - unparsed files list
  - warnings by run_id
  - report output filename/path
  - report config path and resolved config

---

## How to extend safely

### Add a new parser
1) Create a new module under `parsers/<vendor>/...py`
2) Implement:
   - `can_parse(path, header) -> bool`
   - `parse(path, run_id) -> ParsedArtifact`
3) Use shared helpers from `utils.parsing`
4) Register the parser in CLI registry construction (or future auto-registration)
5) Ensure output attaches to `AnalysisPackage` (new field or `metadata`)

### Add a new report section
1) Create `reports/html/sections/<name>.py` with `render_<name>(ctx) -> str`
2) Add entry to `reports/html/registry.py` with a stable section id
3) Enable it in `report.yaml`

### Add behavior configuration
- Add keys under `report:` or section-specific options under each section entry in `report.yaml`
- Extend `reports/html/config.py` to pass section options in `ctx`

---

## Operational conventions
- Never mix naive and tz-aware datetimes in canonical outputs.
- Prefer Summary KPIs when available; fall back to derived metrics from daily/trades.
- Always include run_id on combined datasets for cross-run analytics.
- Keep report sections independent and reusable (no hard-coded run_id assumptions).

