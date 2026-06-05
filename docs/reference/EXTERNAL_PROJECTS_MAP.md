# External Sibling Projects — Ecosystem Map

**Created 2026-06-05.** Companion to `COMPLETE_SYSTEM_MAP.md` (which covers ta_foundation
*internal* capabilities). This documents the **external D:\ projects** that are part of the same
day-trading effort but live in separate repos. They kept getting overlooked, causing duplicated
work. **Read this before proposing any new build** — odds are a capability already exists here or
inside ta_foundation.

> Maturity/claims below come from a 2026-06-05 read-only inventory of each repo. Verify by running
> the named entry point before depending on it for live money.

---

## The one-system picture (who owns each stage)

A near-complete research→execution pipeline already exists, split across repos. The gaps are
**integration glue** and a few **brains** (per-firm DD rules), not missing stages.

```
ONLINE RESEARCH        D:\local-deep-research  ──(already wired via ta_foundation/research_intake/ldr.py)
  → HYPOTHESIS LEDGER  ta_foundation/research_ledger + agent/roles + D:\agentic-engine (validation ledger)
  → DISCOVERY/SWEEPS   ta_foundation/analysis/* (8 families, pattern engine, walk-forward, prop_evaluation)
  → EDGE → .cs         D:\NinjatraderDocScrapper (factory + repair + parity)  *and*  ta_foundation/nt_strategy_loop
                       *and* generic ta_foundation/strategies/TaFoundationExecutionBridge (ExecutionShell.cs)
  → BACKTEST/OPTIMIZE  ta_foundation optimizer + Deployment Matrix (252)
  → PAPER/SHADOW       ta_foundation/shadow/
  → ACCOUNT + EXECUTE  D:\NinjaAccountManager (account ingest + bridge)  +  TaFoundationExecutionBridge
  → DAILY PLAN         ta_foundation/prediction/ (daily Claude + horizon ensemble) + D:\DailyAnalysis (context)
```

**Known duplication to reconcile (not yet decided):** three discovered-edge→`.cs` paths
(`StrategyDiscoveryFilter.cs` vs `TaFoundationExecutionShell.cs` vs the NinjatraderDocScrapper
factory); the daily "ChatGPT lineup" worker vs the existing `prediction/` system; and any overlap
between a new drawdown engine and `analysis/prop_evaluation/simulation.py`.

---

## D:\local-deep-research — online research agent

- **Purpose:** Mature LangGraph AI research agent; iterative multi-turn search over 30+ engines
  (web + arXiv/PubMed/GitHub/etc.), parallel subagent decomposition, cited markdown reports.
- **Run:** `ldr-web` (Flask :5000), `ldr` (CLI), `ldr-mcp` (MCP), or import `AdvancedSearchSystem`
  with `strategy_name="langgraph-agent"`. Local LLM via Ollama or API.
- **Provides ta_foundation:** automated edge/hypothesis research, prop-firm-rules research (e.g. the
  APEX rulebook), market-context gathering — with citations.
- **Integration:** **already connected** — `ta_foundation/research_intake/ldr.py` + `import_ldr.py`
  ingest LDR output (see `docs/ideas/LDR_INTAKE_*`). Feeds the hypothesis-author loop.
- **Maturity:** production-ready (PyPI/Docker, CI, tests). **Deps:** Python 3.12+, langchain/langgraph,
  Flask, SQLCipher, playwright/trafilatura, sentence-transformers/faiss.

## D:\NinjatraderDocScrapper — NinjaScript strategy FACTORY + learning RAG

- **Purpose:** NOT just a scraper. Generates compilable NT8 `.cs` strategies from canonical JSON
  specs, with a learning RAG over NinjaTrader docs and a compile-error repair loop.
- **Run:** `scrape_ninjatrader_desktop_docs.py` → `build_ollama_index.py` → `generate_ninjascript.py`
  / `python -m strategy_factory.factory --spec <spec>.json` (deterministic) / `chat_ninjascript.py`
  / `webgui_server.py` (:8765, label good/bad) / `batch_factory_run.py` (auto-install to NT).
- **RAG:** Ollama `nomic-embed-text` embeddings → SQLite vector store (`rag_index.sqlite`);
  generator `qwen3-coder:30b`. Indexes NT docs + module cards + known-good/bad code. "Learning" =
  manual good/bad labeling → future fine-tune corpus (`training_exports/`); not online-retraining yet.
- **Provides ta_foundation:** the canonical **discovered-edge → working `.cs`** path. Deterministic
  factory currently covers MA-cross + fixed stop/target + time window + risk lockouts; emits a
  ta_foundation-compatible template JSON. **Parity tools** run ta_foundation signals vs NinjaScript.
- **Integration:** shares `C:\ta_foundation\nt_compile_loop\` + `C:\ta_foundation\nt_results\`;
  needs `D:\Backup\projects\PythonProject\ta_foundation\src` on PYTHONPATH for parity. **This likely
  supersedes the StrategyDiscoveryFilter.cs parity effort — reconcile before extending either.**
- **Maturity:** working prototype, tested (deterministic path compiles reliably; RAG path can
  hallucinate APIs). **Deps:** Python 3.11+, Ollama, beautifulsoup4/playwright/requests.

## D:\NinjaAccountManager — real-time NT account monitor + execution API

- **Purpose:** Python+NinjaScript real-time monitor of NT8 accounts (balance/equity/margin/PnL,
  positions, orders) with a programmatic order API. DearPyGUI dashboard.
- **Run:** `python main.py`. NinjaScript client indicator `ninjascript/NinjaAccountManager.cs`
  connects to Python WebSocket server `ws://127.0.0.1:8765/ws`; strategy API on tcp `:8766`.
- **Connects to NT via WebSocket/JSON-lines, NOT plugin hooks** (the "plugin" idea is largely
  redundant — a working bridge exists).
- **Provides ta_foundation:** live per-account state ingest + order submission + signal-intake gating
  + thread-safe state/event-bus to hang risk logic on.
- **LACKS (the real gap):** trailing-drawdown math, daily-loss limits, challenge-vs-funded logic,
  APEX rules, per-account configs. `daily_lockout` flag exists but is **never triggered**. A
  per-firm DD/risk engine should plug into its event bus (do not rebuild the bridge/ingest).
- **Maturity:** working, early-stage (4 test modules). **Deps:** Python 3.11+, dearpygui, websockets.

## D:\DailyAnalysis — rule-based NQ daily session-plan generator

- **Purpose:** Single-file tool producing an intraday **context** report for NQ (bias, key levels,
  VWAP/EMA/ATR, pivots, macro-news parse). **No strategy selection, no LLM.**
- **Run:** `python dailyAnalysis.py --ohlc <Last.txt|csv> [--news <txt>] [--symbol "NQ 06-26"] [--out plan.md]`.
- **Provides ta_foundation:** reusable OHLCV loader (NT Last.txt + CSV), indicator suite, news parser,
  and daily market context to feed the daily plan. Reads `D:\MarketData`.
- **Does NOT do:** strategy/template lineup selection (that is the existing `prediction/` system's job).
- **Maturity:** functional, no tests/README. **Deps:** Python 3.13, pandas, numpy.

## D:\agentic-engine — local-first idea→evidence validation LEDGER

- **Purpose:** SQLite/stdlib workflow ledger: `idea → hypothesis → test_plan → test_run → result →
  decision_memo → asset`. Orchestrates research by shelling out to existing tools; indexes their
  outputs as assets. Zero external Python deps by design.
- **Run:** `python -m app.backend.main` (HTTP :8765), `python scripts/scan_repos.py` (index assets).
- **Provides ta_foundation:** a decision/pre-registration ledger + asset catalog + runner pattern
  (`runners/ta_foundation.py` spawns `python -m ta_foundation.cli.main ...`) + Ollama/Claude agent
  hooks. Overlaps ta_foundation's own `research_ledger/` — decide which is canonical.
- **Maturity:** working core, 33 tests; UI/agents partial; Claude provider stubbed.

---

## Maintenance

When an external project changes materially, update this file and the one-liner in
`docs/CAPABILITY_CATALOG.md`. Consider teaching `scripts/build_ai_index.py` to scan these external
roots so `AI_REPO_INDEX.md` surfaces them automatically.
