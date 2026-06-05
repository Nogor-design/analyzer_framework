# TA Foundation — Complete System Map & Capability Catalog

> **⚠ VERIFIED REFRESH — 2026-06-05.** This map was re-audited against actual code + tests (the
> original May-24 pass read aspirational). Trust this header + the "Verified status" table over older
> body prose. Full evidence: `docs/audits/capability_and_cleanup_audit_2026-06-05.md`. External
> sibling repos: `docs/reference/EXTERNAL_PROJECTS_MAP.md`.
>
> **Key corrections from the audit:** the agentic research loop is **shipped** (not "Partial");
> `prediction/` **is** the daily lineup/forecast engine (daily Claude + horizon ensemble), not just
> scaffolding; `analysis/prop_evaluation/` **already implements the APEX trailing-drawdown model**;
> the three internal `.cs` paths (StrategyDiscoveryFilter / ExecutionShell / DataExport) are
> **distinct and all tested**, not duplicates. Status legend: ✅ shipped · 🟡 partial · 🔩 stub · ⚰ dead.

**Last Updated:** 2026-06-05 (verified refresh; original draft May 24, 2026)
**Scope:** Comprehensive discovery of all capabilities, entry points, integrations, and workflows

---

## Table of Contents

1. [System Overview](#system-overview)
2. [10 Core Capabilities](#10-core-capabilities)
3. [All Entry Points (CLI + Web)](#all-entry-points)
4. [Analysis Subsystems Deep Dive](#analysis-subsystems-deep-dive)
5. [Agentic Workflows (Phase A-D)](#agentic-workflows)
6. [Prediction Systems (Daily + Horizon)](#prediction-systems)
7. [NinjaTrader Integration](#ninjatrader-integration)
8. [Configuration & Registry Systems](#configuration--registry-systems)
9. [Data Flow & Architecture](#data-flow--architecture)
10. [Hidden Capabilities](#hidden-capabilities)
11. [External Integrations](#external-integrations)
12. [Gaps & Undocumented Areas](#gaps--undocumented-areas)

---

## System Overview

**ta_foundation** is a **10-layer production analytics and autonomy platform** for algorithmic trading:

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 10: Agentic Workflow Orchestration (Phase A-D + Future)   │
│           (hypothesis authoring, triage, scribe, shadow monitor) │
├─────────────────────────────────────────────────────────────────┤
│ Layer 9:  Prediction Systems (Daily Claude + Multi-Horizon)     │
├─────────────────────────────────────────────────────────────────┤
│ Layer 8:  NinjaTrader Execution Bridge & Autonomous Loop        │
├─────────────────────────────────────────────────────────────────┤
│ Layer 7:  Analysis Engines (8 strategy families + discovery)     │
│           (MA anchor, pattern engine, regime, excursion)         │
├─────────────────────────────────────────────────────────────────┤
│ Layer 6:  Report Rendering System (120+ HTML sections)          │
├─────────────────────────────────────────────────────────────────┤
│ Layer 5:  Web App & Flask API (Discovery UI, Strategy Lab)      │
├─────────────────────────────────────────────────────────────────┤
│ Layer 4:  Optimization & Parameter Sweep (Graveyard refusal)    │
├─────────────────────────────────────────────────────────────────┤
│ Layer 3:  Market Data & Tick Caching (Shared, TimeZone-safe)    │
├─────────────────────────────────────────────────────────────────┤
│ Layer 2:  Core Pipeline (AnalysisPackage, MarketDataStore)      │
├─────────────────────────────────────────────────────────────────┤
│ Layer 1:  Parser Registry & Ingest (NinjaTrader formats)        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Verified status at a glance (2026-06-05)

| # | Capability | Status | Verified notes |
|---|---|---|---|
| 1 | Backtest report generation | ✅ shipped | 130 registered report sections |
| 2 | Strategy discovery funnel | ✅ shipped | 8 entry families + pattern engine + walk-forward |
| 3 | Daily prediction (Claude) | ✅ shipped | `claude_agent.py`; **this is the daily lineup engine** |
| 4 | Horizon prediction ensemble | ✅ shipped | statistical/analogue/regime/session + stacking + ECE |
| 5 | Autonomous NT strategy loop | ✅ shipped | author→compile→repair→optimize, validated live |
| 6 | Agentic research program | ✅ shipped | roles + scheduler + research_ledger (map previously said "Partial") |
| 7 | Pattern engine + Monte Carlo | ✅ shipped | but `pattern_engine/monte_carlo.py` is an empty ⚰ stub |
| 8 | Large candle excursion | ✅ shipped | |
| 9 | Execution bridge | ✅ shipped | 34 integration tests |
| 10 | Discovery UI / web workbench | ✅ shipped | ~151 Flask routes, 54 web test files |
| + | Prop-account evaluation (APEX DD) | ✅ shipped | `analysis/prop_evaluation/simulation.py` — trailing DD, daily loss, MC |
| + | External sibling repos | see `EXTERNAL_PROJECTS_MAP.md` | local-deep-research, NinjatraderDocScrapper, NinjaAccountManager, DailyAnalysis, agentic-engine |

**Known dead/stub (cleanup register has full list):** `plots/`, `agent/graph.py`,
`pattern_engine/monte_carlo.py`, 3 `test_anchor_*.py` files in `reports/html/sections/`,
`prediction/ollama_agent.py` (stub), duplicate registry key `anchor_interaction_overview`.

## 10 Core Capabilities

### 1. **Backtest Report Generation** — Single/Multi-Run Analysis
- **Entry Point:** `python -m ta_foundation.cli.main`
- **Input:** NinjaTrader CSV exports (trades, daily, summary, settings, optimization)
- **Output:** Self-contained HTML report + manifest + optional PNG cards
- **Configuration:** YAML-driven (sections, options, feature blocks)
- **Use Cases:**
  - Single-run review (KPIs, equity curve, daily scoreboard)
  - Multi-run comparison (competitive analysis, parameter sensitivity)
  - Export execution cards for leaderboards
  - Include run-specific images (planograms, market context)

---

### 2. **Strategy Discovery Funnel** — 6-Stage Signal Discovery
- **Entry Point:** `python -m ta_foundation.cli.main --report-config discovery/NN_*.yaml`
- **Workflow:** Quick scan → Candle patterns → Levels/regions → NY open → ORB → Validate
- **Input:** Backtest exports + market data (minute bars)
- **Output:** Ranked entry signals, IS/OOS validation, NinjaTrader templates
- **Configuration:** Per-stage YAML (quick_scan → candle → levels → ny_open → orb → validate)
- **Use Cases:**
  - Discover high-probability entry patterns on new instruments
  - Validate IS/OOS degradation before live trading
  - Generate C# strategy templates for NinjaTrader
  - Export validated signal rules for execution

---

### 3. **Prediction System (Daily)** — Claude-Driven Next-Day Forecasting
- **Entry Point:** `python -m ta_foundation.prediction.run_prediction --config prediction.yaml`
- **Input:** Market data (minute bars), economic calendar, prior-day context
- **Output:** Direction + confidence, key levels, breakout probability, trade reasoning
- **Models:** Claude Opus 4.7 (adaptive thinking + tool-use)
- **Measurement:** Next-session outcome scoring (Brier, level touch, breakout)
- **Use Cases:**
  - End-of-day decision support for next session
  - Historical analogue matching (find 5 similar market patterns)
  - Proper scoring rule tracking (confidence calibration)
  - ECE drift detection (confidence decay over time)

---

### 4. **Prediction System (Horizon)** — Multi-Agent Multi-Horizon Ensemble
- **Entry Point:** `python -m ta_foundation.prediction.backtest_horizon_predictions`
- **Input:** Minute bars, config (timeframes, horizons, agents)
- **Output:** Calibrated probability distributions, tradable zones, decision recommendations
- **Agents (Built-in):**
  - Statistical baseline (conditional frequency + empirical Bayes)
  - Analogue probability (KNN on 4-dim feature space)
  - Regime specialist (analogue locked to market regime)
  - Session specialist (analogue locked to trading session)
  - Ensemble (stacking weights learned from composite scores)
- **Use Cases:**
  - 5m/15m/1h candle probability prediction
  - Walk-forward backtesting with rolling ECE calibration
  - Trading zone conversion (probabilities → trade signals)
  - Kelly sizing and risk-of-ruin calculations
  - Drift detection (when agent edge decays)

---

### 5. **Autonomous NinjaTrader Strategy Loop** — Author→Compile→Repair→Optimize
- **Entry Point:** `python -m ta_foundation.nt_strategy_loop.cli full-loop --spec strategy.json`
- **Workflow:** Author .cs → Install → Observe compile → Repair on error → Run optimizer → Analyze
- **Features:**
  - Deterministic repair (class name/filename fixes, missing using directives)
  - LLM-assisted repair (Ollama with qwen3-coder:30b, fail-soft)
  - Strategy Analyzer integration (seed template generation, result intake)
  - Guardrail scoring (min trades, PF, drawdown, max loss filters)
- **Use Cases:**
  - Fully autonomous strategy parameter optimization
  - Repair loop for handling compile errors deterministically
  - Seed template generation from spec → optimizer run
  - Candidate decision recording (archive vs. reject)

---

### 6. **Agentic Research Program (Phase A-D)** — Autonomous Hypothesis Testing
- **Entry Point:** `python -m ta_foundation.agent.cli <subcommand>`
- **Phases:**
  - **A:** Research ledger (persistent database of families, hypotheses, runs, candidates)
  - **B:** Read-only agents (triage, scribe, post-mortem generation)
  - **C:** Authoring agents (hypothesis author, sweep operator)
  - **D:** Forward observation (shadow health, scribe narrative)
- **Components:**
  - **Hypothesis Author:** Proposes new pre-registered hypotheses (family whitelist, param bounds)
  - **Sweep Operator:** Executes discovery probes, ingests results, checks graveyard
  - **Triage Analyst:** Classifies candidates (graveyard/research/hardening/shadow/decayed)
  - **Scribe:** Generates post-mortems, weekly letters, shadow health narratives
- **Use Cases:**
  - Fully autonomous discovery loop with human-in-the-loop checkpoints
  - Prevent redundant probes via graveyard refusal registry
  - Capture hypothesis evolution in persistent ledger
  - Generate research narratives from probe results

---

### 7. **Pattern Engine with Monte Carlo** — Template Sweep & Robustness
- **Entry Point:** Enabled via `pattern_engine:` YAML block in report config
- **Features:**
  - Sweep parameterized templates (ORB, breakout, pullback, level, candle, MA, BB, LCR)
  - Clustering of results by feature similarity
  - Monte Carlo path simulation for robustness
  - Cross-validation robustness scoring
  - Trade-level pattern audit (which patterns matched which trades)
- **Output:** Parquet artifacts under `.ta_artifacts/pattern_engine/<run_id>/`
- **Use Cases:**
  - Discover which pattern families explain executed trades
  - Assess robustness of pattern across different market regimes
  - Monte Carlo sensitivity testing (sample size → edge decay)
  - Cluster similar patterns to reduce dimensionality

---

### 8. **Large Candle Excursion Analysis** — Event Study & Downstream Discovery
- **Entry Point:** Enabled via `discovery.README.md` stage 03 or standalone
- **Workflow:** Identify large candles → Find downstream discoveries → Rate via tradability/signal coverage
- **Components:**
  - Large candle identification (ATR-relative thresholds)
  - Downstream context families (which patterns follow?)
  - Signal coverage metrics (% of downstream candles explained by entry rules)
  - Recursive search (which candle types predict which other candles?)
- **Output:** Findings with rank, fragility index, next tests
- **Use Cases:**
  - Find macro-level order-flow events → entry opportunities
  - Diagnose which candle features matter for next move
  - Assess strategy coverage vs. market structure

---

### 9. **Execution Bridge** — NinjaTrader Signal Protocol & Live Monitoring
- **Entry Point:** `python -m ta_foundation.cli.bridge_operator` + C# shell at `TaFoundationExecutionShell.cs`
- **Signal Contract:** JSON messages in inbox folder → parsed by NT shell → executed as orders
- **Features:**
  - Enter long/short with fixed stop and target
  - Move stop/take partial (pyramiding, scaling)
  - Heartbeat protocol (keep-alive for signal freshness)
  - Archive/reject folders for signal lifecycle tracking
  - Processed-IDs deduplication (prevent duplicate fills)
- **Runbooks:**
  - Phase 1: basic entry/exit
  - Phase 2: advanced order handling (conflict resolution, recovery, heartbeat)
- **Use Cases:**
  - Send algorithmic signals from Python → NinjaTrader execution
  - Monitor signal freshness (heartbeat)
  - Live execution with deterministic order handling
  - Soak monitoring of live/paper trades

---

### 10. **Discovery UI & Web Workbench** — Interactive Strategy Discovery & Job Management
- **Entry Point:** `python -m ta_foundation.web.app --port 7734`
- **Tabs:**
  - Backtest Reports (run selection, YAML editor, preset picker)
  - Prediction (daily + horizon job scheduling)
  - Strategy Templates (interactive template builder, backtest runner)
  - Strategy Discovery (funnel stepper, session-based state)
  - System Map (capabilities browser)
- **Features:**
  - Stateful discovery sessions (resume across restarts)
  - YAML validation + preview before run
  - Job scheduling + status tracking
  - Report artifact browsing
  - Instrument registry + tradable zone calculator
  - Conditional promotion (generate next probe YAML based on metrics)
- **API Routes:** 20+ Flask endpoints for session management, schema, capabilities

---

## All Entry Points (CLI + Web)

### CLI Entry Points

| Entry Point | Command | Purpose | Location |
|---|---|---|---|
| **Backtest Report** | `python -m ta_foundation.cli.main` | Ingest → analyze → report | `src/ta_foundation/cli/main.py` |
| **Agent Scheduler** | `python -m ta_foundation.agent.cli` | Run triage/scribe/author/operator passes | `src/ta_foundation/agent/cli.py` |
| **NinjaTrader Loop** | `python -m ta_foundation.nt_strategy_loop.cli` | Author → compile → repair → optimize | `src/ta_foundation/nt_strategy_loop/cli.py` |
| **Daily Prediction** | `python -m ta_foundation.prediction.run_prediction` | Next-day Claude forecast | `src/ta_foundation/prediction/run_prediction.py` |
| **Horizon Backtest** | `python -m ta_foundation.prediction.backtest_horizon_predictions` | Walk-forward horizon prediction testing | `src/ta_foundation/prediction/backtest_horizon_predictions.py` |
| **Multi-Agent Prediction** | `python -m ta_foundation.prediction.run_multi_agent` | Ensemble daily prediction with fallback | `src/ta_foundation/prediction/run_multi_agent.py` |
| **Promote Strategy** | `python -m ta_foundation.cli.promote_strategy` | Promote candidate to live | `src/ta_foundation/cli/promote_strategy.py` |
| **Register Hypothesis** | `python -m ta_foundation.cli.register_hypothesis` | Register new hypothesis (interactive or CLI) | `src/ta_foundation/cli/register_hypothesis.py` |
| **Bridge Operator** | `python -m ta_foundation.cli.bridge_operator` | Monitor/manage NinjaTrader signal inbox | `src/ta_foundation/cli/bridge_operator.py` |
| **Soak Monitor** | `python -m ta_foundation.cli.soak_monitor` | Monitor live/paper trades for execution health | `src/ta_foundation/cli/soak_monitor.py` |
| **Ledger Summary** | `python -m ta_foundation.research_ledger.cli_summary` | Query research ledger (candidates, runs, families) | `src/ta_foundation/research_ledger/cli_summary.py` |
| **Ledger Next Actions** | `python -m ta_foundation.research_ledger.cli_next_actions` | Show next recommended actions from ledger | `src/ta_foundation/research_ledger/cli_next_actions.py` |
| **Web App** | `python -m ta_foundation.web.app --port 7734` | Interactive workbench (all capabilities) | `src/ta_foundation/web/app.py` |

### Web API Routes

| Route | Method | Purpose |
|---|---|---|
| `GET /` | GET | Workbench index |
| `GET /discovery` | GET | Discovery UI (stateful funnel) |
| `GET /discovery/sessions/<id>/resume` | GET | Resume session via cookie |
| `GET /api/schema` | GET | Template/strategy JSON schema |
| `GET /api/capabilities` | GET | List all runnable workflows |
| `GET /api/discovery/stages` | GET | All 6 discovery stages |
| `POST /api/discovery/stages/<stage>/preview` | POST | Build + validate stage YAML |
| `GET /api/discovery/glossary` | GET | Discovery terminology + definitions |
| `GET /api/discovery/instruments` | GET | Instrument registry (tick sizes, defaults) |
| `GET /api/discovery/sessions` | GET | List all discovery sessions |
| `POST /api/discovery/sessions` | POST | Create new session |
| `GET /api/discovery/sessions/<id>` | GET | Get session state |
| `POST /api/discovery/sessions/<id>` | POST | Update session (context, label, instrument) |
| `DELETE /api/discovery/sessions/<id>` | DELETE | Delete session |
| `POST /api/discovery/sessions/<id>/runs` | POST | Start discovery run for session |
| `POST /api/discovery/sessions/<id>/promote` | POST | Promote session to live |
| 15+ more | ... | Optimizer, prediction, strategy lab routes |

---

## Analysis Subsystems Deep Dive

### 1. MA Anchor Interaction (`analysis/ma_structure/`)

**Problem:** Which moving average pairs explain most of the strategy's profitability?

**Entry Point:** `orchestrator.run_anchor_interaction_analysis(...)`

**Configuration Block:**
```yaml
anchor_interaction:
  enabled: true
  strategy_family: "SMA"
  anchors:
    - family: "SMA"
      length: 20
      source: "close"
    - family: "EMA"
      length: 50
      source: "close"
```

**Modules:**
- `anchors.py` — detect anchor crossings, segments
- `segment_detection.py` — identify market regimes (trending vs choppy)
- `tp_sl_engine.py` — score profit target placement relative to anchor
- `trade_alignment.py` — recommend anchor-relative TP/SL levels
- `regime_context.py` — classify trades by market regime (ADX, ATR-based)

**Output:** `pkg.metadata["derived"]["anchor_interaction"]`
- Anchor crossings per trade
- Segment-level statistics (win rate by anchor pair)
- TP/SL effectiveness analysis
- Recommendations for anchor adjustments

**Report Sections:** 15+ sections covering config, matrices, diagnostics, hourly profiles, TP/SL specs, recommendations

---

### 2. Pattern Engine (`analysis/pattern_engine/`)

**Problem:** Which recurring price patterns explain executed trades? How robust are they?

**Entry Point:** Template sweep via YAML `pattern_engine: { enabled: true }`

**Template Registry:**
- **Built-in families:** ORB (opening range), Breakout, Pullback, Level, Candle, MA, BB, LCR (large candle regions)
- **Template format:** `{family}::{structure}` (e.g., `ORB::orb_break_retest`)
- **Registration:** `builtins.py` → `default_template_registry()`

**Modules:**
- `engine.py` — sweep templates across bars/ticks, collect matches
- `cluster.py` — cluster similar pattern instances by feature similarity
- `monte_carlo.py` — simulate path variations for robustness
- `robustness_cv.py` — cross-validation degradation scoring
- `trade_pattern_audit.py` — match patterns to historical trades

**Output:** Parquet files under `.ta_artifacts/pattern_engine/<run_id>/`
- Artifact references in `pkg.metadata["derived"]["pattern_engine"]["artifacts"]`
- Diagnostics: match frequencies, clustering results, CV scores

**Report Sections:** 10+ sections for overview, diagnostics, per-template results, Monte Carlo analysis

---

### 3. Entry Strategy Discovery (`analysis/entry_strategies/`)

**Problem:** Which entry signals have best risk-adjusted returns? How do they validate walk-forward?

**8 Strategy Families:**
1. **Candle** — candle pattern recognition (supports multi-timeframe via `mtf.py`)
2. **MA** — moving average crossover signals
3. **BB** — Bollinger Band touch/break signals
4. **ORB** — Opening Range Breakout
5. **Breakout** — general breakout patterns (N-bar)
6. **Pullback** — pullback confirmation entries
7. **Level** — support/resistance level entries
8. **LCR** — Left-Center-Right large candle region discovery

**Shared Infrastructure:**
- `_sweep_base.py` — parameter expansion, filtering, combo runner
- `outcome/` — trade simulation (entry fill, exit fill, outcomes)
- `ranking.py` — entry signal ranking (Sharpe, MAE/MFE, trade count)
- `validation.py` — IS/OOS walk-forward validation, degradation scoring

**Per-Family Structure:**
- `<family>/features.py` — compute entry-specific features (candle body %, ATR bands, etc.)
- `<family>/signals.py` — detect signal conditions
- `<family>_sweep.py` — orchestrator for parameter sweep

**Output:** `pkg.metadata["derived"]["strategy_discovery"]`
- Signal ranks (by Sharpe, Sortino, hit rate, PF)
- Entry pattern rules with parameter bounds
- Walk-forward validation results (IS/OOS degradation)
- NinjaTrader C# template generation

**Report Sections:** 30+ sections per family (overview, ranking, rules, combo matrix, decision ledger, template preview)

---

### 4. Strategy Discovery (`analysis/strategy_discovery/`)

**Problem:** Which discovered signals + exit policies create validated trading systems?

**Modules:**
- `orchestrator.py` — main entry point
- `validation.py` — walk-forward validator (rolling/anchored windows)
- `evaluation.py` — performance scoring (Sharpe, Sortino, MAE/MFE, risk metrics)
- `pure_discovery.py` — parameter optimization pipeline
- `entry_pattern_bridge.py` — connect discovered patterns to entry rules
- `nt_template_generator.py` — generate C# NinjaTrader templates
- `pantheon_bot_v2_template.py`, `pantheon_master_template.py` — strategy-specific templates

**Configuration Block:**
```yaml
strategy_discovery:
  enabled: true
  instrument: "NQ"
  contract: "H25"
  timeframe: "5m"
```

**Output:** `pkg.metadata["derived"]["strategy_discovery"]`
- Discovered entry/exit rules with parameter ranges
- Validation results (train/test separation, walk-forward scores)
- Risk-adjusted performance metrics
- Generated NinjaTrader templates (C# code, ready to import)

---

### 5. Regime Recommender (`analysis/regime_recommender/`)

**Problem:** Which market regimes are best for this strategy? When should we trade vs. sit out?

**Modules:**
- `classifier.py` — regime classification (ADX for trend, ATR for volatility)
- `recommender.py` — per-regime recommendations (trade vs. pass)
- `storage.py` — persistence for recommendations

**Output:** `pkg.metadata["derived"]["regime_recommender"]`
- Regime labels (trending up/down/choppy, high/low volatility)
- Per-regime performance (PF, win rate, avg trade duration)
- Recommendations (trade this regime? Avoid that one?)

---

### 6. Large Candle Excursion (`analysis/large_candle_excursion/`)

**Problem:** Do large market moves create tradeable downstream patterns?

**Modules:**
- `event_scanner.py` — identify large candles (ATR thresholds)
- `context_extractor.py` — find context families (which patterns often follow?)
- `findings.py` — rank findings by tradability + signal coverage
- `recursive_search.py` — which candle types predict which others?
- `strategy_construction.py` — build entry rules from findings
- `downstream_reports.py` — render discovery tables

**Output:** Findings with:
- Candle feature that triggered
- Most likely downstream patterns (by frequency)
- Signal coverage (% explained by entry rules)
- Recursion depth (how deep does the pattern chain go?)

---

## Agentic Workflows (Phase A-D)

### Phase A: Research Ledger Foundation

**Storage:** `~/.ta_foundation/research_ledger.sqlite` (default, configurable)

**Entities:**
- **Families** — hypothesis families with param ranges (e.g., "sma_cross_9_21")
- **Hypotheses** — individual pre-registered probes
- **Runs** — execution records (when, duration, error, manifest)
- **Candidates** — classification results (passed/graveyarded/in-research)
- **Journal** — append-only decision log (triage, promotion, rejection)
- **Shadow Signals** — live trade entries/exits captured from execution bridge

**Entry Points:**
- `python -m ta_foundation.research_ledger.cli_summary` — query state
- `python -m ta_foundation.research_ledger.cli_next_actions` — show next steps

---

### Phase B: Read-Only Agents (B.1-B.3)

#### B.1: Triage Analyst
- **Role:** Classify untriaged candidates into terminal/intermediate states
- **States:** {graveyard, research, hardening_queue, shadow, decayed}
- **Logic:** Deterministic classifier (`derive_triage_state`) + LLM scribe
- **LLM:** Claude 3.5 Sonnet with linter (max 2 retries on hallucination)
- **Linting:** Validate scribe citations match actual metrics (numeric claim checks)
- **Output:** `TriagePassReport` (count of HITL-flagged candidates)
- **Entry:** `python -m ta_foundation.agent.cli triage-pass`

#### B.2: Scribe Role
- **Components:**
  - **Post-mortem writer:** 200-8000 char markdown per graveyarded candidate
  - **Weekly letter:** 400-8000 char aggregate weekly narrative
  - **Shadow health:** Daily prose from shadow signals (D.3)
- **Artifacts:** Written to `runs/inbox/{post_mortems, weekly_letters, shadow_health}/`
- **Format:** YAML frontmatter + markdown body
- **Linting:** Citation validation (same as triage)
- **HITL:** Drafts marked `_LINT_FAIL` require manual review
- **Entry:** `python -m ta_foundation.agent.cli daily-pass` (includes scribe)

#### B.3: Inbox System (HITL)
- **Location:** `src/ta_foundation/agent/inbox.py`
- **Operations:**
  - `list_drafts()` → Show all pending artifacts
  - `show_draft(draft_id)` → Display full content
  - `accept_draft(draft_id)` → Move to final, journal
  - `reject_draft(draft_id, reason)` → Mark rejected
- **Artifacts:** Post-mortems, proposals, weekly letters, shadow health
- **Entry:** `python -m ta_foundation.agent.cli inbox`

---

### Phase C: Authoring Agents (C.1-C.2)

#### C.1: Hypothesis Author
- **Role:** Propose new pre-registered hypotheses (with guardrails)
- **Input:** Family whitelist, param ranges, coverage cap (40%)
- **Checks:**
  - Family is whitelisted
  - Parameters in valid ranges
  - No param duplication in active hypotheses
  - No collision with graveyard
  - Coverage cap not exceeded
- **Output:** Proposals JSON (single object with proposals array)
- **Drafts:** `runs/inbox/proposals/<hypothesis_id>.md`
- **Reports:** `AuthorReport` (proposal success/fail per ID)
- **HITL:** `_LINT_FAIL` marker for validation failures
- **Entry:** `python -m ta_foundation.agent.cli authoring-pass`

#### C.2: Sweep Operator
- **Role:** Execute accepted hypotheses (author → discover → ingest)
- **Workflow:**
  1. Author probe YAML from hypothesis spec
  2. Run discovery probe (config hash validation)
  3. Ingest results (compare vs guardrails)
  4. Write manifest + sidecar
- **Guardians:**
  - Config hash validation (prevent config drift)
  - Timeout handling (bail on stuck probes)
  - Result intake validation
- **Output:** Discovery manifest, sidecar JSON, operator report
- **HITL:** Config/result validation failures
- **Entry:** `python -m ta_foundation.agent.cli operator-pass`

---

### Phase D: Forward Observation Agents (D.1-D.3)

#### D.1: Shadow Health Monitor
- **Input:** `ShadowSignal` rows from execution bridge (entry fills, exit fills, live P&L)
- **Computation:** Daily trade stats (win rate, avg trade, max drawdown, Sharpe)
- **Output:** `ShadowHealthReport` (fed into D.3)
- **Entry:** Continuous via shadow signals table

#### D.2: Shadow Trader
- **Role:** Monitor live/paper execution for signal freshness and compliance
- **Entry:** `python -m ta_foundation.cli.soak_monitor`

#### D.3: Scribe Narrative Pass
- **Role:** Write prose narrative from shadow health stats (daily)
- **Artifacts:** `runs/inbox/shadow_health/<YYYY-MM-DD>.md`
- **Format:** YAML frontmatter + markdown narrative
- **Entry:** `python -m ta_foundation.agent.cli shadow-scribe-pass`

---

### Scheduler & HITL Loop

**Location:** `src/ta_foundation/agent/scheduler.py`

**Scheduled Passes:**
- `daily_pass()` — triage → post-mortem (run nightly)
- `weekly_pass()` — weekly letter generation
- `authoring_pass()` — hypothesis author session
- `operator_pass()` — sweep operator execution
- `weekly_authoring_pass()` — authoring + operator combined

**Output:** `CombinedReport` (per-role results + error list)

**HITL Workflow:**
1. Schedule pass runs
2. Agents generate draft artifacts (with validation)
3. Inbox shows `_LINT_FAIL` or other issues
4. Operator reviews + accepts/rejects
5. Accepted artifacts moved to ledger
6. Journal entry logged

---

## Prediction Systems

### Daily Prediction (Claude-Driven)

**Workflow:**
```
After NY close
    ↓
Build multi-timeframe context
    ↓
Call Claude Opus 4.7 (adaptive thinking)
    ↓
Extract structured predictions
    ↓
Validate + persist to JSONL
    ↓  (next session closes)
Measure outcome (direction, level touch, breakout)
    ↓
Score prediction (proper scoring rules)
    ↓
Update calibration (ECE, drift detection)
```

**Configuration:**
```yaml
instrument: NQ
contract: "06-26"
market_data: C:/NinjaTrader/exports
calendar: optional_forexfactory_calendar.csv
model: claude-opus-4-7
n_similar: 5  # historical analogues to include
```

**Context Builder (`context_builder.py`):**
- Multi-timeframe features (1m, 5m, 15m, 1h)
- Current regime (trending, choppy, volatility state)
- Daily pivot levels (classic, Camarilla)
- Economic calendar events
- Historical analogues (K-nearest market patterns)

**Claude Agent (`claude_agent.py`):**
- Model: `claude-opus-4-7` (with extended thinking)
- Structured output (tool-use JSON)
- Predicts: direction, confidence, key levels, breakout probability, trade reasoning

**Prediction Output (`DailyPrediction`):**
- `direction` — bullish/bearish/neutral
- `confidence` — 0.0-1.0
- `key_levels` — support/resistance
- `breakout_probability` — above/below recent range
- `reasoning` — prose explanation
- `timestamp`, `model`, `asof_date`

**Measurement (`outcome_measurer.py`):**
- Actual direction (up/down/chop)
- Efficiency ratio (next-day range movement)
- Level touches (which predicted levels hit?)
- Breakout detection (did breakout occur?)

**Scoring (`scorer.py`):**
- Brier score (direction prediction)
- Composite score = 0.35×direction + 0.25×chop + 0.25×levels + 0.15×breakout
- Penalty for confident incorrect (reward confident correct)

**Calibration (`calibrator.py`):**
- Expected Calibration Error (ECE) per confidence bucket
- Drift detection (confidence decay over rolling windows)
- Update historical analogue pool with new outcomes

**Storage:** `.ta_artifacts/predictions/<instrument>/<contract>/predictions.jsonl`

**Measurement Entry:**
```python
from ta_foundation.prediction.orchestrator import measure_and_learn
outcomes = measure_and_learn(
    bars_next_day=bars,
    store=store,
    target_date="2025-03-22",
    prior_atr=85.0
)
```

---

### Horizon Prediction (Multi-Agent Ensemble)

**Workflow:**
```
Load bars (minute-level)
    ↓
For each bar at horizon H (5min, 15min, 1h):
    ├─ Compute 4-dim feature vector (trend, vol, session, hour)
    ├─ Call registered agents (statistical, analogue, ensemble, etc.)
    ├─ Collect probability predictions
    └─ Compute tradable zones
         │
         ├─ Kelly sizing
         ├─ Risk of ruin
         └─ Decision signal (long/short/pass)
        ↓
Persist predictions to JSONL (batch)
```

**Configuration (`prediction.yaml`):**
```yaml
instruments:
  - symbol: NQ
    contract: "06-26"
    timeframes: [5m, 15m]

horizons:
  - bars: 3
  - bars: 5

agents:
  - name: statistical_baseline
    enabled: true
  - name: analogue_probability
    enabled: true
  - name: ensemble
    members: [statistical_baseline, analogue_probability]

costs:
  tick_value: 20
  commissions: 10
  slippage: 0.5

tradable_zones:
  kelly_fraction: 0.25
  max_leverage: 1.5
```

**Built-in Agents:**

1. **StatisticalProbabilityAgent** (`horizon_probability.py`)
   - Baseline: conditional frequency (% up when prior bars were up)
   - Empirical Bayes smoothing
   - Fallback for low-count bins

2. **AnalogueProbabilityAgent** (`analogue_probability_agent.py`)
   - KNN on 4-dim feature space:
     - Trend (EMA slope)
     - Volatility (rolling ATR)
     - Session (RTH/ONH/premarket)
     - Hour of day
   - Distance-weighted K=5 neighbors
   - Smooth probabilities via weighted aggregation

3. **RegimeSpecialist** (`horizon_specialists.py`)
   - Analogue agent locked to current market regime
   - E.g., "only use K nearest when regime is trending"

4. **SessionSpecialist** (`horizon_specialists.py`)
   - Analogue agent locked to current trading session
   - E.g., "only use NY open patterns for next 1h"

5. **EnsembleHorizonAgent** (`horizon_ensemble.py`)
   - Combines registered members
   - Stacking weights learned from rolling composite scores
   - Per-bucket weights (different combos may be best in different regimes)
   - Soft fallback (if member abstains, scale weights)

**Tradable Zone Calculation (`horizon_tradable_zone.py`):**
- Converts probability → trade signal
- Kelly fraction sizing: `position_size = kelly_fraction × (2×prob - 1) / odds`
- Risk of ruin calculation
- Max leverage guard

**Output (`CandleHorizonPrediction`):**
- `bullish_prob`, `bearish_prob`, `neutral_prob`
- Return distribution (percentiles)
- Path statistics (max intrabar move)
- Threshold hits (% of paths hitting price targets)
- Tradable zone recommendation

**Measurement (`horizon_outcome_measurer.py`):**
- Actual direction (up/down/chop)
- Return magnitude
- MAE (max adverse excursion)
- MFE (max favorable excursion)
- Threshold hits (which predicted levels touched?)

**Scoring (`horizon_scorer.py`):**
- Brier score (direction)
- MAE/MFE scoring
- Threshold accuracy bonus
- Composite score (weighted combination)

**Calibration (`horizon_calibrator.py`):**
- Per-confidence-bucket ECE
- Per-regime ECE
- Per-session ECE
- Reliability diagrams (predicted vs actual frequency)

**Reporting (`horizon_reports.py`):**
- Agent leaderboard (by timeframe/horizon combo)
- Horizon matrix (performance by horizon length)
- Session matrix (performance by trading session)
- Edge cells (where is the edge? which regimes/times?)
- Calibration report (ECE, reliability curves, drift)

**Storage:** `.ta_artifacts/horizon/<instrument>_<contract>/`
- `horizon_predictions.jsonl` — all predictions
- `horizon_outcomes.jsonl` — all measured outcomes
- `stacking_weights.json` — learned ensemble weights per bucket
- `calibration/` — ECE reports, reliability diagrams

**Walk-Forward Backtest:**
```bash
python -m ta_foundation.prediction.backtest_horizon_predictions \
  --minute-bars-file "C:/path/to/NQ 06-26.Last.txt" \
  --store-dir .ta_artifacts/horizon \
  --timeframes 5m,15m \
  --horizons 3,5 \
  --asof-warmup 200 \
  --asof-stride 10 \
  --print-report
```

---

## NinjaTrader Integration

### Execution Bridge (`strategies/TaFoundationExecutionBridge/`)

**Components:**
1. **TaFoundationExecutionShell.cs** — NinjaScript strategy that polls inbox for signals
2. **TaFoundationMinuteBarExporter.cs** — NinjaScript indicator that exports market data
3. **bridge_sender.py** — Python client for authoring signals
4. **execution_runtime_client.py** — Runtime interaction (status polling)
5. **Test harness** — Phase 1-2 acceptance specs with evidence bundles

**Signal Protocol:**

Signal message (JSON, written to inbox folder):
```json
{
  "message_id": "uuid",
  "timestamp_utc": "2026-05-24T14:30:00Z",
  "signal_type": "enter_long",
  "template_name": "reversal_template",
  "parameters": {
    "entry_price": 5450.00,
    "stop_price": 5440.00,
    "target_price": 5470.00,
    "quantity": 1
  }
}
```

**Supported Actions:**
- `enter_long` — initiate long position
- `enter_short` — initiate short position
- `move_stop` — adjust stop loss
- `take_partial` — scale out of position
- `exit_all` — close position
- `heartbeat` — keep-alive signal (no trade action)

**Lifecycle:**
1. Python writes JSON to `inbox/` folder
2. NT shell polls (configurable interval, default 1s)
3. Shell parses message, executes order
4. Shell writes to `archive/` (success) or `rejected/` (error)
5. Processed-IDs prevent duplicate fills

**Safety Features:**
- Heartbeat timeout (signal stale after N seconds)
- Processed-ID deduplication
- Contract validation (prevent wrong instrument)
- Quantity guards (max order size)

**Monitoring (`cli/soak_monitor.py`):**
- Parse shell logs for health events
- Monitor outbox files for execution status
- Alert on signal processing delays
- Track position state (filled, partial, rejected)

---

### Autonomous Strategy Loop (`nt_strategy_loop/`)

**Full Workflow:**

```
StrategySpec (JSON)
    ↓
Author .cs file (from family template)
    ↓
Install into NinjaTrader
    ↓
Observe compile (via AddOn IPC)
    ├─ Compile clean → next
    └─ Compile error → repair
        ├─ Deterministic repair (class name, using directives)
        │   └─ Compile again
        └─ If still error + --repair-llm:
            ├─ Call Ollama model
            ├─ Extract corrected source
            └─ Compile again
                └─ If still error → halt (stop reason: repair_declined)
        ↓
Compile clean
    ↓
Generate seed template (from spec → Strategy Analyzer format)
    ↓
Run optimizer batch
    ├─ NT Strategy Analyzer runs RunBatch
    └─ Produces `*_Optimization.csv`
        ↓
Ingest results
    ├─ Parse param combinations
    ├─ Compute PF, Sharpe, drawdown, etc.
    └─ Score vs guardrails
        ├─ PF ≥ 1.5, DD ≤ 2500, trades ≥ 20 → candidate (archive for next pass)
        └─ else → reject
        ↓
Record decision
    └─ Write to decisions/ folder (append-only session)
```

**Entry Points:**

1. **ensure-nt-ready** — Startup/auth/wait
   ```powershell
   python -m ta_foundation.nt_strategy_loop.cli ensure-nt-ready [--restart]
   ```

2. **observe-compile** — Lowest-level building block
   ```powershell
   python -m ta_foundation.nt_strategy_loop.cli observe-compile \
     --source "D:\path\MyStrategy.cs" \
     --strategy-name MyStrategy \
     --out result.json
   ```

3. **repair-loop** — Author → install → observe → repair loop
   ```powershell
   python -m ta_foundation.nt_strategy_loop.cli repair-loop \
     --spec my_strategy.json \
     --max-repair-attempts 5
   ```

4. **optimizer-bridge** — Seed template → optimizer run → ingest
   ```powershell
   python -m ta_foundation.nt_strategy_loop.cli optimizer-bridge \
     --session-dir ".ta_artifacts\nt_strategy_lab\sessions\<id>" \
     --compile-clean-source "<id>\compile_clean\MyStrategy.cs"
   ```

5. **full-loop** — repair-loop + optimizer-bridge end to end
   ```powershell
   python -m ta_foundation.nt_strategy_loop.cli full-loop \
     --spec my_strategy.json \
     --instrument "NQ 06-26" \
     --max-drawdown 2500 \
     --min-trades 10 \
     --min-profit-factor 1.5
   ```

**StrategySpec Format:**
```json
{
  "strategy_name": "MyCrossBot",
  "family": "sma_cross",
  "intent": "9/21 SMA cross with fixed stops",
  "parameters": {
    "FastPeriod": 9,
    "SlowPeriod": 21,
    "ProfitTargetTicks": 24,
    "StopLossTicks": 16
  },
  "risk_note": "Backtest only — not cleared for live"
}
```

**Repair Policy:**

Deterministic repair (always first):
- Fix class name ↔ filename mismatch
- Add missing `using` directives
- Correct syntax from error message

LLM repair (optional, with --repair-llm):
- Call local Ollama model (e.g., qwen3-coder:30b)
- Feed: spec, current source, compiler errors
- Extract corrected `.cs` from response
- Sanity-check (must look like NinjaScript)
- If invalid or unreachable → decline

Stop reasons:
- `compile_clean` — success
- `max_attempts` — exceeded repair attempts
- `repeated_signature` — same error appears twice
- `repair_declined` — both heuristics and LLM declined
- `peer_compile_block` — another strategy is compiling
- `stale_assembly` — NinjaTrader assembly cache issue
- `worker_error` — AddOn communication failure

**Session Folder Structure:**
```
.ta_artifacts/nt_strategy_lab/sessions/<session_id>/
├── session.json               # Metadata (strategy name, family, created_at)
├── strategy_spec.json         # Original spec
├── attempts/                  # Compile attempts (numbered)
│   ├── 00_initial.cs
│   ├── 00_compile_result.json
│   ├── 01_repaired.cs
│   └── 01_compile_result.json
├── compile_clean/             # Final clean compile
│   └── MyStrategy.cs
├── optimizer/                 # Optimizer results
│   ├── seed_template.xml
│   ├── run_batch_config.json
│   └── MyStrategy_Optimization.csv
└── decisions/                 # Candidate decisions (append-only)
    └── <candidate_id>.json    # { verdict: "archive" | "reject", score, reason }
```

---

## Configuration & Registry Systems

### Parser Registry (`core/registry.py`)

**Built-in Parsers (7 formats):**
1. `NinjaTraderTradesCsvParser` — `*_Trades.csv`
2. `NinjaTraderDailyAnalysisCsvParser` — `*_Analysis.csv`
3. `NinjaTraderSummaryCsvParser` — `*_Summary.csv` / `*_Summery.csv`
4. `NinjaTraderSettingsCsvParser` — settings
5. `NinjaTraderOptimizationCsvParser` — `*_Optimization.csv` (routes to OptimizationStore)
6. `MinuteBarsLastTxtParser` — minute bars (routes to MarketDataStore)
7. `TickLastTxtParser` — tick data (routes to MarketDataStore)

**Extension:**
```python
from ta_foundation.core.registry import ParserRegistry
registry = ParserRegistry()
registry.register(MyCustomParser())
```

---

### Pattern Template Registry (`analysis/pattern_engine/templates/`)

**Built-in Families:**
- ORB (opening range breakout)
- Breakout (N-bar)
- Pullback
- Level (support/resistance)
- Candle (pattern-based)
- MA (moving average)
- BB (Bollinger Band)
- LCR (large candle region)

**Registration:**
```python
# in builtins.py
def default_template_registry():
    registry = {}
    registry["ORB::orb_break_retest"] = OrbBreakRetestTemplate(...)
    return registry
```

---

### Indicator Registry (`analysis/indicators/registry.py`)

**Available Indicators:**
- SMA, EMA, DEMA, TEMA
- ADX, ATR
- Bollinger Bands (20/2)
- RSI, Stoch
- VWAP
- Custom composites (trend detector, vol percentile)

---

### Discovery Registry (`discovery_registry/`)

**Probe Registry (`_probe_registry.json`):**
- Append-only record of every probe ever run
- Used for Bonferroni cross-probe correction

**Graveyard Registry (`_graveyard_registry.json`):**
- Entry for every rejected hypothesis (PF < 1.0 broad or hardening_passed=False)
- Includes: hash, reason, families, instrument, stress_failure, override history

**Refusal System (`refusal.py`):**
- Pre-run check: prevent redundant probes
- Matching modes:
  - `exact_hash` — identical probe
  - `near_match` — Jaccard 80%+ overlap on params + outcomes
- Returns: `RegistryHit` with explain() narrative

---

### Family Registry (`research_ledger/family_registry.py`)

**Registration:**
```python
register_family(
    name="sma_cross",
    legitimate_params_json={
        "fast_period": {"min": 3, "max": 50},
        "slow_period": {"min": 10, "max": 200}
    },
    mechanism_template="<automated by SMA crossing>"
)
```

**Whitelist Check:** Authors can only propose hypotheses from registered families

---

### Instrument Registry (`web/discovery_instruments.py`)

**Default Instruments:**
- NQ (E-mini Nasdaq), NZH (micro Nasdaq)
- ES (E-mini S&P 500), MES (micro S&P)
- GC (Gold), SI (Silver)
- CL (Crude Oil), NG (Natural Gas)
- etc.

**Metadata per instrument:**
- Tick size (0.01 for equities, 0.05 for FX, etc.)
- Tick value ($20 for NQ, etc.)
- Point value
- RTH session defaults (9:30-16:00 ET)
- Premarket/afterhours ranges

**Customization:**
```python
from ta_foundation.web.discovery_instruments import register_custom_instrument
register_custom_instrument(
    symbol="MyFuture",
    tick_size=0.05,
    tick_value=50,
    rth_start=09_30,
    rth_end=16_00
)
```

---

## Data Flow & Architecture

### Ingest Flow

```
CLI Arguments (--input, --market-data, --recursive, --run-id-regex)
    ↓
ParserRegistry.discover(input_folder)
    ├─ Match files to parsers by name/header
    ├─ Group into runs by run_id
    └─ Collect artifacts (trades, daily, summary, settings, optimization, market)
        ↓
Pipeline (core/pipeline.py)
    ├─ For each run_id:
    │   └─ Create AnalysisPackage
    │       ├─ Load trades, daily, summary, settings
    │       ├─ Attach assets (images)
    │       ├─ Compute derived metrics (daily outcomes, trade profile, exec ratio)
    │       ├─ Attach metadata["derived"][...]
    │       └─ Add warnings
    │
    ├─ For artifacts with run_id=None:
    │   └─ Route to MarketDataStore (shared)
    │       ├─ Minute bars
    │       ├─ Tick data (optional, cacheable)
    │       └─ Resample to requested timeframes
    │
    └─ For *_Optimization.csv:
        └─ Route to OptimizationStore (not AnalysisPackage)
            ├─ Parse parameter format
            ├─ Create OptimizationBatch
            └─ Store by batch_id
                ↓
Analysis Pipeline (orchestration in cli/main.py)
    ├─ If anchor_interaction enabled:
    │   └─ orchestrator.run_anchor_interaction_analysis(packages, market)
    │       └─ Attach to pkg.metadata["derived"]["anchor_interaction"]
    │
    ├─ If pattern_engine enabled:
    │   └─ engine.sweep(packages, market, templates)
    │       └─ Write parquet → .ta_artifacts/pattern_engine/<run_id>/
    │       └─ Attach artifact refs to metadata["derived"]["pattern_engine"]
    │
    ├─ If strategy_discovery enabled:
    │   └─ orchestrator.run(packages, market, config)
    │       └─ Attach to metadata["derived"]["strategy_discovery"]
    │
    ├─ If regime_recommender enabled:
    │   └─ orchestrator.run(packages, market)
    │       └─ Attach to metadata["derived"]["regime_recommender"]
    │
    └─ All derived data is JSON-safe (no DataFrames, callables, registries)
                ↓
Report Rendering (reports/html/builder.py)
    ├─ Load YAML config
    ├─ For each requested section:
    │   ├─ Look up in SECTION_REGISTRY
    │   ├─ Call render_section(ctx)
    │   │   ├─ ctx["packages"] = {run_id: AnalysisPackage, ...}
    │   │   ├─ ctx["market"] = MarketDataStore
    │   │   ├─ ctx["options"] = section-local YAML options
    │   │   ├─ ctx["all_options"] = full merged YAML
    │   │   └─ return HTML string
    │   └─ No IO, no ingest, no YAML parsing allowed in section
    │
    ├─ Embed images as base64
    ├─ Combine HTML into single document
    └─ Write to <output>/<report_filename>.html
        ↓
Manifests & Artifacts
    ├─ manifest.json — parse statistics, hashes, warnings
    ├─ unparsed_files.txt — files not matched by parser
    ├─ optional .png cards (if --export-exec-cards-png)
    └─ .ta_artifacts/pattern_engine/<run_id>/*.parquet
```

### Prediction Flow

#### Daily
```
Market data (minute bars) + Prior day context
    ↓
context_builder.build_prediction_context(...)
    ├─ Multi-TF features (1m, 5m, 15m, 1h)
    ├─ Regime classification
    ├─ Daily pivot levels
    ├─ Economic calendar
    └─ Historical analogues (K-NN match)
        ↓
claude_agent.predict(context)
    ├─ Model: claude-opus-4-7
    ├─ Extended thinking enabled
    └─ Return: DailyPrediction (direction, confidence, levels, reasoning)
        ↓
orchestrator.persist(prediction)
    └─ Store to .ta_artifacts/predictions/<instrument>/<contract>/predictions.jsonl
        ↓  (next session closes)
outcome_measurer.measure(bars_next_day)
    ├─ Detect actual direction, level touches, breakout
    └─ Return: PredictionOutcome
        ↓
scorer.score(prediction, outcome)
    ├─ Brier, Chop, Levels, Breakout components
    └─ Composite score
        ↓
calibrator.update_outcomes(outcomes, store)
    ├─ Compute ECE per bucket
    ├─ Detect drift
    └─ Update analogue pool for next predictions
```

#### Horizon
```
Minute bars (loaded in advance)
    ↓
For each bar (at horizon H: 5m, 15m, 1h, etc.):
    ├─ Compute feature vector (4 dims)
    ├─ Call each registered agent:
    │   ├─ Statistical baseline (conditional frequency)
    │   ├─ Analogue probability (KNN)
    │   ├─ Regime specialist (filtered analogue)
    │   ├─ Session specialist (filtered analogue)
    │   └─ Ensemble (weighted combination)
    │
    ├─ Collect probabilities (bullish, bearish, neutral)
    ├─ Compute tradable zone (Kelly, max leverage)
    └─ Output: CandleHorizonPrediction
        ↓
Batch persist to .ta_artifacts/horizon/<contract>/predictions.jsonl
    ↓  (walk-forward: each asof point)
measure_horizon_outcome(bars, asof_idx)
    ├─ Detect direction, return, MAE/MFE
    └─ Output: CandleHorizonOutcome
        ↓
horizon_scorer.score(prediction, outcome)
    ├─ Per-threshold scoring
    ├─ Composite score
    └─ Per-bucket calibration
        ↓
Aggregate calibration
    ├─ ECE per confidence bucket
    ├─ Drift detection
    └─ Update ensemble stacking weights for next batch
```

---

## Hidden Capabilities

### 1. **Conditional Promotion System**
- **Location:** `web/conditional_promotion.py`
- **Purpose:** Generate next-probe YAML based on candidate metrics
- **Example:** "If PF > 1.3 and IS/OOS degradation < 15%, expand TP sweep for next probe"

### 2. **Multi-Model AI Handoff Queue**
- **Location:** `docs/handoffs/`
- **Purpose:** Delegate tasks to Codex, Gemini, Grok (with Claude as PM)
- **Workflow:** Claude writes spec → Operator hands off → Executor runs → Verify → Status flip
- **Specs:** Self-contained, no external context

### 3. **NinjaTrader Compiler & Repair Loop**
- Deterministic heuristics (class name, using directives)
- Optional LLM repair (Ollama, fail-soft)
- Stop reasons tracking (compile_clean, max_attempts, repair_declined, etc.)

### 4. **Strategy Lab (Autonomous StrategyLoop)**
- Append-only session folders
- Decisions folder (candidate/archive/reject) with scoring
- Compile clean, optimizer, and attempt history

### 5. **Discovery Sessions (Stateful)**
- Persistent across browser restarts (cookie-based)
- Current stage, context, label, instrument tracking
- Can resume mid-funnel

### 6. **Optimizer Decision Dashboard**
- Flag-adjusted ranking (composite scores with guardrail filters)
- Candidate recommendations based on neighborhood stability

### 7. **Proper Scoring Rules (Daily)**
- Multi-component scoring (direction, chop, levels, breakout)
- Composite weight: 0.35×trend + 0.25×chop + 0.25×levels + 0.15×breakout
- Rewards confident correct, penalizes confident wrong

### 8. **ECE Calibration & Drift Detection**
- Per-confidence-bucket expected calibration error
- Rolling window drift (when confidence edge decays)
- Reliability diagrams

### 9. **Large Candle Excursion Analysis**
- Identify macro order-flow events
- Find downstream patterns (which patterns follow?)
- Signal coverage metrics
- Recursive search (candle → candle chains)

### 10. **Market Data Dashboard**
- Separate tool: `market_data_dashboard.py`
- Inspect file freshness and availability
- Contract sorting, age tracking

---

## External Integrations

### 0. **External sibling repos (the ecosystem)** — see `docs/reference/EXTERNAL_PROJECTS_MAP.md`
This project is one repo in a multi-repo effort. Before building, check the ecosystem map:
- `D:\local-deep-research` — online research agent; **already wired** via `research_intake/ldr.py`.
- `D:\NinjatraderDocScrapper` — NinjaScript **strategy factory** + learning RAG (discovered edge → `.cs`).
- `D:\NinjaAccountManager` — real-time NT account monitor + order API.
- `D:\DailyAnalysis` — rule-based NQ daily market context.
- `D:\agentic-engine` — idea→hypothesis→decision validation ledger.

### 1. **D:\NinjaAccountManager**
- Real-time NT8 account monitor (balance/equity/margin/PnL, positions, orders) + order-submission API.
- Connects to NT via **WebSocket/JSON-lines** (`ws://127.0.0.1:8765`) + strategy API tcp `:8766` —
  **not** NT plugin hooks. Has an unused `daily_lockout` flag; lacks per-firm drawdown rules (the gap
  the existing `analysis/prop_evaluation` DD math would fill once wired).

### 2. **NinjaTrader 8**
- AddOn: `BatchStrategyOptimizerAddOn`
- Exposes: `ObserveCompile` (IPC), `RunBatch` (optimizer launch)
- Requires: logged-in, AddOn authorized, NT running
- Talks to via: `bridge_operator`, `nt_strategy_loop`

### 3. **Ollama (Optional)**
- Local LLM for code repair
- Model: `qwen3-coder:30b` (or similar)
- URL: `http://localhost:11434` (configurable)
- Fail-soft: if unreachable or invalid output, decline repair

### 4. **Anthropic API**
- Daily prediction: `claude-opus-4-7` with extended thinking
- Multi-agent fallback: `claude-sonnet-4-6`
- Requires: `ANTHROPIC_API_KEY` env var
- Cost-tracked per prediction

### 5. **Economic Calendar (Optional)**
- Forex Factory CSV (if provided)
- Used in daily prediction context
- Improves economic event near/far classification

---

## Gaps, Known Issues & Cleanup (refreshed 2026-06-05)

**Current verified gaps + the full cleanup register live in
`docs/audits/capability_and_cleanup_audit_2026-06-05.md`.** Summary: the genuine gaps are small —
(1) a daily-lineup *selection surface* over `prediction/` + the deployment-matrix pool (the engine
exists; the picking UI/logic does not); (2) a versioned **APEX DD profile** wiring the existing
`prop_evaluation` math to `NinjaAccountManager` live account state; (3) discoverability hygiene so
existing capability stops getting rebuilt. Known code cleanup: duplicate registry key, dead
`plots/`/`agent/graph.py`/`monte_carlo.py`, 22MB checked-in `node_modules`, ~25 stray root `*.yaml`.

> The original May-24 "documentation wishlist" is retained below for history; several of its items
> are now done (e.g. a capability catalog exists at `docs/CAPABILITY_CATALOG.md`).

### Critical Documentation Gaps

1. **Complete Web API Reference**
   - All 20+ Flask routes not documented
   - Request/response shapes missing
   - OpenAPI spec would help

2. **Report Sections Catalog**
   - 120+ sections exist but not indexed
   - No section-by-section guide with examples
   - Discovery of available sections is trial-by-error

3. **Data Model Examples**
   - SummaryBlock structure (kpis_all, kpis_long, kpis_short)
   - metadata["derived"] shapes for each subsystem
   - JSON examples missing

4. **Analysis Subsystem READMEs**
   - ma_structure/, pattern_engine/, entry_strategies/ lack local docs
   - Entry points are clear; logic is opaque
   - Problem statements not articulated

5. **Configuration Schema**
   - YAML structure for all feature blocks not formally specified
   - Pydantic models exist but not documented
   - No JSON Schema or example YAML per feature

6. **Testing Patterns**
   - How to write tests for new analysis modules
   - Fixture patterns, mock strategies missing
   - CI/CD expectations unclear

7. **CONTRIBUTING.md**
   - No first-day checklist for new developers
   - How to add: new parsers, analysis modules, report sections, entry strategy families
   - Code review expectations missing

8. **Handoff System Integration**
   - `docs/handoffs/` is underdocumented
   - How to write specs, verify, close loop (OPERATOR_GUIDE.md helps, but incomplete)
   - Multi-model coordination workflow unclear

### Areas Needing Clarification

1. **Agentic Workflow HITL Loop**
   - Exact approval flow for inbox drafts
   - Fallback behavior when inbox decisions are delayed
   - Interaction with research ledger (journal entries)

2. **Phase D (Forward Observation)**
   - Shadow health → scribe prose pipeline
   - Real-time shadow signal capture from execution bridge
   - Integration with decision ledger

3. **Prediction Store Lifecycle**
   - How to backfill predictions (--asof)
   - Handling duplicate prediction IDs (idempotency)
   - Store maintenance (cleanup, archival)

4. **Performance Tuning**
   - Pattern engine scaling (1M+ templates on large datasets)
   - Tick cache memory footprint
   - Report rendering bottlenecks (120+ sections)

5. **Error Recovery**
   - What to do if nt_strategy_loop halts mid-repair
   - Resume repair-loop from specific attempt
   - Rollback strategy if optimizer fails

6. **Multi-Instrument Workflows**
   - How to run discovery across multiple contracts (NQ H25 vs M25)
   - Shared market data vs per-contract storage
   - Batch report generation

### Undocumented or Under-Explained Features

1. **Graveyard Refusal Registry** (`discovery_registry/`)
   - Jaccard matching logic (exact percentages)
   - How override_history works
   - Bonferroni correction application

2. **Session Stateful Storage** (`web/discovery_session.py`)
   - Persistence layer (SQLite? JSON? Filesystem?)
   - Concurrent session handling
   - Lock semantics

3. **Stacking Weights Learning** (`horizon_ensemble.py`)
   - How weights are computed from rolling scores
   - Update frequency (per batch? per day?)
   - Cold-start initialization

4. **LLM Repair Prompt Template** (`nt_strategy_loop/repair.py`)
   - Exact prompt sent to Ollama
   - Response parsing logic
   - Validation heuristics

5. **Trade Pattern Audit** (`pattern_engine/trade_pattern_audit.py`)
   - How historical trades are matched to patterns
   - Ambiguity resolution (multiple patterns match)
   - Coverage scoring

---

## Recommendations for Documentation

### Immediate (This Week)

1. **COMPLETE_CAPABILITIES_MATRIX.md**
   - One-page table: capability | entry point | input | output | use case
   - Helps users find the right tool fast

2. **REPORT_SECTIONS_CATALOG.md**
   - 120+ sections listed with 1-line description
   - Grouped by category (core, discovery, optimization, prediction, etc.)
   - Links to YAML option keys

3. **WEB_API_REFERENCE.md**
   - All 20+ Flask routes with request/response shapes
   - cURL examples
   - Error codes

4. **QUICK_START_BY_USE_CASE.md**
   - "I want to..." → here's the command/config
   - 10-15 common use cases with end-to-end examples

### Short-term (Next 2 Weeks)

5. **Analysis Subsystem READMEs**
   - `src/ta_foundation/analysis/ma_structure/README.md`
   - `src/ta_foundation/analysis/pattern_engine/README.md`
   - `src/ta_foundation/analysis/entry_strategies/README.md`
   - Problem statement, entry point, key modules, output shape

6. **Data Model Schema.md**
   - JSON examples for SummaryBlock, AnalysisPackage metadata, OptimizationBatch
   - Shape of each metadata["derived"] key (anchor_interaction, pattern_engine, etc.)
   - JSON Schema or Pydantic model reference

7. **TESTING.md**
   - How to write tests for new modules
   - Fixture setup (create mock AnalysisPackage, MarketDataStore, etc.)
   - Common test patterns

8. **CONTRIBUTING.md**
   - First-day checklist
   - How to add new: parser, analysis module, report section, entry strategy family
   - Code review expectations, style guide

### Medium-term (Next Month)

9. **Agentic Workflow HITL Runbook**
   - Step-by-step workflow from hypothesis authoring → triage → promotion
   - Inbox review patterns
   - Decision tree for triage classifications

10. **NinjaTrader Integration Guide**
   - AddOn setup and authorization
   - Repair loop troubleshooting
   - Optimizer seed template format

11. **Prediction System Tuning Guide**
   - Calibration interpretation
   - Drift detection and response
   - Agent selection per regime/session

12. **Handoff & Multi-Model Coordination**
   - How to write a spec for Codex/Gemini/Grok
   - Verify + close loop workflow
   - Common spec patterns

---

## Conclusion

The **ta_foundation** project is a production-grade, multi-layered analytics and autonomy platform with:

- **10 major capabilities** (report generation, discovery, prediction, NinjaTrader loop, agentic research, execution bridge, pattern engine, large candle analysis, optimization, web workbench)
- **5-phase agentic workflow** (research ledger, read-only agents, authoring agents, forward observation, future expansion)
- **2 prediction systems** (daily Claude + multi-horizon ensemble)
- **12+ CLI entry points** + 20+ web API routes
- **120+ report sections** with YAML-driven configuration
- **Rigorous architectural contracts** (4-layer pipeline, timezone safety, JSON metadata, pure sections)
- **Persistent research ledger** with SQLite backend
- **NinjaTrader integration** with deterministic repair and autonomous optimization loop
- **Multi-model AI coordination** (Claude PM + Codex/Gemini executors)

**Verified conclusion (2026-06-05):** the system is **more built than it is documented** — the
2026-06-05 audit found the agentic loop, prediction engine, prop-account DD model, and execution
bridge all shipped and tested, contradicting the impression of half-finished scaffolding. The real
risk is **not missing capability but discoverability**: capabilities (internal and in the 5 sibling
repos) keep getting rebuilt because they aren't surfaced. The fixes are this verified map,
`docs/CAPABILITY_CATALOG.md`, `docs/reference/EXTERNAL_PROJECTS_MAP.md`, and the cleanup register in
`docs/audits/capability_and_cleanup_audit_2026-06-05.md` — not another round of feature building.
