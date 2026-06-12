# Regenerating Recipe Seed Templates

**Single source of truth.** This is the only doc you need for seed generation.
If another file in the repo says something different, that file is stale —
delete it or update it to match this one.

---

## Default flow — works for any strategy, no NinjaTrader UI needed

`regenerate_recipe_seed()` (`src/ta_foundation/web/optimizer_strategy_catalog.py`)
auto-generates a valid Optimize template from a strategy's C# source. **You do
not need to save a template from NinjaTrader first.** Any strategy compiled in
NinjaTrader's `bin/Custom/Strategies/` directory can be seeded directly from
its `.cs` file.

### From the web UI

1. Recipe Optimizer → Recipe Setup tab
2. Pick the strategy from the dropdown
3. Click **Regenerate seed**

The seed lands at:
```
C:\Users\Owner\Documents\NinjaTrader 8\templates\Strategy\<StrategyName>\<StrategyName>_recipe_seed.xml
```

That file is now selected as the seed for the current session.

### From Python

```python
from ta_foundation.web.optimizer_strategy_catalog import regenerate_recipe_seed

summary = regenerate_recipe_seed(
    "MyStrategy",
    instrument="NQ 06-26",       # optional
    from_date="2026-04-14",      # optional
    to_date="2026-05-14",        # optional
)
print(summary.path)
```

---

## What the generator does

It tries three sources in priority order. **In practice tier 3 always works**,
so the higher tiers exist only for users who want a curated baseline.

| Tier | Source | When it fires |
|------|--------|--------------|
| 1 | `src/ta_foundation/strategies/<name>/templates/sampleTemplate.xml` | A curated, in-repo baseline exists for that strategy (rare — optional override) |
| 2 | Any non-recipe-seed XML in `templates/Strategy/<name>/` under the NT install | The user has a hand-saved NT template they want to start from |
| 3 | `<name>.cs` parsed from `bin/Custom/Strategies/` | Default. Always available for any compiled strategy. |

Whichever tier supplies the XML, the generator then:

1. Sets `OptimizerType = DefaultOptimizer`.
2. Sets `OptimizationFitness = MaxProfitFactor`.
3. Removes `BacktestType` and sets `Category = Optimize`.
4. Patches `InstrumentOrInstrumentList`, `From`, `To` from arguments.
5. **Pins every `<OptimizationParameters>` block to `Min == Max ==
   ValueSerializable`**, so the seed contributes zero swept dimensions.
6. Injects a single `__recipe_placeholder__` sweep so NT still recognizes the
   file as an Optimize template. The placeholder is stripped at stage-template
   generation time (`_strip_recipe_placeholder_parameter` in
   `optimizer_recipe_templates.py`).

**The recipe planner is the sole source of sweep ranges.** If the baseline
contributed sweeps, NT would multiply them with the recipe's sweeps and a
30+ parameter strategy would explode into hundreds of millions of
combinations. This contract is enforced regardless of which tier supplied the
XML.

---

## Enum parameters come out EMPTY — consumers must post-patch them

`_normalize_csharp_default()` (`seed_template.py`) cannot resolve enum
right-hand sides (`PantheonRegimeMode.TrendingOnly`, `Calculate.OnBarClose`),
so Tier-3 seeds emit **empty tags** for every enum parameter:

```xml
<RegimeMode></RegimeMode>
<ForceEntry></ForceEntry>
```

Live-confirmed 2026-06-12: the optimizer-bridge AddOn applies the `<Strategy>`
block per-element with a try/catch, so empty enum tags are *silently skipped*
and the strategy runs with its `.cs` defaults for those parameters. Any
stricter consumer (XmlSerializer-style) aborts at the first empty tag and
loses everything after it.

**Convention:** anything that builds a runnable template from a seed must
post-patch every enum parameter to a concrete value with
`_replace_or_insert_strategy_tag()` (`web/optimizer_template_writer.py`).
See `scripts/run_parity_backtest.py` → `build_parity_backtest_template()` for
the pattern — it patches `DiscoveryExitPolicy`, `RegimeMode`, and `ForceEntry`,
then **hard-fails if any `<X></X>` empty pair remains** in the template. Copy
that guard into new template-producing flows.

---

## Optional: curating an in-repo baseline (Tier 1)

You almost never need this. Use it only when you want a specific
`BarsPeriod`, `TradingHoursSerializable`, or other NT-only setting that auto-gen
doesn't infer from the C# source.

1. Save a template from NT's Strategy Analyzer ("Save as Template").
2. Copy the XML to:
   ```
   src/ta_foundation/strategies/<StrategyName>/templates/sampleTemplate.xml
   ```
3. Commit it.

Next regenerate will pick up the curated baseline (Tier 1) and still pin every
parameter / inject the placeholder. The strategy-specific knobs you saved
flow through; the wide sweeps don't.

---

## Troubleshooting

**Tier 3 fails with "No NinjaScriptProperty parameters found".**
The C# source has no `[NinjaScriptProperty]` attributes. Either the file
is a base class or the parser couldn't see the attributes — confirm the
`.cs` exists in `bin/Custom/Strategies/<name>.cs` and that the properties
are decorated correctly.

**Stage 1 sends NT an absurd combination count (millions).**
Old `_recipe_seed.xml` files generated before 2026-05-28 carry wide sweep
ranges per-parameter. Click **Regenerate seed** again to overwrite the stale
file. The new generator pins everything.

**The regenerated seed shows no swept parameters in the seed dropdown.**
That's correct. The only swept row is the `__recipe_placeholder__` and the
UI hides it. The recipe planner is responsible for adding real sweeps when
it builds stage templates.

**I deleted the seed and now Regenerate seed fails.**
Make sure the strategy `.cs` is in NT's `bin/Custom/Strategies/`. If it
isn't there, Tier 3 has nothing to parse and the only remaining paths are
Tier 1 (curated baseline) or Tier 2 (hand-saved NT template).

---

## Source map

- `src/ta_foundation/web/optimizer_strategy_catalog.py` — `regenerate_recipe_seed()`, the three-tier resolver, `_pin_all_optimization_parameters()`, `_ensure_placeholder_parameter()`.
- `src/ta_foundation/nt_strategy_loop/seed_template.py` — Tier 3 auto-generator. `_bounded_sweep()` always returns `(default, default)` so generated baselines contribute zero sweeps.
- `src/ta_foundation/web/optimizer_recipe_templates.py` — stage template generator. `_strip_recipe_placeholder_parameter()` removes the seed's placeholder before NT sees the stage XML.
- `src/ta_foundation/web/app.py` — `POST /api/optimizer/strategies/<id>/regenerate-seed` route.
