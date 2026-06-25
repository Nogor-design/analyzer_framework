# Capability Workbenches Plan

**Status:** Plan of record · **Created:** 2026-06-19 · **Author:** Claude (acting PM)

## Build status (2026-06-20)

Shipped & verified: **workbench-kit**, **ma-cross** (retrofit), **exit-policy-lab**,
**prop-survival-lab**, **cross-asset-scout** (reproduces the OOS-validated TSMOM edge:
full t=4.94 / OOS t=2.97), **pattern-discovery**, **regime-monitor**, and now
**entry-discovery** (#4, port 7795) — the generalization of ma-cross to all 8 entry
families. Verified 2026-06-20 (`smoke_test.py`: Discover 8/8 families return real
ta_foundation numbers, cross-instrument Prove wired + pooling for ma/bb/breakout/
pullback/level = 5/5, 0 failures). **Update 2026-06-24: the candle/orb/lcr entry
emitters are now wired too — Prove is live for ALL 8 families** (candle via
`detect_pattern` on candle features; orb via `detect_orb` on 1m bars with the real
candle ATR attached; lcr via the `lcr.regions` engine + `emit_lcr_entries` with
fixed tick tp/sl). All reuse the real ta_foundation engines; verified pooling
across NQ/ES/YM/RTY (candle t=−2.76/2,051 trades, lcr t=−9.66/37,660, orb
t=−2.48/369), and the honest gate correctly rejects all three on current data.
**All planned workbenches (Tiers A+B + kit) are now built, with every entry family
fully Explore→Discover→Prove.** Honest result: no single entry family
shows a robust cross-instrument edge on default params (the gate correctly rejects),
consistent with the cost-walled intraday-entry finding.

**Tier C #7 (Large-Candle Excursion Lab) retrofitted onto the kit 2026-06-25.** It
had been built off-pattern (Flask app that shelled out to a `run_analysis.py`
subprocess via SSE, no kit, no `capabilities.py`, no FINDINGS/catalog row). It is now
a thin in-process shell at `D:\strategy-analysis\large-candle` (port 7796):
`large_candle/capabilities.py` wraps `run_large_candle_excursion` (Sweep) +
`build_large_candle_excursion_findings` (Findings) + the engine's `edge_validation_engine`
IS/OOS split (Validate), reusing the kit loaders/chrome and caching the sweep so
Findings/Validate don't recompute. Verified end-to-end (`smoke_test.py`, NQ 06-26:
82,087 bars → 8/8 combos → 1,006 candidates ranked → 5 elite setups → 22 IS/OOS-validated,
**1 stable_edge survives OOS**, 0 likely_overfit). DoD now met (kit + capabilities +
docs/{LARGE_CANDLE_CAPABILITIES,FINDINGS}.md + catalog row). Tier C #8 (Prediction)
and #9 (MA Anchor) remain deferred per below.

**Upstream bugs RESOLVED (commit `a1af6ed`, 2026-06-20; re-verified 2026-06-24).** The two
ta_foundation issues that surfaced while wrapping are now fixed with regression tests:
(1) `pattern_engine` orchestrator referenced `events_exec_df` before assignment and called
`build_pattern_clusters` with a stale signature — both swallowed by inner `except`, so the
market_discovery scope was silently disabled and clusters never built; (2)
`regime_recommender/features.py` lookback windows were shorter than the EMA(50) computed on
them, so the 4h trend-slope was always NaN→0 and the classifier was stuck on `range`
(widened 15m→4d, 1h→15d, 4h→45d; NQ 06-26 4h went 19 bars/slope 0 → 197 bars/slope 43.99).
Re-verified 2026-06-24 against the fixed engine: regime-monitor now produces real
trend_up/trend_down regimes (no longer stuck on `range`); pattern-discovery builds real
clusters and the honest gate correctly rejects (t=−1.51, CI includes zero). Stale
"call-around" comment in `regime-monitor/capabilities.py` updated to point at the fix.

## Purpose

Decompose `ta_foundation`'s value into a small set of **standalone capability
workbenches** — each a separate `D:\strategy-analysis\<name>` project that
**imports `ta_foundation` as a library and never reimplements analysis**. This is
the validated alternative to the `D:\phase1-services` microservices direction
(see "Why not microservices" below).

The template is the already-built **`D:\strategy-analysis\ma-cross`** workbench
(memory: `project-ma-cross-workbench`).

## The pattern we are replicating

`ma-cross` works because it is a **thin Flask shell** over `ta_foundation`:

- `capabilities.py` = thin wrappers; the engine stays in the monolith.
- It walks **one coherent capability** through a fixed honest workflow:
  **Explore → Discover → Prove**, with guards (cross-instrument t-stat, IS/OOS
  degradation, net-of-cost) so you cannot select a small-sample mirage.
- Bars come from `D:\MarketData`; `ta_foundation` is pip-installed editable.

A good "pull into its own project" candidate needs all four:
1. a coherent capability cluster in `ta_foundation`,
2. real backing code (verified LOC, not a stub),
3. a natural explore → prove narrative,
4. standalone value.

## Why not microservices (context)

`D:\phase1-services` (Copilot, 2026-06-17) scaffolded 3 FastAPI/Docker services
but the bodies are **stubs** — exit-sim returns `random.uniform(...)`, market-data
is an in-memory dict that never parses NT files, report-builder returns a
placeholder HTML string. The 12-service REST/K8s plan pays the full microservices
tax (network contracts, orchestration, 12 partial copies of `analysis/`) for
benefits a solo operator cannot use, and it stops exactly at the NT-licensed
parts that are the real edge. The library-import workbench pattern gives
reusable capabilities with one source of truth and full IDE navigation. **Shelve
`D:\phase1-services`; standardize on workbenches.**

## Shared kit (extract once, reuse everywhere)

Before cloning the pattern N times, lift the non-capability parts of `ma-cross`
into `strategy-workbench-kit`:

- bar loading from `D:\MarketData` (`data.py`: `list_datasets`, panel loaders),
- the **cross-instrument proof harness** (pooled t-stat gate),
- the Flask shell + Explore/Discover/Prove `index.html` template + `app.js`/`style.css`,
- the already-solved gotchas (cp1252 console → `PYTHONIOENCODING=utf-8`/ASCII;
  `run_ma_discovery` deep-merge leak; intrabar-1m exit-resolution caveat).

Each new workbench = kit + a `capabilities.py` of thin wrappers + a docs page.
This is the guard against the project's #1 failure mode: rebuilding what exists.

## Candidate capability projects (ranked)

LOC = real, measured 2026-06-19 (non-`__init__` Python lines).

### Tier A — build first (high value · clean isolation · demand proven)

| # | Project | Pulls from `ta_foundation` | Explore → Discover → Prove | LOC |
|---|---|---|---|---|
| 1 | **Exit Policy Lab** | `analysis/exits/` (`policies.py`, `simulate.py`, `pantheon_trail_battery.py`) + `marketdata/tick_cache.py` | list policies → simulate trades on tick/bar data → robustness across policies/params | 2,514 |
| 2 | **Prop Survival & Risk Lab** | `analysis/prop_evaluation/` + `analysis/risk/` + `analysis/selection/` + `scripts/holdout_*`, account-survival harness | load a lineup → APEX trailing-DD equity path → survival-filtered selection + sizing | 2,558 |
| 3 | **Cross-Asset Edge Scout** | `scripts/cross_instrument_daily_scout.py` / `cross_instrument_scout.py` / `fetch_daily_history.py` + `analysis/statistics/` | pick instruments/horizon → pooled scan → OOS + cross-instrument t-stat gate (proven daily TSMOM edge) | ~1,500 |

### Tier B — strong, larger surface

| # | Project | Pulls from | Notes | LOC |
|---|---|---|---|---|
| 4 | **Entry-Strategy Discovery Workbench** | `analysis/entry_strategies/` (all 8 families) + `sweep.py` + `validation.py` | Generalization of ma-cross — candle/bb/orb/breakout/pullback/level/lcr. ma-cross becomes one preset. | 13,767 |
| 5 | **Pattern Discovery Workbench** | `analysis/pattern_engine/` (engine, templates, cluster, monte_carlo, robustness_cv) | template sweep → cluster → Monte Carlo robustness | 4,144 |
| 6 | **Regime Monitor** | `analysis/regime_recommender/` + `analysis/features/` | "what is the market today + which families work" — lightweight daily-use | 857 |

### Tier C — niche / heavier / NT-coupled (defer or keep in monolith)

| # | Project | Pulls from | Why later |
|---|---|---|---|
| 7 | **Large-Candle Excursion Lab** | `analysis/large_candle_excursion/` | ~~defer~~ **BUILT 2026-06-25** (port 7796, retrofitted onto kit): Sweep→Findings→Validate over the LCE engine. Focused slice (sweep + findings + IS/OOS validation), not the full 45-section report. |
| 8 | **Prediction Workbench** | `prediction/` (daily + horizon) | **DEFERRED — not workbench-shaped (verified 2026-06-25).** 9,187 LOC is a full app: inference agents (`claude_agent`/`ollama_agent`/statistical/analogue), ensembles, calibrators, abstention/tradable-zone, cost models, persistence stores, prompts, its own `prediction.yaml`/`run_prediction.py` CLI. A shell over it would own model state + LLM calls + calibration artifacts — that's a 2nd app, not a thin wrapper. If it ever gets a UI, give it its OWN dedicated app; do NOT force it into the kit pattern. Stays in monolith/own CLI for now. |
| 9 | **MA Anchor Analyzer** | `analysis/ma_structure/` | **DEFERRED, not built (rationale corrected 2026-06-25).** Earlier "fold into #4 / overlaps ma-cross" was WRONG: verified neither ma-cross nor entry-discovery imports `ma_structure` — they wrap `entry_strategies/ma` (MA *crossover entries*), a different thing. `ma_structure` is a DISTINCT, uncovered capability (anchor detection → segment detection → `tp_sl_engine` scoring → `trade_alignment` → `regime_context`) and the cleanest remaining kit candidate (~2,261 LOC, clean Explore[detect anchors]→Discover[score anchor-relative TP/SL]→Prove[validate alignment]). Not a gap — anchor analysis already runs in the report pipeline. Build a standalone workbench only on demand (~1 day kit clone); low priority given cost-walled-intraday findings. |
| — | NT Optimizer / Strategy Loop / Execution Bridge | `optimization/`, `nt_strategy_loop/`, bridge `.cs` | Hard NT-license + IPC coupling; stay in the monolith — not workbench-shaped |

**Realistic count:** ~6 useful standalone workbenches (Tiers A+B) + ma-cross
already built = 7, plus Tier C #7 (Large-Candle Excursion Lab) retrofitted onto
the kit 2026-06-25 = 8 shipped. Tier C #8 (Prediction) and #9 (MA Anchor) remain
deferred.

## Recommended sequence

1. **Extract `strategy-workbench-kit`** from ma-cross (~½ day). Retrofit ma-cross
   onto it to prove the kit.
2. **Exit Policy Lab** (#1) — cleanest isolation; validates the kit on a 2nd
   capability. *(Chosen as first new workbench, 2026-06-19.)*
3. **Prop Survival & Risk Lab** (#2) — current live priority.
4. **Cross-Asset Edge Scout** (#3) — wraps the proven edge for repeatable use.
5. Tier B as appetite allows. #4 can absorb/retire standalone ma-cross later.

## Per-project scaffold recipe (~1 day each, not a rewrite)

```
D:\strategy-analysis\<name>\
├── app.py                 # Flask routes (from kit)
├── <name>\
│   ├── data.py            # re-export kit loaders
│   └── capabilities.py    # THIN wrappers over ta_foundation — no reimplementation
├── templates/index.html   # kit Explore/Discover/Prove shell
├── static/{app.js,style.css}
└── docs/{CAPABILITIES.md, FINDINGS.md}
```

**Hard rule (same as ma-cross):** wrappers only; the engine stays in
`ta_foundation`, imported editable. Run with `PYTHONIOENCODING=utf-8`.

## Definition of done (per workbench)

- `python app.py --port <p>` serves Explore/Discover/Prove with real numbers.
- Every analysis call lands in `ta_foundation` (grep shows no duplicated logic).
- Honest guards visible in the UI (trade count, net-of-cost, IS/OOS,
  cross-instrument t where applicable).
- `docs/CAPABILITIES.md` (what it wraps) + `docs/FINDINGS.md` (what it found).
- Catalog row added to `docs/CAPABILITY_CATALOG.md`.
```
