# Agentic Research Workflow Guide

**Status:** Complete guide to the autonomous research program  
**Last Updated:** May 24, 2026  
**Audience:** Researchers, strategy developers, system operators  

---

## Overview

The **Agentic Research Program** is a 5-phase autonomous discovery system that uses AI agents with human-in-the-loop (HITL) gates to systematically discover, test, and refine trading strategies. It replaces manual hypothesis testing with a persistent, self-documenting research loop.

```
Research Ledger (SQLite)
  ↓
Phase A: Initialize ledger (families, hypotheses, runs, candidates)
  ↓
Phase B: Read-only agents (Triage, Scribe) → HITL inbox
  ↓
Phase C: Authoring agents (Hypothesis Author, Sweep Operator) → auto-discovery
  ↓
Phase D: Forward observation (Shadow Health Monitor, Narrative Scribe)
  ↓
Phase E: Future expansion
```

**The guiding principle:** Deterministic, reproducible discovery with human approval gates at critical decision points.

---

## What is the Research Ledger?

The research ledger is a **persistent SQLite database** that lives at:

```
.ta_artifacts/research_ledger.db
```

It tracks:
- **Families** — Strategy families (SMA cross, breakout, etc.)
- **Hypotheses** — Proposed entry/exit rules
- **Runs** — Backtest execution records
- **Candidates** — Results that passed validation
- **Decisions** — HITL approvals and rejections

Every phase writes to this ledger. The ledger is append-only and audit-able. You can inspect it at any time with:

```bash
sqlite3 .ta_artifacts/research_ledger.db ".tables"
```

---

## Phase Overview

### Phase A: Initialize & Persist

**What happens:** Set up the research ledger and register strategy families.

**When to run:** Once, at project setup.

**Command:**
```bash
python -m ta_foundation.agent.cli triage-pass --limit 0
```

(An empty triage pass with --limit 0 just initializes the DB; no candidates processed.)

**Output:** Creates `.ta_artifacts/research_ledger.db` with 5 tables:
- `families` — Strategy family definitions
- `hypotheses` — Proposed rules
- `runs` — Backtest records
- `candidates` — Passing results
- `decisions` — HITL approvals

---

### Phase B: Triage & Post-Mortem (Daily Pass)

**Purpose:** Classify untriaged candidates and write post-mortems for archived ones.

**What happens:**

```
Triage Pass (daily):
  Read all untriaged candidates from backtest/discovery runs
  → Classify each one using LLM (Claude via Ollama)
    - Is this a viable entry signal?
    - Does it avoid obvious overfitting?
    - Is the logic sound?
  → Mark as: candidate | dismiss | review
  → Write triage_state to ledger

Post-Mortem Pass (after triage):
  For each dismissed candidate:
    → Write a post-mortem explaining why it was rejected
    → Extract learnings for future hypotheses
    → Flag if human review needed
```

**Commands:**

```bash
# Just triage
python -m ta_foundation.agent.cli triage-pass \
  --limit 25 \
  --model claude-opus-4-6 \
  --triaged-by researcher-1

# Triage + Post-Mortem (recommended)
python -m ta_foundation.agent.cli daily-pass \
  --triage-limit 25 \
  --post-mortem-limit 25 \
  --model claude-opus-4-6

# Weekly narrative summary
python -m ta_foundation.agent.cli weekly-pass \
  --model claude-opus-4-6
```

**Output:** JSON report with triage counts:
```json
{
  "triage": {
    "processed": 25,
    "new_candidates": 5,
    "dismissed": 15,
    "flagged_for_review": 5,
    "hitl_flagged": 5
  },
  "post_mortem": {
    "processed": 15,
    "written": 15,
    "errors": 0
  }
}
```

**HITL gates (things that stop and require human review):**

| Trigger | Action |
|---|---|
| Candidate marked `needs_review` | Item added to inbox for human inspection |
| Post-mortem mentions red flags | Flagged for review |
| Triage LLM confidence < 0.7 | Added to inbox |

---

### Phase C: Hypothesis Authoring & Sweep (Weekly)

**Purpose:** Author new hypotheses based on discovery results and run parameter sweeps.

**What happens:**

```
Hypothesis Author (weekly):
  Read Phase B triage results
  → Identify patterns: which entries worked? which failed?
  → Generate new hypotheses: "Try wider stops" or "Filter by regime"
  → Write hypothesis spec with:
    - Rationale (why this rule makes sense)
    - Parameter ranges (what to optimize over)
    - Expected outcome
  → Queue for sweep operator

Sweep Operator (continuous):
  Read queued hypotheses
  → Generate parameter sweep configs
  → Run discovery: backtest all parameter combinations
  → Rank by profit factor, Sharpe, MAE/MFE
  → Feed top candidates back to ledger
  → Return to Phase B (triage them)
```

**Commands:**

```bash
# Author new hypotheses (weekly)
python -m ta_foundation.agent.cli authoring-pass \
  --n-proposals 5 \
  --session-quota 3 \
  --weekly-quota 15 \
  --model claude-opus-4-6

# Run discovery sweeps on queued hypotheses
python -m ta_foundation.agent.cli operator-pass \
  --input-dir .ta_artifacts/queued_probes \
  --output-dir .ta_artifacts/discovery_results \
  --market-data "D:\MarketData" \
  --limit 3

# Combined weekly flow
python -m ta_foundation.agent.cli weekly-authoring-pass \
  --input-dir .ta_artifacts/queued_probes \
  --output-dir .ta_artifacts/discovery_results \
  --market-data "D:\MarketData" \
  --model claude-opus-4-6
```

**Output:** Hypotheses queued in ledger; discovery results flow to Phase B triage.

**Quotas (prevent runaway discovery):**

| Quota | Default | Meaning |
|---|---|---|
| `n_proposals` | 5 | Hypotheses to author per week |
| `session_quota` | 3 | Max active discovery sessions at once |
| `weekly_quota` | 15 | Max total discoveries per week |

---

### Phase D: Shadow Health & Narrative (Ongoing)

**Purpose:** Monitor live/paper trading performance and write narrative summaries.

**What happens:**

```
Shadow Health Monitor:
  Poll current open positions
  → Calculate live P&L, max adverse excursion (MAE), max favorable excursion (MFE)
  → Check against strategy guardrails
  → Flag if open position is aging badly or max risk exceeded
  → Write health record to ledger

Narrative Scribe:
  Read shadow health + daily P&L
  → Write prose summary: "Today we saw 3 breakouts, 2 winners, 1 breakeven"
  → Identify patterns: "Afternoon sessions show lower PF"
  → Flag if health metrics exceed thresholds
  → Attach to daily summary
```

**Commands:**

```bash
# Run shadow health & narrative (daily after close)
python -m ta_foundation.agent.cli shadow-scribe-pass \
  --for-date 2026-05-24 \
  --trailing-window 5 \
  --model claude-opus-4-6 \
  --open-age-warn-hours 4
```

**Output:** Health records + prose narrative in ledger.

---

## Full Automation: Scheduled Runs

### Daily Automation

Run every evening after market close:

```bash
# 4:30 PM ET: Triage + Post-mortem
python -m ta_foundation.agent.cli daily-pass \
  --triage-limit 25 \
  --post-mortem-limit 25 \
  --model claude-opus-4-6

# 5:00 PM ET: Shadow health
python -m ta_foundation.agent.cli shadow-scribe-pass \
  --for-date $(date +%Y-%m-%d) \
  --trailing-window 5 \
  --model claude-opus-4-6
```

### Weekly Automation

Run on Monday morning:

```bash
# 8:00 AM ET: Weekly narrative
python -m ta_foundation.agent.cli weekly-pass \
  --model claude-opus-4-6

# 9:00 AM ET: Author new hypotheses
python -m ta_foundation.agent.cli authoring-pass \
  --n-proposals 5 \
  --session-quota 3 \
  --weekly-quota 15 \
  --model claude-opus-4-6

# 10:00 AM ET: Run operator pass (discovery)
python -m ta_foundation.agent.cli operator-pass \
  --input-dir .ta_artifacts/queued_probes \
  --output-dir .ta_artifacts/discovery_results \
  --market-data "D:\MarketData" \
  --limit 3
```

### Setting Up Scheduled Tasks

Use ta_foundation's built-in scheduled task system:

```bash
# Create a nightly daily-pass task
python -m ta_foundation.scheduled_tasks.create \
  --task-id daily-triage-postmortem \
  --cron "0 16 * * *" \
  --command "python -m ta_foundation.agent.cli daily-pass --triage-limit 25"

# Create a Monday authoring task
python -m ta_foundation.scheduled_tasks.create \
  --task-id weekly-authoring \
  --cron "0 9 * * 1" \
  --command "python -m ta_foundation.agent.cli authoring-pass --n-proposals 5"
```

(See SCHEDULED_TASKS.md for full setup.)

---

## HITL Inbox System

Certain events trigger **human-in-the-loop** gates:

| Event | Action | How to Review |
|---|---|---|
| Triage marked `needs_review` | Item queued to inbox | See "Reading the Inbox" below |
| Post-mortem confidence low | Flagged for human review | Check inbox |
| Confidence < 0.7 | Confidence score too low | Re-run triage with lower threshold |
| Shadow health violation | Alert + manual inspection | Review health record |

### Reading the Inbox

Items are stored in the ledger under `decisions` table with state `inbox`. View with:

```bash
# List all inbox items
sqlite3 .ta_artifacts/research_ledger.db \
  "SELECT id, candidate_id, reason FROM decisions WHERE state='inbox' ORDER BY created_at DESC"

# View a specific item details
python -c "
from ta_foundation.research_ledger import get_repository
repo = get_repository('.ta_artifacts/research_ledger.db')
items = repo.list_drafts()  # Get inbox items
for item in items[:3]:
    print(f'ID: {item.id}')
    print(f'Reason: {item.reason}')
    print(f'---')
"
```

### Approving or Rejecting Inbox Items

```python
from ta_foundation.research_ledger import get_repository

repo = get_repository('.ta_artifacts/research_ledger.db')

# Get an inbox item
items = repo.list_drafts()
item = items[0]

# Approve it (promotes to candidate)
repo.accept_draft(
    item.id,
    approved_by="researcher-1",
    notes="Looks good. Approve for deployment."
)

# Or reject it (dismisses)
repo.reject_draft(
    item.id,
    rejected_by="researcher-1",
    reason="Entry logic doesn't match market regime assumptions."
)
```

---

## CLI Command Reference

### `triage-pass`

**Purpose:** Classify untriaged candidates using LLM.

**Usage:**
```bash
python -m ta_foundation.agent.cli triage-pass [options]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--limit` | 25 | Number of candidates to triage per run |
| `--model` | `claude-opus-4-6` | LLM model to use |
| `--temperature` | 0.5 | LLM temperature (0.0–1.0) |
| `--max-retries` | 2 | LLM call retries on failure |
| `--triaged-by` | `anonymous` | Researcher name for audit trail |
| `--ledger-db` | `.ta_artifacts/research_ledger.db` | Ledger path |

**Example:**
```bash
python -m ta_foundation.agent.cli triage-pass \
  --limit 50 \
  --model claude-opus-4-6 \
  --triaged-by team-researcher
```

---

### `daily-pass`

**Purpose:** Run triage + post-mortem (combined daily workflow).

**Usage:**
```bash
python -m ta_foundation.agent.cli daily-pass [options]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--triage-limit` | 25 | Candidates to triage |
| `--post-mortem-limit` | 25 | Post-mortems to write |
| `--model` | `claude-opus-4-6` | LLM model |
| `--temperature` | 0.5 | LLM temperature |
| `--max-retries` | 2 | LLM retries |
| `--ledger-db` | `.ta_artifacts/research_ledger.db` | Ledger path |

**Example:**
```bash
python -m ta_foundation.agent.cli daily-pass
```

---

### `weekly-pass`

**Purpose:** Write weekly narrative summary.

**Usage:**
```bash
python -m ta_foundation.agent.cli weekly-pass [options]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--model` | `claude-opus-4-6` | LLM model |
| `--temperature` | 0.5 | LLM temperature |
| `--max-retries` | 2 | LLM retries |
| `--week-start` | Last Monday | ISO week start date (YYYY-MM-DD) |
| `--ledger-db` | `.ta_artifacts/research_ledger.db` | Ledger path |

**Example:**
```bash
python -m ta_foundation.agent.cli weekly-pass
```

---

### `authoring-pass`

**Purpose:** Author new hypotheses based on prior discovery.

**Usage:**
```bash
python -m ta_foundation.agent.cli authoring-pass [options]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--n-proposals` | 5 | Hypotheses to author |
| `--session-quota` | 3 | Max active discovery sessions |
| `--weekly-quota` | 15 | Max discoveries per week |
| `--model` | `claude-opus-4-6` | LLM model |
| `--temperature` | 0.6 | LLM temperature (slightly higher for creativity) |
| `--max-retries` | 2 | LLM retries |
| `--ledger-db` | `.ta_artifacts/research_ledger.db` | Ledger path |

**Example:**
```bash
python -m ta_foundation.agent.cli authoring-pass \
  --n-proposals 5 \
  --session-quota 3 \
  --weekly-quota 15
```

---

### `operator-pass`

**Purpose:** Run parameter sweeps on queued hypotheses.

**Usage:**
```bash
python -m ta_foundation.agent.cli operator-pass [options]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--input-dir` | `.ta_artifacts/queued_probes` | Directory with hypothesis YAML files |
| `--output-dir` | `.ta_artifacts/discovery_results` | Results output directory |
| `--market-data` | `D:\MarketData` | Market data root folder |
| `--limit` | 3 | Max hypotheses to run per pass |
| `--timeout-seconds` | 600 | Max time per sweep (10 min) |
| `--ledger-db` | `.ta_artifacts/research_ledger.db` | Ledger path |

**Example:**
```bash
python -m ta_foundation.agent.cli operator-pass \
  --input-dir .ta_artifacts/queued_probes \
  --output-dir .ta_artifacts/discovery_results \
  --market-data "D:\MarketData" \
  --limit 3
```

---

### `weekly-authoring-pass`

**Purpose:** Combined authoring + operator pass (full weekly discovery).

**Usage:**
```bash
python -m ta_foundation.agent.cli weekly-authoring-pass [options]
```

**Options:** (combines authoring-pass + operator-pass options)

**Example:**
```bash
python -m ta_foundation.agent.cli weekly-authoring-pass \
  --n-proposals 5 \
  --session-quota 3 \
  --input-dir .ta_artifacts/queued_probes \
  --output-dir .ta_artifacts/discovery_results \
  --market-data "D:\MarketData"
```

---

### `shadow-scribe-pass`

**Purpose:** Monitor live positions and write narrative.

**Usage:**
```bash
python -m ta_foundation.agent.cli shadow-scribe-pass [options]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--for-date` | Today | Date to analyze (YYYY-MM-DD) |
| `--trailing-window` | 5 | Days of history to review |
| `--model` | `claude-opus-4-6` | LLM model |
| `--temperature` | 0.5 | LLM temperature |
| `--open-age-warn-hours` | 4 | Hours to flag aging position |
| `--max-retries` | 2 | LLM retries |
| `--ledger-db` | `.ta_artifacts/research_ledger.db` | Ledger path |

**Example:**
```bash
python -m ta_foundation.agent.cli shadow-scribe-pass \
  --for-date 2026-05-24 \
  --trailing-window 5 \
  --open-age-warn-hours 4
```

---

## Understanding Discovery Output

### Discovery YAML Format

When the Hypothesis Author creates a hypothesis, it's written as a YAML file in `.ta_artifacts/queued_probes/`:

```yaml
hypothesis_id: "hyp_20260524_001"
family: "sma_cross"
rationale: "Wider stop losses reduce false exits; test 20–30 tick range."
parameters:
  FastPeriod: 9
  SlowPeriod: 21
  StopLossTicks:
    - 20
    - 22
    - 24
    - 26
    - 28
    - 30
  ProfitTargetTicks: 24
expected_outcome: "PF > 1.5 with < 2500 max drawdown"
created_at: "2026-05-24T08:00:00Z"
created_by: "hypothesis_author_v1"
```

### Discovery Results

After the Sweep Operator runs, results are in `.ta_artifacts/discovery_results/`:

```
discovery_results/
  hyp_20260524_001/
    backtest_results.csv          # All parameter combinations tested
    ranked_results.json           # Top 10 results by profit factor
    discovery_summary.md          # Human-readable summary
    diagnostics.json              # Sweep metrics (coverage, degradation)
```

### Result CSV Columns

| Column | Meaning |
|---|---|
| `param_FastPeriod` | Parameter value tried |
| `param_SlowPeriod` | Parameter value tried |
| `param_StopLossTicks` | Parameter value tried |
| `total_net_profit` | Total P&L |
| `profit_factor` | Gross profit / Gross loss |
| `trade_count` | Number of trades |
| `max_drawdown` | Peak-to-valley drawdown |
| `sharpe_ratio` | Risk-adjusted return |
| `mae_avg` | Avg max adverse excursion |
| `mfe_avg` | Avg max favorable excursion |
| `is_valid` | Passes guardrails? (true/false) |

---

## Research Ledger Schema

Inspect the ledger with SQL:

```bash
sqlite3 .ta_artifacts/research_ledger.db ".schema"
```

### Tables

#### `families`
```
id           — UUID
name         — Strategy family (e.g., "sma_cross")
description  — Free text
created_at   — Timestamp
```

#### `hypotheses`
```
id             — UUID
family_id      — Foreign key to families
author         — Researcher name
rationale      — Why this hypothesis
parameter_spec — JSON (parameter ranges)
created_at     — Timestamp
```

#### `runs`
```
id              — UUID
hypothesis_id   — Foreign key to hypotheses
start_date      — Backtest start
end_date        — Backtest end
results_path    — Path to CSV results
run_count       — Number of parameter combinations tested
created_at      — Timestamp
```

#### `candidates`
```
id           — UUID
run_id       — Foreign key to runs
parameters   — JSON (winning parameters)
pnl          — Total net profit
pf           — Profit factor
max_dd       — Max drawdown
triage_state — untriaged | candidate | dismiss | review_needed
created_at   — Timestamp
triaged_at   — Timestamp (when triage_state set)
```

#### `decisions`
```
id            — UUID
candidate_id  — Foreign key to candidates
decision      — approved | rejected | inbox
approved_by   — Researcher name
reason        — Approval/rejection rationale
created_at    — Timestamp
```

---

## Example Workflow: Tuesday Morning

**Monday 5 PM:** Triage & post-mortem completed. 3 new candidates marked `needs_review` → inbox.

**Tuesday 8 AM:** Researcher checks inbox:

```python
from ta_foundation.research_ledger import get_repository
repo = get_repository()
inbox = repo.list_drafts()
for item in inbox:
    print(f"ID {item.id}: {item.reason}")
    # Review backtest results in discovery_results/
```

**Tuesday 8:30 AM:** Researcher approves 2, rejects 1:

```python
repo.accept_draft(inbox[0].id, approved_by="researcher-1", notes="PF 1.8, good entry logic")
repo.reject_draft(inbox[1].id, rejected_by="researcher-1", reason="Overfitted to Feb data")
```

**Tuesday 9 AM:** Weekly authoring pass runs automatically:
- Reads approved candidates + post-mortems
- Generates 5 new hypotheses
- Queues for sweep

**Tuesday 10 AM:** Operator pass runs:
- Sweeps 3 hypotheses (stop losses, profit targets, filter combinations)
- 450 total parameter combinations tested
- Results ranked and queued for triage

**Tuesday 4:30 PM:** Daily triage runs:
- Processes 25 new candidates from operator pass
- Marks 3 as candidate, 8 as dismiss, 5 as needs_review

**Cycle repeats** next day.

---

## Ledger Inspection & Debugging

### Query Examples

```bash
# Count candidates by triage_state
sqlite3 .ta_artifacts/research_ledger.db \
  "SELECT triage_state, COUNT(*) FROM candidates GROUP BY triage_state"

# Find best hypothesis (highest max PF)
sqlite3 .ta_artifacts/research_ledger.db \
  "SELECT h.id, h.rationale, MAX(c.pf) as best_pf FROM hypotheses h \
   JOIN runs r ON h.id = r.hypothesis_id \
   JOIN candidates c ON r.id = c.run_id \
   GROUP BY h.id ORDER BY best_pf DESC LIMIT 5"

# Export all approved candidates as CSV
sqlite3 .ta_artifacts/research_ledger.db \
  "SELECT c.*, d.approved_by FROM candidates c \
   JOIN decisions d ON c.id = d.candidate_id \
   WHERE d.decision='approved'" > approved_candidates.csv
```

### Backup the Ledger

```bash
cp .ta_artifacts/research_ledger.db \
   .ta_artifacts/research_ledger_backup_$(date +%Y%m%d_%H%M%S).db
```

### Reset the Ledger

⚠️ **Destructive operation — not normally needed.**

```bash
rm .ta_artifacts/research_ledger.db
python -m ta_foundation.agent.cli triage-pass --limit 0  # Recreates empty DB
```

---

## Quotas & Guardrails

### Discovery Quotas

Prevent runaway discovery with these defaults:

| Quota | Default | Adjustable? | Purpose |
|---|---|---|---|
| `n_proposals` per week | 5 | Yes (`--n-proposals`) | Limit hypothesis authoring |
| `session_quota` | 3 | Yes (`--session-quota`) | Max concurrent sweeps |
| `weekly_quota` | 15 | Yes (`--weekly-quota`) | Max discoveries per week |

Adjust via CLI:

```bash
python -m ta_foundation.agent.cli authoring-pass \
  --n-proposals 10 \
  --session-quota 5 \
  --weekly-quota 30
```

### Confidence Thresholds

LLM-based triage uses confidence scores (0.0–1.0):

| Threshold | Action |
|---|---|
| ≥ 0.8 | Auto-approve (no HITL) |
| 0.7–0.8 | Candidate (requires review) |
| < 0.7 | Inbox (human required) |

Adjust in `triage.py` if needed.

---

## Integration with Other Systems

### Connecting to Discovery Results

The `operator-pass` outputs backtest CSVs. Feed these to other ta_foundation capabilities:

```bash
# Generate backtest report from discovery results
python -m ta_foundation.cli.main \
  --input .ta_artifacts/discovery_results/hyp_20260524_001 \
  --output ./reports \
  --report-config discovery_report.yaml
```

### Promoting to Strategy Loop

Best candidates can flow to the NinjaTrader Strategy Loop:

```bash
# Extract best parameters from discovery
sqlite3 .ta_artifacts/research_ledger.db \
  "SELECT parameters FROM candidates WHERE decision='approved' ORDER BY pf DESC LIMIT 1"

# Create a strategy spec from the top candidate
python -c "
import json
# Use the extracted parameters to build a StrategySpec
spec = {
    'strategy_name': 'ApprovedBot_v1',
    'family': 'sma_cross',
    'parameters': {
        'FastPeriod': 9,
        'SlowPeriod': 21,
        ...
    }
}
with open('approved_strategy.json', 'w') as f:
    json.dump(spec, f, indent=2)
"

# Feed to full-loop
python -m ta_foundation.nt_strategy_loop.cli full-loop \
  --spec approved_strategy.json \
  --instrument "NQ 06-26"
```

---

## Troubleshooting

### "Triage says LLM call failed"

**Cause:** Ollama not running or API error.

**Fix:**
1. Check Ollama: `ollama serve` running?
2. Check model: `ollama list` includes `claude-opus-4-6`?
3. Retry with `--max-retries 3`

---

### "Inbox has 50 items — can't keep up"

**Cause:** Discovery is outpacing manual review.

**Fix:**
1. Increase `--triage-limit` to batch-process faster
2. Lower `--weekly-quota` to slow discovery
3. Schedule fewer `operator-pass` runs
4. Delegate inbox review to other researchers

---

### "Operator pass timed out"

**Cause:** Sweep took > 600 seconds (10 min).

**Fix:**
1. Reduce parameter space (fewer values to test)
2. Increase `--timeout-seconds 900` (15 min)
3. Lower `--limit 1` (one hypothesis per pass)

---

### "Can't find discovery results CSV"

**Cause:** Operator pass didn't complete.

**Fix:**
1. Check `--output-dir` exists and is writable
2. Check logs for operator errors
3. Re-run with `--limit 1` to isolate problem hypothesis

---

## Monitoring & Alerting

### Daily Checks

Run each morning:

```bash
# Count inbox items
sqlite3 .ta_artifacts/research_ledger.db \
  "SELECT COUNT(*) FROM decisions WHERE decision='inbox'"

# Check last triage time
sqlite3 .ta_artifacts/research_ledger.db \
  "SELECT MAX(triaged_at) FROM candidates"
```

### Weekly Report

Generate a summary:

```python
from ta_foundation.research_ledger import get_repository
repo = get_repository()

candidates = repo.list_candidates()
print(f"Total candidates: {len(candidates)}")
print(f"Approved: {sum(1 for c in candidates if c.approved)}")
print(f"Inbox: {sum(1 for c in candidates if c.needs_review)}")
print(f"This week: {sum(1 for c in candidates if c.created_this_week())}")
```

---

## Best Practices

### 1. Review Before Promoting

Never promote a discovery result to NinjaTrader Loop without:
- [ ] Reading post-mortem
- [ ] Checking guardrails (PF, DD, trades)
- [ ] Reviewing walk-forward degradation
- [ ] Confirming entry logic makes market sense

### 2. Use Quotas Aggressively

Start conservative:
- `--n-proposals 2` (author slowly)
- `--weekly-quota 5` (discover slowly)
- Increase only after reviewing quality

### 3. Audit the Ledger Weekly

```bash
# Export all approved
sqlite3 research_ledger.db \
  "SELECT * FROM candidates WHERE triage_state='candidate' ORDER BY created_at DESC LIMIT 20"

# Look for patterns in dismissals
sqlite3 research_ledger.db \
  "SELECT rationale FROM hypotheses WHERE id IN \
   (SELECT hypothesis_id FROM runs WHERE id IN \
    (SELECT run_id FROM candidates WHERE triage_state='dismiss'))"
```

### 4. Name Your Researchers

Always specify `--triaged-by`:
```bash
python -m ta_foundation.agent.cli daily-pass \
  --triaged-by alice-smith
```

This creates an audit trail. Useful if decisions are later disputed.

### 5. Monitor Confidence Scores

Low confidence = likely false positives. If triage has many < 0.7 scores, check:
- Is the hypothesis clear and testable?
- Is market data from a calm period?
- Are parameter ranges reasonable?

---

## See Also

- **research_ledger/README.md** — Ledger architecture
- **agent/roles/** — Agent implementations
- **COMPLETE_CAPABILITIES_MATRIX.md** — Capability 6 entry
- **DISCOVERY_SUMMARY.md** — 3 surprise capabilities overview

---

## Glossary

| Term | Meaning |
|---|---|
| **Hypothesis** | A proposed entry/exit rule with parameter ranges |
| **Run** | Execution of a hypothesis sweep across parameter space |
| **Candidate** | A result (parameter set) that passed triage and guardrails |
| **Decision** | HITL approval, rejection, or inbox flag |
| **Triage** | LLM-based classification: is this a real signal or overfitted? |
| **Post-mortem** | Narrative explanation of why a candidate was dismissed |
| **Ledger** | SQLite DB tracking families, hypotheses, runs, candidates, decisions |
| **HITL** | Human-in-the-loop; human review gate |
| **Quota** | Limit to prevent runaway discovery (e.g., max 5 discoveries/week) |
| **Shadow health** | Monitoring of live/paper position P&L and risk metrics |

