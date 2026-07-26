# Contributing to ta_foundation

Welcome! This guide will get you productive in 30 minutes and help you understand where to contribute.

---

## First Day: 30-Minute Onboarding Checklist

### Step 1: Understand the Project (5 min)
Read **DISCOVERY_SUMMARY.md** for a 5-minute high-level overview of what ta_foundation does and which of the 10 capabilities matters most to your contribution.

**Action:** Open `DISCOVERY_SUMMARY.md` in your text editor. Skim the "What is ta_foundation" section and the capability matrix.

### Step 2: Set Up Your Environment (5 min)

**Install the project in editable mode:**
```bash
cd D:\Backup\projects\PythonProject\ta_foundation
pip install -e .
```

**Verify the installation:**
```bash
python -m ta_foundation.cli.main --help
```

You should see the CLI help text. If not, check that you’re in the correct directory and Python is in your PATH.

### Step 3: Read the Architecture (10 min)

Read **CLAUDE.md** (in the project root). Focus on:
- **The 4-layer system** — Parsers, Pipeline, Analysis, Sections. Understand why layers don’t collapse.
- **Core Data Model** — AnalysisPackage, MarketDataStore, OptimizationStore. Know what data flows where.
- **Non-Negotiable Contracts** — Timestamps (tz-aware America/Denver), JSON-safe metadata, no dynamic attributes on AnalysisPackage.

**Key takeaway:** Every file you write must follow these contracts. Breaking them breaks other code downstream.

### Step 4: Pick Your Subsystem (5 min)

Read the relevant subsystem section from **CLAUDE.md** based on what you’ll work on:

- **Adding a report section?** → Read "Report Section Contract" + "Report YAML Config"
- **Adding an analysis metric?** → Read "Analysis Subsystems" + "metadata["derived"] Key Conventions"
- **Adding a parser?** → Read "Parser Protocol"
- **Adding an entry strategy?** → Read "Entry Strategy Discovery" under Analysis Subsystems
- **Anything else?** → Read "Adding New Functionality" table at the end

### Step 5: Run a Quick Example (5 min)

Create a minimal `report.yaml`:
```yaml
report:
  title: "Test Run"
  output_filename: "test.html"

sections:
  - id: run_kpi_cards
```

Place a NinjaTrader backtest CSV export in a folder (e.g., `./test_input`), then run:
```bash
python -m ta_foundation.cli.main \
  --input ./test_input \
  --output ./test_output \
  --report-config report.yaml
```

If it creates `test_output/test.html`, you’re set up correctly. Open the HTML in a browser — you should see KPI cards for your backtest.

---

## Architecture Principles

### The 4-Layer Pipeline

```
1. Parsers      → Ingest CSV/TXT files, produce ParsedArtifact objects
2. Pipeline     → Assemble AnalysisPackage (per run) + MarketDataStore (shared)
3. Analysis     → Compute metrics, attach under metadata["derived"]
4. Sections     → Pure HTML renderers; consume ctx only, no IO
```

**Why this matters:** Each layer has a clear responsibility. Don’t let them collapse:
- Sections must never call analysis code or read files.
- Analysis must never output HTML or call parser code.
- Parsers must never compute metrics or render HTML.

### Data Contracts (Non-Negotiable)

**Timestamps:** All datetimes must be **tz-aware**, localized to **America/Denver**.
```python
import pandas as pd
dt = pd.Timestamp("2024-01-15 10:30:00", tz="America/Denver")
```
Naive timestamps will cause bugs in downstream analysis. Always check your parser output.

**Metadata:** `pkg.metadata` must be **JSON-safe**. Never store DataFrames, callables, or registry objects.
```python
# ✅ GOOD
pkg.metadata["derived"]["my_metric"] = {"value": 42, "note": "quarterly"}

# ❌ BAD
pkg.metadata["derived"]["my_data"] = pd.DataFrame(...)  # Not JSON-safe
pkg.metadata["my_func"] = lambda x: x * 2  # Not JSON-safe
```

**AnalysisPackage attributes:** Never add dynamic top-level attributes. Use `metadata["derived"]`:
```python
# ✅ GOOD
pkg.metadata["derived"]["my_analysis"] = {...}

# ❌ BAD
pkg.my_new_field = "value"  # Will break serialization
```

### Report Section Contract

Every report section is a pure function:
```python
def render_my_section(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}       # Section-local options from YAML
    all_options = ctx.get("all_options") or {} # Full YAML (top-level blocks too)
    market = ctx.get("market")
    
    # Compute HTML
    html = f"<h2>My Section</h2>"
    
    # Embed images as base64
    from ta_foundation.reports.html.embed import fig_to_base64_png
    img_b64 = fig_to_base64_png(my_matplotlib_fig)
    html += f’<img src="data:image/png;base64,{img_b64}" />’
    
    return html
```

**Key rules:**
- No file IO, no YAML parsing, no database calls.
- Return pure HTML string (all assets base64-embedded).
- Register in `src/ta_foundation/reports/html/registry.py` → `SECTION_REGISTRY`.
- Enable via YAML config under `sections:`.

---

## Common Tasks & Code Patterns

### Task 1: Add a New Report Section

**Files to touch:**
- `src/ta_foundation/reports/html/sections/<category>_<name>.py` (create)
- `src/ta_foundation/reports/html/registry.py` (register)
- `report.yaml` (enable in config)

**Example: Daily trade summary section**

**File: `src/ta_foundation/reports/html/sections/core_daily_trade_summary.py`**
```python
from typing import Any

def render_daily_trade_summary(ctx: dict[str, Any]) -> str:
    """Daily trade count and P&L summary."""
    packages = ctx.get("packages", {}) or {}
    
    html = "<h2>Daily Trade Summary</h2><table><tr><th>Date</th><th>Trades</th><th>P&L</th></tr>"
    
    for pkg in packages.values():
        daily = pkg.daily
        for idx, row in daily.iterrows():
            date = row["date"].strftime("%Y-%m-%d")
            trades = int(row["trade_count"])
            pnl = f"${row[‘pnl’]:.2f}"
            html += f"<tr><td>{date}</td><td>{trades}</td><td>{pnl}</td></tr>"
    
    html += "</table>"
    return html
```

**File: `src/ta_foundation/reports/html/registry.py` (add to SECTION_REGISTRY)**
```python
SECTION_REGISTRY = {
    "daily_trade_summary": {
        "label": "Daily Trade Summary",
        "render": render_daily_trade_summary,
        "category": "Daily & Session",
    },
    # ... rest of registry
}
```

**File: `report.yaml` (add to sections list)**
```yaml
sections:
  - id: daily_trade_summary
```

**Test it:** Run the CLI with this section enabled. It should appear in the output HTML.

---

### Task 2: Add a New Analysis Module

**Files to touch:**
- `src/ta_foundation/analysis/<subsystem>/<name>.py` (create)
- `src/ta_foundation/cli/main.py` (call it in pipeline, store results under `metadata["derived"]`)

**Example: Custom P&L attribution analysis**

**File: `src/ta_foundation/analysis/my_analysis.py`**
```python
import pandas as pd
from typing import dict, Any

def analyze_pnl_attribution(pkg) -> dict[str, Any]:
    """
    Decompose P&L into win/loss/breakeven trades.
    Results must be JSON-safe.
    """
    trades = pkg.trades
    
    wins = (trades["pnl"] > 0).sum()
    losses = (trades["pnl"] < 0).sum()
    breakeven = (trades["pnl"] == 0).sum()
    total_pnl = trades["pnl"].sum()
    
    return {
        "win_count": int(wins),
        "loss_count": int(losses),
        "breakeven_count": int(breakeven),
        "total_pnl": float(total_pnl),
        "win_rate": float(wins / len(trades)) if len(trades) > 0 else 0,
    }
```

**File: `src/ta_foundation/cli/main.py` (call it in pipeline)**
```python
from ta_foundation.analysis.my_analysis import analyze_pnl_attribution

# In the pipeline loop (after AnalysisPackage is created):
for run_id, pkg in packages.items():
    pkg.metadata["derived"]["pnl_attribution"] = analyze_pnl_attribution(pkg)
```

**Then create a section that renders it:**
```python
def render_pnl_attribution(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    
    html = "<h2>P&L Attribution</h2><div>"
    for pkg in packages.values():
        attr = pkg.metadata.get("derived", {}).get("pnl_attribution", {})
        html += f"<p>Win Rate: {attr.get(‘win_rate’, 0):.1%}</p>"
    
    html += "</div>"
    return html
```

---

### Task 3: Add a New Parser

**Files to touch:**
- `src/ta_foundation/parsers/ninjatrader/<format>_csv.py` (create)
- `src/ta_foundation/core/registry.py` (register)
- `src/ta_foundation/cli/main.py` (add to ParserRegistry)

**Example: Custom equity curve export**

**File: `src/ta_foundation/parsers/ninjatrader/equity_curve_csv.py`**
```python
from pathlib import Path
from ta_foundation.parsers.base import ParsedArtifact

class EquityCurveParser:
    kind = "equity_curve"
    
    def can_parse(self, path: Path, header: str) -> bool:
        return "equity" in path.stem.lower() and header.startswith("date,equity")
    
    def parse(self, path: Path, run_id: str | None) -> ParsedArtifact:
        import pandas as pd
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize("UTC").dt.tz_convert("America/Denver")
        
        return ParsedArtifact(
            kind=self.kind,
            source_path=path,
            run_id=run_id,
            data={"equity_curve": df},
            warnings=[],
        )
```

**File: `src/ta_foundation/cli/main.py` (register)**
```python
from ta_foundation.parsers.ninjatrader.equity_curve_csv import EquityCurveParser

parser_registry = ParserRegistry([
    TradesParser(),
    SummaryParser(),
    EquityCurveParser(),  # Add here
    # ... rest
])
```

---

### Task 4: Run Tests

**Run a single test file:**
```bash
python -m pytest src/ta_foundation/tests/analysis/ma_structure/test_orchestrator.py -v
```

**Run all tests:**
```bash
python -m pytest src/ta_foundation/tests/ -v
```

**Write a test for your new code:**
```python
# File: src/ta_foundation/tests/test_my_feature.py
import pytest
from ta_foundation.analysis.my_analysis import analyze_pnl_attribution
from ta_foundation.core.model import AnalysisPackage
import pandas as pd

def test_pnl_attribution():
    # Create a minimal AnalysisPackage
    trades = pd.DataFrame({
        "pnl": [100, -50, 0, 75, -25],
    })
    pkg = AnalysisPackage(
        trades=trades,
        daily=pd.DataFrame(),
        summary=None,
        settings=None,
        assets={},
        metadata={},
        warnings=[],
    )
    
    result = analyze_pnl_attribution(pkg)
    
    assert result["win_count"] == 2
    assert result["loss_count"] == 2
    assert result["breakeven_count"] == 1
```

**Run your test:**
```bash
python -m pytest src/ta_foundation/tests/test_my_feature.py::test_pnl_attribution -v
```

---

### Task 5: Update the AI Index

When you add new modules, regenerate the index:
```bash
python scripts/build_ai_index.py
```

This updates `docs/AI_REPO_INDEX.md`, which is the source of truth for the codebase structure. Keep it current.

---

## Code Style Patterns

### Type Hints
Always use type hints on function signatures:
```python
from typing import Any, dict, list

def process_trades(trades: pd.DataFrame, instrument: str) -> dict[str, Any]:
    results: list[float] = []
    for idx, row in trades.iterrows():
        results.append(float(row["pnl"]))
    return {"values": results}
```

### Timezone-Aware Timestamps
All timestamps must be localized to America/Denver:
```python
import pandas as pd

# ✅ Correct
dt = pd.Timestamp("2024-01-15 10:30:00", tz="America/Denver")

# ✅ Also correct (convert from UTC)
dt_utc = pd.Timestamp("2024-01-15 17:30:00", tz="UTC")
dt_denver = dt_utc.tz_convert("America/Denver")

# ❌ Wrong
dt_naive = pd.Timestamp("2024-01-15 10:30:00")  # No timezone
```

### JSON-Safe Metadata
Store only JSON-serializable types in `metadata["derived"]`:
```python
# ✅ Good
pkg.metadata["derived"]["results"] = {
    "win_count": 42,
    "pnl": 1000.50,
    "dates": ["2024-01-15", "2024-01-16"],
    "stats": {"mean": 100.0, "std": 50.0},
}

# ❌ Bad
pkg.metadata["derived"]["results"] = {
    "trades_df": pd.DataFrame(...),  # Not JSON-safe
    "func": lambda x: x * 2,  # Not JSON-safe
}
```

### Pure Report Sections
Report sections must be pure functions (no side effects, no IO):
```python
# ✅ Good
def render_kpis(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {})
    html = "<table>"
    for pkg in packages.values():
        html += f"<tr><td>{pkg.summary.get(‘total_net_profit’)}</td></tr>"
    html += "</table>"
    return html

# ❌ Bad
def render_kpis(ctx: dict[str, Any]) -> str:
    # Reading from disk
    with open("cache.pkl", "rb") as f:
        data = pickle.load(f)  # No IO in sections!
    
    # Modifying global state
    global LAST_RENDER
    LAST_RENDER = time.time()  # No side effects!
    
    return "<div>KPIs</div>"
```

---

## File Organization

```
ta_foundation/
├── CLAUDE.md                    # Architecture & contracts (READ THIS)
├── CONTRIBUTING.md              # This file
├── DISCOVERY_SUMMARY.md         # 5-min overview
├── COMPLETE_CAPABILITIES_MATRIX.md  # Capability reference
├── pyproject.toml              # Project metadata
├── src/ta_foundation/
│   ├── cli/
│   │   └── main.py             # Entry point & orchestrator
│   ├── core/
│   │   ├── model.py            # AnalysisPackage, SummaryBlock
│   │   ├── pipeline.py         # Ingest → derivation pipeline
│   │   └── registry.py         # ParserRegistry
│   ├── parsers/
│   │   └── ninjatrader/        # 7 NinjaTrader parsers
│   ├── analysis/
│   │   ├── ma_structure/       # MA anchor analysis
│   │   ├── pattern_engine/     # Pattern sweep + Monte Carlo
│   │   ├── entry_strategies/   # 8 entry strategy families
│   │   ├── strategy_discovery/ # Strategy synthesis
│   │   └── regime_recommender/ # Regime classification
│   └── reports/html/
│       ├── sections/           # 120+ report renderers
│       └── registry.py         # SECTION_REGISTRY
└── tests/
    └── [mirror of src/ structure]
```

---

## Testing Checklist

Before submitting a pull request:

- [ ] Run all tests: `python -m pytest src/ta_foundation/tests/ -v`
- [ ] No test failures or errors
- [ ] New code has unit tests (if applicable)
- [ ] New report sections render without errors
- [ ] New analysis modules produce JSON-safe metadata
- [ ] All timestamps are tz-aware (America/Denver)
- [ ] No dynamic attributes added to AnalysisPackage
- [ ] Code follows the style patterns above
- [ ] CLAUDE.md contracts are respected

---

## Troubleshooting: First-Run Issues

### Issue: "No module named ‘ta_foundation’"
**Cause:** Project not installed in editable mode.
**Fix:** Run `pip install -e .` from the project root.

### Issue: "tz-aware timestamp" error in analysis
**Cause:** Parsing naive timestamps instead of tz-aware.
**Fix:** Ensure your parser uses `.tz_localize("UTC").tz_convert("America/Denver")` for all timestamps.

### Issue: "JSON serialization error" when saving metadata
**Cause:** Storing non-JSON-safe objects (DataFrames, functions) in `metadata["derived"]`.
**Fix:** Convert DataFrames to dicts/lists and remove callable objects before storing.

### Issue: Report section renders as blank
**Cause:** Section not registered in `SECTION_REGISTRY` or context is missing expected data.
**Fix:** Check registry.py; add debug print statements to see what ctx contains.

### Issue: Tests fail with "permission denied" on Windows
**Cause:** File locking on Windows during test cleanup.
**Fix:** Use `pytest --tb=short` for clearer errors; close any IDE windows holding files.

---

## Where to Ask Questions

- **Architecture questions?** → Read CLAUDE.md, check the "4-layer system" section
- **What can I build?** → Read COMPLETE_CAPABILITIES_MATRIX.md
- **How do I do X?** → Check "Common Tasks" section above
- **Error with timestamps?** → See "Timezone-Aware Timestamps" style pattern
- **Need to add a feature?** → See "Adding New Functionality" table in CLAUDE.md

---

## Key Documents Reading Order

1. **DISCOVERY_SUMMARY.md** (5 min) — What is this project?
2. **COMPLETE_CAPABILITIES_MATRIX.md** (10 min) — What can I build?
3. **CLAUDE.md** (20 min) — How does it work? What are the contracts?
4. **This file: CONTRIBUTING.md** (15 min) — How do I contribute?
5. **Subsystem README** (depends) — Deep dive into your area (e.g., pattern_engine/, entry_strategies/)
6. **Code examples** — Look at existing sections/modules for patterns

---

## Next Steps: After Your First Contribution

Once you’ve made your first contribution:

1. **Run the full test suite** to ensure nothing broke
2. **Build a report** using your new feature via `report.yaml`
3. **Open the HTML output** and verify it renders correctly
4. **Regenerate the AI index:** `python scripts/build_ai_index.py`
5. **Update CONTRIBUTING.md** if you found a gap in onboarding

---

## Sign-Off

**Purpose:** Unblock contributors with a clear, fast path to productivity.  
**Target audience:** New contributors (developers, data scientists, strategists).  
**Estimated time to first success:** 30 minutes.  
**Success criteria:** New contributor completes a small task (e.g., add a report section) without external help.

---

Last updated: May 24, 2026  
Maintainer: Claude (Documentation team)
