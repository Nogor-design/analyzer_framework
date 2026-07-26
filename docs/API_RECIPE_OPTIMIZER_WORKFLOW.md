# Recipe Optimizer Full Loop — REST API Documentation

This documents the complete workflow for running the Recipe Optimizer end-to-end using direct REST API calls.

---

## Overview

The Recipe Optimizer processes a multi-stage backtest pipeline:
1. **Session creation** — allocate optimizer workspace
2. **Recipe definition** — configure matrix expansion, stages, selection criteria
3. **Plan generation** — expand base matrix into Stage 1 templates
4. **Template execution** — NinjaTrader processes optimizer runs
5. **Result ingestion** — parse optimizer CSV outputs
6. **Candidate selection** — pick finalists per original bucket
7. **Final template generation** — create backtest XML for deployment
8. **Dashboard reporting** — display bucket coverage and decision workspace

---

## Prerequisites

**Web App Running:**
```
python -m ta_foundation.web.app --port 7734
```

**Environment:**
- Base URL: `http://localhost:7734`
- API base: `http://localhost:7734/api/optimizer`
- Python: `requests` library for HTTP calls
- Timeouts: 10+ seconds (template generation can be slow)

**NinjaTrader (for full execution):**
- Running and listening for RunBatch commands
- Strategy compiled and accessible
- Market data loaded for backtest period

---

## API Endpoints & Workflow

### 1. Health Check
**Purpose:** Verify web app accessibility before proceeding

**Endpoint:** `GET /`  
**Expected Status:** 200

```python
resp = requests.get(f"{BASE_URL}/", timeout=5)
if resp.status_code == 200:
    print("✓ Web app is running")
else:
    print("✗ Web app not responding")
    return False
```

**Failure Mode:** If this fails, web app is not running or network issue exists. Check that port 7734 is not in use by another process.

---

### 2. Create Session
**Purpose:** Allocate a new optimizer session in the web app

**Endpoint:** `POST /api/optimizer/sessions`

**Request Payload:**
```json
{
  "strategy_id": "PantheonMasterBotV01TesterV2",
  "instrument": "NQ",
  "contract": "M26"
}
```

**Response:**
```json
{
  "session_id": "opt_abc123def456",
  "strategy_id": "PantheonMasterBotV01TesterV2",
  "created_at": "2026-05-26T14:30:00Z"
}
```

**Code Example:**
```python
payload = {
    "strategy_id": strategy_id,
    "instrument": "NQ",
    "contract": "M26",
}
resp = requests.post(f"{API_BASE}/sessions", json=payload, timeout=10)
data = resp.json()
session_id = data.get("session_id")
print(f"✓ Session created: {session_id}")
```

**Output to Track:** `session_id` — used in all subsequent calls

---

### 3. Save Recipe Configuration
**Purpose:** Configure the recipe with matrix axes, stages, and selection criteria

**Endpoint:** `PUT /api/optimizer/sessions/{session_id}/recipe`

**Request Payload Structure:**
```json
{
  "recipe_version": 1,
  "mode": "matrix_sequence",
  "recipe_id": "rec_timebucket_test",
  "recipe_name": "Time Bucket Validation Test",
  "strategy_id": "PantheonMasterBotV01TesterV2",
  "target_final_candidates": 4,
  "entries_per_direction": 2,
  "safety_caps": {
    "max_total_combinations": 500000,
    "max_templates_per_stage": 250,
    "max_total_runtime_minutes": 720
  },
  "base_matrix": [
    {
      "param": "StartTimeH",
      "role": "matrix_axis",
      "values": [0, 4, 8, 12, 16, 20],
      "description": "6 different 4-hour trading windows"
    },
    {
      "param": "DurationTimeH",
      "role": "fixed",
      "value": 4,
      "description": "Each window is 4 hours"
    },
    {
      "param": "Reverse",
      "role": "matrix_axis",
      "values": [false, true],
      "description": "Long and Short trading modes"
    }
  ],
  "stages": [
    {
      "stage_id": "stage_1",
      "stage_type": "optimizer",
      "description": "Broad optimization across 12 time buckets",
      "optimize_inside_template": {
        "averageSlow": {"min": 50, "max": 400, "step": 50},
        "averageFast": {"min": 2, "max": 50, "step": 2},
        "MaxStop": {"min": 50, "max": 350, "step": 50},
        "MaxTPRatio": {"min": 0.5, "max": 2.0, "step": 0.5}
      },
      "selection": {
        "group_by": ["StartTimeH", "Reverse"],
        "keep_per_group": 2,
        "rank_by": "portfolio_score",
        "hard_filters": {
          "min_trades": 10,
          "min_profit_factor": 1.1
        }
      }
    },
    {
      "stage_id": "final_backtest",
      "stage_type": "fixed_backtest",
      "from": "stage_1.selected_rows",
      "finalists_per_bucket": 2,
      "description": "Final validation: 2 best candidates per time bucket"
    }
  ]
}
```

**Key Configuration Notes:**

| Field | Purpose | Example Values |
|-------|---------|-----------------|
| `base_matrix` | Axes to expand into templates | StartTimeH: [0,4,8,12,16,20], Reverse: [false, true] |
| `matrix_axis` role | Expansion parameter (creates combinations) | StartTimeH × Reverse = 12 templates |
| `fixed` role | Constant value within all templates | DurationTimeH = 4 |
| `stages[0].selection.group_by` | How to cluster results for selection | ["StartTimeH", "Reverse"] → 12 buckets |
| `stages[0].selection.keep_per_group` | Finalists per cluster | 2 = pick top 2 from each time bucket |
| `stages[1].finalists_per_bucket` | Final template count per bucket | 2 × 12 buckets = 24 final templates |

**Code Example:**
```python
resp = requests.put(
    f"{API_BASE}/sessions/{session_id}/recipe",
    json=RECIPE_CONFIG,
    timeout=10
)
if resp.status_code in [200, 201]:
    print(f"✓ Recipe saved: {RECIPE_CONFIG['recipe_id']}")
else:
    print(f"✗ Failed: {resp.text[:200]}")
    return False
```

**Output to Track:**
- Recipe ID is confirmed saved
- No errors in configuration validation

---

### 4. Generate Recipe Plan
**Purpose:** Expand matrix axes into Stage 1 templates

**Endpoint:** `POST /api/optimizer/sessions/{session_id}/recipe/plan`

**Request:** No body (uses saved recipe)

**Response:**
```json
{
  "recipe_id": "rec_timebucket_test",
  "template_count": 12,
  "stages": [
    {
      "stage_id": "stage_1",
      "stage_type": "optimizer",
      "template_count": 12,
      "templates": [
        {
          "template_id": "stage_1__starttimeh_00__reverse_false",
          "matrix_values": {"StartTimeH": 0, "DurationTimeH": 4, "Reverse": false}
        },
        {
          "template_id": "stage_1__starttimeh_00__reverse_true",
          "matrix_values": {"StartTimeH": 0, "DurationTimeH": 4, "Reverse": true}
        },
        {
          "template_id": "stage_1__starttimeh_20__reverse_true",
          "matrix_values": {"StartTimeH": 20, "DurationTimeH": 4, "Reverse": true}
        }
      ]
    },
    {
      "stage_id": "final_backtest",
      "stage_type": "fixed_backtest",
      "template_count": 0
    }
  ]
}
```

**Validation Checkpoints:**

```python
resp = requests.post(
    f"{API_BASE}/sessions/{session_id}/recipe/plan",
    timeout=10
)
if resp.status_code in [200, 201]:
    data = resp.json()
    print(f"✓ Plan generated: {data.get('template_count')} templates")
    
    # CRITICAL: Verify 12 unique time buckets
    stage_1 = data["stages"][0]
    assert len(stage_1["templates"]) == 12, "Expected 12 templates"
    
    # Verify each has unique StartTimeH
    start_h_values = set()
    for tmpl in stage_1["templates"]:
        start_h = tmpl["matrix_values"]["StartTimeH"]
        start_h_values.add(start_h)
    assert len(start_h_values) == 6, "Expected 6 unique StartTimeH values"
    
    print(f"✓ All 12 templates have unique StartTimeH: {sorted(start_h_values)}")
else:
    print(f"✗ Failed: {resp.text[:200]}")
    return False
```

**Output to Track:**
- Template count = 12 (6 StartTimeH × 2 Reverse)
- Each template has unique `matrix_values`
- `template_id` format: `stage_1__starttimeh_XX__reverse_true/false`

---

### 5. Wait for NinjaTrader Execution (Async Process)

**What Happens Next (automatic in real run):**

1. Web app generates 12 XML template files from Stage 1 plan
2. Each XML includes unique `StartTimeH` value (0, 4, 8, 12, 16, 20)
3. RunBatch command file created for NinjaTrader
4. NinjaTrader Strategy Analyzer runs each optimizer job
5. Each produces CSV with optimizer results (`param_StartTimeH`, `param_Reverse`, metrics, etc.)
6. Results auto-ingest when available

**Endpoint to Check Status:** `GET /api/optimizer/sessions/{session_id}/status`  
(Returns current stage, progress, any errors)

---

### 6. Retrieve Stage Results
**Purpose:** Fetch optimizer results after NT execution completes

**Endpoint:** `GET /api/optimizer/sessions/{session_id}/stages/{stage_id}/results`

**Expected Response for Stage 1:**
```json
{
  "stage_id": "stage_1",
  "template_count": 12,
  "results_available": true,
  "total_rows": 192,
  "selected_rows": 24,
  "selection_status": "complete",
  "rows": [
    {
      "candidate_id": "S1_00_false_1",
      "template_id": "stage_1__starttimeh_00__reverse_false",
      "param_StartTimeH": 0,
      "param_Reverse": "false",
      "param_averageSlow": 100,
      "param_averageFast": 10,
      "profit_factor": 1.89,
      "total_net_profit": 4780,
      "max_drawdown": 1050,
      "total_trades": 38,
      "portfolio_score": 5.42,
      "selection_status": "selected",
      "selection_reason": "top_per_group"
    }
  ]
}
```

**Key Parameter Mapping (CSV → JSON):**
- `param_Start_Time_(HH)` → Ingested as `param_StartTimeH` via canonical name function
- `param_Duration_Time_(HH)` → `param_DurationTimeH`
- `param_Reverse` → `param_Reverse` (direct match)
- Metrics columns: `profit_factor`, `total_net_profit`, `max_drawdown`, `total_trades`

**Code Example:**
```python
resp = requests.get(
    f"{API_BASE}/sessions/{session_id}/stages/stage_1/results",
    timeout=10
)
if resp.status_code == 200:
    data = resp.json()
    print(f"✓ Stage 1 Results: {data['total_rows']} rows, {data['selected_rows']} selected")
    
    # Verify bucket grouping
    by_bucket = {}
    for row in data["rows"]:
        key = (row["param_StartTimeH"], row["param_Reverse"])
        if key not in by_bucket:
            by_bucket[key] = []
        by_bucket[key].append(row)
    
    print(f"✓ Results grouped into {len(by_bucket)} buckets")
    for (start_h, reverse), rows in sorted(by_bucket.items()):
        print(f"  Bucket StartTimeH={start_h}, Reverse={reverse}: {len(rows)} rows")
```

**Output to Track:**
- Total rows processed
- Selected rows (should be 24: 2 per bucket × 12 buckets)
- Parameter values are correctly ingested from CSV

---

### 7. Generate Final Templates
**Purpose:** Create backtest XML files for finalists

**Endpoint:** `POST /api/optimizer/sessions/{session_id}/stages/final_backtest/generate`

**Request Payload:** (optional, uses defaults from recipe)
```json
{
  "from_stage": "stage_1"
}
```

**Response:**
```json
{
  "stage_id": "final_backtest",
  "templates_generated": 24,
  "bucket_report": [
    {
      "bucket_key": "starttimeh_00__reverse_false",
      "bucket_values": {"StartTimeH": 0, "Reverse": false},
      "target_count": 2,
      "selected_count": 2,
      "status": "selected"
    },
    {
      "bucket_key": "starttimeh_20__reverse_true",
      "bucket_values": {"StartTimeH": 20, "Reverse": true},
      "target_count": 2,
      "selected_count": 2,
      "status": "selected"
    }
  ],
  "templates": [
    {
      "template_id": "F_001",
      "bucket_id": "starttimeh_00__reverse_false",
      "parent_candidate_id": "S1_00_false_1",
      "strategy_values": {
        "StartTimeH": 0,
        "DurationTimeH": 4,
        "Reverse": false,
        "averageSlow": 100,
        "averageFast": 10
      },
      "metrics": {
        "profit_factor": 1.89,
        "total_net_profit": 4780,
        "max_drawdown": 1050,
        "total_trades": 38
      },
      "path": "/path/to/final_backtest/F_001.xml"
    }
  ]
}
```

**Critical Validation:**

```python
resp = requests.post(
    f"{API_BASE}/sessions/{session_id}/stages/final_backtest/generate",
    timeout=10
)
if resp.status_code in [200, 201]:
    data = resp.json()
    
    # CRITICAL: Verify bucket preservation
    assert data["templates_generated"] == 24, "Expected 24 final templates"
    assert len(data["bucket_report"]) == 12, "Expected 12 original buckets"
    
    # Verify each bucket has correct StartTimeH in final templates
    for report in data["bucket_report"]:
        bucket_values = report["bucket_values"]
        start_h = bucket_values["StartTimeH"]
        print(f"✓ Bucket {report['bucket_key']}: "
              f"Target={report['target_count']}, Selected={report['selected_count']}, "
              f"Status={report['status']}")
    
    # Spot-check: verify a final template preserves StartTimeH correctly
    for tmpl in data["templates"]:
        start_h = tmpl["strategy_values"]["StartTimeH"]
        print(f"✓ {tmpl['template_id']}: StartTimeH={start_h}")
        break
else:
    print(f"✗ Failed: {resp.text[:200]}")
```

**Output to Track:**
- 24 final templates generated
- 12 unique buckets in bucket_report
- Each final template has correct `StartTimeH` from parent bucket
- All statuses are "selected" (none "missing" or "partial")

---

### 8. Retrieve Decision Dashboard
**Purpose:** Get final candidate recommendations and reporting links

**Endpoint:** `GET /api/optimizer/sessions/{session_id}/decision-dashboard`

**Response:**
```json
{
  "session_id": "opt_abc123def456",
  "recipe_id": "rec_timebucket_test",
  "decision_status": "ready_for_review",
  "final_candidates": [
    {
      "candidate_id": "F_007",
      "bucket_id": "starttimeh_00__reverse_false",
      "profit_factor": 1.89,
      "total_net_profit": 4780,
      "max_drawdown": 1050,
      "total_trades": 38,
      "recommendation": "pass",
      "notes": "Best available regression candidate for London Early"
    }
  ],
  "rejection_summary": {
    "total_rejected": 23,
    "reasons": {
      "failed_min_profit_factor": 12,
      "failed_min_trades": 8,
      "high_drawdown": 3
    }
  },
  "report_links": {
    "all_template_report": "/optimizer/sessions/{session_id}/candidate-report",
    "per_candidate_reports": "/optimizer/sessions/{session_id}/candidates/{run_id}/report",
    "template_files": "/optimizer/sessions/{session_id}/templates/final"
  }
}
```

**Dashboard Summary Extraction:**

```python
resp = requests.get(
    f"{API_BASE}/sessions/{session_id}/decision-dashboard",
    timeout=10
)
if resp.status_code == 200:
    data = resp.json()
    
    print(f"\n{'='*80}")
    print(f"DECISION DASHBOARD - {data['session_id']}")
    print(f"{'='*80}")
    print(f"\nStatus: {data['decision_status']}")
    print(f"\nRecommended Finalists: {len(data['final_candidates'])}")
    
    for cand in data["final_candidates"]:
        print(f"\n  {cand['candidate_id']}: PF={cand['profit_factor']}, "
              f"Net=${cand['total_net_profit']}, "
              f"DD=${cand['max_drawdown']}, "
              f"Trades={cand['total_trades']}")
        print(f"    Bucket: {cand['bucket_id']}")
        print(f"    Recommendation: {cand['recommendation']}")
        if cand.get('notes'):
            print(f"    Notes: {cand['notes']}")
    
    print(f"\n\nRejected: {data['rejection_summary']['total_rejected']} candidates")
    for reason, count in data['rejection_summary']['reasons'].items():
        print(f"  - {reason}: {count}")
    
    print(f"\n\nAvailable Reports & Downloads:")
    print(f"  All-Template Report: {data['report_links']['all_template_report']}")
    print(f"  Per-Candidate: {data['report_links']['per_candidate_reports']}")
    print(f"  Template Files: {data['report_links']['template_files']}")
```

**Output to Track:**
- Passing candidates count
- Their metrics and bucket associations
- Rejection reasons
- Links to reports and templates

---

## Critical Validation Checkpoints

| Stage | Validation | Expected |
|-------|-----------|----------|
| Session Creation | Response contains `session_id` | Non-empty string |
| Recipe Save | Status 200/201 | Recipe stored |
| Plan Generation | `template_count` in response | 12 |
| Stage 1 Plan | Unique `matrix_values` per template | 6 StartTimeH × 2 Reverse |
| NinjaTrader Execution | Results in `stage_1/results` | 12 optimizer runs completed |
| Result Ingestion | `param_StartTimeH` values present | 0, 4, 8, 12, 16, 20 all represented |
| Final Generation | `templates_generated` | 24 (2 × 12 buckets) |
| Bucket Report | Length of `bucket_report` | 12 unique original buckets |
| Dashboard | All passing candidates linked to buckets | Bucket IDs match original Stage 1 |

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| "Cannot connect to localhost:7734" | Web app not running | `python -m ta_foundation.web.app --port 7734` |
| Status 422 on recipe save | Invalid recipe format | Validate JSON schema matches examples |
| Stage 1 stuck on "waiting for results" | NinjaTrader not running or RunBatch not executing | Verify NT is running, check NT logs, verify RunBatch command file created |
| `param_StartTimeH` values missing | CSV parsing failed | Check `Start_Time_(HH)` column exists in optimizer CSV export |
| Final template count < 24 | Some buckets failed hard filters | Check `bucket_report` for missing buckets and `rejection_summary` for reasons |
| Decision dashboard empty | Results not yet available | Polling timeout might be too short; increase iteration count |

---

## Time Bucket Preservation Flow

```
Stage 1 Template Expansion (Plan)
├─ StartTimeH=[0,4,8,12,16,20] × Reverse=[F,T] = 12 templates
├─ Each: stage_1__starttimeh_XX__reverse_YY.xml
└─ Matrix values: {"StartTimeH": XX, "Reverse": YY}

        ↓

NinjaTrader Optimizer Execution
├─ 12 optimizer jobs run (one per template)
├─ Each produces CSV: param_StartTimeH, param_Reverse, metrics, etc.
└─ param_Start_Time_(HH) ingested as param_StartTimeH

        ↓

Result Ingestion & Selection (Stage 1 Results)
├─ Parse 12 optimizer CSVs
├─ Map param_StartTimeH → canonical "starttimeh" via _canonical_recipe_column_name()
├─ Group by ["StartTimeH", "Reverse"] = 12 buckets
├─ Select top 2 per bucket by portfolio_score = 24 selected rows
└─ Each row tagged with initial_bucket_key: "starttimeh_XX__reverse_YY"

        ↓

Final Template Generation (Final Backtest Stage)
├─ Read initial_bucket_key from each selected row
├─ Extract strategy_values: {"StartTimeH": XX, ...}
├─ Generate final XML with StartTimeH=XX
├─ Create bucket_report with 12 entries showing target/selected counts
└─ Output: 24 final templates, each preserving original time bucket

        ↓

Decision Dashboard
├─ Display passing candidates
├─ Each linked to original bucket (e.g., "starttimeh_00__reverse_false")
├─ Show bucket coverage table (12 rows)
└─ Template download links include correctly-valued XML files
```

---

**This documentation serves as the reference for Recipe Optimizer automation, testing, and debugging.**
