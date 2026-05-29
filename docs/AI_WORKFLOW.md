# AI Workflow

Use this workflow when working on this repository with Codex, Claude, or another AI coding agent. The goal is to keep context small, avoid generated noise, and make each task land in the right subsystem.

## Starting Context

For a new task, read only these first:

- `CLAUDE.md`
- `docs/DOCS_INDEX.md` — single authoritative map of current vs archived docs; check here before opening any other `.md`
- `docs/AI_REPO_INDEX.md`
- `docs/AI_CAPABILITY_MAP.md` when the task touches web UI, RAG/docs, reports, prediction, strategy templates, or strategy discovery
- `docs/designs/real_edge_discovery_program.md` when the task touches edge discovery, probes, hardening, shadow runner, or the graveyard
- `docs/designs/agentic_nt_strategy_knowledge_base.md` when the task touches agentic strategy research, NinjaTrader strategy generation, StrategyDiscoveryFilter, the Strategy Factory, NT optimizer loops, shadow promotion, NinjaAccountManager, or execution bridge automation
- `docs/designs/autonomous_research_to_paper_trade_loop_build_plan.md` when the task asks to build the full autonomous loop from discovery through NT validation, shadow, and Sim101 paper trading
- The specific source and tests identified from the relevant category

Avoid opening the entire repository tree or large generated output folders.

## Standard Task Prompt

```text
Task: <describe the requested change>

Use a narrow-context workflow:
1. Read CLAUDE.md and docs/AI_REPO_INDEX.md.
2. Identify the smallest relevant subsystem.
3. Inspect only the source and tests needed for that subsystem.
4. Before editing, summarize the files you believe are relevant.
5. Make the smallest safe change.
6. Run targeted tests first; broaden only if shared behavior changed.

Ignore .venv*/, outputs*/, .pytest_cache/, .ta_artifacts/, __pycache__/, *.pyc, *.duckdb, logs, and IDE metadata unless explicitly needed.
```

## Discovery-Only Prompt

```text
I only want discovery, no code edits yet.

Read CLAUDE.md and docs/AI_REPO_INDEX.md, then identify:
- the likely subsystem
- the files to inspect
- the tests that probably cover this area
- any open questions or risks

Do not scan generated folders or output artifacts.
```

## Implementation Prompt

```text
Implement the change using the discovery result.

Keep edits scoped to the identified subsystem. Do not refactor unrelated code. Add or update focused tests when behavior changes. Run the narrowest relevant pytest command and report the result.
```

## Review Prompt

```text
Review this change as a code reviewer.

Prioritize correctness bugs, regressions, missing tests, data model violations, and places where generated/runtime files were accidentally included. Reference exact files and lines. Keep summary secondary to findings.
```

## RAG Indexing Guidance

Good candidates for retrieval:

- `*.py`
- `*.md`
- `*.yaml`, `*.yml`
- `*.toml`
- selected `*.txt`

Bad candidates for retrieval:

- `.venv*/`
- `outputs*/`
- `.pytest_cache/`
- `.ta_artifacts/`
- `__pycache__/`
- `*.pyc`
- `*.duckdb`
- logs
- generated HTML/report artifacts unless the task is about a specific generated result

For code RAG, prefer chunks around modules, classes, functions, and tests. For docs, chunk by headings. Store path, category, symbol name, and last modified time as metadata.

## Refreshing The Repo Index

Run:

```bash
python scripts/build_ai_index.py
```

The generated `docs/AI_REPO_INDEX.md` should be small enough to give an AI agent a map without flooding context with implementation details.

## Local RAG Commands

Build the local retrieval index:

```bash
python scripts/ai_rag.py build
```

Search for task-relevant chunks:

```bash
python scripts/ai_rag.py search "large candle excursion reports" --top 8
```

Limit retrieval to a category or path:

```bash
python scripts/ai_rag.py search "horizon calibration scoring" --category "Prediction" --top 8
python scripts/ai_rag.py search "deployment board parser" --path "reports" --top 8
```

Write a markdown context pack for an AI session:

```bash
python scripts/ai_rag.py context "execution bridge heartbeat recovery" --top 10
```

Generated RAG data is written under `.ta_artifacts/ai_rag/`, which is ignored by git. Commit the scripts and docs, not the generated chunk index.

## Recommended RAG Task Flow

1. Read `CLAUDE.md` and `docs/AI_REPO_INDEX.md`.
2. Read `docs/AI_CAPABILITY_MAP.md` for broad capability-routing tasks.
3. Run `python scripts/ai_rag.py search "<task>" --top 8`.
4. Open only the returned files and nearby tests.
5. If the task is broad, run a second search with `--category` or `--path`.
6. Use `context` when you want to hand a compact retrieved pack to another AI tool.

## Capability Routing For AI Agents

Do not flatten the project into "report generation." Route by capability first:

- Backtest Reports: `cli/main.py`, `web/report_builder.py`, `web/report_catalog.py`, `reports/html/config.py`, `reports/html/registry.py`, report YAML files.
- Prediction: `src/ta_foundation/prediction/`, especially `run_prediction.py`, `run_multi_agent.py`, `backtest_horizon_predictions.py`, `prediction.yaml`.
- Strategy Templates: `analysis/strategy_composer/`, web `/api/generate`, `/api/backtest`, `/api/validate`, template schema.
- Strategy Discovery: `analysis/strategy_discovery/`, `strategy_discovery_report.yaml`, and `strategy_discovery_*` report sections.
- Web Orchestration: `web/app.py`, `web/jobs.py`, `web/prediction_jobs.py`, `web/report_catalog.py`, `web/templates/index.html`.
- Agentic NT Strategy Loop: start with `docs/designs/agentic_nt_strategy_knowledge_base.md`, then route to `src/ta_foundation/agent/`, `src/ta_foundation/research_ledger/`, `src/ta_foundation/shadow/`, `src/ta_foundation/nt_strategy_loop/`, `src/ta_foundation/strategies/StrategyDiscoveryFilter/`, `src/ta_foundation/strategies/TaFoundationExecutionBridge/`, or external `D:\NinjaAccountManager` as needed.
