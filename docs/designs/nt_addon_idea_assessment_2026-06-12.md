# Assessment: "Automated Strategy Deployment and Risk Management System" AddOn idea

**Source:** `docs/ideas/NTAddOnIdea.txt` · **Assessed:** 2026-06-12 · **Verdict: do not build as written.**
One section (programmatic strategy lifecycle) is extracted into a scoped Phase-2 research
spike below; the rest is deferred or already owned elsewhere.

---

## What the idea proposes

A new headless WPF "control deck" AddOn with four pillars:

1. NTWindow/NTTabPage UI with deployment grids and dashboards
2. Programmatic strategy lifecycle — load template XMLs, instantiate strategies by name,
   bind to Account/Instrument/BarsPeriod, flip `Enabled = true` without a click
3. Multi-account management and trade mirroring across SIM/live accounts
4. Account-level drawdown circuit breaker (`Account.Flatten()` on threshold breach)

## Why not as written

**It re-grows what already exists.** The ecosystem's documented recurring failure mode is
rebuilding owned capabilities (`docs/CAPABILITY_CATALOG.md`,
`docs/reference/EXTERNAL_PROJECTS_MAP.md`):

| Idea pillar | Already owned by |
|---|---|
| Headless strategy dispatch + batch runs | Optimizer batch AddOn (`D:\ninjatraderOptimizer`) — proven again live 2026-06-12 in the parity-loop validation |
| Template XML generation/parsing | `nt_strategy_loop/seed_template.py`, `optimizer_template_writer.py`, `REGENERATE_SEED_GUIDE.md` flow |
| Account layer | `D:\NinjaAccountManager` (sibling repo) |
| What gets deployed (selection) | Deployment Matrix 252 / pool + predictor manifest work |
| Trade mirroring | Explicitly hazardous territory — LocalTradeCopier was quarantined 2026-05-19; vendor tools (Replikanto) already installed |

**Its API claims are unverified.** The centerpiece call,
`NinjaTrader.NinjaScript.Strategy.Configurator.ProcessNewStrategy`, has **zero hits across
the full offline NT8 docs mirror** (1,152 pages in `NinjatraderDocScrapper`, checked
2026-06-12 — neither `ProcessNewStrategy` nor `Configurator` appears anywhere). The text
reads like LLM output; treat every named API in it as hypothesis, not fact. If a
programmatic enable path exists it is internal/undocumented and therefore
version-fragile — which is a reason to wrap it in ONE small primitive, not to build a
product on top of it.

**It inverts the dependency order.** A risk circuit breaker and live deployment console
matter only after strategy behavior is trustworthy — which is exactly what the parity
gate (Phase 1–3) is establishing. Building the console first automates the deployment of
unverified strategies.

## What to extract — the Phase-2 research spike

Phase 2 of the parity plan (`project_parity_automation`) needs exactly one new primitive:
**enable a configured strategy on a Playback connection without a human click.** That is
idea pillar 2, nothing more. Scope it as:

- **Research-only first**: find how NT's own UI enables a strategy (reflection inspection
  of the strategies grid / `StrategyBase.SetState`, the same way the existing
  `inspect_*.ps1` tooling in `D:\ninjatraderOptimizer` reverse-engineered the Strategy
  Analyzer). Check the NT support forum pages in the docs mirror for AddOn lifecycle
  threads. Do NOT trust `ProcessNewStrategy` until observed in the actual assemblies.
- **Delivery shape**: one new IPC command (e.g. `EnableStrategy`) on the EXISTING optimizer
  batch AddOn — same command/status JSON files, same authorization model. No new window,
  no WPF dashboards.
- **Guardrails** (unchanged from the parity plan): test hooks behind a TestMode flag;
  never re-grow trading logic into the AddOn.

## Deferred, with owners

- **Drawdown circuit breaker / account dashboards** → the account-risk-engine phase of
  `docs/designs/strategy_business_roadmap.md`; evaluate `D:\NinjaAccountManager` first.
- **Multi-account mirroring** → not planned. Quarantine history + vendor overlap make
  this negative-value; revisit only with a concrete business need.
- **WPF control deck UI** → the web UI (`ta_foundation.web.app`) is the operator surface
  of record; a second NT-native console splits state across two UIs.
