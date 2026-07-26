AI EXTENSION STARTER PROMPT

For extending the Pattern Engine inside ta_foundation

You are extending a production-grade Python reporting framework named ta_foundation.

This system already has a wired Pattern Engine subsystem.
Your job is to extend it without breaking architecture contracts.

1️⃣ Architectural Rules (Non-Negotiable)
Rendering Contract

HTML sections are pure render functions:

def render_section(ctx: Dict[str, Any]) -> str:

Sections:

DO NOT perform analysis

DO NOT reload files

DO NOT call compute functions

Only read from:

ctx["packages"]

ctx["market"]

pkg.metadata["derived"]

Context Contract

When a section is rendered:

ctx = {
    "packages": packages,
    "market": market,
    "report_config": cfg,
    "all_options": cfg.raw,     # FULL YAML
    "options": section.options  # SECTION-LOCAL only
}
Important:

ctx["options"] = section-local options only

ctx["all_options"] = full YAML (contains pattern_engine: block)

Never assume ctx["options"] contains global config.

2️⃣ Pattern Engine Execution Flow

Pattern Engine runs once in:

build_report_from_config()

Before rendering sections:

compute_and_attach_pattern_engine(packages, market, options=cfg.raw.get("pattern_engine", {}))

It attaches results to:

pkg.metadata["derived"]["pattern_engine"]

Sections consume only this metadata.

Never move compute into render.

3️⃣ Pattern Engine Structure

Pipeline:

orchestrator
    → run_pattern_sweep()
    → build_pattern_clusters()
    → compute_purged_walkforward_cv()
    → run_prop_monte_carlo()

Artifacts written to:

.ta_artifacts/pattern_engine/<run_id>/

Metadata contains:

{
  version,
  engine,
  artifacts,
  diagnostics
}
4️⃣ Template Registry Rules

Templates are registered in:

analysis/pattern_engine/templates/builtins.py

Registry key format:

family::structure

Example:

ORB::orb_break_retest

If a template is missing, registry throws:

KeyError("... available templates: ...")

All new templates must be registered via default_template_registry().

5️⃣ Market Data Rules

All bars and ticks must come from:

MarketDataStore

Never reload CSV/parquet inside PatternEngine or sections.

All timestamps are localized to:

America/Denver
6️⃣ Known Stability Fixes (Do NOT Regress)

These issues have already been fixed:

Top-level YAML blocks preserved in config

Compute runs in builder, not render

JSON-safe snapshot of options

No DataFrame .setdefault() misuse

Template registry not empty

Section/global option confusion resolved

Do not reintroduce these.

7️⃣ YAML Structure (Correct Layout)
report:
  title: ...
  output_filename: ...

pattern_engine:
  enabled: true
  instrument: ES
  contract: "03-26"
  tick_size: 0.25
  timeframe: "1m"
  horizons: [10,20,40]

  sweep:
    patterns:
      - family: ORB
        structure: orb_break_retest
        params:
          orb_minutes: [5,10]
          retest_bars: [1,2]

  clusters:
    method: kmeans
    k: 25

  cv:
    n_folds: 5

  prop:
    trailing_drawdown_usd: 1500
    daily_loss_limit_usd: 1000
    tick_value_usd: 12.5

  monte_carlo:
    n_paths: 2000
    block_unit: day

sections:
  - id: pattern_engine_overview
    options:
      top_n_runs: 12
8️⃣ When Extending Pattern Engine

Follow this decision tree:

Adding new pattern template?

Add detector in templates/

Register in builtins.py

Ensure registry key matches YAML

Adding new robustness metric?

Implement in analysis layer

Store in artifacts

Add column to metadata

Render in section (read-only)

Adding new prop constraint?

Extend run_prop_monte_carlo()

Keep equity simulation deterministic

Never embed heavy objects in metadata

Adding new visualization?

Modify section only

Never modify compute for visual needs

9️⃣ Robustness Goals (Preferred Direction)

When improving Pattern Engine, prioritize:

FDR correction for multiple testing

Parameter stability scoring

Regime-aware Monte Carlo

Bootstrap cluster stability

Prop-firm survival probability modeling

Avoid adding fragile feature engineering before strengthening robustness.

🔟 If Something Breaks

If PatternEngine disables:

Inspect pattern_engine_exception

Verify bars found via MarketDataStore

Confirm template registered

Ensure sweep patterns configured

Ensure clustering k ≤ number of patterns

System Identity Summary

This is a deterministic research framework for:

Futures markets (ES, NQ)

Prop firm evaluation survival

Cluster-based pattern ranking

Monte Carlo robustness validation

The design philosophy is:

Analysis first. Render second.
Deterministic artifacts.
No hidden state.
No implicit reloads.

If you need to change architecture, explain why first.
If extending features, modify the smallest possible surface area.