# Strategy Business & Capability Roadmap

*Created 2026-06-05. Owner: Eric. PM: Claude. Status: draft for agreement.*
*This is the sequencing-of-record. It ties together the trading-capability work and the
business build-out so we agree on ORDER and DEPENDENCIES before building. Individual
phases get their own design docs as they start.*

---

## North star

Turn what is today a **signal service** (a pool of MA-cross templates + a daily lineup)
into **automated, account-aware risk execution for prop & funded traders** — a system that
extracts edge *and* keeps each account alive under its specific trailing-drawdown rules.
The eventual business (sales, marketing, support) is downstream of proving that core works.

## Who we serve (context)

Active day traders running our strategies on live + prop accounts. Each account differs by:
trailing-drawdown position, challenge vs funded status, and firm-specific DD math. Some
clients run 20+ prop accounts that must be traded *differently* (challenge accounts trade to
pass; funded accounts trade to protect). Today the weekly plan goes out by meeting + email; a
separate prediction AI (fed chart data + templates) picks a daily lineup.

## Guiding principles (apply to every phase)

1. **Edge honesty over feature count.** This codebase has a documented history of fake edges
   from weak validation (same-instrument IS/OOS). Nothing scales until it beats a baseline
   out-of-sample. A measured negative result is a real deliverable.
2. **Account survival is the product.** Most prop failures are DD violations, not bad
   strategies. Risk-management correctness ranks above signal quality.
3. **Reuse the existing layers; stay additive.** The 4-layer architecture, the optimizer
   recipe pipeline, exit-sim, regime analysis, and the `template_naming` DD math
   (`compute_true_max_loss`, `compute_effective_trades`, `prop_max_daily_loss`) are the
   foundation. New work extends them; it does not fork them.
4. **Prove, then systematize, then scale.** Don't build the storefront before the proof.

## Dependency graph (why this order)

```
Phase 0  Measurement loop (in flight) ──┬──> Phase 1  Prediction-AI validation
                                        │
                                        └──> Phase 2  Account-risk layer (differentiator)
                                                          │
Phase 1 + Phase 2 ───────────────────────────────────────┴──> Phase 3  Productized weekly deliverable
                                                                              │
Phase 3 + honest track record ─────────────────────────────────────────────> Phase 4  Go-to-market
```

A meaningful track record (the asset that makes Phase 4 possible) requires BOTH a defensible
selection method (Phase 1) AND accounts that don't blow up (Phase 2). That's why those two are
the spine.

---

## Phase 0 — Lock the measurement loop (NOW, mostly in flight)

**Goal:** make the current weekly process repeatable and *instrumented*, so every later claim
is backed by numbers. Cheap; largely existing work.

**Deliverables**
- Finish the deployment-matrix pool hardening: the A/B risk-knob / trade-aware-selection
  experiment (`.ta_artifacts/dm_risk_experiment_plan.md`) — run when Eric is away.
- Wire MA-pool enrichment (regime + exit-policy pre-selection) into pool selection per the
  existing design (`ma_pool_enrichment_and_pantheonmaster_migration.md`).
- Stand up a simple **outcome ledger**: for each weekly lineup, record what was recommended
  vs what actually happened (per template, per account). This is the raw material for both
  the prediction-AI audit and the track record. **✅ BUILT 2026-06-08** —
  `analysis/selection/ledger.py` (append-only JSONL: `recommendation` + `actuals`, joined by
  `ledger_id`), `grade.py` (`track_record` + `grade_against_baselines` on the same graded
  days), drivers `scripts/record_lineup.py` / `scripts/grade_ledger.py`. Validated end-to-end
  on opt_a09359e6b60b (20 backfilled days): it reproduces the honest Phase-1 verdict through
  the *ledger* pipeline — composite v1 wins net/expectancy ($826/d) but **fails survival**
  (maxDD −$3,035, Sharpe 0.38) vs `equal_weight` (maxDD −$361, Sharpe 1.02). Per-account
  actuals are deferred to Phase 2 (the ledger schema already keys per template).

**Reuses:** optimizer recipe pipeline, deployment matrix, exit-sim, regime recommender.
**Exit criteria:** a repeatable weekly run that emits a manifest + an outcome ledger entry. ✅

## Phase 1 — Validate (or replace) the prediction AI

**Goal:** answer one question — *do the daily lineup picks beat a dumb baseline out-of-sample?*

**Deliverables**
- Document what the prediction agent is trained on, its inputs, and how (if at all) its picks
  have been validated.
- Backtest its historical picks vs baselines (e.g. "run the top-PF template per cell,"
  "run the regime-matched template," "equal-weight the pool") on held-out data.
- Verdict: **keep / fix / replace.** If it can't beat baseline OOS, the baseline becomes the
  selector until a better method is proven.

**Reuses:** walk-forward validation, permutation/null tests (anti-fake-edge, already shipped),
the Phase-0 outcome ledger.
**Gate:** do not market "AI-selected lineups" until this passes. **Owner mix:** Claude designs
the test; Codex builds the harness; Claude reviews.

## Phase 2 — Account-risk layer + NT account-management plugin (THE DIFFERENTIATOR)

**Goal:** convert generic pool signals into **per-account daily plans** that respect each
account's trailing-drawdown rules and challenge/funded status.

**The hard part is NOT the NT hooks — it's the per-firm DD rules engine.** Sequenced sub-phases:

- **2a — DD rules research.** Catalog each prop firm's exact trailing-DD math (intraday vs
  EOD, balance vs equity, high-water-mark, lock-at-funded behavior) and challenge-vs-funded
  differences. *Owner:* a web-research model (Grok/Gemini) drafts; Claude verifies; encode as
  per-firm **risk profiles** (config, not code).
- **2b — DD / risk engine (Python).** Given an account's firm profile + current balance/threshold,
  compute remaining cushion and a per-account **daily risk budget**. Extends
  `compute_true_max_loss` / `compute_effective_trades` / `prop_max_daily_loss`.
- **2c — Allocator.** Map the weekly pool → per-account daily lineup + size + daily-loss cap,
  respecting the budget and challenge/funded intent (challenge = trade to pass; funded =
  protect). Pure Python, testable offline.
- **2d — NT AddOn (data pipe).** Read NT-connected account state (equity, open positions,
  daily P&L); let the user enter firm + trailing-DD parameters. Leverages existing NT-internals
  experience. **Scope caveat:** only sees NT-connected accounts — confirm all clients trade
  through NT (firms on their own platforms are out of scope v1).

**Reuses:** DD math primitives, exit-sim, the NT bridge for execution.
**Risks to validate before live:** managed-SL/TP backtest vs explicit-stop+ChangeOrder live
(trail parity trap); ATR Wilder-vs-SMA parity; DD math correctness (a wrong "safe" signal is
worse than none). **Gate:** DD engine validated against known account histories before any
client relies on it.

## Phase 3 — Productize the weekly deliverable

**Goal:** turn the weekly meeting + emails into a repeatable, semi-automated client deliverable:
per-account daily plans generated from Phases 1+2, delivered in a consistent format.

**Reuses:** existing report/HTML infra, the weekly coverage package.
**Exit criteria:** a client receives an account-specific weekly/daily plan with zero manual
spreadsheet work.

## Phase 4 — Track record → go-to-market

**Goal:** only after Phases 1–3 produce honest proof on the current small client list —
**(1) accounts stay alive (no DD violations), (2) positive expectancy OOS** — build the
business layer: sales, marketing, support, onboarding.

**Hard prerequisite:** a short, honest, *live* track record. The proof is the product's
moat and its marketing.
**Compliance flag (do once, before scaling clients/marketing):** providing daily trade plans
to clients trading real money may be construed as regulated investment advice (CTA territory in
the US) depending on structure. Get a one-time securities-attorney review before public
marketing and client-list expansion. Not an engineering blocker — just must not surprise us.

---

## AI bench (how we staff the work)

- **Claude** — PM, architecture, specs, code review, the validation/edge-honesty calls.
- **Codex** — heavy mechanical builds (harnesses, engines) from tight specs.
- **Gemini** — well-specified additive work; lighter tasks.
- **Grok / web-capable models** — external research (the per-firm DD rules in 2a), Claude verifies.
- **Local LLMs** — light/cheap tasks.

## Open questions to resolve as phases start

1. Do **all** current clients trade through NinjaTrader? (gates Phase 2d scope)
2. What is the prediction AI actually trained on, and has anyone measured it vs a baseline? (Phase 1)
3. Which prop firms do the current clients use? (bounds the Phase 2a rules catalog)
```
