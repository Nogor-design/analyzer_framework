A) PatternEngine internal object model (robust version)
Core objects (analysis-layer; not in sections)

PatternTemplate

family, structure

param_schema (tokens, semantics >= / <=, units)

eligibility (requires_ticks, valid_regimes)

detect_fn (bar/tick feature inputs → boolean mask + direction)

feature_keys_emitted (so clustering/meta-model uses the same schema)

PatternInstance

pattern_id (canonical)

params (normalized dict)

template_ref

“signature” vector (engine-generated numeric representation for clustering; see below)

SignalEvent

pattern_id

dt, instrument/contract, direction

entry_ref_price

features: flat numeric dict (already in spec)

group keys: session_id, day_id, regime_label, bucket (needed for block bootstrap + CV)

OutcomeRecord

per-horizon: ret_ticks_H, mfe_ticks_H, mae_ticks_H

optional: time_to_mfe/mae

prop proxies (derived cheaply): adverse excursion distribution, worst adverse over H, etc.

PatternStats

aggregated metrics (net, avg, win_rate, p10/p90, etc.)

stability metrics (below)

prop survival metrics (from Monte Carlo layer)

Cluster

cluster_id

member pattern_ids

cluster centroid signature

aggregate stats (and dispersion across members)

“winner selection” policy: pick representative instance(s) per cluster

Key change: You don’t primarily rank PatternInstance. You rank Cluster, then pick a small set of representatives per cluster. This collapses the hypothesis space.

B) Monte Carlo robustness layer integrated with the sweep framework
What “robustness” must answer (for prop accounts)

For each candidate (cluster or representative instance), compute distributions of:

Max drawdown (trailing & closed-equity versions)

Max daily loss

Longest losing streak

Probability of breach under given rules (Apex-style trailing DD, daily cap)

Expected time-to-breach

“Pass probability” for eval rules (profit target + constraints)

Sensitivity to slippage/fees

Why standard trade-resampling is insufficient

Signals are temporally clustered and regime-dependent. So the Monte Carlo must preserve:

within-day dependence

regime clustering

volatility clustering

Recommended Monte Carlo design (3-tier, escalating cost)
MC-1: Block bootstrap on “trade blocks”

Build “blocks” = all signal outcomes within:

a session, or

a fixed time window (e.g., 30–60 minutes), or

a day.

Resample blocks with replacement to generate synthetic equity paths.

Preserves clustering and “bad day” behavior.

MC-2: Regime-stratified block bootstrap

Partition blocks by regime label (TREND/CHOP/EXP/SQZ).

Resample regime sequences using a simple Markov chain estimated from historical regime transitions.

This directly tests regime-change survivability: “what if EXP clusters 2× as often?”

MC-3: Stress Monte Carlo (prop-focused)

On top of MC-2 paths, apply stress transforms:

+slippage (ticks): {+1, +2, +4}

widened stops / worse fills for high spread_pct deciles

skip-trade rules under low liquidity (simulates live constraints)

“news shock days” injected (forced adverse move blocks)

Integration point with your sweep engine

After Tier 1 discovery, you do cluster formation (below), then run Monte Carlo on:

cluster-level aggregated return stream, and/or

a small set of representatives per cluster (e.g., top-2 stable members).

This prevents running expensive MC on 2,000 instances.

Outputs added to PatternStats / ClusterStats

Add these fields (minimum):

mc_dd_p50, mc_dd_p90, mc_dd_p99

mc_daily_loss_breach_prob

mc_trailing_dd_breach_prob

mc_eval_pass_prob (if you model targets)

mc_median_days_to_breach

mc_ulcer_index (or similar path pain metric)

stability_oos_score (see next section)

Then your leaderboard can rank by something like:

PropSurvivalScore

maximize expected net ticks

penalize breach probabilities

penalize tail DD

penalize instability across folds/regimes

C) Overfitting control: “purged walk-forward CV” + stability scoring

To have any confidence in “real edge,” you need out-of-sample evaluation integrated into scoring.

Recommended CV protocol (time-series safe)

Split by time into K folds (e.g., 5 folds).

For each fold:

train = prior window

test = next window

purge/embargo around boundaries by H horizon and retest windows (prevents leakage via overlapping outcomes)

Compute stats per fold.

Stability metrics (hard requirements)

For each pattern/cluster:

oos_net_ticks (sum across test folds)

fold_dispersion (std/iqr across folds)

regime_dispersion (variance across regimes)

sign_consistency (% folds with positive expectancy)

parameter_neighborhood_stability (see below)

If a candidate is “great” in-sample but inconsistent OOS, it’s curve-fit by definition.

D) AI meta-model that ranks pattern clusters (not patterns)

This is the right move. It reduces multiple-testing and produces a portfolio-like selection.

Step 1: Build cluster signatures

Each PatternInstance gets a vector embedding that is deterministic and comparable:

template family/structure one-hot (or learned embedding)

parameter values (normalized)

expected trigger “geometry” features (e.g., typical vol_pct/range_atr_mult at signal time)

regime distribution of signals

time-of-day distribution

tick-quality distribution (if applicable)

Then cluster via:

hierarchical clustering or HDBSCAN (density-based is nice for pruning),

distance metric mixing params + behavioral features.

Step 2: Define the meta-learning target

The meta-model should predict out-of-sample robustness, not in-sample net ticks.

Targets:

oos_prop_survival_score (from CV + Monte Carlo on test folds)

or multi-target: (oos expectancy, tail risk, breach probability)

Step 3: Features to the meta-model (cluster-level)

cluster centroid signature

intra-cluster dispersion (how “tight” the concept is)

performance dispersion across folds/regimes

signal count (liquidity of edge)

sensitivity to slippage (stress test deltas)

stability to parameter perturbation (“neighborhood”)

Step 4: Meta-model form (keep it simple)

Start with something interpretable and robust:

Gradient-boosted trees or regularized regression on engineered stability features.

Then consider a more complex model later.

Critical: the meta-model must be trained in a nested way:

Outer split for evaluation of the meta-model itself.

Inner training uses only past data.

Outcome: what you rank

You rank clusters, then pick:

1–3 representatives per cluster that are most stable (not necessarily the highest net ticks).

This naturally yields a diversified “pattern portfolio” that is more likely to survive regime changes and prop constraints.

5) The redesigned workflow (end-to-end)
Phase 0: Correctness gate (mandatory)

Run Tier 0 + validation suite exactly as your spec outlines.

PATTERN_ENGINE_DESIGN

Phase 1: Discovery (cheap, broad)

Tier 1 sweeps

Fixed-horizon outcomes

Basic filters (min signals)

No “final selection” here

Phase 2: Structure reduction (cluster)

Build embeddings for all non-trivial instances

Cluster

Compute cluster stats + dispersion

Drop clusters that are:

too rare,

too unstable in-sample,

too dependent on ticks (unless explicitly desired)

Phase 3: Robustness evaluation (time-series CV)

Purged walk-forward CV at cluster and representative level

Produce OOS stats

Phase 4: Monte Carlo (prop-focused)

Run MC-1/2/3 only on top clusters by OOS score

Compute breach probabilities and DD distributions

Re-rank by PropSurvivalScore

Phase 5: Final selection + deployment packaging

Choose diversified set:

limited number of clusters,

limited exposure per regime/time bucket,

explicit “kill-switch” conditions.

your contracts:

Sections are pure render functions

All options from report.yaml via ctx["options"]

Use AnalysisPackage only (no reloading files)

Market data shared in ctx["market"]

Timestamps are tz-aware America/Denver

I’m going to give you:

A minimal analysis-layer API surface (module layout + function signatures)

The exact dataframe schemas returned

The exact package metadata schema to attach under pkg.metadata["derived"]["pattern_engine"]

The HTML section contracts (what they read; no analysis inside them)

The Monte Carlo layer integration points (including prop constraints)

The cluster meta-model hooks (rank clusters, not patterns)

1) Minimal module layout and APIs
1.1 New analysis modules (smallest set)
ta_foundation/analysis/pattern_engine/model.py

Dataclasses / typed dicts only (no IO, no pandas heavy lifting).

Core types

PatternTemplate

PatternInstance

SignalEvent

OutcomeRecord

ClusterSpec

PropConstraints

MonteCarloConfig

MetaModelConfig

ta_foundation/analysis/pattern_engine/engine.py

Pattern detection + signal generation (vectorized over bars; optional tick confirm hooks).

Primary entry

def run_pattern_sweep(
    *,
    pkg: AnalysisPackage,
    market: dict,                       # ctx["market"]
    options: dict,                      # ctx["options"]["pattern_engine"]
) -> dict:
    """
    Returns a dict of pandas DataFrames + dict payloads (schemas defined below),
    suitable for attaching to pkg.metadata["derived"]["pattern_engine"].
    """
ta_foundation/analysis/pattern_engine/cluster.py

Clustering and cluster-level aggregation.

def build_pattern_clusters(
    *,
    signals_df,
    outcomes_df,
    pattern_stats_df,
    options: dict,   # ctx["options"]["pattern_engine"]["clusters"]
) -> dict:
    """
    Returns clusters_df, cluster_members_df, cluster_stats_df, embeddings_df.
    """
ta_foundation/analysis/pattern_engine/robustness_cv.py

Purged walk-forward CV metrics.

def compute_purged_walkforward_cv(
    *,
    events_df,          # signal+outcome merged (one row per signal per horizon)
    options: dict,      # cv settings + embargo/purge
) -> dict:
    """
    Returns fold_stats_df, oos_stats_df (pattern + cluster variants).
    """
ta_foundation/analysis/pattern_engine/monte_carlo.py

Block bootstrap + regime stratified + stress transforms; prop evaluation.

def run_prop_monte_carlo(
    *,
    equity_events_df,   # one row per realized trade/event in time order
    constraints: dict,  # PropConstraints
    mc_options: dict,   # MonteCarloConfig
) -> dict:
    """
    Returns mc_summary_df + mc_paths_df (optional) + breach_stats_df.
    """
ta_foundation/analysis/pattern_engine/meta_model.py

Optional. Learns ranking over clusters based on stability/robustness features.

def train_cluster_meta_model(
    *,
    cluster_features_df,
    target_df,
    options: dict,      # MetaModelConfig
) -> dict:
    """
    Returns model_artifact (serializable dict) + predictions_df + feature_importance_df.
    """
2) DataFrames: exact schemas

These are the “contracts” the rest of the system uses.

2.1 patterns_df (instances generated by sweep)

One row per pattern instance (canonical ID).

Columns:

pattern_id (str) primary key

family (str) e.g., "ORB", "REV", "MIC"

structure (str) e.g., "orb_break_retest", "pin_reject"

direction_mode (str) "long"|"short"|"both"

params_json (str) canonical JSON (stable key order)

requires_ticks (bool)

version (str) (engine version hash)

signature_v (object) (numpy array or list[float]) optional

created_utc (datetime) optional

2.2 signals_df

One row per signal occurrence (per pattern).

Columns (minimum):

signal_id (str) unique stable ID (pattern_id + dt + direction + instrument)

pattern_id (str)

dt (datetime tz-aware America/Denver)

instrument (str) "NQ" / "ES"

contract (str) "03-26" etc

direction (int) +1 / -1

entry_ref_price (float) (bar close or tick trigger)

session_id (str) e.g., "2026-02-03_RTH" or "2026-02-03_ETH"

day_id (date)

tod_bucket (str) e.g., "open_0_30", "mid", "close"

regime (str) e.g., "TREND"|"CHOP"|"EXP"|"SQZ"|...

spread_pct (float) optional

liq_bucket (str) optional

features_json (str) canonical JSON of numeric features (flat)

2.3 outcomes_df

One row per signal per horizon (fixed-horizon outcomes).

Columns:

signal_id (str)

pattern_id (str)

dt (datetime) (same as signal dt)

horizon (int) bars or minutes (whichever your engine defines; must be consistent)

ret_ticks (float)

mfe_ticks (float)

mae_ticks (float)

exit_ref_price (float) optional

time_to_mfe (float) optional

time_to_mae (float) optional

2.4 pattern_stats_df

One row per pattern_id per horizon or per horizon collapsed (pick one; I recommend per horizon).

Columns:

pattern_id

horizon

n_signals

net_ticks

avg_ticks

win_rate

p10, p50, p90

mfe_p50, mae_p50

expectancy (avg_ticks)

edge_tstat (optional)

quality_flags (str) e.g., "min_signals_fail;leakage_warn"

rank_score_raw (float) (pre-robustness score)

2.5 embeddings_df (for clustering)

One row per pattern instance.

Columns:

pattern_id

emb_dim (int)

emb_v (object list[float]) (or separate columns e0..eN if you want pure parquet-friendly)

2.6 clusters_df

One row per cluster.

Columns:

cluster_id (str)

cluster_method (str) "hdbscan"|"hier"|"kmeans"

n_members (int)

centroid_emb_v (object) optional

dispersion (float) (intra-cluster distance metric)

notes (str) optional

2.7 cluster_members_df

Many-to-one membership.

Columns:

cluster_id

pattern_id

is_representative (bool)

rep_rank (int) (1..k)

member_weight (float) optional

2.8 cluster_stats_df

One row per cluster per horizon.

Columns:

cluster_id

horizon

n_signals

net_ticks

avg_ticks

win_rate

p10, p50, p90

stability_score (float) placeholder until CV

prop_survival_score (float) placeholder until MC

2.9 cv_fold_stats_df (purged walk-forward)

One row per (entity, fold, horizon).

Columns:

entity_type (str) "pattern"|"cluster"

entity_id (str) pattern_id or cluster_id

fold_id (int)

train_start, train_end, test_start, test_end (datetime)

horizon

test_n

test_net_ticks

test_avg_ticks

test_win_rate

test_p10, test_p90

sign_positive (bool)

2.10 oos_stats_df

Collapsed OOS metrics (used for ranking).

Columns:

entity_type "pattern"|"cluster"

entity_id

horizon

oos_n

oos_net_ticks

oos_avg_ticks

oos_win_rate

fold_dispersion (float)

sign_consistency (float 0..1)

regime_dispersion (float)

stability_oos_score (float)

2.11 mc_summary_df (prop Monte Carlo)

One row per (entity, horizon) evaluated.

Columns:

entity_type "cluster"|"pattern"

entity_id

horizon

n_paths (int)

dd_p50, dd_p90, dd_p99 (float)

daily_loss_breach_prob (float 0..1)

trailing_dd_breach_prob (float 0..1)

median_days_to_breach (float)

eval_pass_prob (float) optional

stress_slip_ticks (int) (if stressed)

prop_survival_score (float)

3) The exact pkg.metadata["derived"]["pattern_engine"] payload

Attach a single dict per run/package (this is what report sections read).

pkg.metadata.setdefault("derived", {})
pkg.metadata["derived"]["pattern_engine"] = {
  "version": "pe_v1",
  "engine": {
    "tick_size": 0.25,
    "instrument": "NQ",
    "contract": "03-26",
    "bar_tf": "1m",
    "tz": "America/Denver",
  },
  "options_snapshot": { ... },   # deep copy of ctx["options"]["pattern_engine"]
  "artifacts": {
    # All are either: (a) pandas DataFrame converted to records,
    # or (b) file paths to parquet/csv saved by analysis layer (preferred for size).
    "patterns": {"type": "parquet", "path": ".../patterns.parquet"},
    "signals": {"type": "parquet", "path": ".../signals.parquet"},
    "outcomes": {"type": "parquet", "path": ".../outcomes.parquet"},
    "pattern_stats": {"type": "parquet", "path": ".../pattern_stats.parquet"},
    "embeddings": {"type": "parquet", "path": ".../embeddings.parquet"},

    "clusters": {"type": "parquet", "path": ".../clusters.parquet"},
    "cluster_members": {"type": "parquet", "path": ".../cluster_members.parquet"},
    "cluster_stats": {"type": "parquet", "path": ".../cluster_stats.parquet"},

    "cv_fold_stats": {"type": "parquet", "path": ".../cv_fold_stats.parquet"},
    "oos_stats": {"type": "parquet", "path": ".../oos_stats.parquet"},

    "mc_summary": {"type": "parquet", "path": ".../mc_summary.parquet"},
  },
  "diagnostics": {
    "validation": {
      "ok": True,
      "issues": [ ... ],
    },
    "counts": {
      "n_patterns": 0,
      "n_signals": 0,
      "n_outcomes": 0,
      "n_clusters": 0,
    }
  }
}

Why parquet paths instead of embedding full records in metadata: pattern sweeps can easily reach millions of signal/outcome rows; metadata should remain light.

4) Report sections: what they read (pure rendering)

Add (or extend) sections, but they must do no analysis.

4.1 pattern_engine_overview

Reads:

pattern_stats_df

oos_stats_df (if present)

mc_summary_df (if present)

Renders:

Top clusters (by PropSurvivalScore, else by StabilityOOSScore, else by RawScore)

“data health” diagnostics

4.2 pattern_cluster_drilldown

Options:

cluster_id

horizon

top_k_members

Reads:

cluster_members_df, pattern_stats_df, oos_stats_df, mc_summary_df

Renders:

cluster summary

representative members

performance dispersion table

4.3 pattern_signal_examples

Options:

pattern_id or cluster_id

n_examples

filter_regime optional

chart_mode optional

Reads:

signals_df (and market from ctx["market"] for charts)

Renders:

example signal rows + charts

5) Monte Carlo layer: “integrates with sweep framework”

This is the crucial integration: MC must run on the same entities you rank (clusters first, not patterns).

5.1 How to produce an equity-events stream for MC

Create equity_events_df from (signals + outcomes) for the chosen horizon:

Columns:

dt (time-ordered)

entity_type, entity_id (cluster or pattern)

pnl_ticks (from ret_ticks)

day_id, session_id, regime, tod_bucket

optional: slip_bucket, liq_bucket

For cluster-level MC:

either aggregate events of representative members only, OR

include all members but cap exposure (to avoid cluster simply meaning “more trades”).

5.2 PropConstraints (minimal schema)
prop:
  trailing_drawdown_usd: 1500
  daily_loss_limit_usd: 1000
  profit_target_usd: 3000   # optional
  tick_value_usd: 5         # NQ, or 12.5 for ES
  max_contracts: 1
  allow_scale: false
5.3 MonteCarloConfig (minimal schema)
monte_carlo:
  n_paths: 2000
  block_unit: "day"              # "session"|"day"|"window"
  regime_stratified: true
  regime_markov: true
  stress:
    slippage_ticks: [0, 1, 2, 4]
    skip_low_liq: false
5.4 PropSurvivalScore (the ranking objective)

Make it explicit so it’s consistent across reports:

Example:

prop_survival_score =
  oos_avg_ticks
  - λ1 * dd_p90
  - λ2 * trailing_dd_breach_prob
  - λ3 * daily_loss_breach_prob
  - λ4 * fold_dispersion

You can store λ’s in options so the report is reproducible.

6) AI meta-model: ranking clusters (not patterns)

The meta-model should learn: “which clusters generalize and survive constraints?”

6.1 Cluster feature table (cluster_features_df)

Columns (suggested minimum):

cluster_id

n_members

dispersion

signal_count

regime_entropy (diversity of regimes)

tod_entropy

oos_avg_ticks, fold_dispersion, sign_consistency

dd_p90, trailing_dd_breach_prob, daily_loss_breach_prob

slippage_sensitivity (delta of score from slip 0 → slip 2)

member_consensus (how aligned member directions/edges are)

6.2 Target

prop_survival_score computed on OOS windows (or next window)

Or classify “top decile survivor” vs not (more stable early on)

6.3 Output artifacts

Store into metadata as:

meta_model_predictions_df (cluster_id → predicted score)

meta_model_feature_importance_df

meta_model_artifact (serializable dict)

Then ranking is:

If meta-model present: rank by predicted survival

Else: rank by computed survival from MC + CV

7) Recommended implementation order (keeps risk low)

Implement run_pattern_sweep() → produces patterns_df, signals_df, outcomes_df, pattern_stats_df

Implement clustering (build_pattern_clusters())

Implement purged walk-forward CV (compute_purged_walkforward_cv())

Implement Monte Carlo (run_prop_monte_carlo()) on clusters only

Add report sections (render-only)

Add meta-model last (optional)

This keeps complexity justified: each layer has a clear correctness gate.

Next: I can turn this into exact code skeletons (whole files)

If you want me to proceed immediately, I’ll generate whole-file copy/paste skeletons for:

model.py

engine.py (with stub template registry + deterministic ID + dataframe builders)

cluster.py

robustness_cv.py

monte_carlo.py

1–2 HTML sections that read from pkg.metadata["derived"]["pattern_engine"]

…and I’ll keep each file minimal but production-grade (type hints, validation, no pipeline changes).
Integration notes you should apply (no code changes required here)

Where to attach metadata
After your analysis pipeline builds a package, do:

pkg.metadata.setdefault("derived", {})
pkg.metadata["derived"]["pattern_engine"] = run_pattern_sweep(...)

Then, after clustering/CV/MC, append artifacts into that same dict (meta["artifacts"][...] = ...) and update diagnostics counts.

Registry / templates
The TemplateRegistry is intentionally minimal. In your repo you likely already have a registry system—plug templates in there and pass it via options["registry"].

No pipeline flow changes
You can implement this as a new analysis stage that runs after bars are in ctx["market"].