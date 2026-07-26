# Web API Reference — ta_foundation

**Flask REST API for the Web Workbench**  
**Base URL:** `http://localhost:7734` (default port)  
**Last Updated:** May 24, 2026

---

## Table of Contents

1. [Authentication](#authentication)
2. [Core Pages](#core-pages)
3. [Schema & Metadata](#schema--metadata)
4. [Discovery Management](#discovery-management)
5. [Discovery Sessions](#discovery-sessions)
6. [Reporting & Analysis](#reporting--analysis)
7. [Jobs & Status](#jobs--status)
8. [Error Handling](#error-handling)
9. [Examples](#examples)

---

## Authentication

**Current Status:** No authentication required for local development.

**For production:** Implement your own authentication layer (API keys, OAuth, JWT, etc.)

---

## Core Pages

### GET / — Main Workbench
Returns the HTML workbench UI (5 tabs: Backtest Reports, Prediction, Strategy Templates, Strategy Discovery, System Map)

```
GET /
Accept: text/html
```

**Response:** 200 OK (HTML page with embedded CSS/JS)

---

### GET /discovery — Discovery UI
Returns the interactive discovery page (separate from main workbench tabs)

```
GET /discovery
Accept: text/html
```

**Query Parameters:**
- `session_id` (optional) — Resume existing session

**Response:** 200 OK (HTML page with discovery funnel stepper)

---

### GET /discovery/sessions/<session_id>/resume — Resume Session
Resume a discovery session by ID (typically via cookie)

```
GET /discovery/sessions/abc123/resume
```

**Response:** 
- 200 OK → Redirects to /discovery with session_id cookie
- 404 Not Found → Session doesn't exist

---

## Schema & Metadata

### GET /api/schema — Template/Strategy JSON Schema
Returns the JSON schema for strategy specs and templates

```
GET /api/schema
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "strategy_spec": {
    "type": "object",
    "properties": {
      "strategy_name": {"type": "string"},
      "family": {"type": "string"},
      "intent": {"type": "string"},
      "parameters": {"type": "object"},
      "risk_note": {"type": "string"}
    },
    "required": ["strategy_name", "family"]
  },
  "template": {
    "type": "object",
    "properties": { ... }
  }
}
```

---

### GET /api/capabilities — List All Capabilities
Returns a list of all runnable workflows and report sections

```
GET /api/capabilities
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "backtest_reports": [
    {"id": "comparison_overview", "name": "Comparison Overview", "description": "..."},
    {"id": "run_kpi_cards", "name": "KPI Cards", "description": "..."},
    ...
  ],
  "discovery": [
    {"id": "01_quick_scan", "name": "Quick Scan", "description": "..."},
    ...
  ],
  "prediction": [
    {"id": "daily_prediction", "name": "Daily Prediction", "description": "..."},
    ...
  ],
  "workflows": [...]
}
```

---

### GET /api/discovery/stages — All Discovery Stages
Returns all 6 discovery stages (quick scan, candle, levels, NY open, ORB, validate)

```
GET /api/discovery/stages
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "stages": [
    {
      "id": "01_quick_scan",
      "name": "Quick Scan",
      "description": "Which signal families have edge?",
      "runtime_estimate": "3-5 min",
      "combos": 250,
      "default_yaml": { ... }
    },
    {
      "id": "02_candle_patterns",
      "name": "Candle Patterns",
      "description": "Which candle patterns work best?",
      ...
    },
    ...
  ]
}
```

---

### GET /api/discovery/stages/<stage_id> — Single Stage
Returns details for a specific discovery stage

```
GET /api/discovery/stages/02_candle_patterns
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "id": "02_candle_patterns",
  "name": "Candle Patterns",
  "description": "Which candle patterns win? Best TP/SL?",
  "runtime_estimate": "8-12 min",
  "combos": 600,
  "default_yaml": {
    "strategy_discovery": {
      "enabled": true,
      "entry_strategies": {
        "candle": {
          "enabled": true,
          "patterns": ["large_body", "pin_bar_bullish", ...],
          "tp_ticks": [10, 15, 20, 25],
          "sl_ticks": [8, 12, 16]
        }
      }
    }
  }
}
```

---

### POST /api/discovery/stages/<stage_id>/preview — Preview Stage
Build and validate a stage YAML before running

```
POST /api/discovery/stages/02_candle_patterns/preview
Content-Type: application/json

{
  "strategy_discovery": {
    "enabled": true,
    "entry_strategies": {
      "candle": {
        "enabled": true,
        "patterns": ["large_body", "pin_bar_bullish"],
        "tp_ticks": [10, 20],
        "sl_ticks": [8]
      }
    }
  }
}
```

**Response:** 200 OK
```json
{
  "valid": true,
  "estimated_combos": 12,
  "estimated_runtime": "2-3 min",
  "warnings": [],
  "yaml_preview": "strategy_discovery:\n  enabled: true\n  ..."
}
```

Or if invalid:
```json
{
  "valid": false,
  "errors": [
    "Invalid pattern name: 'unknown_pattern'",
    "tp_ticks must be a list of integers"
  ]
}
```

---

### GET /api/discovery/glossary — Discovery Glossary
Returns definitions of discovery terms (IS/OOS, PF, MAE/MFE, Sharpe, etc.)

```
GET /api/discovery/glossary
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "terms": {
    "IS/OOS": {
      "full_name": "In-Sample / Out-of-Sample",
      "definition": "IS is the first 70% of bars (training), OOS is the last 30% (test)",
      "why_it_matters": "Measures overfitting — good signals degrade < 15% IS→OOS"
    },
    "PF": {
      "full_name": "Profit Factor",
      "definition": "Gross profit / gross loss",
      "interpretation": "PF ≥ 1.2 is acceptable, ≥ 1.5 is excellent"
    },
    "MAE": {
      "full_name": "Maximum Adverse Excursion",
      "definition": "Worst intrabar move against the entry",
      "why_it_matters": "Helps size stops and assess trade timing"
    },
    ...
  }
}
```

---

### GET /api/discovery/instruments — Instrument Registry
Returns available trading instruments and their metadata

```
GET /api/discovery/instruments
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "instruments": [
    {
      "symbol": "NQ",
      "name": "E-mini Nasdaq 100",
      "tick_size": 0.25,
      "tick_value": 20,
      "point_value": 100,
      "rth_start": "09:30",
      "rth_end": "16:00",
      "contracts": ["H26", "M26", "U26", "Z26"]
    },
    {
      "symbol": "ES",
      "name": "E-mini S&P 500",
      "tick_size": 0.25,
      "tick_value": 50,
      ...
    },
    ...
  ]
}
```

---

## Discovery Management

### GET /api/discovery/sessions — List All Sessions
Returns all discovery sessions (user's past and current)

```
GET /api/discovery/sessions
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "sessions": [
    {
      "session_id": "abc123",
      "created_at": "2026-05-24T10:30:00Z",
      "label": "NQ Edge Discovery",
      "current_stage": "02_candle_patterns",
      "instrument": "NQ H26",
      "last_accessed": "2026-05-24T14:15:00Z"
    },
    {
      "session_id": "def456",
      "created_at": "2026-05-20T09:00:00Z",
      "label": "ES Breakout Patterns",
      "current_stage": "06_validate",
      ...
    }
  ]
}
```

---

### POST /api/discovery/sessions — Create New Session
Creates a new discovery session

```
POST /api/discovery/sessions
Content-Type: application/json

{
  "label": "NQ Edge Discovery",
  "instrument": "NQ H26"
}
```

**Response:** 201 Created
```json
{
  "session_id": "xyz789",
  "label": "NQ Edge Discovery",
  "instrument": "NQ H26",
  "current_stage": "01_quick_scan",
  "created_at": "2026-05-24T14:30:00Z"
}
```

Set-Cookie: `session_id=xyz789`

---

### GET /api/discovery/sessions/<session_id> — Get Session State
Returns the current state of a session

```
GET /api/discovery/sessions/abc123
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "session_id": "abc123",
  "label": "NQ Edge Discovery",
  "instrument": "NQ H26",
  "current_stage": "02_candle_patterns",
  "context": {
    "previous_results": {
      "01_quick_scan": {
        "top_families": ["candle", "lcr"],
        "pf_threshold": 1.2
      }
    }
  },
  "runs": [
    {
      "run_id": "20260524_001",
      "stage": "01_quick_scan",
      "status": "completed",
      "output_html": "/outputs/discovery_01_quick_scan.html"
    }
  ]
}
```

---

### POST /api/discovery/sessions/<session_id> — Update Session
Updates session state (label, instrument, context)

```
POST /api/discovery/sessions/abc123
Content-Type: application/json

{
  "label": "NQ Edge Discovery (Updated)",
  "context": {
    "notes": "Top family is candle patterns, testing TP sweep next"
  }
}
```

**Response:** 200 OK
```json
{
  "session_id": "abc123",
  "label": "NQ Edge Discovery (Updated)",
  "context": { ... }
}
```

---

### DELETE /api/discovery/sessions/<session_id> — Delete Session
Permanently deletes a session

```
DELETE /api/discovery/sessions/abc123
```

**Response:** 204 No Content

---

### POST /api/discovery/sessions/<session_id>/runs — Start Discovery Run
Starts a new discovery run for the session at the current stage

```
POST /api/discovery/sessions/abc123/runs
Content-Type: application/json

{
  "yaml_override": {
    "strategy_discovery": {
      "enabled": true,
      "entry_strategies": {
        "candle": {
          "enabled": true,
          "patterns": ["large_body"],
          "tp_ticks": [10, 20]
        }
      }
    }
  }
}
```

**Response:** 202 Accepted
```json
{
  "run_id": "20260524_002",
  "session_id": "abc123",
  "stage": "02_candle_patterns",
  "status": "running",
  "started_at": "2026-05-24T14:35:00Z",
  "job_url": "/api/jobs/j_abc123_002"
}
```

---

### POST /api/discovery/sessions/<session_id>/promote — Promote to Live
Promotes discovered signals from session to live trading (writes candidates to ledger)

```
POST /api/discovery/sessions/abc123/promote
Content-Type: application/json

{
  "signals_to_promote": [
    {
      "family": "candle",
      "pattern": "large_body",
      "tp_ticks": 20,
      "sl_ticks": 8,
      "notes": "IS/OOS degradation < 10%, PF 1.3"
    }
  ]
}
```

**Response:** 200 OK
```json
{
  "promoted": 1,
  "candidate_ids": ["h_candle_large_body_20_8"],
  "next_steps": "Candidates added to hypothesis pool. Run operator-pass to execute."
}
```

---

## Reporting & Analysis

### GET /api/reports/presets — Report Presets
Returns available report configuration presets

```
GET /api/reports/presets
Content-Type: application/json
```

**Response:** 200 OK
```json
{
  "presets": [
    {
      "id": "single_run",
      "name": "Single Run Report",
      "description": "Overview, KPIs, equity curve, daily scoreboard",
      "yaml_template": { ... }
    },
    {
      "id": "comparison",
      "name": "Multi-Run Comparison",
      "description": "Compare 2-5 strategies side by side",
      ...
    },
    {
      "id": "discovery",
      "name": "Discovery Report",
      "description": "Entry discovery, ranking, validation",
      ...
    }
  ]
}
```

---

### POST /api/reports/build — Build Report
Starts a report generation job

```
POST /api/reports/build
Content-Type: application/json

{
  "input_folder": "C:/backtest_exports",
  "output_folder": "./reports",
  "report_config": {
    "report": {
      "title": "NQ Strategy Comparison",
      "output_filename": "nq_comparison.html"
    },
    "sections": [
      {"id": "comparison_overview"},
      {"id": "run_kpi_cards"},
      {"id": "daily_scoreboard"}
    ]
  },
  "include_cards_png": true
}
```

**Response:** 202 Accepted
```json
{
  "job_id": "j_report_001",
  "status": "queued",
  "estimated_duration": "5 min"
}
```

---

### GET /api/jobs/<job_id> — Job Status
Returns the status of a running job (report, discovery, prediction, etc.)

```
GET /api/jobs/j_report_001
```

**Response:** 200 OK
```json
{
  "job_id": "j_report_001",
  "type": "report_build",
  "status": "running",
  "progress": {
    "percent_complete": 45,
    "current_step": "Running strategy discovery analysis",
    "elapsed_seconds": 180,
    "estimated_remaining_seconds": 200
  },
  "output": null
}
```

When complete:
```json
{
  "job_id": "j_report_001",
  "type": "report_build",
  "status": "completed",
  "progress": {
    "percent_complete": 100,
    "elapsed_seconds": 380
  },
  "output": {
    "report_html": "/outputs/nq_comparison.html",
    "manifest": "/outputs/manifest.json",
    "cards_png": ["/outputs/cards/run_1.png", "/outputs/cards/run_2.png"],
    "artifacts": {
      "pattern_engine": "/outputs/.ta_artifacts/pattern_engine/run_id1/",
      "strategy_discovery": "/outputs/.ta_artifacts/strategy_discovery/"
    }
  }
}
```

---

## Jobs & Status

### GET /api/jobs — List All Jobs
Returns all current and recent jobs

```
GET /api/jobs?type=report_build&status=running
```

**Query Parameters:**
- `type` (optional) — Filter by job type: `report_build`, `prediction`, `discovery`, `optimizer`
- `status` (optional) — Filter by status: `queued`, `running`, `completed`, `failed`
- `limit` (optional) — Max results (default 20)

**Response:** 200 OK
```json
{
  "jobs": [
    {
      "job_id": "j_report_001",
      "type": "report_build",
      "status": "running",
      "progress_percent": 45,
      "created_at": "2026-05-24T14:00:00Z"
    },
    ...
  ]
}
```

---

### POST /api/jobs/<job_id>/cancel — Cancel Job
Cancels a running job

```
POST /api/jobs/j_report_001/cancel
```

**Response:** 200 OK
```json
{
  "job_id": "j_report_001",
  "status": "cancelled",
  "cancelled_at": "2026-05-24T14:05:00Z"
}
```

---

## Error Handling

### Common HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| **200 OK** | Request succeeded | GET request returns data |
| **201 Created** | Resource created | POST creates new session |
| **202 Accepted** | Request queued (async) | Job submitted |
| **204 No Content** | Success, no response body | DELETE resource |
| **400 Bad Request** | Invalid request format | Malformed JSON, missing required field |
| **404 Not Found** | Resource doesn't exist | Session ID not found |
| **409 Conflict** | Can't process request | Session already running a job |
| **500 Internal Server Error** | Server error | Unexpected exception |

---

### Error Response Format

```json
{
  "error": true,
  "code": "INVALID_STAGE_ID",
  "message": "Stage '99_unknown' does not exist",
  "details": {
    "valid_stages": ["01_quick_scan", "02_candle_patterns", ...]
  }
}
```

---

### Validation Errors

```json
{
  "error": true,
  "code": "VALIDATION_ERROR",
  "message": "Invalid YAML configuration",
  "fields": {
    "strategy_discovery.entry_strategies.candle.patterns": [
      "Unknown pattern: 'invalid_name'"
    ],
    "strategy_discovery.entry_strategies.candle.tp_ticks": [
      "Must be a list of positive integers"
    ]
  }
}
```

---

## Examples

### Example 1: Complete Discovery Workflow

```bash
# 1. Get available stages
curl http://localhost:7734/api/discovery/stages

# 2. Create new session
curl -X POST http://localhost:7734/api/discovery/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "label": "NQ Quick Edge Test",
    "instrument": "NQ H26"
  }'
# Response: session_id = abc123

# 3. Preview stage with custom YAML
curl -X POST http://localhost:7734/api/discovery/stages/01_quick_scan/preview \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_discovery": {
      "enabled": true,
      "entry_strategies": {
        "candle": {"enabled": true},
        "ma": {"enabled": true}
      }
    }
  }'

# 4. Start discovery run
curl -X POST http://localhost:7734/api/discovery/sessions/abc123/runs \
  -H "Content-Type: application/json" \
  -d '{
    "yaml_override": {...}
  }'
# Response: job_id = j_abc123_001

# 5. Poll job status
curl http://localhost:7734/api/jobs/j_abc123_001

# 6. When complete, promote signals
curl -X POST http://localhost:7734/api/discovery/sessions/abc123/promote \
  -H "Content-Type: application/json" \
  -d '{
    "signals_to_promote": [...]
  }'
```

---

### Example 2: Report Generation via API

```bash
# 1. Build report
curl -X POST http://localhost:7734/api/reports/build \
  -H "Content-Type: application/json" \
  -d '{
    "input_folder": "C:/backtest_exports",
    "output_folder": "./reports",
    "report_config": {
      "report": {
        "title": "Strategy Analysis",
        "output_filename": "analysis.html"
      },
      "sections": [
        {"id": "comparison_overview"},
        {"id": "run_kpi_cards"}
      ]
    }
  }'
# Response: job_id = j_report_001

# 2. Check progress
curl http://localhost:7734/api/jobs/j_report_001

# 3. When complete, fetch output
# (Report is written to ./reports/analysis.html)
```

---

### Example 3: Query Report Presets

```bash
curl http://localhost:7734/api/reports/presets

# Output shows available presets with YAML templates
# Copy YAML from preset and customize as needed
```

---

## Rate Limiting

No rate limiting is currently enforced. For production, implement:
- Per-IP rate limiting (e.g., 100 req/min)
- Per-job queue length (e.g., max 10 queued jobs)
- Long-running job timeout (e.g., 60 min max)

---

## Authentication (Future)

Planned enhancements:
- API key authentication
- JWT tokens with expiration
- Role-based access control (admin, researcher, trader)
- Audit logging for all API calls

---

## See Also

- **COMPLETE_SYSTEM_MAP.md** — System architecture
- **GETTING_STARTED.md** — Web UI walkthrough
- **discovery/README.md** — Discovery workflow guide
- **Flask app source:** `src/ta_foundation/web/app.py`

