# ta_foundation — Report Sections

All HTML report content is composed from reusable “sections”.
Sections are enabled, ordered, and titled by `report.yaml`.

Each section:
- has a stable `id` (used in YAML),
- renders HTML given a context dict:
  - ctx["packages"] : dict[str, AnalysisPackage]
- may generate embedded images (base64 PNG).

---

## Section registry
Sections are registered in:
`src/ta_foundation/reports/html/registry.py`

---

## Current sections

### 1) comparison_overview
**Default title:** Comparison Overview  
**File:** `reports/html/sections/comparison_overview.py`  
**Purpose:** Ranked table across all runs.  
**Data sources:**
- Primary: `pkg.summary.kpis_all` (normalized keys)
- Fallback: `pkg.daily` totals if summary missing  
**Outputs:**
- HTML table with columns:
  - run_id
  - total net profit
  - profit factor
  - max drawdown
  - trades
  - win rate

**Notes:**
- Sorting typically by net profit descending.

---

### 2) equity_curve_comparison
**Default title:** Equity Curve Comparison  
**File:** `reports/html/sections/equity_curve.py`  
**Purpose:** Compare equity curves across runs on one chart.  
**Data sources (preference order):**
1) `pkg.daily["date"]` + `pkg.daily["cum_net_profit"]`
2) `pkg.trades["exit_time"]` + cumulative sum of `pkg.trades["profit"]`

**Output:**
- Embedded PNG (matplotlib) as data URI.

**Time handling:**
- X-axis uses tz-aware America/Denver timestamps.

---

### 3) run_metadata_cards
**Default title:** Run Metadata Cards  
**File:** `reports/html/sections/run_metadata.py`  
**Purpose:** Show run-level metadata per run.  
**Data sources:**
- `pkg.summary.start_dt`, `pkg.summary.end_dt`
- Presence checks:
  - trades table present
  - daily table present
  - summary present

**Output:**
- Card per run with:
  - start, end (America/Denver)
  - duration
  - file presence indicators

---

### 4) run_kpi_cards
**Default title:** Run KPI Cards  
**File:** `reports/html/sections/run_kpis.py`  
**Purpose:** KPI cards per run for quick scanning.  
**Data sources:**
- `pkg.summary.kpis_all` (normalized keys)

**Typical KPIs displayed:**
- total net profit
- profit factor
- max drawdown
- percent profitable
- total number of trades

**Output:**
- Card per run with KPI tiles.

---

## Section authoring guidelines

### Inputs
- Always assume a run may be missing:
  - trades, daily, or summary
- Handle missing data gracefully with placeholders and/or a muted note.
- Do not crash on missing columns; emit a readable message instead.

### Embedded images
- Use matplotlib only.
- Convert figures with `fig_to_base64_png(fig)` from `reports/html/embed.py`.
- Never write image files to disk for HTML reports.

### KPI access
- Use normalized-key dict lookups:
  - `k = pkg.summary.kpis_all`
  - `k.get("total net profit")`, `k.get("profit factor")`, etc.

### Cross-run comparisons
- Always label output by run_id.
- Prefer daily-based series when available for stability.
### daily_leaderboard_cards
**Default title:** Daily Leaders (Session Winners)  
**File:** `reports/html/sections/daily_leaderboard_cards.py`  
**Purpose:** For a selected day, show top winners per session using `_card.png` tiles, plus a bar chart summarizing session PnL.  
**Data sources:** `_Trades.csv` (profit via exit_time date; session inferred via entry_time)  
**Options:**
- `target_date`: "YYYY-MM-DD" (defaults to most recent date found)
- `top_n`: int (default 8)
- `hide_missing_cards`: bool (default true)
- `session_windows`: custom windows (same schema as `run_card_catalog.py`)
- `fallback_session_label`, `fallback_market_label`

---

### weekly_leaderboard_cards
**Default title:** Weekly Leaders  
**File:** `reports/html/sections/weekly_leaderboard_cards.py`  
**Purpose:** Top bots by total PnL for the week.  
**Data sources:** `_Trades.csv`  
**Options:**
- `week_ending`: "YYYY-MM-DD" (defaults to most recent date found; week is Mon→Sun around that date)
- `top_n`: int (default 12)
- `hide_missing_cards`: bool (default true)
- `session_windows`, `fallback_session_label`, `fallback_market_label`

### 5) drawdown_curve
**Default title:** Drawdown Curve Comparison  
**File:** `reports/html/sections/drawdown_curve.py`  
**Purpose:** Compare drawdown curves across runs and quantify recovery time.  
**Data sources (preference order):**
1) `pkg.daily["date"/"Period"]` + `pkg.daily["cum_net_profit"]`
2) `pkg.trades["exit_time"]` + cumulative sum of `pkg.trades["profit"]`

**Output:**
- Embedded PNG: drawdown curves (equity - running peak), with max drawdown trough markers.
- Table per run:
  - max drawdown
  - peak time
  - trough time
  - recovery time (first return to prior peak)
  - recovery duration (trough → recovery)

**Notes:**
- If a run never recovers to its prior peak within the dataset, recovery fields are blank and `recovered = No`.


### Future config options
If a section needs parameters (e.g., top N trades), prefer YAML-driven options:
```yaml
- id: top_trades_table
  title: "Top Trades"
  options:
    top_n: 20


