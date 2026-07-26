# NT Fitness Parameter Control — PARKED

*Created:* 2026-06-05 · *Status:* **parked, not scheduled** · *Reads-with:*
`docs/designs/deployment_matrix_optimization_redesign.md`

## Decision: don't build this yet

The idea: let the web optimizer set custom NT optimization-fitness properties
(`MinTrades`, `MaxDrawdownPct` on `CustomMultiObjectiveFitness`) the way it
already sets optimizer type and fitness class.

We're **not doing it now.** The deployment-matrix redesign concluded the
post-hoc Python min-trades floor (`refine_selection_min_trades`, default 10) and
the existing walk-forward gate already give us trades-backed, honest cells. An
in-optimizer fitness gate is more machinery (C# + AddOn rebuild) for a benefit we
haven't proven we need.

## The one thing that would revive it

An **in-search** `MinTrades` gate differs from the **post-hoc** floor in exactly
one way: it changes what NT *retains* (`KeepBestResults`) during the search, not
just what we filter afterward. It only earns its keep if NT's `KeepBestResults`
is discarding trades-backed candidates *before* our floor can select them.

**Reviving gate (one experiment, do this before any code):** on a small slice
(1–2 sessions × 3 tiers, NQ, current window), inspect the default optimizer's
retained rows. Does the top-N already contain enough ≥10-trade candidates for the
floor to pick a good lane winner? If **yes** → the floor is sufficient, keep this
parked. If **no** (the optimizer threw the trades-backed rows away) → build it.

## If revived, the change is small (pointer, not a plan)

- AddOn already has `ApplySimpleXmlProperties`; in `ApplyOptimizerTemplate`
  (`D:\ninjatraderOptimizer\NinjaTraderAddOnProject\StrategyAnalyzerAutomation.cs`)
  apply an `<OptimizationFitnessParameters>` block to the instantiated fitness
  object before the run.
- Web side: add `optimization_fitness_parameters` to the recipe doc
  (`optimizer_recipe.py`) and emit the XML block after `<OptimizationFitness>`
  (`optimizer_recipe_templates.py`).
- Runtime caveat to verify: `MaxDrawdownPct` shape (is it `20`, `0.20`?).
- Keep custom fitness source in the standalone `NinjaTraderOptimizerProject`, not
  the batch AddOn (avoid duplicate NinjaScript type compilation).

## Caution if/when we add gates

We already have **two** trade gates: the Python selection floor (default 10) and
the final-review guardrail (default 10). An NT-fitness `MinTrades` would be a
third, at a third layer, and they interact non-obviously (NT `MinTrades=30` +
Python floor 10 ⇒ 10–29-trade rows never exist to select). If this is ever built,
ship a one-line table of *which gate, which layer, what for* with it.
