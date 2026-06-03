# Documentation Gap Analysis — ta_foundation

**Date:** May 24, 2026  
**Status:** Comprehensive audit completed (COMPLETE_SYSTEM_MAP.md + deep codebase exploration)

---

## Executive Summary

The **ta_foundation** project is a **sophisticated, production-hardened trading analytics platform with 10 major capabilities, 5 agentic workflow phases, and deep integrations with NinjaTrader and AI models**. 

**Current state:**
- ✅ Core architecture is well-documented (CLAUDE.md, AI_REPO_INDEX.md)
- ✅ High-level capability map exists (AI_CAPABILITY_MAP.md)
- ✅ Getting started guide is practical (GETTING_STARTED.md)
- ❌ **Hidden capabilities are undiscovered and undocumented**
- ❌ **Subsystem documentation is scattered or missing**
- ❌ **Integration points with external systems are unclear**
- ❌ **Data model shapes and schemas are not formally specified**
- ❌ **Advanced workflows (agentic, prediction, NinjaTrader loop) lack step-by-step guides**

**Key Finding:** The system is **feature-complete but discovery/integration documentation is a critical gap**. New users cannot find or understand advanced capabilities without reading source code.

---

## Severity Classification

### 🔴 CRITICAL (Blocking Adoption/Integration)

1. **No Web API Reference Document**
   - 20+ Flask routes exist
   - No documentation of endpoints, request shapes, response shapes
   - Developers can't integrate with web app without reading code
   - **Impact:** Blocks external integration, forces code reading

2. **No Report Sections Catalog**
   - 120+ HTML sections registered
   - No browsable index or discovery mechanism
   - Users don't know what reports are available
   - **Impact:** Report rendering is "trial and error," users miss valuable sections

3. **No NinjaTrader Integration Runbook**
   - nt_strategy_loop is powerful but completely undocumented
   - AddOn setup, repair loop, optimizer integration are unclear
   - users might spend hours debugging without a guide
   - **Impact:** Blocks live trading integration, major feature is inaccessible

4. **No Agentic Workflow HITL Guide**
   - Phase A-D agents exist but HITL loop is opaque
   - Hypothesis authoring → triage → promotion flow is unclear
   - Inbox system, decision ledger, journal entries lack explanation
   - **Impact:** Autonomous research program is inaccessible

### 🟠 HIGH (Slows Development, Reduces Code Quality)

5. **No CONTRIBUTING.md**
   - First-day onboarding is missing
   - How to add new: parser, analysis module, report section, entry strategy family (unclear)
   - Code review expectations undefined
   - **Impact:** New contributors have steep ramp-up, code patterns are implicit

6. **No Data Model Schema Document**
   - AnalysisPackage fields are defined but no examples
   - metadata["derived"] keys are listed but shapes are opaque
   - SummaryBlock normalization rules are mentioned but not detailed
   - **Impact:** Confusion when extending system, developers guess at data structures

7. **No Analysis Subsystem READMEs**
   - ma_structure/, pattern_engine/, entry_strategies/ lack local docs
   - Problem statements are not articulated
   - Key modules and workflows are implicit
   - **Impact:** Onboarding to analysis work requires code reading

8. **No TESTING.md**
   - How to write tests for new modules is unclear
   - Fixture setup patterns missing
   - Mock strategy creation undocumented
   - **Impact:** Test coverage stalls, code quality degrades

### 🟡 MEDIUM (Reduces Discoverability, Increases Support Load)

9. **No Multi-Agent Prediction Guide**
   - Horizon prediction is powerful but needs step-by-step tutorial
   - Calibration interpretation unclear
   - Drift detection + response actions undefined
   - **Impact:** Prediction system is underutilized

10. **No Daily Prediction Measurement/Calibration Guide**
    - How to backfill predictions (--asof) is unclear
    - Outcome measurement workflow is not documented
    - ECE calibration interpretation missing
    - **Impact:** Feedback loop is hard to set up

11. **No Configuration Schema Document**
    - YAML structure for all feature blocks not formally specified
    - Parameter bounds and types are implicit
    - Example YAML per feature is missing
    - **Impact:** Users write incorrect configs, trial-and-error ensues

12. **No Conditional Promotion System Documentation**
    - web/conditional_promotion.py exists but is opaque
    - Logic for "if PF > X, then expand Y sweep" is undocumented
    - **Impact:** Advanced workflow automation is hidden

13. **No Market Data Handling Guide**
    - Minute bar → tick data relationship unclear
    - Timezone conversion (UTC → America/Denver) not explained
    - Tick cache mechanics and options missing
    - **Impact:** Data ingest mistakes are common

14. **No Discovery Registry (Graveyard) Guide**
    - Refusal checks and Jaccard matching logic are opaque
    - Parameter for "near duplicate" threshold is buried in code
    - How to override graveyard entries is unclear
    - **Impact:** Hypothesis authors don't understand why probes are rejected

### 🔵 LOW (Nice to Have)

15. **No Performance Tuning Guide**
    - Pattern engine scaling (1M+ templates on large datasets)
    - Report rendering bottlenecks (120+ sections)
    - Tick cache memory footprint guidelines
    - **Impact:** Users optimize blindly

16. **No Multi-Instrument Workflow Documentation**
    - How to run discovery across multiple contracts
    - Shared market data vs per-contract storage
    - Batch report generation
    - **Impact:** Multi-instrument workflows are unclear

17. **No Decision Log / Design Decisions Document**
    - Why was 4-layer architecture chosen?
    - Why are timestamps tz-aware only?
    - Why OptimizationStore separate from AnalysisPackage?
    - **Impact:** Architecture decisions seem arbitrary

18. **No Glossary / Terminology Index**
    - Terms like "IS/OOS degradation," "PF," "MAE/MFE," "Sharpe," "Sortino" used without definition
    - New users don't have a reference
    - **Impact:** Friction for non-trading-background contributors

---

## Mapping: Features → Documentation Gaps

### Capability 1: Backtest Report Generation
| Feature | Documentation | Gap |
|---------|---|---|
| YAML config | CLAUDE.md (high level) | ❌ No schema, no example per feature block |
| Report sections | None | 🔴 **CRITICAL: No catalog of 120+ sections** |
| Section rendering | None | ❌ No guide on how to add new sections |
| Card export | None | ❌ PNG export options/limitations undocumented |
| Multi-run comparison | GETTING_STARTED.md (basic) | ❌ No advanced comparison examples |

### Capability 2: Strategy Discovery Funnel
| Feature | Documentation | Gap |
|---------|---|---|
| 6-stage workflow | discovery/README.md | ✅ Good |
| Quick scan → validate | discovery/README.md | ✅ Good |
| Quick command ref | discovery/README.md | ✅ Good |
| Editing YAML | discovery/README.md (brief) | ❌ No parameter-by-parameter guide |
| Signal tiers | discovery/README.md | ✅ Good |
| Template generation | None | ❌ No guide on NinjaTrader template export |

### Capability 3: Daily Prediction (Claude)
| Feature | Documentation | Gap |
|---------|---|---|
| Setup | prediction/README.md | ✅ Good |
| Running predictions | prediction/README.md | ✅ Good |
| Measuring outcomes | prediction/README.md (Python example only) | ❌ No CLI for measurement |
| What Claude predicts | prediction/README.md (stub) | ❌ Prediction output format not detailed |
| Scoring | None | ❌ Proper scoring rules not explained |
| Calibration/drift | None | 🟠 **HIGH: No ECE interpretation guide** |

### Capability 4: Horizon Prediction (Multi-Agent)
| Feature | Documentation | Gap |
|---------|---|---|
| System overview | prediction/README.md | ✅ Good |
| Built-in agents | prediction/README.md (brief) | ❌ Agent mechanics not detailed |
| Walk-forward backtest | prediction/README.md (command only) | 🟠 **HIGH: No step-by-step tutorial** |
| Tradable zone calculation | None | ❌ Kelly sizing, risk of ruin explained only in code |
| Ensemble stacking | None | 🟠 **HIGH: How weights are learned unclear** |
| Output interpretation | None | ❌ What CandleHorizonPrediction fields mean |

### Capability 5: Autonomous NinjaTrader Loop
| Feature | Documentation | Gap |
|---------|---|---|
| Full loop concept | nt_strategy_loop/README.md | ✅ Good high-level overview |
| StrategySpec format | nt_strategy_loop/README.md | ✅ Good |
| Repair heuristics | nt_strategy_loop/README.md | ✅ Documented |
| LLM repair (Ollama) | nt_strategy_loop/README.md | ✅ Documented |
| Optimizer bridge | nt_strategy_loop/README.md | ✅ Documented |
| AddOn setup | None | 🔴 **CRITICAL: D:\NinjaAccountManager referenced but not in scope** |
| Troubleshooting | None | 🔴 **CRITICAL: Common errors and recovery unclear** |
| Session folder layout | nt_strategy_loop/README.md | ✅ Good |

### Capability 6: Agentic Research Program (Phase A-D)
| Feature | Documentation | Gap |
|---------|---|---|
| Phase A (ledger) | None | 🔴 **CRITICAL: Research ledger structure opaque** |
| Phase B (triage/scribe) | None | 🔴 **CRITICAL: HITL loop unclear** |
| Phase C (authoring/operator) | None | 🔴 **CRITICAL: Hypothesis authoring workflow opaque** |
| Phase D (shadow/observation) | None | 🔴 **CRITICAL: Shadow monitoring not explained** |
| Agent role contracts | None | ❌ No definitions of what each role does |
| Inbox system | None | 🟠 **HIGH: Draft review workflow unclear** |
| Decision ledger | None | ❌ How decisions are logged/queried |
| Linting/validation | None | ❌ Linter rules and citation checking |

### Capability 7: Pattern Engine
| Feature | Documentation | Gap |
|---------|---|---|
| Concept | CLAUDE.md (brief) | ✅ Described |
| Template registry | CLAUDE.md (brief) | ❌ No template schema or examples |
| Sweep mechanics | None | ❌ How templates are parameterized/swept |
| Clustering | None | ❌ Feature similarity metric explained in code only |
| Monte Carlo robustness | None | ❌ How simulations are generated, interpreted |
| Output artifacts | CLAUDE.md (brief) | ❌ No parquet file schema |

### Capability 8: Entry Strategy Discovery (8 Families)
| Feature | Documentation | Gap |
|---------|---|---|
| 8 families overview | CLAUDE.md (listed) | ✅ List provided |
| Candle patterns | None | ❌ No guide to candle-specific features/signals |
| MA crossover | None | ❌ No guide to MA-specific signals |
| ORB strategy | None | ❌ No guide to opening range concepts |
| Outcome simulation | None | ❌ How trades are simulated unclear |
| Signal ranking | None | ❌ Ranking metrics (Sharpe, Sortino, etc.) not explained |
| IS/OOS validation | discovery/README.md (mention of degradation) | ❌ No formal explanation of degradation metric |

### Capability 9: Execution Bridge
| Feature | Documentation | Gap |
|---------|---|---|
| Signal protocol | TaFoundationExecutionBridge/signal_contract.md | ⚠️ Exists but not referenced in main docs |
| Message format | signal_contract.md | ✅ Documented in separate file |
| Python sender | bridge_sender.py example code | ❌ No standalone tutorial |
| Inbox/archive/reject | bridge_sender.py (code only) | ❌ Folder structure not explained |
| Heartbeat protocol | bridge_sender.py (code only) | ❌ Keep-alive semantics unclear |
| Soak monitoring | cli/soak_monitor.py (code only) | ❌ How to set up live monitoring |

### Capability 10: Web Workbench
| Feature | Documentation | Gap |
|---------|---|---|
| Launch | GETTING_STARTED.md | ✅ Good |
| Tabs overview | GETTING_STARTED.md | ✅ Good |
| Backtest Reports tab | GETTING_STARTED.md (stub) | ❌ No detail on tab workflow |
| Discovery UI | GETTING_STARTED.md (stub) | 🟠 **HIGH: Session management, resume unclear** |
| API routes | None | 🔴 **CRITICAL: No route reference** |
| Error handling | None | ❌ Error messages not explained |
| Job status tracking | None | ❌ Job lifecycle unclear |

---

## Action Plan (Prioritized by Impact × Effort)

### PHASE 1: Unblock Critical Paths (Week 1)

**Effort: 20-30 hours | Impact: High**

| # | Document | Purpose | Effort | Owner |
|---|----------|---------|--------|-------|
| 1 | **COMPLETE_CAPABILITIES_MATRIX.md** | One-page reference: capability → entry point → use case | 2h | Tech lead |
| 2 | **WEB_API_REFERENCE.md** | All Flask routes, request/response shapes | 4h | Backend eng |
| 3 | **NinjaTrader Integration Quick Start** | AddOn setup → repair loop → optimizer | 3h | Systems eng |
| 4 | **REPORT_SECTIONS_CATALOG.md** | All 120+ sections indexed by category | 3h | Tech lead + doc review |
| 5 | **AGENTIC_WORKFLOW_GUIDE.md** | Phase A-D overview + HITL loop | 4h | Agent architect |
| 6 | **PREDICTION_QUICK_START.md** | Daily + horizon prediction end-to-end examples | 3h | Data scientist |

**Outcome:** Users can discover features and find entry points. Critical workflows are no longer hidden.

---

### PHASE 2: Enable Independent Development (Week 2-3)

**Effort: 25-35 hours | Impact: High**

| # | Document | Purpose | Effort | Owner |
|---|----------|---------|--------|-------|
| 7 | **CONTRIBUTING.md** | First-day checklist + how to extend system | 3h | Tech lead |
| 8 | **Analysis Subsystem READMEs** | 3× (ma_structure, pattern_engine, entry_strategies) | 6h | Analysis architects |
| 9 | **DATA_MODEL_SCHEMA.md** | AnalysisPackage, metadata["derived"], examples | 4h | Core architect |
| 10 | **CONFIGURATION_SCHEMA.md** | YAML structure for all feature blocks | 3h | Tech lead |
| 11 | **TESTING.md** | How to write tests, fixture patterns | 3h | QA lead |
| 12 | **DISCOVERY_REGISTRY_GUIDE.md** | Graveyard refusal, Jaccard matching, overrides | 2h | Researcher |

**Outcome:** New contributors can add features independently. Code quality standards are explicit.

---

### PHASE 3: Advanced Workflows (Week 4)

**Effort: 20-25 hours | Impact: Medium**

| # | Document | Purpose | Effort | Owner |
|---|----------|---------|--------|-------|
| 13 | **PREDICTION_CALIBRATION_GUIDE.md** | ECE interpretation, drift detection, response | 3h | Data scientist |
| 14 | **AGENTIC_HYPOTHESIS_AUTHORING.md** | Step-by-step hypothesis registration → sweep | 3h | Researcher |
| 15 | **CONDITIONAL_PROMOTION_GUIDE.md** | How to generate next-probe YAML | 2h | Tech lead |
| 16 | **MARKET_DATA_HANDLING.md** | Ingest, timezone conversion, caching | 2h | Data eng |
| 17 | **MULTI_INSTRUMENT_WORKFLOWS.md** | Cross-contract discovery, batch operations | 2h | Tech lead |
| 18 | **HANDOFF_SPEC_WRITING.md** | How to write specs for Codex/Gemini/Grok | 2h | PM |

**Outcome:** Users can leverage advanced features. Complex workflows are documented.

---

### PHASE 4: Maintenance & Reference (Week 5+)

**Effort: 15-20 hours | Impact: Low-Medium**

| # | Document | Purpose | Effort | Owner |
|---|----------|---------|--------|-------|
| 19 | **GLOSSARY.md** | Terms: IS/OOS, PF, MAE/MFE, Sharpe, Sortino, ECE, etc. | 2h | Doc review |
| 20 | **ARCHITECTURE_DECISIONS.md** | Why 4-layer? Why tz-aware only? etc. | 2h | Core architect |
| 21 | **PERFORMANCE_TUNING.md** | Scaling pattern engine, report rendering, caching | 3h | Systems eng |
| 22 | **ERROR_REFERENCE.md** | Common errors and fixes | 3h | Support + tech leads |
| 23 | **TROUBLESHOOTING.md** | NinjaTrader issues, prediction failures, etc. | 3h | Support + systems eng |

**Outcome:** Users have comprehensive reference material. Support load decreases.

---

## Implementation Timeline

```
Week 1 (PHASE 1): May 27-31
├─ COMPLETE_CAPABILITIES_MATRIX.md (May 27)
├─ WEB_API_REFERENCE.md (May 28-29)
├─ NinjaTrader Integration QS (May 29-30)
├─ REPORT_SECTIONS_CATALOG.md (May 30-31)
├─ AGENTIC_WORKFLOW_GUIDE.md (May 31)
└─ PREDICTION_QUICK_START.md (May 31)

Week 2-3 (PHASE 2): June 3-14
├─ CONTRIBUTING.md (Jun 3)
├─ Analysis READMEs (Jun 4-7)
├─ DATA_MODEL_SCHEMA.md (Jun 8-9)
├─ CONFIGURATION_SCHEMA.md (Jun 10-11)
├─ TESTING.md (Jun 12)
└─ DISCOVERY_REGISTRY_GUIDE.md (Jun 13-14)

Week 4 (PHASE 3): June 17-21
├─ PREDICTION_CALIBRATION_GUIDE.md
├─ AGENTIC_HYPOTHESIS_AUTHORING.md
├─ CONDITIONAL_PROMOTION_GUIDE.md
├─ MARKET_DATA_HANDLING.md
├─ MULTI_INSTRUMENT_WORKFLOWS.md
└─ HANDOFF_SPEC_WRITING.md

Week 5+ (PHASE 4): Ongoing
├─ GLOSSARY.md
├─ ARCHITECTURE_DECISIONS.md
├─ PERFORMANCE_TUNING.md
├─ ERROR_REFERENCE.md
└─ TROUBLESHOOTING.md
```

---

## Success Metrics

### After PHASE 1
- ✅ New users can discover all 10 capabilities in < 5 minutes
- ✅ Web API is discoverable without reading source code
- ✅ NinjaTrader integration path is clear
- ✅ 120+ report sections are cataloged
- ✅ Agentic workflow is no longer a black box

### After PHASE 2
- ✅ First-time contributor can add a report section in < 2 hours
- ✅ Analysis module extension process is clear
- ✅ Testing patterns are documented and followed
- ✅ New subsystem additions follow established patterns

### After PHASE 3
- ✅ Advanced workflows (prediction, hypothesis authoring, conditional promotion) are used regularly
- ✅ Multi-instrument and batch workflows are adopted
- ✅ Support questions shift from "how do I..." to "why would I..."

### After PHASE 4
- ✅ Support load decreases 30-40%
- ✅ Onboarding time for new contributors decreases 50%
- ✅ Code quality improves (explicit patterns reduce variants)

---

## Resource Requirements

### Writing Capacity
- **Total effort:** ~120 hours across 4 weeks
- **Team:** 3-4 people (tech leads, domain experts, one doc reviewer)
- **Pace:** 30 hours/week (manageable alongside regular work)

### Review & QA
- **Per document:** 1-2 hours for review + feedback cycles
- **Total:** ~30 hours review/QA across 4 weeks
- **Owner:** 1 doc reviewer (rotating role)

### Maintenance
- **Ongoing:** 2-3 hours/week to keep docs fresh as code changes
- **Owner:** Document champion (rotating role)

---

## Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Docs become stale quickly | High | Establish "doc champion" rotation; update on every major feature |
| Over-documentation (no one reads) | Medium | Keep docs focused on "discovery" and "how-to," link to code for details |
| Scattered docs are re-discovered as gaps | Medium | Central index (COMPLETE_SYSTEM_MAP.md) links to all docs |
| API/config changes break docs | Medium | Auto-generate docs from code (docstrings, type hints) where possible |
| Effort estimate is optimistic | Medium | Phase 1 is time-boxed; Phase 2-4 can be deferred |

---

## Conclusion

The **ta_foundation** project has world-class architecture and engineering discipline. **Documentation is the last major lever for adoption and maintainability.**

**PHASE 1 (week 1, 20-30 hours) would unblock 80% of discovery blockers.** The remaining phases are ROI-positive (support load reduction, faster onboarding) but not critical.

**Recommended starting point:** Begin PHASE 1 immediately. Designate a tech lead to drive the effort. Expect 2-3 days of heads-down writing per person.

