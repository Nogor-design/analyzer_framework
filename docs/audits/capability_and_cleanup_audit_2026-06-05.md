# Verified Capability & Cleanup Audit — 2026-06-05

**Method:** 5 parallel read-only audits, each instructed to **verify against actual code + tests**
and NOT trust docstrings or `COMPLETE_SYSTEM_MAP.md` (which reads aspirational). Partitions:
analysis/, reports+config, web/, autonomy+prediction, NT-backbone+cruft. This doc is the corrected
capability picture + an actionable cleanup register. Supersedes the unverified claims in
`docs/reference/COMPLETE_SYSTEM_MAP.md` where they conflict (that file should be refreshed or retired).

---

## A. Corrections to the prior (May-24) system map

| Prior map said | Reality (verified) |
|---|---|
| Agentic loop = "Partial" | **Shipped.** agent roles (hypothesis_author, triage, scribe, sweep_operator, inbox), scheduler passes, research_ledger (SQLite + 6 migrations), shadow runner — all implemented + tested. |
| (Implied DD/prop work is missing) | **`analysis/prop_evaluation/simulation.py` already implements the APEX model**: trailing drawdown (global/session, lock at start+buffer), daily-loss limit, profit target, regime-aware Monte Carlo, slippage stress surface. Plus `apex_trailing_model.py` + `apex_portfolio_mc.py`. The DD math we almost rebuilt EXISTS. |
| Prediction = forecasting only | **`prediction/` is effectively the daily lineup engine**: daily (Claude Opus, `claude_agent.py`) + multi-horizon ensemble (statistical/analogue/regime/session + stacking), outcome scoring, ECE calibration. The hand-ChatGPT worker is redundant with this. |
| (No cleanup lens) | Significant cruft exists (below): a duplicate registry key, dead packages, 22MB checked-in node_modules, ~25 stray root configs. |

## B. Verified capability status (by area)

- **analysis/** (187 py) — Core is **shipped**: all 8 entry families, pattern_engine, ma_structure,
  regime_recommender, strategy_discovery, large_candle_excursion, exits, indicators, **prop_evaluation**.
  Weak spots: `features/` minimal, `leaderboards`/`trade_enrichment` untested, `premarket` family
  incomplete (no signals.py), docstrings on big orchestrators 5x out of date.
- **reports/** (161 py) — **Healthy**: 130 registered sections. Issues: `anchor_interaction_overview`
  registered **twice** (bug); 3 `test_anchor_*.py` stubs sitting in sections/; docs say 127 sections.
- **web/** (58 py) — **Mature**: ~151 routes, optimizer suite + discovery UI + deployment matrix all
  wired and tested (54 web test files). **No daily-lineup *surface*** — prediction is dispatch-only;
  the gap is a selection UI over prediction + the pool, not a prediction engine.
- **autonomy + prediction** — **Shipped** (see corrections). `research_intake/ldr.py` (Local Deep
  Research import → hypothesis author) is **wired and tested**. `agent/graph.py` is **deprecated/dead**.
  `prediction/ollama_agent.py` looks like a **stub** (no real Ollama call).
- **NT backbone** — **Shipped**: `nt_strategy_loop` (author→compile→repair→optimize, validated live).
  **The .cs paths do NOT overlap** (this corrects my earlier worry): `StrategyDiscoveryFilter.cs` =
  discovery-edge→backtest confirmation harness (with Python-parity engine, tested);
  `TaFoundationExecutionShell.cs` = live execution bridge (34 tests); `TaFoundationDataExportStrategy/
  MinuteBarExporter` = data ingestion. (The external NinjatraderDocScrapper factory is a 4th, distinct
  role: *generates* bespoke `.cs` from specs — complements, doesn't duplicate, the SDF harness.)

## C. Cleanup register (prioritized)

### Critical (bugs / safe quick wins)
| Item | Path | Action | Conf |
|---|---|---|---|
| Duplicate registry key | `reports/html/registry.py` (`anchor_interaction_overview` ×2) | Remove the 2nd definition | HIGH |
| Test stubs in sections/ | `reports/html/sections/test_anchor_*.py` (3) | Delete | HIGH |
| Checked-in node deps | `node_modules/` (22MB) + `package.json`/`package-lock.json` | gitignore + move the 3 board-report `.js` to `scripts/legacy/` | HIGH |
| Dead package | `analysis/`-adjacent `plots/` (0 callers, 4mo cold) | Delete | HIGH |
| Empty stub | `analysis/pattern_engine/monte_carlo.py` (docstring only) | Delete | HIGH |
| Dead module | `agent/graph.py` (deprecated) + `agent/tools/analysis_tools.py` (only graph.py uses it) | Delete after confirming no other callers | MED-HIGH |

### High (config + doc sprawl)
| Item | Path | Action | Conf |
|---|---|---|---|
| Root config sprawl | ~25 stray root `*.yaml` (stub `report_*.yaml`, `pattern.yaml`, `myReport*.yaml`, superseded `ma_report`/`ma_report2`, `regime_disc*`, `PatternE.yaml`, etc.) | Delete stubs; move keepers to a `configs/` dir | HIGH |
| Design notes at root | `AI Financial SYSTEM DESIGN.txt`, `ClaudePromptArchitecture.txt`, `agenticIdeas.txt`, `cli_report_prompts.txt` | Move to `docs/` (research/_archive) | MED |
| Synthetic-data dupe | root `gen_synthetic.py` vs `generate_synthetic_data.py` | Consolidate into `validation/`; delete the dupe | MED |
| Stale archive | `docs/_archive/` (34 files) overlapping `designs/` | Prune ~10-15 obsolete; merge unique history | MED |
| Aspirational map | `docs/reference/COMPLETE_SYSTEM_MAP.md` | Refresh against this audit or mark superseded | HIGH |

### Medium (consolidation / verify)
| Item | Path | Action | Conf |
|---|---|---|---|
| 3 Apex simulators share logic | `prop_evaluation/simulation.py` + `apex_trailing_model.py` + `apex_portfolio_mc.py` | Extract one `ApexSimulator`; others wrap it | MED |
| Possible module dupe | `web/optimizer_recipe_templates.py` vs `optimizer_template_writer.py` | Code-review; merge/delete if confirmed | MED |
| Prediction stub | `prediction/ollama_agent.py` | Implement the Ollama call or remove | MED |
| Incomplete family | `analysis/entry_strategies/premarket/` (no signals.py) | Finish or remove from sweep | MED |
| Regime classifier creep | regime in `regime_recommender` vs `strategy_discovery/regime.py` vs `large_candle_excursion/regime_discovery.py` | Pick one canonical classifier; others wrap | MED |
| Untested utilities | `analysis/leaderboards.py`, `trade_enrichment.py` | Add tests | MED |
| `.gitignore` gaps | `.pytest_cache/`, `.codex_tmp/`, `node_modules/` | Add | HIGH |

## D. The actual gaps (small — glue + config, not engines)

1. A **daily-lineup selection surface** that joins `prediction/` output + the deployment-matrix pool
   (the engine exists; the picking UI/logic does not).
2. **Per-firm (APEX) DD config + live wiring**: `prop_evaluation` has the math; `NinjaAccountManager`
   has live account state with an unused `daily_lockout`. The gap is connecting them with a versioned
   APEX profile — not building DD math.
3. **Discoverability hygiene** (this audit + the ecosystem map) so capabilities stop being re-built.

## E. Recommended execution

Do the cleanup as a **reviewed batch on a branch** (not piecemeal): Critical + High items are low-risk
and high-clarity. Medium items need a code-review pass each. Refresh/retire `COMPLETE_SYSTEM_MAP.md`
as part of it. Nothing here blocks the trading work; it removes the fog that caused the duplication.
