# AGENTS.md — Agent guidance for working in ta_foundation

Planned changes: expand actionable onboarding, examples, troubleshooting, and concrete code snippets so an AI coding agent can be productive immediately.

Start checklist (do these first)
- Read `docs/AI_REPO_INDEX.md` (run `python scripts/build_ai_index.py` if missing).
- Install editable package and dependencies:

```powershell
pip install -e .
```

- Run the core unit tests smoke-check:

```powershell
python -m pytest src/ta_foundation/tests/ -q
```

- Run a small ingest with sample inputs to exercise parsers and pipeline:

```powershell
python -m ta_foundation.cli.main --input "C:/path/to/ninjatrader/exports" --output ./outputs --report-config report.yaml --no-tick-data
```

Quick architecture (4-layer view and data flow)
- Parsers (src/ta_foundation/parsers/) — detect and normalize files into ParsedArtifact objects. Each parser implements the Parser protocol (see `parsers/base.py`) and returns a ParsedArtifact. Parsers may route to the `MarketDataStore` (for `run_id=None`) or into an `AnalysisPackage` (for run-scoped artifacts).
- Pipeline (src/ta_foundation/core/pipeline.py) — orchestrates ingest: it builds `AnalysisPackage` objects keyed by `run_id` and a shared `MarketDataStore`. The CLI (`cli/main.py`) drives this pipeline.
- Analysis (src/ta_foundation/analysis/) — domain analytics: anchor engine, pattern engine, entry strategy discovery, strategy discovery, regime recommender. Analyses must write JSON-safe outputs into `pkg.metadata["derived"]` (see key conventions below).
- Sections (src/ta_foundation/reports/html/sections/) — pure HTML renderers that consume the prepared context (`ctx`) and must perform no IO or heavy analytics. Use `reports/html/embed.py` to embed images.

Why this structure: separation ensures parsers are cheap and deterministic, analysis stages can be re-run independently (their outputs are recorded in `pkg.metadata`), and rendering is a pure function of data (makes HTML reports reproducible and embeddable).

Non-negotiable contracts (concrete examples)
- Timestamps: always timezone-aware in America/Denver. Example:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

dt = datetime(2026, 6, 14, 12, 0, tzinfo=ZoneInfo("America/Denver"))
```

- Derived data location: write analysis outputs here and ensure JSON-safe types:

```python
pkg.metadata.setdefault("derived", {})
pkg.metadata["derived"]["my_metric"] = {
	"window": "2026-01-01/2026-06-01",
	"total_net_profit": 1234.5,  # numbers, strings, lists, dicts only
}
```

- Market data: artifacts intended for reuse across runs must be created with `run_id=None` and stored in `MarketDataStore` (see `marketdata/store.py`). Do not copy minute/tick data into `pkg.trades` or `pkg.metadata` — reference it from the store.

Adding a new parser (step-by-step)
1. Create `src/ta_foundation/parsers/<vendor>/my_parser.py` implementing the Parser protocol:

```python
from pathlib import Path
from typing import Optional
from ta_foundation.parsers.base import ParsedArtifact, Parser

class MyParser(Parser):
	kind = "my_vendor"

	def can_parse(self, path: Path, header: str) -> bool:
		return path.suffix == ".csv" and "MyVendor" in header

	def parse(self, path: Path, run_id: Optional[str]) -> ParsedArtifact:
		# produce ParsedArtifact and return
		...
```

2. Register your parser in `src/ta_foundation/cli/main.py`'s ParserRegistry (look for existing registrations and follow pattern).
3. If the parser should route to a store other than AnalysisPackage (e.g., `OptimizationStore` or MarketDataStore), update `core/pipeline.py` to intercept and route before creating packages.

Adding an analysis metric
- Create a module under `src/ta_foundation/analysis/<feature>/` and add a well-named entry function (e.g., `run_my_metric(pkg, market, options)`). Ensure it is invoked by the pipeline/orchestrator (follow the existing pattern in `analysis/ma_structure/orchestrator.py`).
- Always attach results under `pkg.metadata["derived"]` and only include JSON-serializable content.

Example:

```python
def run_my_metric(pkg, market, options):
	result = {"kpi": 1.23}
	pkg.metadata.setdefault("derived", {})
	pkg.metadata["derived"]["my_metric"] = result
```

Report section contract & example
- Sections are pure functions with this signature:

```python
def render_my_section(ctx: dict) -> str:
	# ctx contains packages, market, options, all_options
	return "<div>simple HTML</div>"
```

- If embedding matplotlib figures, use `fig_to_base64_png`:

```python
from ta_foundation.reports.html.embed import fig_to_base64_png

def render_plot_section(ctx):
	fig = build_figure_from_ctx(ctx)
	uri = fig_to_base64_png(fig)
	return f"<img src=\"{uri}\" />"
```

- Register the section in `src/ta_foundation/reports/html/registry.py` (follow the `SECTION_REGISTRY` pattern) and enable via `report.yaml` sections[].

Pattern engine & artifacts
- Templates live in `src/ta_foundation/analysis/pattern_engine/templates/` and are registered in `builtins.py` via `default_template_registry()`.
- Sweeps write parquet artifacts to `.ta_artifacts/pattern_engine/<run_id>/` and only references are stored in `pkg.metadata["derived"]["pattern_engine"]["artifacts"]`.

NinjaTrader (NT) integration notes — critical
- Canonical market data exists on the host at `D:\MarketData`. Use `--market-data` to point the CLI or the web UI loader; do not prompt users for the location.
- For automated NT actions (start/login), use the sanctioned CLI helper:

```powershell
Start-Process "C:\Program Files\NinjaTrader 8\bin\NinjaTrader.exe"
python -m ta_foundation.nt_strategy_loop.cli ensure-nt-ready --username eirwin --password-file "C:\Users\Owner\Downloads\P.txt"
```

- NEVER mix managed (`SetStopLoss`/`SetProfitTarget`) and explicit `Exit*StopMarket` orders on the same signal — see `CLAUDE.md` and the NT doc mirror in `NinjatraderDocScrapper`.

Testing and debugging tips
- Run a single test file (example):

```powershell
python -m pytest src/ta_foundation/tests/analysis/ma_structure/test_orchestrator.py -q
```

- Common failure modes and checks:
  - Naive datetimes in metadata: search for `datetime(` without tzinfo or localized calls.
  - Non-JSON-serializable data in `pkg.metadata`: DataFrames, numpy types, callables. Use `.tolist()` or `float()`/`int()` conversions.
  - Parser not picked up: ensure it's registered in `cli/main.py` ParserRegistry and `can_parse()` returns True for headers.
  - Missing `.ta_artifacts` references: sweep must write parquet files to `.ta_artifacts/pattern_engine/<run_id>/` and `pkg.metadata` must reference relative paths.

Inspect pipeline outputs
- The CLI writes these artifacts to the output directory:
  - `<output_filename>.html` — self-contained report
  - `manifest.json` — parsed file list, hashes, warnings
  - `unparsed_files.txt` — files not matched by any parser
  - `.ta_artifacts/` — parquet artifacts

Where to read before proposing changes
- `docs/AI_REPO_INDEX.md` — canonical index of repo capabilities (generated by `scripts/build_ai_index.py`).
- `CLAUDE.md` — NT/C# and NT integration runbook snippets (must read before touching `.cs`).
- `docs/CAPABILITY_CATALOG.md` and `docs/reference/EXTERNAL_PROJECTS_MAP.md` — this repo is part of a larger ecosystem; verify capability ownership before adding new features.

Quick file map (fast-check targets)
- `src/ta_foundation/cli/main.py` — entrypoint, ParserRegistry, pipeline flags
- `src/ta_foundation/core/pipeline.py` — ingest orchestration (where routing decisions happen)
- `src/ta_foundation/core/model.py` — `AnalysisPackage`, `SummaryBlock` (where `metadata` shape is defined)
- `src/ta_foundation/marketdata/store.py` — shared market data rules
- `src/ta_foundation/analysis/pattern_engine/` — sweep engine, `builtins.py` registration
- `src/ta_foundation/reports/html/registry.py` and `reports/html/sections/` — section definitions and registration

Don'ts (explicit)
- Do NOT edit NinjaTrader `.cs` files without consulting `NinjatraderDocScrapper` and runbooks.
- Do NOT add CLI flags for rendering/report behavior — use `report.yaml`.
- Do NOT serialize pandas DataFrames or callables into `pkg.metadata`.

If you want more
- I can add a short checklist for adding a new entry strategy family (files to create and tests to run).
- I can open `cli/main.py` and generate a parser registration patch example if you want.

— End of expanded agent guide

