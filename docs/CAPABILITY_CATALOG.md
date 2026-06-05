# Capability Catalog

Compact PM/agent routing table — **load this first** to know what exists before
proposing new work. One line per capability: purpose · entry point · status ·
canonical doc. Deeper detail lives in the linked docs and `docs/AI_REPO_INDEX.md`
(generated file index) / `docs/AI_CAPABILITY_MAP.md` (narrative). Keep this file
small; it is meant to be cheap to load every session.

## Product capabilities

| Capability | Purpose | Entry point | Status | Canonical doc |
|---|---|---|---|---|
| Backtest report generation | Ingest NinjaTrader exports → YAML-selected HTML report sections | `python -m ta_foundation.cli.main` | Shipped | `CLAUDE.md`, `docs/GETTING_STARTED.md` |
| Strategy discovery | Run ranking, entry/filter/exit discovery, NT template report sections | `cli.main` + `strategy_discovery:` YAML | Shipped | `docs/designs/` (discovery), `CLAUDE.md` |
| Prediction (daily + horizon) | Daily/horizon prediction jobs, scoring, horizon stores | `ta_foundation.prediction.*` | Shipped | (prediction docs) |
| Strategy template building | Build/edit a strategy template + local template backtests | `analysis.strategy_composer.*`, web `/api/generate` | Shipped | `docs/AI_CAPABILITY_MAP.md` |
| NT Optimizer (recipe/web) | Session setup → preflight → RunBatch → phase 2/3 → final backtests → decision package; promote & run; row refine; walk-forward; shadow; robustness | web `/optimizer`, `optimization/*`, `web/optimizer_*` | Shipped | `docs/runbooks/pantheon_web_optimizer_full_run.md`, `docs/designs/ninjatrader_optimizer_web_ui.md` |
| Weekly Coverage | Lane-grid package; **3-stage auto-chain** (broad search → risk-knob refine → final validation), per-bucket count, Fit-ranges cost helper, package + reports | web `/optimizer/weekly-coverage`, `web/optimizer_weekly_coverage_package.py` | Shipped (auto-chain added 2026-06-04) | `docs/runbooks/weekly_optimization_and_reports_guide.md`, `docs/runbooks/weekly_coverage_package_user_guide.md` |
| Deployment Matrix (252) | Fixed 252 named templates (7 session × 2 single/multi × 9 tier × 2 god/monster) for the daily-prediction pool | web `/optimizer/deployment-matrix`, `web/optimizer_deployment_matrix*.py` (+ bundle-axis in `optimizer_recipe*.py`) | **Engine + manifest + launcher done; validated live on NT (smoke); names via canonical `template_naming`** | `docs/designs/deployment_matrix_252_capability.md` |
| Agentic NT strategy loop | Promote research → deterministic NT validation, optimizer runs, shadow, supervised execution | `agent/`, `nt_strategy_loop/` | Partial | `docs/designs/agentic_nt_strategy_knowledge_base.md` |
| Execution bridge | Send/monitor execution messages to NinjaTrader runtime/shell | `strategies/TaFoundationExecutionBridge/*`, `cli/bridge_operator.py` | Shipped | `docs/runbooks/NINJATRADER_INTEGRATION_RUNBOOK.md` |
| Market data store / scan | Shared minute/tick bars; file freshness dashboard | `marketdata/*`, `market_data_dashboard.py` | Shipped | `CLAUDE.md` |

## Analysis subsystems (used by reports/discovery)

| Subsystem | Purpose | Code |
|---|---|---|
| MA anchor interaction | MA anchor detection + TP/SL scoring | `analysis/ma_structure/` |
| Pattern engine | Template sweep, clustering, Monte Carlo | `analysis/pattern_engine/` |
| Entry strategies | 8 families (candle/ma/bb/orb/breakout/pullback/level/lcr) | `analysis/entry_strategies/` |
| Regime recommender | Regime classification + recommendations | `analysis/regime_recommender/` |

## Web workbench section ids (`web/capabilities.py`)

`core_comparison_report`, `optimization_overview`, `anchor_interaction`,
`pattern_engine`, `strategy_discovery`, `regime_recommender`, `daily_prediction`,
`horizon_prediction`, `market_data_scan`, `strategy_composer`, `deployment_boards`,
`trade_diagnostics`, `leaderboards`, `large_candle_excursion`.

## External sibling projects (separate D:\ repos — part of the same effort)

**Check `docs/reference/EXTERNAL_PROJECTS_MAP.md` before building** — much of the pipeline lives here.

| Project | Purpose | Status |
|---|---|---|
| `D:\local-deep-research` | Online research agent (LangGraph, 30+ engines) → edge/rules research; already wired via `research_intake/ldr.py` | Production |
| `D:\NinjatraderDocScrapper` | NinjaScript **strategy factory** + learning RAG: discovered edge → compilable `.cs` + repair + parity | Working (likely supersedes `StrategyDiscoveryFilter.cs`) |
| `D:\NinjaAccountManager` | Real-time NT account monitor + order API (WebSocket bridge, not plugin); has account state, **lacks DD/prop rules** | Working, early |
| `D:\DailyAnalysis` | Rule-based NQ daily context (bias/levels/news); no selection, no LLM | Functional |
| `D:\agentic-engine` | Idea→hypothesis→test→decision validation ledger (overlaps internal `research_ledger/`) | Working core |

## PM working docs

- `docs/reference/EXTERNAL_PROJECTS_MAP.md` — the 5 external sibling repos + the one-system pipeline (read before any new build).
- `docs/reference/COMPLETE_SYSTEM_MAP.md` — internal capability deep-map (verify against code; under audit 2026-06).
- `docs/DOCS_INDEX.md` — canonical vs archived doc map.
- `docs/AI_REPO_INDEX.md` — generated file/category index (regen: `python scripts/build_ai_index.py`).
- Auto-memory (`MEMORY.md` + `memory/*.md`) — durable PM state: decisions, handoffs, project status. This is the PM knowledge base; keep it current so context can be cleared safely.

> Maintenance: update the row when a capability ships or changes status; re-run
> `build_ai_index.py` after structural changes. Prefer extending this table over
> creating new README files.
