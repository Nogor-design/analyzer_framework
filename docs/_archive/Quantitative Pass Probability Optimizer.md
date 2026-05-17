Here’s a Quantitative Pass Probability Optimizer design you can drop into ta_foundation as an analysis module that sits on top of your PatternEngine cluster events + prop Monte Carlo. It optimizes risk controls and allocation to maximize:

max
⁡
  
𝑃
(
Hit Target Before DD
)
maxP(Hit Target Before DD)

—not raw expectancy.

1) Inputs and outputs
Inputs

From PatternEngine artifacts (already produced per run):

mc_events stream (cluster-level recommended): columns like
dt, entity_id (cluster_id), pnl_ticks, day_id, session_id, regime

tick_value_usd, tick_size

Prop constraints: trailing_drawdown_usd, daily_loss_limit_usd, profit_target_usd

Candidate cluster set (e.g., top 25 by OOS + FDR-passing)

Optimizer decision variables (what it searches)

These are the knobs that actually change pass probability:

Risk & trade governance

risk_per_trade_usd (or contracts)

max_trades_per_day

daily_stop_frac (stop trading after X% of daily loss limit)

dd_buffer_frac (stop/reduce when equity buffer < X% of trailing DD)

profit_lock_frac (reduce/stop after hitting X% of target)

Allocation

selection of clusters (top-K)

cluster weights (or “enabled/disabled by regime”)

optional: regime gating thresholds

Stress

slippage ticks scenario set (grid)

optional spread widening

Output (what you want)

A single table ranking policies:

policy_id

pass_prob (base)

pass_prob_stress_p90 (worst acceptable stress)

expected_days_to_pass

dd_breach_prob

daily_loss_breach_prob

p5_equity, p50_equity, p95_equity at evaluation end

max_consecutive_losses_p95

recommended guardrails (the knobs)

2) Objective function (institutional)

Primary objective:

𝐽
=
𝑃
(
target hit before trailing DD breach and daily loss breach
)
J=P(target hit before trailing DD breach and daily loss breach)

But you should optimize robustly across slippage/regimes:

𝐽
robust
=
quantile
𝑞
(
𝐽
(
stress
)
)
J
robust
	​

=quantile
q
	​

(J(stress))

Typical: q = 0.25 (optimize the 25th percentile pass probability across stress grid).

Hard constraints (reject policies that violate):

dd_breach_prob > threshold (e.g., 0.20)

daily_loss_breach_prob > threshold (e.g., 0.25)

expected_days_to_pass too large (optional)

3) The simulator you optimize against (deterministic MC)

You already have run_prop_monte_carlo(). The optimizer wraps it, but adds:

Path-dependent governance rules (these matter for trailing DD)

At each trade/day step in the simulated path:

enforce max_trades_per_day

apply daily_stop_frac: if daily PnL < -daily_loss_limit_usd * daily_stop_frac, stop trading for day

apply dd_buffer_frac: if equity - trailing_floor < trailing_drawdown_usd * dd_buffer_frac, reduce size or stop

apply profit_lock_frac: if equity gain > profit_target * profit_lock_frac, reduce size or stop

apply slippage stress: subtract slip_ticks * tick_value_usd * contracts per trade

This transforms the same underlying event stream into different pass outcomes.

Key: It’s risk control optimization, not entry optimization.

4) Search strategy (fast + avoids overfitting)

Don’t brute force everything. Use a staged search:

Stage A — coarse grid (screening)

Search over a small grid:

risk_per_trade_usd: [50, 75, 100, 125, 150]

max_trades_per_day: [2, 3, 4, 6]

daily_stop_frac: [0.5, 0.7, 0.85]

dd_buffer_frac: [0.10, 0.20, 0.30]

profit_lock_frac: [0.30, 0.50, 0.70, 1.0]

Evaluate pass prob on:

500 paths

2–3 slippage scenarios (0, 2, 4 ticks)

Keep top ~30 candidates.

Stage B — local refinement (coordinate descent / Bayesian opt)

For each survivor:

run 2,000–5,000 paths

full stress grid

refine continuous knobs (risk_per_trade, fractions) around best values

Stage C — validation (anti-overfit)

Re-evaluate winners on different time blocks / folds (walk-forward segments)

Penalize policies with high variance of pass probability across folds

Deliver top 5 with fold-consistency scores.

5) How to integrate into ta_foundation cleanly
New analysis module

Add (analysis layer):

src/ta_foundation/analysis/pattern_engine/pass_optimizer.py

It should expose:

build_equity_events_for_optimizer(...) (from artifacts)

simulate_policy_paths(...) (calls MC core with governance)

optimize_pass_probability(...) (staged search)

write_artifacts(...)

Orchestrator hook

In pattern_engine/orchestrator.py, after MC runs, add optional:

if options.get("pass_optimizer", {}).get("enabled", False):
    run_pass_optimizer(...)

Write artifacts:

pass_opt_results.parquet

pass_opt_best_policy.json (JSON-safe, tiny)

Attach to:
pkg.metadata["derived"]["pattern_engine"]["artifacts"]["pass_opt_results"]

New report section (render-only)

Add:

pass_optimizer_overview showing top policies + stress table + recommended guardrails

6) Recommended YAML block

Top-level (global), not section-local:

pattern_engine:
  # ...existing...
  pass_optimizer:
    enabled: true
    entity_level: "cluster"         # "pattern" allowed but not recommended
    candidate_top_k: 25
    objective_quantile: 0.25        # optimize worst-ish case across stress
    n_paths_screen: 500
    n_paths_refine: 3000
    slippage_ticks_grid: [0, 1, 2, 4]
    folds: 5                        # walk-forward segments for validation

    grid:
      risk_per_trade_usd: [50, 75, 100, 125, 150]
      max_trades_per_day: [2, 3, 4, 6]
      daily_stop_frac: [0.5, 0.7, 0.85]
      dd_buffer_frac: [0.10, 0.20, 0.30]
      profit_lock_frac: [0.30, 0.50, 0.70, 1.0]

    constraints:
      max_dd_breach_prob: 0.20
      max_daily_loss_breach_prob: 0.25
      max_expected_days_to_pass: 20
7) Practical defaults for common prop setups

If trailing DD is tight (e.g., $1500):

risk_per_trade_usd typically 50–100

max_trades_per_day 2–4

dd_buffer_frac 0.20–0.30

daily_stop_frac 0.70–0.85

profit_lock_frac 0.50–0.70

These knobs are exactly what the optimizer should discover.

8) What “good” looks like

For a viable evaluation strategy, you want:

base pass_prob ≥ 0.65

pass_prob at 2–4 ticks slippage still ≥ 0.55

dd_breach_prob ≤ 0.20

consistent across folds (low dispersion)

If you can’t get that, the edge is either too weak or too volatile for prop constraints.