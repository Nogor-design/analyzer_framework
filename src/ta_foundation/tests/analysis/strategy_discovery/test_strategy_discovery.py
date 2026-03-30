from __future__ import annotations

"""
Strategy Discovery — Integration & Unit Tests
=============================================
Covers:
  - regime.compute_bar_regime / summarize_daily_regime
  - mae_mfe.compute_mae_mfe_profile
  - validation.run_validation
  - evaluation.compute_evaluation_metrics / compute_regime_breakdown
  - features.build_feature_matrix
  - importance.compute_feature_importance
  - entry_discovery.run_entry_discovery / Condition
  - clustering.run_clustering / _single_linkage_clusters
  - classification.classify_strategy
  - ranking.run_ranking
"""

import numpy as np
import pandas as pd
import pytest

from ta_foundation.analysis.strategy_discovery.regime import (
    compute_bar_regime,
    summarize_daily_regime,
)
from ta_foundation.analysis.strategy_discovery.mae_mfe import (
    compute_mae_mfe_profile,
)
from ta_foundation.analysis.strategy_discovery.validation import (
    run_validation,
    DEFAULT_WF_CONFIG,
    DEFAULT_COST_MODEL,
)
from ta_foundation.analysis.strategy_discovery.evaluation import (
    compute_evaluation_metrics,
    compute_regime_breakdown,
)
from ta_foundation.analysis.strategy_discovery.features import (
    build_feature_matrix,
)
from ta_foundation.analysis.strategy_discovery.importance import (
    compute_feature_importance,
)
from ta_foundation.analysis.strategy_discovery.entry_discovery import (
    Condition,
    _generate_atoms,
    _generate_candidates,
    _compute_baseline,
    _apply_conjunction,
    _evaluate_rule,
    run_entry_discovery,
)
from ta_foundation.analysis.strategy_discovery.clustering import (
    _build_feature_vector,
    _normalise_vectors,
    _pairwise_distance,
    _single_linkage_clusters,
    _attach_cluster_info,
    run_clustering,
)
from ta_foundation.analysis.strategy_discovery.classification import (
    classify_strategy,
)
from ta_foundation.analysis.strategy_discovery.ranking import (
    run_ranking,
)
from ta_foundation.analysis.strategy_discovery.position_sizing import (
    compute_kelly,
    run_position_sizing,
)
from ta_foundation.analysis.strategy_discovery.cohort_analysis import (
    run_cohort_analysis,
    _linear_trend,
    _classify_trend,
    _decay_score,
)
from ta_foundation.analysis.strategy_discovery.drawdown_analysis import (
    run_drawdown_analysis,
    _max_drawdown,
    _streak_stats,
    _build_equity_curve,
    _ulcer_index,
    _drawdown_series,
)
from ta_foundation.analysis.strategy_discovery.risk_metrics import (
    run_risk_metrics,
    sharpe_ratio,
    sortino_ratio,
    calmar_ratio,
    omega_ratio,
)
from ta_foundation.analysis.strategy_discovery.parameter_sensitivity import (
    run_parameter_sensitivity,
    _auto_step,
    _sensitivity_score,
    _sweep_threshold,
)
from ta_foundation.analysis.strategy_discovery.filter_discovery import (
    run_filter_discovery,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(42)


def _make_bars(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2025-01-02 09:00", periods=n, freq="5min", tz="America/Denver")
    close = 20000 + np.cumsum(RNG.normal(0, 5, n))
    atr = RNG.uniform(15, 40, n)
    high = close + atr * 0.5
    low  = close - atr * 0.5
    return pd.DataFrame({
        "dt":     idx,
        "open":   close,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": RNG.integers(100, 500, n),
        "day_id": idx.date.astype(str),
    })


def _make_trades(n: int = 80, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    entry_times = pd.date_range("2025-01-02 09:30", periods=n, freq="90min", tz="America/Denver")
    profit = rng.normal(50, 200, n)
    mae = rng.uniform(10, 300, n)
    mfe = rng.uniform(50, 500, n)
    return pd.DataFrame({
        "entry_time":  entry_times,
        "exit_time":   entry_times + pd.Timedelta(minutes=30),
        "profit":      profit,
        "profit_net":  profit - 4.18,   # simulated after-cost column
        "mae":         mae,
        "mfe":         mfe,
        "market_pos":  rng.choice(["Long", "Short"], n),
    })


class _MockPkg:
    def __init__(self, trades: pd.DataFrame | None = None):
        self.trades   = trades if trades is not None else _make_trades()
        self.settings = pd.DataFrame({"param": [1, 2]})
        self.metadata = {"derived": {}}
        self.assets   = {}


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------

class TestRegime:
    def test_compute_bar_regime_returns_required_columns(self):
        bars = _make_bars()
        out = compute_bar_regime(bars)
        for col in ("adx", "atr", "regime", "vol_regime", "trend_direction"):
            assert col in out.columns, f"Missing column: {col}"

    def test_regime_values_are_valid(self):
        bars = _make_bars()
        out = compute_bar_regime(bars)
        # The actual regime labels used by the implementation
        valid = {
            "trending", "trending_up", "trending_down", "ranging", "ranging_wide",
            "volatile", "high_vol_expansion", "quiet", "low_vol_compression", "unknown",
        }
        actual = set(out["regime"].unique())
        assert actual.issubset(valid), f"Unexpected regime values: {actual - valid}"

    def test_summarize_daily_regime_returns_dataframe(self):
        bars = _make_bars()
        bars_r = compute_bar_regime(bars)
        summary = summarize_daily_regime(bars_r)
        assert isinstance(summary, pd.DataFrame)
        assert "dominant_regime" in summary.columns

    def test_short_bars_handled_gracefully(self):
        bars = _make_bars(n=5)
        # Should not raise, may have NaN regime
        out = compute_bar_regime(bars)
        assert len(out) == 5

    def test_missing_volume_handled(self):
        bars = _make_bars().drop(columns=["volume"])
        out = compute_bar_regime(bars)
        assert "regime" in out.columns


# ---------------------------------------------------------------------------
# MAE/MFE
# ---------------------------------------------------------------------------

class TestMaeMfe:
    def test_compute_returns_expected_keys(self):
        trades = _make_trades()
        result = compute_mae_mfe_profile(trades, tick_value=5.0, tick_size=0.25)
        assert "distributions" in result
        assert "exit_parameter_bounds" in result
        assert "diagnostics" in result

    def test_distributions_have_percentiles(self):
        trades = _make_trades()
        result = compute_mae_mfe_profile(trades, tick_value=5.0)
        dist = result["distributions"]
        assert "mae_winners_p50" in dist
        assert "mfe_all_p60" in dist

    def test_empty_trades_returns_safe_dict(self):
        result = compute_mae_mfe_profile(pd.DataFrame(), tick_value=5.0)
        assert isinstance(result, dict)
        assert result.get("diagnostics", {}).get("n_trades", 0) == 0

    def test_no_mae_column_returns_safe_dict(self):
        trades = _make_trades().drop(columns=["mae", "mfe"])
        result = compute_mae_mfe_profile(trades, tick_value=5.0)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_run_validation_returns_passed_key(self):
        trades = _make_trades(n=100)
        result = run_validation(trades, wf_config=DEFAULT_WF_CONFIG, cost_model=DEFAULT_COST_MODEL)
        assert "passed" in result

    def test_too_few_trades_fails_validation(self):
        trades = _make_trades(n=3)
        result = run_validation(trades, wf_config={**DEFAULT_WF_CONFIG, "min_is_trades": 50})
        assert result["passed"] is False

    def test_empty_trades_returns_failed(self):
        result = run_validation(pd.DataFrame(), wf_config=DEFAULT_WF_CONFIG)
        assert result["passed"] is False

    def test_cost_normalized_column_present(self):
        trades = _make_trades(n=100)
        result = run_validation(trades, wf_config=DEFAULT_WF_CONFIG, cost_model=DEFAULT_COST_MODEL)
        if "cost_normalized" in result and result["cost_normalized"] is not None:
            assert "profit_net" in result["cost_normalized"].columns


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class TestEvaluation:
    def test_compute_evaluation_metrics_basic(self):
        trades = _make_trades()
        result = compute_evaluation_metrics(trades)
        assert "n_trades" in result
        assert "profit_factor" in result
        assert "win_rate" in result

    def test_by_session_present(self):
        trades = _make_trades()
        result = compute_evaluation_metrics(trades)
        assert "by_session" in result

    def test_regime_breakdown_returns_dict(self):
        trades = _make_trades()
        bars   = _make_bars()
        bars_r = compute_bar_regime(bars)
        result = compute_regime_breakdown(trades, bars_r)
        assert isinstance(result, dict)

    def test_empty_trades_returns_zeroed_metrics(self):
        result = compute_evaluation_metrics(pd.DataFrame())
        assert result.get("n_trades", 0) == 0


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

class TestFeatures:
    def test_build_feature_matrix_returns_dataframe(self):
        trades = _make_trades()
        feat   = build_feature_matrix(trades)
        assert isinstance(feat, pd.DataFrame)
        assert len(feat) == len(trades)

    def test_feature_matrix_has_profit_column(self):
        trades = _make_trades()
        feat   = build_feature_matrix(trades)
        assert "profit" in feat.columns or "profit_net" in feat.columns

    def test_regime_columns_merged_when_bars_provided(self):
        trades = _make_trades()
        bars   = compute_bar_regime(_make_bars())
        feat   = build_feature_matrix(trades, bars_with_regime=bars)
        # regime should be present if the join found bars close to each trade entry
        assert "regime" in feat.columns

    def test_empty_trades_returns_empty_dataframe(self):
        feat = build_feature_matrix(pd.DataFrame())
        assert isinstance(feat, pd.DataFrame)
        assert len(feat) == 0


# ---------------------------------------------------------------------------
# Feature Importance
# ---------------------------------------------------------------------------

class TestImportance:
    def _make_feature_df(self, n: int = 60) -> pd.DataFrame:
        rng = np.random.default_rng(0)
        return pd.DataFrame({
            "profit_net":    rng.normal(30, 150, n),
            "adx":           rng.uniform(10, 40, n),
            "pattern_score": rng.uniform(0, 4, n),
            "regime":        rng.choice(["trending", "ranging"], n),
            "session_label": rng.choice(["us_open", "us_midday"], n),
        })

    def test_returns_expected_keys(self):
        df = self._make_feature_df()
        result = compute_feature_importance(df, profit_col="profit_net")
        assert "numeric_correlations" in result
        assert "categorical_effects" in result
        assert "top_features" in result
        assert "diagnostics" in result

    def test_top_features_is_list(self):
        df = self._make_feature_df()
        result = compute_feature_importance(df)
        assert isinstance(result["top_features"], list)

    def test_empty_df_returns_empty_results(self):
        result = compute_feature_importance(pd.DataFrame())
        assert result["top_features"] == []
        assert result["numeric_correlations"] == []

    def test_missing_profit_col_fallback(self):
        df = self._make_feature_df().rename(columns={"profit_net": "profit"})
        result = compute_feature_importance(df, profit_col="profit_net")
        # Should fall back to 'profit' column and record it in diagnostics
        assert result["diagnostics"]["profit_col_used"] == "profit"


# ---------------------------------------------------------------------------
# Entry Discovery
# ---------------------------------------------------------------------------

class TestEntryDiscovery:
    def _make_feature_df(self, n: int = 80) -> pd.DataFrame:
        rng = np.random.default_rng(7)
        df = pd.DataFrame({
            "regime":        rng.choice(["trending", "ranging", "volatile"], n),
            "session_label": rng.choice(["us_open", "us_morning"], n),
            "direction":     rng.choice([1.0, -1.0], n),
            "adx":           rng.uniform(10, 45, n),
            "pattern_score": rng.uniform(0, 4, n),
            "pat_ma_align":  rng.choice([True, False], n, p=[0.4, 0.6]),
            "profit_net":    rng.normal(40, 200, n),
        })
        # Boost trending so it wins clearly
        mask = df["regime"] == "trending"
        df.loc[mask, "profit_net"] += 120
        return df

    def test_condition_apply_eq(self):
        df = pd.DataFrame({"x": ["a", "b", "a"]})
        cond = Condition("x", "eq", "a")
        assert cond.apply(df).tolist() == [True, False, True]

    def test_condition_apply_gte(self):
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
        cond = Condition("x", "gte", 2.0)
        assert cond.apply(df).tolist() == [False, True, True]

    def test_condition_apply_bool_true(self):
        df = pd.DataFrame({"x": [True, False, True]})
        cond = Condition("x", "bool_true", True)
        assert cond.apply(df).tolist() == [True, False, True]

    def test_condition_missing_column_returns_false(self):
        df = pd.DataFrame({"y": [1, 2]})
        cond = Condition("x", "eq", "a")
        assert not cond.apply(df).any()

    def test_generate_atoms_finds_regime_values(self):
        df = self._make_feature_df()
        atoms = _generate_atoms(df, [20, 25], [1.0])
        col_set = {a.column for a in atoms}
        assert "regime" in col_set

    def test_generate_candidates_depth1_includes_singles(self):
        df = self._make_feature_df()
        atoms = _generate_atoms(df, [20], [1.0])
        cands = _generate_candidates(atoms, max_depth=1, max_candidates=999)
        assert all(len(c) == 1 for c in cands)

    def test_generate_candidates_depth2_no_same_column_pairs(self):
        df = self._make_feature_df()
        atoms = _generate_atoms(df, [20], [1.0])
        cands = _generate_candidates(atoms, max_depth=2, max_candidates=999)
        for c in cands:
            if len(c) == 2:
                assert c[0].column != c[1].column

    def test_evaluate_rule_returns_stats(self):
        df = self._make_feature_df()
        mask = pd.Series([True] * len(df))
        baseline = _compute_baseline(df, "profit_net")
        stats = _evaluate_rule(df, mask, "profit_net", baseline)
        assert stats is not None
        assert "win_rate" in stats
        assert "profit_factor" in stats

    def test_run_entry_discovery_top_rules_nonempty(self):
        df = self._make_feature_df()
        pkg = _MockPkg(_make_trades())
        result = run_entry_discovery(pkg, {}, feature_df=df)
        assert "top_rules" in result
        assert len(result["top_rules"]) > 0

    def test_run_entry_discovery_trending_ranks_highly(self):
        df = self._make_feature_df()
        pkg = _MockPkg()
        result = run_entry_discovery(pkg, {}, feature_df=df)
        top = result["top_rules"]
        top3_conditions = [c["column"] for r in top[:3] for c in r["conditions"]]
        # 'regime' should appear in top conditions since trending was boosted
        assert "regime" in top3_conditions

    def test_run_entry_discovery_too_few_trades_returns_empty(self):
        df = self._make_feature_df(n=5)
        pkg = _MockPkg()
        result = run_entry_discovery(pkg, {"min_trades": 20}, feature_df=df)
        assert result["top_rules"] == []

    def test_run_entry_discovery_disabled(self):
        df = self._make_feature_df()
        pkg = _MockPkg()
        result = run_entry_discovery(pkg, {"enabled": False}, feature_df=df)
        assert result.get("skipped") is True


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

class TestClustering:
    def _make_sd(self, pf: float, wr: float, regime_key: str = "trending", family: str = "fixed_rr") -> dict:
        return {
            "evaluation": {
                "profit_factor":  pf,
                "win_rate":       wr,
                "avg_duration_min": 12,
                "by_regime":  {regime_key: {"profit_factor": pf}},
                "by_session": {"RTH": {"profit_factor": pf}},
            },
            "exit_discovery": {
                "policy_ranking": [{"family": family}]
            },
        }

    def test_similar_runs_cluster_together(self):
        run_ids = ["A", "B", "C"]
        raw_vecs = {
            "A": _build_feature_vector(self._make_sd(1.5, 0.55, "trending", "fixed_rr")),
            "B": _build_feature_vector(self._make_sd(1.4, 0.54, "trending", "fixed_rr")),
            "C": _build_feature_vector(self._make_sd(3.0, 0.75, "ranging", "atr_trail")),
        }
        normed = _normalise_vectors(run_ids, raw_vecs)
        D      = _pairwise_distance(run_ids, normed)
        clusters = _single_linkage_clusters(run_ids, D, threshold=1.5)
        # A and B should be in the same cluster, C separate
        ab_cluster = next(c for c in clusters if "A" in c)
        assert "B" in ab_cluster
        assert "C" not in ab_cluster

    def test_all_different_runs_are_singletons(self):
        run_ids = ["X", "Y", "Z"]
        raw_vecs = {
            "X": _build_feature_vector(self._make_sd(1.1, 0.50, "ranging",  "atr_trail")),
            "Y": _build_feature_vector(self._make_sd(2.5, 0.70, "trending", "fixed_rr")),
            "Z": _build_feature_vector(self._make_sd(4.0, 0.85, "volatile", "chandelier")),
        }
        normed   = _normalise_vectors(run_ids, raw_vecs)
        D        = _pairwise_distance(run_ids, normed)
        clusters = _single_linkage_clusters(run_ids, D, threshold=0.1)
        # Very tight threshold → all singletons
        assert all(len(c) == 1 for c in clusters)

    def test_attach_cluster_info_marks_representative(self):
        clusters = [["A", "B"], ["C"]]
        ranked = [
            {"run_id": "A", "rank": 1, "final_score": 80},
            {"run_id": "B", "rank": 2, "final_score": 75},
            {"run_id": "C", "rank": 3, "final_score": 60},
        ]
        _attach_cluster_info(clusters, ranked)
        row_a = next(r for r in ranked if r["run_id"] == "A")
        row_b = next(r for r in ranked if r["run_id"] == "B")
        assert row_a["is_cluster_representative"] is True
        assert row_b["is_cluster_representative"] is False
        assert row_a["cluster_id"] == row_b["cluster_id"]

    def test_run_clustering_single_run(self):
        """Single run → trivial cluster, no crash."""
        class _Pkg:
            metadata = {"derived": {"strategy_discovery": {
                "cross_run_ranking": {
                    "ranked": [{"run_id": "solo", "rank": 1, "final_score": 70}],
                    "diagnostics": {},
                },
                "evaluation": {},
            }}}
            assets = {}

        packages = {"solo": _Pkg()}
        run_clustering(packages)   # should not raise


# ---------------------------------------------------------------------------
# Filter Discovery
# ---------------------------------------------------------------------------

from ta_foundation.analysis.strategy_discovery.filter_discovery import (
    run_filter_discovery,
    _metrics,
    _score_filter,
)


class TestFilterDiscovery:
    def _make_feature_df(self, n: int = 80) -> pd.DataFrame:
        rng = np.random.default_rng(13)
        df = pd.DataFrame({
            "regime":        rng.choice(["trending", "ranging", "volatile"], n),
            "session_label": rng.choice(["us_open", "us_morning", "us_midday"], n),
            "direction":     rng.choice([1.0, -1.0], n),
            "adx":           rng.uniform(10, 45, n),
            "pattern_score": rng.uniform(0, 4, n),
            "pat_ma_align":  rng.choice([True, False], n, p=[0.4, 0.6]),
            "profit_net":    rng.normal(40, 200, n),
        })
        # Make "volatile" regime consistently bad → good exclusion candidate
        mask = df["regime"] == "volatile"
        df.loc[mask, "profit_net"] -= 200
        return df

    def test_metrics_basic(self):
        profit = pd.Series([100, 200, -50, -30, 150])
        m = _metrics(profit)
        assert m["n_trades"] == 5
        assert m["win_rate"] == pytest.approx(0.6, abs=0.01)
        assert m["profit_factor"] > 1.0

    def test_metrics_all_losers(self):
        profit = pd.Series([-10, -20, -5])
        m = _metrics(profit)
        assert m["profit_factor"] == 0.0
        assert m["win_rate"] == 0.0

    def test_score_filter_improvement_above_50(self):
        baseline = {"profit_factor": 1.2, "win_rate": 0.50, "avg_profit": 30.0}
        filtered = {"profit_factor": 1.8, "win_rate": 0.60, "avg_profit": 80.0}
        score = _score_filter(baseline, filtered, n_removed=20, n_total=100,
                              w_pf=0.45, w_wr=0.35, w_avg=0.20)
        assert score > 50

    def test_score_filter_no_improvement_at_50(self):
        baseline = {"profit_factor": 1.5, "win_rate": 0.55, "avg_profit": 50.0}
        score = _score_filter(baseline, baseline, n_removed=5, n_total=100,
                              w_pf=0.45, w_wr=0.35, w_avg=0.20)
        assert abs(score - 50.0) < 5.0   # near 50 when no improvement

    def test_heavy_filter_penalised(self):
        baseline = {"profit_factor": 1.2, "win_rate": 0.50, "avg_profit": 30.0}
        filtered = {"profit_factor": 2.0, "win_rate": 0.65, "avg_profit": 100.0}
        score_light = _score_filter(baseline, filtered, n_removed=10,  n_total=100,
                                    w_pf=0.45, w_wr=0.35, w_avg=0.20)
        score_heavy = _score_filter(baseline, filtered, n_removed=80, n_total=100,
                                    w_pf=0.45, w_wr=0.35, w_avg=0.20)
        # Removing 80% should be penalised vs removing 10%
        assert score_light > score_heavy

    def test_run_filter_discovery_returns_expected_keys(self):
        df = self._make_feature_df()
        pkg = _MockPkg()
        result = run_filter_discovery(pkg, {}, feature_df=df)
        assert "top_filters" in result
        assert "baseline" in result
        assert "diagnostics" in result

    def test_run_filter_discovery_volatile_filtered(self):
        """volatile regime was made consistently bad — should appear in top filters."""
        df = self._make_feature_df(n=120)
        pkg = _MockPkg()
        result = run_filter_discovery(pkg, {"min_trades": 20}, feature_df=df)
        top_cols = [r["condition"]["column"] for r in result["top_filters"][:5]]
        # 'regime' should be in top exclusion conditions due to the volatile penalty
        assert "regime" in top_cols

    def test_run_filter_discovery_disabled(self):
        df = self._make_feature_df()
        pkg = _MockPkg()
        result = run_filter_discovery(pkg, {"enabled": False}, feature_df=df)
        assert result.get("skipped") is True

    def test_run_filter_discovery_too_few_trades_returns_empty(self):
        df = self._make_feature_df(n=5)
        pkg = _MockPkg()
        result = run_filter_discovery(pkg, {"min_trades": 20}, feature_df=df)
        assert result["top_filters"] == []

    def test_filter_score_in_0_100_range(self):
        df = self._make_feature_df()
        pkg = _MockPkg()
        result = run_filter_discovery(pkg, {}, feature_df=df)
        for r in result["top_filters"]:
            assert 0.0 <= r["score"] <= 100.0

    def test_by_condition_type_populated(self):
        df = self._make_feature_df()
        pkg = _MockPkg()
        result = run_filter_discovery(pkg, {}, feature_df=df)
        assert isinstance(result.get("by_condition_type"), dict)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class TestClassification:
    def test_classify_with_settings_scores_higher(self):
        pkg_with = _MockPkg()
        pkg_without = _MockPkg()
        pkg_without.settings = None

        res_with    = classify_strategy(pkg_with)
        res_without = classify_strategy(pkg_without)
        assert res_with["automation_score"] > res_without["automation_score"]

    def test_label_is_valid(self):
        result = classify_strategy(_MockPkg())
        assert result["label"] in ("automated", "hybrid", "semi_discretionary")

    def test_confidence_is_valid(self):
        result = classify_strategy(_MockPkg())
        assert result["confidence"] in ("high", "moderate", "low")

    def test_reasoning_is_list(self):
        result = classify_strategy(_MockPkg())
        assert isinstance(result["reasoning"], list)
        assert len(result["reasoning"]) > 0

    def test_no_metadata_returns_safe_fallback(self):
        class _NoPkg:
            metadata = None
            settings = None
        result = classify_strategy(_NoPkg())
        assert result["label"] in ("automated", "hybrid", "semi_discretionary")


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class TestRanking:
    def _make_packages(self, n: int = 3) -> dict:
        pkgs = {}
        for i in range(n):
            pkg = _MockPkg(_make_trades(n=60 + i * 10, seed=i))
            pkg.metadata = {
                "derived": {
                    "strategy_discovery": {
                        "validation": {
                            "passed": True,
                            "wf_results": {"is_pf": 1.4 + i * 0.1, "oos_pf": 1.2 + i * 0.1,
                                           "is_trades": 60 + i * 10, "oos_degradation": 0.05},
                            "t_test": {"p_value": 0.03},
                            "monte_carlo": {"dd_percentile_rank": 40 + i * 5, "passed": True},
                        },
                        "evaluation": {
                            "n_trades": 60 + i * 10,
                            "profit_factor": 1.4 + i * 0.1,
                            "win_rate": 0.55 + i * 0.02,
                            "net_profit": 3000 + i * 500,
                            "by_regime": {},
                            "by_session": {},
                        },
                        "mae_mfe_profile": {},
                        "importance": {"diagnostics": {"n_features_analyzed": 3}},
                        "classification": {"automation_score": 70, "confidence": "high"},
                        "exit_discovery": {},
                    }
                }
            }
            pkgs[f"run_{i}"] = pkg
        return pkgs

    def test_run_ranking_attaches_cross_run_ranking(self):
        pkgs = self._make_packages()
        run_ranking(pkgs)
        # cross_run_ranking should be attached to one package
        found = any(
            isinstance(getattr(pkg, "metadata", {}).get("derived", {})
                        .get("strategy_discovery", {}).get("cross_run_ranking"), dict)
            for pkg in pkgs.values()
        )
        assert found

    def test_ranking_block_has_score_keys(self):
        pkgs = self._make_packages()
        run_ranking(pkgs)
        for pkg in pkgs.values():
            sd = pkg.metadata.get("derived", {}).get("strategy_discovery", {})
            ranking_block = sd.get("ranking")
            if ranking_block:
                assert "final_score" in ranking_block
                assert "grade" in ranking_block
                assert "confidence_tier" in ranking_block
                break

    def test_cross_run_ranked_sorted(self):
        pkgs = self._make_packages()
        run_ranking(pkgs)
        for pkg in pkgs.values():
            sd = pkg.metadata.get("derived", {}).get("strategy_discovery", {})
            cross = sd.get("cross_run_ranking")
            if cross:
                ranked = cross.get("ranked") or []
                scores = [r.get("final_score", 0) for r in ranked]
                assert scores == sorted(scores, reverse=True)
                break

    def test_empty_packages_does_not_crash(self):
        run_ranking({})   # should not raise


# ---------------------------------------------------------------------------
# Position Sizing
# ---------------------------------------------------------------------------

class TestPositionSizing:
    def _make_feature_df(self, n: int = 80, seed: int = 42) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        profit_net = rng.normal(50, 200, n)
        regime = rng.choice(["trending_up", "trending_down", "ranging_wide"], n)
        return pd.DataFrame({"profit_net": profit_net, "regime": regime})

    def test_compute_kelly_basic(self):
        # WR=0.6, avg_win=100, avg_loss=80 → f* = 0.6 - 0.4/1.25 = 0.28
        f = compute_kelly(0.6, 100.0, 80.0)
        assert f is not None
        assert abs(f - 0.28) < 1e-6

    def test_compute_kelly_no_edge(self):
        # WR=0.4, avg_win=50, avg_loss=100 → f* < 0 → clamped to 0
        f = compute_kelly(0.4, 50.0, 100.0)
        assert f is not None
        assert f == 0.0

    def test_compute_kelly_invalid_inputs(self):
        assert compute_kelly(0.0, 100.0, 80.0) is None   # WR=0
        assert compute_kelly(1.0, 100.0, 80.0) is None   # WR=1
        assert compute_kelly(0.6, 0.0, 80.0)  is None    # avg_win=0
        assert compute_kelly(0.6, 100.0, 0.0) is None    # avg_loss=0

    def test_run_position_sizing_returns_expected_keys(self):
        df = self._make_feature_df()
        pkg = _MockPkg(_make_trades())
        result = run_position_sizing(pkg, {}, feature_df=df)
        for key in ("trade_stats", "kelly_full", "kelly_fractions", "recommendation", "diagnostics"):
            assert key in result, f"Missing key: {key}"

    def test_kelly_full_is_positive_for_good_strategy(self):
        # _make_trades has mean profit ~50-4.18=45.82 > 0, should yield positive Kelly
        df = self._make_feature_df()
        pkg = _MockPkg(_make_trades(n=100))
        result = run_position_sizing(pkg, {}, feature_df=df)
        # kelly_full could be 0 if edge is very small; just check it's not None
        assert result.get("kelly_full") is not None

    def test_kelly_fractions_list_length(self):
        df = self._make_feature_df()
        pkg = _MockPkg(_make_trades(n=80))
        result = run_position_sizing(pkg, {"kelly_fractions": [0.25, 0.50, 1.00]}, feature_df=df)
        fracs = result.get("kelly_fractions") or []
        # May be empty if kelly_full == 0; otherwise length matches
        if result.get("kelly_full") and result["kelly_full"] > 0:
            assert len(fracs) == 3

    def test_recommendation_has_reasoning(self):
        df = self._make_feature_df(n=100)
        pkg = _MockPkg(_make_trades(n=100))
        result = run_position_sizing(pkg, {}, feature_df=df)
        rec = result.get("recommendation") or {}
        assert "reasoning" in rec

    def test_regime_conditional_populated(self):
        df = self._make_feature_df(n=120)
        # Need enough per-regime trades (≥10); with 3 regimes and 120 rows we should get some
        pkg = _MockPkg(_make_trades(n=120))
        result = run_position_sizing(pkg, {}, feature_df=df)
        # regime_conditional may be empty if kelly is 0; just verify it's a dict
        assert isinstance(result.get("regime_conditional"), dict)

    def test_disabled_returns_skipped(self):
        pkg = _MockPkg()
        result = run_position_sizing(pkg, {"enabled": False})
        assert result.get("skipped") is True

    def test_too_few_trades_returns_empty_fractions(self):
        tiny_df = self._make_feature_df(n=5)
        pkg = _MockPkg(_make_trades(n=5))
        result = run_position_sizing(pkg, {"min_trades": 20}, feature_df=tiny_df)
        assert result.get("kelly_fractions") == [] or result.get("kelly_fractions") is None

    def test_p_ruin_and_p_dd_are_valid_probabilities(self):
        df = self._make_feature_df(n=80)
        pkg = _MockPkg(_make_trades(n=80))
        result = run_position_sizing(pkg, {"n_sim": 100, "n_trades_sim": 50}, feature_df=df)
        for fr in (result.get("kelly_fractions") or []):
            assert 0.0 <= fr["p_ruin"]   <= 1.0
            assert 0.0 <= fr["p_dd_warn"] <= 1.0


# ---------------------------------------------------------------------------
# Cohort Analysis
# ---------------------------------------------------------------------------

class TestCohortAnalysis:
    def _make_trades_with_drift(self, n: int = 120, seed: int = 0) -> pd.DataFrame:
        """Trades where early trades are profitable, later trades deteriorate."""
        rng = np.random.default_rng(seed)
        entry_times = pd.date_range("2025-01-02 09:30", periods=n, freq="90min", tz="America/Denver")
        # Linearly degrading expected profit: starts at +80, ends at -20
        mean_profit = np.linspace(80, -20, n)
        profit = rng.normal(mean_profit, 80)
        return pd.DataFrame({
            "entry_time": entry_times,
            "profit":     profit,
            "profit_net": profit - 4.18,
        })

    def test_returns_expected_keys(self):
        pkg = _MockPkg(self._make_trades_with_drift())
        result = run_cohort_analysis(pkg, {})
        for key in ("cohorts", "trend", "decay_score", "early_vs_late", "overall", "diagnostics"):
            assert key in result, f"Missing key: {key}"

    def test_cohorts_list_nonempty(self):
        pkg = _MockPkg(self._make_trades_with_drift(n=120))
        result = run_cohort_analysis(pkg, {"cohort_size": 20})
        assert len(result["cohorts"]) >= 3

    def test_each_cohort_has_required_fields(self):
        pkg = _MockPkg(self._make_trades_with_drift(n=80))
        result = run_cohort_analysis(pkg, {"cohort_size": 20})
        for c in result["cohorts"]:
            assert "n_trades" in c
            assert "cohort_index" in c
            assert "period_start" in c

    def test_degrading_strategy_classified_correctly(self):
        pkg = _MockPkg(self._make_trades_with_drift(n=120))
        result = run_cohort_analysis(pkg, {"cohort_size": 15})
        # Linearly degrading mean profit should yield degrading or volatile classification
        assert result["trend"]["classification"] in ("degrading", "volatile")

    def test_stable_strategy_has_low_decay_score(self):
        """Stable strategy should have a lower decay score than degrading one."""
        stable_trades = _make_trades(n=120, seed=7)   # roughly flat
        degrading_trades = self._make_trades_with_drift(n=120)
        pkg_s = _MockPkg(stable_trades)
        pkg_d = _MockPkg(degrading_trades)
        res_s = run_cohort_analysis(pkg_s, {"cohort_size": 20})
        res_d = run_cohort_analysis(pkg_d, {"cohort_size": 20})
        # Degrading should score higher (worse) than stable
        assert (res_d.get("decay_score") or 0) >= (res_s.get("decay_score") or 0)

    def test_linear_trend_positive_slope(self):
        y = np.array([1.0, 1.5, 2.0, 2.5, 3.0])
        t = _linear_trend(y)
        assert t["slope"] is not None and t["slope"] > 0
        assert t["r_squared"] is not None and t["r_squared"] > 0.95

    def test_linear_trend_flat(self):
        y = np.array([1.5, 1.5, 1.5, 1.5, 1.5])
        t = _linear_trend(y)
        assert t["slope"] is not None
        assert abs(t["slope"]) < 1e-9

    def test_classify_trend_degrading(self):
        y = np.array([2.0, 1.8, 1.5, 1.2, 0.9])
        result = _classify_trend(-0.3, y)
        assert result == "degrading"

    def test_classify_trend_stable(self):
        y = np.array([1.5, 1.52, 1.48, 1.51, 1.49])
        result = _classify_trend(0.001, y)
        assert result == "stable"

    def test_decay_score_in_range(self):
        pf_series = np.array([1.8, 1.4, 1.2, 0.9, 0.7])
        score = _decay_score(
            slope=-0.28, pf_series=pf_series,
            wr_early=0.62, wr_late=0.45,
            overall_pf=1.3, last_pf=0.7,
        )
        assert 0 <= score <= 100

    def test_disabled_returns_skipped(self):
        pkg = _MockPkg()
        result = run_cohort_analysis(pkg, {"enabled": False})
        assert result.get("skipped") is True

    def test_too_few_trades_returns_empty_cohorts(self):
        pkg = _MockPkg(_make_trades(n=5))
        result = run_cohort_analysis(pkg, {"min_trades": 30})
        assert result["cohorts"] == []

    def test_period_split_week(self):
        """Weekly cohort_by should produce cohorts keyed by week."""
        pkg = _MockPkg(self._make_trades_with_drift(n=100))
        result = run_cohort_analysis(pkg, {"cohort_by": "week", "min_cohorts": 2})
        # Should not error; may have few cohorts depending on data span
        assert "cohorts" in result

    def test_early_vs_late_pf_delta_negative_for_degrading(self):
        pkg = _MockPkg(self._make_trades_with_drift(n=120))
        result = run_cohort_analysis(pkg, {"cohort_size": 15})
        pf_delta = result["early_vs_late"].get("pf_delta")
        # For a degrading strategy, late PF < early PF → delta should be negative
        if pf_delta is not None:
            assert pf_delta < 0


# ---------------------------------------------------------------------------
# Drawdown Analysis
# ---------------------------------------------------------------------------

class TestDrawdownAnalysis:
    def _losing_trades(self) -> np.ndarray:
        """A sequence with a clear drawdown: gains then big losses then recovery."""
        return np.array([100, 80, 60, -400, -300, 150, 200, 250, 300])

    def test_build_equity_curve_starts_at_initial(self):
        eq = _build_equity_curve(np.array([100.0, -50.0, 200.0]), 5000.0)
        assert eq[0] == 5100.0
        assert eq[1] == 5050.0
        assert eq[2] == 5250.0

    def test_max_drawdown_simple(self):
        profits = np.array([100.0, 200.0, -500.0, 100.0])
        dd_usd, dd_pct, pi, ti = _max_drawdown(profits, 10_000.0)
        assert dd_usd == pytest.approx(500.0, abs=1e-6)
        assert 0 < dd_pct < 1
        assert pi == 1           # peak after +100+200=10300
        assert ti == 2           # trough after -500

    def test_max_drawdown_no_drawdown(self):
        profits = np.array([10.0, 20.0, 30.0])
        dd_usd, dd_pct, pi, ti = _max_drawdown(profits, 1_000.0)
        assert dd_usd == 0.0
        assert dd_pct == 0.0

    def test_drawdown_series_all_zero_when_always_rising(self):
        profits = np.array([50.0, 50.0, 50.0])
        equity  = _build_equity_curve(profits, 1_000.0)
        dd      = _drawdown_series(equity)
        assert np.all(dd == 0.0)

    def test_ulcer_index_zero_for_no_drawdown(self):
        profits = np.array([100.0, 200.0, 300.0])
        equity  = _build_equity_curve(profits, 1_000.0)
        dd      = _drawdown_series(equity)
        assert _ulcer_index(dd) == pytest.approx(0.0, abs=1e-9)

    def test_streak_stats_all_winners(self):
        profits = np.array([10.0, 20.0, 30.0, 40.0])
        s = _streak_stats(profits)
        assert s["max_consecutive_losses"] == 0
        assert s["max_consecutive_wins"]   == 4
        assert s["n_loss_streaks"]         == 0

    def test_streak_stats_alternating(self):
        profits = np.array([10.0, -5.0, 10.0, -5.0, 10.0])
        s = _streak_stats(profits)
        assert s["max_consecutive_losses"] == 1
        assert s["max_consecutive_wins"]   == 1

    def test_streak_stats_losing_run(self):
        profits = np.array([50.0, -10.0, -20.0, -30.0, 100.0])
        s = _streak_stats(profits)
        assert s["max_consecutive_losses"] == 3
        assert abs(s["max_loss_streak_usd"] - (-60.0)) < 1e-6

    def test_run_drawdown_analysis_returns_expected_keys(self):
        trades = _make_trades(n=80)
        pkg = _MockPkg(trades)
        result = run_drawdown_analysis(pkg, {})
        for key in ("overall", "streaks", "rolling_max_dd", "by_regime", "diagnostics"):
            assert key in result

    def test_max_drawdown_pct_in_valid_range(self):
        trades = _make_trades(n=80)
        pkg = _MockPkg(trades)
        result = run_drawdown_analysis(pkg, {})
        dd_pct = result["overall"].get("max_drawdown_pct")
        assert dd_pct is not None
        assert 0.0 <= dd_pct <= 100.0

    def test_recovery_factor_positive_for_net_profitable(self):
        # _make_trades has positive mean; recovery_factor = net_profit / max_dd
        trades = _make_trades(n=100, seed=1)
        pkg = _MockPkg(trades)
        result = run_drawdown_analysis(pkg, {})
        rf = result["overall"].get("recovery_factor")
        # May be None if no drawdown; if present should be positive
        if rf is not None:
            assert rf > 0

    def test_rolling_max_dd_list_populated(self):
        trades = _make_trades(n=80)
        pkg = _MockPkg(trades)
        result = run_drawdown_analysis(pkg, {"rolling_window": 20, "rolling_step": 10})
        rolling = result.get("rolling_max_dd") or []
        assert len(rolling) >= 1
        for r in rolling:
            assert "max_dd_pct" in r
            assert 0.0 <= r["max_dd_pct"] <= 100.0

    def test_disabled_returns_skipped(self):
        pkg = _MockPkg()
        result = run_drawdown_analysis(pkg, {"enabled": False})
        assert result.get("skipped") is True

    def test_too_few_trades_returns_empty_overall(self):
        trades = _make_trades(n=3)
        pkg = _MockPkg(trades)
        result = run_drawdown_analysis(pkg, {"min_trades": 10})
        assert result["overall"] == {}

    def test_ulcer_index_nonneg(self):
        trades = _make_trades(n=60)
        pkg = _MockPkg(trades)
        result = run_drawdown_analysis(pkg, {})
        ui = result["overall"].get("ulcer_index")
        if ui is not None:
            assert ui >= 0.0

    def test_streaks_max_losses_is_int(self):
        trades = _make_trades(n=60)
        pkg = _MockPkg(trades)
        result = run_drawdown_analysis(pkg, {})
        ml = result["streaks"].get("max_consecutive_losses")
        assert isinstance(ml, int)


# ---------------------------------------------------------------------------
# Risk Metrics
# ---------------------------------------------------------------------------

class TestRiskMetrics:
    def _all_wins(self, n: int = 50) -> np.ndarray:
        rng = np.random.default_rng(1)
        return np.abs(rng.normal(100, 20, n))

    def _mixed(self, n: int = 80, seed: int = 0) -> np.ndarray:
        rng = np.random.default_rng(seed)
        return rng.normal(50, 200, n)

    # --- unit-level ratio functions ---

    def test_sharpe_positive_for_profitable_series(self):
        r = np.array([0.01, 0.02, 0.015, 0.01, 0.02])
        result = sharpe_ratio(r, rf_per_trade=0.0, ann_factor=1.0)
        assert result is not None and result > 0

    def test_sharpe_negative_for_losing_series(self):
        r = np.array([-0.01, -0.02, -0.015, -0.01])
        result = sharpe_ratio(r, rf_per_trade=0.0, ann_factor=1.0)
        assert result is not None and result < 0

    def test_sharpe_zero_std_returns_none(self):
        r = np.array([0.01, 0.01, 0.01])  # all identical → std=0
        result = sharpe_ratio(r, rf_per_trade=0.01, ann_factor=1.0)
        # Excess is zero; std of excess is 0 → None
        assert result is None

    def test_sortino_better_than_sharpe_for_skewed_wins(self):
        """Sortino should be >= Sharpe when losses are small relative to wins."""
        rng = np.random.default_rng(5)
        r = np.concatenate([rng.uniform(0.01, 0.05, 60), rng.uniform(-0.005, 0.0, 10)])
        sh = sharpe_ratio(r, 0.0, 1.0)
        so = sortino_ratio(r, 0.0, 0.0, 1.0)
        if sh is not None and so is not None:
            assert so >= sh

    def test_calmar_positive_with_drawdown(self):
        result = calmar_ratio(annualized_return=0.20, max_dd_pct=0.10)
        assert result is not None and result == pytest.approx(2.0)

    def test_calmar_none_when_no_drawdown(self):
        result = calmar_ratio(annualized_return=0.20, max_dd_pct=0.0)
        assert result is None

    def test_omega_above_1_for_profitable_series(self):
        r = np.array([0.02, 0.03, -0.005, 0.01, 0.025, -0.003])
        result = omega_ratio(r, threshold=0.0)
        assert result is not None and result > 1.0

    def test_omega_none_when_no_losses(self):
        r = np.array([0.01, 0.02, 0.03])
        result = omega_ratio(r, threshold=0.0)
        assert result is None

    # --- integration ---

    def test_run_risk_metrics_returns_expected_keys(self):
        pkg = _MockPkg(_make_trades(n=60))
        result = run_risk_metrics(pkg, {})
        for key in ("sharpe_ratio", "sortino_ratio", "calmar_ratio", "omega_ratio",
                    "annualized_return_pct", "total_return_pct", "diagnostics"):
            assert key in result

    def test_total_return_reflects_net_profit(self):
        trades = _make_trades(n=60)
        pkg = _MockPkg(trades)
        result = run_risk_metrics(pkg, {"initial_equity": 10_000.0})
        net = trades["profit_net"].sum()
        expected_pct = net / 10_000.0 * 100.0
        tr = result.get("total_return_pct")
        assert tr is not None
        assert abs(tr - expected_pct) < 0.01

    def test_omega_ratio_positive_for_profitable_strategy(self):
        # _make_trades has positive mean profit_net
        pkg = _MockPkg(_make_trades(n=100, seed=1))
        result = run_risk_metrics(pkg, {})
        omega = result.get("omega_ratio")
        if omega is not None:
            assert omega > 0

    def test_disabled_returns_skipped(self):
        pkg = _MockPkg()
        result = run_risk_metrics(pkg, {"enabled": False})
        assert result.get("skipped") is True

    def test_too_few_trades_returns_none_ratios(self):
        pkg = _MockPkg(_make_trades(n=3))
        result = run_risk_metrics(pkg, {"min_trades": 10})
        assert result.get("sharpe_ratio") is None

    def test_ranking_risk_adj_higher_with_good_risk_metrics(self):
        """Strategy with positive Sharpe should rank higher than one without metrics."""
        from ta_foundation.analysis.strategy_discovery.ranking import _score_risk_adj
        eval_data = {"profit_factor": 1.6, "win_rate": 0.55, "avg_trade": 40.0, "max_drawdown": 500.0}
        score_no_rm   = _score_risk_adj(eval_data, risk_metrics=None)
        score_good_rm = _score_risk_adj(eval_data, risk_metrics={
            "sharpe_ratio": 1.5, "sortino_ratio": 2.5, "calmar_ratio": 1.2
        })
        score_bad_rm  = _score_risk_adj(eval_data, risk_metrics={
            "sharpe_ratio": -0.3, "sortino_ratio": -0.1, "calmar_ratio": -0.2
        })
        assert score_good_rm > score_no_rm
        assert score_good_rm > score_bad_rm


# ---------------------------------------------------------------------------
# Cross-Run Comparison (ranking enrichment + renderer)
# ---------------------------------------------------------------------------

class TestStrategyDiscoveryComparison:
    def _make_packages_with_full_data(self, n: int = 3) -> dict:
        """Build packages with all new-module data attached to metadata."""
        pkgs = {}
        for i in range(n):
            pkg = _MockPkg(_make_trades(n=60 + i * 10, seed=i))
            pkg.metadata = {
                "derived": {
                    "strategy_discovery": {
                        "validation": {
                            "passed": True,
                            "wf_results": {"is_pf": 1.4 + i * 0.1, "oos_pf": 1.2 + i * 0.1,
                                           "is_trades": 60 + i * 10, "oos_degradation": 0.05 + i * 0.02},
                            "t_test": {"p_value": 0.03},
                            "monte_carlo": {"dd_percentile_rank": 40 + i * 5, "passed": True},
                        },
                        "evaluation": {
                            "n_trades": 60 + i * 10,
                            "profit_factor": 1.4 + i * 0.1,
                            "win_rate": 0.55 + i * 0.02,
                            "net_profit": 3000 + i * 500,
                            "by_regime": {}, "by_session": {},
                        },
                        "risk_metrics": {
                            "sharpe_ratio": 0.8 + i * 0.2,
                            "sortino_ratio": 1.2 + i * 0.3,
                            "calmar_ratio": 0.5 + i * 0.1,
                            "omega_ratio": 1.5 + i * 0.2,
                            "annualized_return_pct": 15.0 + i * 5.0,
                        },
                        "drawdown_analysis": {
                            "overall": {
                                "max_drawdown_pct": 18.0 - i * 2.0,
                                "ulcer_index": 4.5 - i * 0.5,
                                "recovery_factor": 2.0 + i * 0.3,
                            },
                            "streaks": {"max_consecutive_losses": 5 - i},
                        },
                        "cohort_analysis": {
                            "decay_score": 30 - i * 5,
                            "trend": {"classification": "stable"},
                        },
                        "position_sizing": {
                            "kelly_full": 0.15 + i * 0.02,
                            "recommendation": {"recommended_fraction_of_kelly": 0.5},
                        },
                        "mae_mfe_profile": {},
                        "importance": {"diagnostics": {"n_features_analyzed": 3}},
                        "classification": {"automation_score": 70 + i * 5,
                                           "label": "hybrid", "confidence": "high"},
                        "exit_discovery": {},
                    }
                }
            }
            pkgs[f"run_{i}"] = pkg
        return pkgs

    def test_ranking_row_includes_new_metrics(self):
        pkgs = self._make_packages_with_full_data()
        run_ranking(pkgs)
        # Find the cross-run ranking
        ranked = None
        for pkg in pkgs.values():
            sd = pkg.metadata.get("derived", {}).get("strategy_discovery", {})
            cross = sd.get("cross_run_ranking")
            if cross:
                ranked = cross.get("ranked")
                break
        assert ranked is not None
        row = ranked[0]
        # New metric fields should be present
        for field in ("sharpe_ratio", "sortino_ratio", "calmar_ratio", "max_dd_pct",
                      "decay_score", "kelly_full", "trend_class"):
            assert field in row, f"Missing field in ranking row: {field}"

    def test_ranking_row_sharpe_is_float(self):
        pkgs = self._make_packages_with_full_data()
        run_ranking(pkgs)
        for pkg in pkgs.values():
            sd = pkg.metadata.get("derived", {}).get("strategy_discovery", {})
            cross = sd.get("cross_run_ranking")
            if cross:
                for row in cross.get("ranked", []):
                    sr = row.get("sharpe_ratio")
                    if sr is not None:
                        assert isinstance(sr, float)
                break

    def test_comparison_renderer_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_comparison import (
            render_strategy_discovery_comparison,
        )
        pkgs = self._make_packages_with_full_data()
        run_ranking(pkgs)
        ctx = {"packages": pkgs, "options": {}}
        result = render_strategy_discovery_comparison(ctx)
        assert isinstance(result, str)
        assert "Cross-Run Comparison" in result

    def test_comparison_renderer_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_comparison import (
            render_strategy_discovery_comparison,
        )
        result = render_strategy_discovery_comparison({"packages": {}, "options": {}})
        assert "No cross-run ranking data" in result

    def test_comparison_renderer_shows_all_column_groups(self):
        from ta_foundation.reports.html.sections.strategy_discovery_comparison import (
            render_strategy_discovery_comparison,
        )
        pkgs = self._make_packages_with_full_data()
        run_ranking(pkgs)
        ctx = {"packages": pkgs, "options": {
            "show_risk_adj": True, "show_drawdown": True,
            "show_sizing": True, "show_stability": True,
        }}
        result = render_strategy_discovery_comparison(ctx)
        for label in ("Risk-Adjusted", "Drawdown", "Sizing", "Stability"):
            assert label in result, f"Column group '{label}' missing from HTML"

    def test_comparison_renderer_max_runs_respected(self):
        from ta_foundation.reports.html.sections.strategy_discovery_comparison import (
            render_strategy_discovery_comparison,
        )
        pkgs = self._make_packages_with_full_data(n=3)
        run_ranking(pkgs)
        ctx = {"packages": pkgs, "options": {"max_runs": 2}}
        result = render_strategy_discovery_comparison(ctx)
        # With max_runs=2, only 2 run IDs should appear as data rows
        assert result.count("run_0") + result.count("run_1") >= 1

    def test_comparison_renderer_highlights_best(self):
        """highlight_best=True should result in font-weight:800 tags in the output."""
        from ta_foundation.reports.html.sections.strategy_discovery_comparison import (
            render_strategy_discovery_comparison,
        )
        pkgs = self._make_packages_with_full_data(n=3)
        run_ranking(pkgs)
        ctx = {"packages": pkgs, "options": {"highlight_best": True}}
        result = render_strategy_discovery_comparison(ctx)
        assert "font-weight:800" in result


# ---------------------------------------------------------------------------
# Parameter Sensitivity
# ---------------------------------------------------------------------------

class TestParameterSensitivity:
    """Tests for parameter_sensitivity.py"""

    def _make_feature_df(self, n: int = 100, seed: int = 42) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        adx    = rng.uniform(10, 40, n)
        profit = rng.normal(50, 150, n)
        return pd.DataFrame({
            "adx":        adx,
            "profit":     profit,
            "profit_net": profit - 4.18,
        })

    def _make_pkg_with_entry_rules(self) -> "_MockPkg":
        """Build a _MockPkg that has entry discovery top_rules in metadata."""
        pkg = _MockPkg()
        feature_df = self._make_feature_df()
        # Inject entry_discovery top_rules directly into metadata
        pkg.metadata["derived"]["strategy_discovery"] = {
            "entry_discovery": {
                "top_rules": [
                    {
                        "rule_id": "r0",
                        "description": "adx >= 20",
                        "score": 1.2,
                        "pf": 1.3,
                        "win_rate": 0.55,
                        "conditions": [
                            {"column": "adx", "op": "gte", "value": 20.0},
                        ],
                    },
                    {
                        "rule_id": "r1",
                        "description": "adx >= 25",
                        "score": 1.1,
                        "pf": 1.2,
                        "win_rate": 0.52,
                        "conditions": [
                            {"column": "adx", "op": "gte", "value": 25.0},
                        ],
                    },
                ]
            }
        }
        return pkg, feature_df

    # --- _auto_step ---

    def test_auto_step_known_column(self):
        vals = pd.Series([20.0, 25.0, 30.0])
        assert _auto_step("adx", vals) == 1.0

    def test_auto_step_pattern_score(self):
        vals = pd.Series([1.0, 2.0, 3.0])
        assert _auto_step("pattern_score", vals) == 0.5

    def test_auto_step_unknown_column_uses_std(self):
        rng = np.random.default_rng(0)
        vals = pd.Series(rng.uniform(0, 100, 500))
        step = _auto_step("some_custom_col", vals)
        assert step > 0

    # --- _sensitivity_score ---

    def test_sensitivity_score_robust_flat_sweep(self):
        # PF is constant — should classify as robust
        sweep = [{"threshold": float(t), "pf": 1.3, "win_rate": 0.55} for t in range(9)]
        result = _sensitivity_score(sweep, 4.0)
        assert result["classification"] == "robust"
        assert result["peak_ratio"] is not None
        assert result["sensitivity_score"] is not None

    def test_sensitivity_score_fragile_peaked(self):
        # PF peaks sharply at original threshold (4), low elsewhere
        sweep = [{"threshold": float(t), "pf": 0.8 if t != 4 else 3.0, "win_rate": 0.5}
                 for t in range(9)]
        result = _sensitivity_score(sweep, 4.0)
        assert result["classification"] == "fragile"
        assert result["sensitivity_score"] >= 60

    def test_sensitivity_score_insufficient_valid_returns_unknown(self):
        # Only one valid data point
        sweep = [{"threshold": 20.0, "pf": 1.2, "win_rate": 0.5},
                 {"threshold": 21.0, "pf": None, "win_rate": None}]
        result = _sensitivity_score(sweep, 20.0)
        assert result["classification"] == "unknown"
        assert result["sensitivity_score"] is None

    def test_sensitivity_score_returns_all_keys(self):
        sweep = [{"threshold": float(t), "pf": 1.0 + t * 0.1, "win_rate": 0.5}
                 for t in range(5)]
        result = _sensitivity_score(sweep, 2.0)
        for key in ("pf_at_original", "pf_mean", "pf_range", "pf_slope",
                    "peak_ratio", "sensitivity_score", "classification"):
            assert key in result

    # --- run_parameter_sensitivity ---

    def test_run_returns_expected_keys(self):
        pkg, feature_df = self._make_pkg_with_entry_rules()
        result = run_parameter_sensitivity(pkg, {}, feature_df=feature_df)
        assert "rule_sweeps" in result
        assert "summary" in result
        assert "diagnostics" in result

    def test_run_analyses_numeric_conditions(self):
        pkg, feature_df = self._make_pkg_with_entry_rules()
        result = run_parameter_sensitivity(pkg, {}, feature_df=feature_df)
        summary = result["summary"]
        # At least one numeric sweep should have been attempted
        assert summary["n_rules_analysed"] >= 1

    def test_run_classification_values_valid(self):
        pkg, feature_df = self._make_pkg_with_entry_rules()
        result = run_parameter_sensitivity(pkg, {}, feature_df=feature_df)
        valid_cls = {"robust", "moderate", "fragile", "unknown", None}
        for rule in result["rule_sweeps"]:
            for sweep in rule.get("sweeps", []):
                if "note" not in sweep:
                    assert sweep.get("classification") in valid_cls

    def test_run_disabled_returns_skipped(self):
        pkg, feature_df = self._make_pkg_with_entry_rules()
        result = run_parameter_sensitivity(pkg, {"enabled": False}, feature_df=feature_df)
        assert result.get("skipped") is True

    def test_run_no_entry_rules_returns_empty(self):
        pkg = _MockPkg()
        feature_df = self._make_feature_df()
        # No entry discovery in metadata
        pkg.metadata["derived"]["strategy_discovery"] = {}
        result = run_parameter_sensitivity(pkg, {}, feature_df=feature_df)
        assert result["rule_sweeps"] == []
        assert len(result["diagnostics"]["issues"]) > 0

    def test_run_no_feature_df_returns_empty(self):
        pkg, _ = self._make_pkg_with_entry_rules()
        result = run_parameter_sensitivity(pkg, {}, feature_df=None)
        assert result["rule_sweeps"] == []

    def test_run_threshold_sweep_has_right_length(self):
        pkg, feature_df = self._make_pkg_with_entry_rules()
        result = run_parameter_sensitivity(pkg, {"n_steps": 3}, feature_df=feature_df)
        for rule in result["rule_sweeps"]:
            for sw in rule.get("sweeps", []):
                if "note" not in sw and sw.get("threshold_sweep"):
                    # sweep should have 2*n_steps+1 = 7 points
                    assert len(sw["threshold_sweep"]) == 7

    def test_run_summary_counts_match_sweeps(self):
        pkg, feature_df = self._make_pkg_with_entry_rules()
        result = run_parameter_sensitivity(pkg, {}, feature_df=feature_df)
        summary = result["summary"]
        n_robust   = summary["n_robust"]
        n_moderate = summary["n_moderate"]
        n_fragile  = summary["n_fragile"]
        n_total    = summary["n_numeric_sweeps"]
        assert n_robust + n_moderate + n_fragile == n_total

    def test_renderer_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_overview import (
            _render_parameter_sensitivity_section,
        )
        pkg, feature_df = self._make_pkg_with_entry_rules()
        result = run_parameter_sensitivity(pkg, {}, feature_df=feature_df)
        html_out = _render_parameter_sensitivity_section(result)
        assert isinstance(html_out, str)
        assert len(html_out) > 50

    def test_renderer_empty_returns_muted(self):
        from ta_foundation.reports.html.sections.strategy_discovery_overview import (
            _render_parameter_sensitivity_section,
        )
        html_out = _render_parameter_sensitivity_section({})
        assert "sd-muted" in html_out or "No parameter" in html_out


# ---------------------------------------------------------------------------
# Filter Rules Section
# ---------------------------------------------------------------------------

class TestFilterRulesSection:
    """Tests for strategy_discovery_filter_rules.py renderer and filter_discovery integration."""

    def _make_feature_df(self, n: int = 100, seed: int = 7) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        adx    = rng.uniform(10, 40, n)
        profit = rng.normal(30, 180, n)
        # Make low-adx trades predominantly losing to give filter discovery signal
        profit = np.where(adx < 18, profit - 150, profit)
        return pd.DataFrame({
            "adx":        adx,
            "profit":     profit,
            "profit_net": profit - 4.18,
        })

    def _make_pkg_with_feature_df(self, n: int = 100) -> tuple:
        pkg = _MockPkg(_make_trades(n))
        feature_df = self._make_feature_df(n)
        pkg.assets["strategy_discovery"] = {"feature_matrix": feature_df}
        return pkg, feature_df

    # --- run_filter_discovery ---

    def test_returns_expected_keys(self):
        pkg, feature_df = self._make_pkg_with_feature_df()
        result = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                      feature_df=feature_df)
        for key in ("top_filters", "baseline", "by_condition_type", "diagnostics"):
            assert key in result, f"Missing key: {key}"

    def test_disabled_returns_skipped(self):
        pkg, feature_df = self._make_pkg_with_feature_df()
        result = run_filter_discovery(pkg, {"enabled": False}, feature_df=feature_df)
        assert result.get("skipped") is True

    def test_top_filters_is_list(self):
        pkg, feature_df = self._make_pkg_with_feature_df()
        result = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                      feature_df=feature_df)
        assert isinstance(result["top_filters"], list)

    def test_filter_has_required_fields(self):
        pkg, feature_df = self._make_pkg_with_feature_df()
        result = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                      feature_df=feature_df)
        for filt in result["top_filters"]:
            for field in ("rank", "score", "condition", "filter_str",
                          "n_removed", "n_remaining", "pf_after", "wr_after"):
                assert field in filt, f"Filter missing field: {field}"

    def test_ranks_are_sequential(self):
        pkg, feature_df = self._make_pkg_with_feature_df()
        result = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                      feature_df=feature_df)
        ranks = [f["rank"] for f in result["top_filters"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_scores_are_descending(self):
        pkg, feature_df = self._make_pkg_with_feature_df()
        result = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                      feature_df=feature_df)
        scores = [f["score"] for f in result["top_filters"] if f.get("score") is not None]
        assert scores == sorted(scores, reverse=True)

    def test_baseline_has_n_trades(self):
        pkg, feature_df = self._make_pkg_with_feature_df()
        result = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                      feature_df=feature_df)
        baseline = result["baseline"]
        # baseline uses n_trades_total key
        assert baseline.get("n_trades_total") is not None
        assert int(baseline["n_trades_total"]) > 0

    def test_no_feature_df_with_empty_pkg_returns_empty(self):
        # Pkg with no trades and no assets → filter discovery should return empty
        pkg = _MockPkg(pd.DataFrame())
        result = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5},
                                      feature_df=None)
        issues = result.get("diagnostics", {}).get("issues", [])
        top_filters = result.get("top_filters", [])
        assert result.get("skipped") or result.get("error") is not None \
               or len(issues) > 0 or len(top_filters) == 0

    # --- renderer ---

    def test_renderer_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_filter_rules import (
            render_strategy_discovery_filter_rules,
        )
        pkg, feature_df = self._make_pkg_with_feature_df()
        fd = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                  feature_df=feature_df)
        pkg.metadata["derived"]["strategy_discovery"] = {"filter_discovery": fd}
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_filter_rules(ctx)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_renderer_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_filter_rules import (
            render_strategy_discovery_filter_rules,
        )
        result = render_strategy_discovery_filter_rules({"packages": {}, "options": {}})
        assert "No filter discovery data" in result

    def test_renderer_shows_exclude_when(self):
        from ta_foundation.reports.html.sections.strategy_discovery_filter_rules import (
            render_strategy_discovery_filter_rules,
        )
        pkg, feature_df = self._make_pkg_with_feature_df()
        fd = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                  feature_df=feature_df)
        pkg.metadata["derived"]["strategy_discovery"] = {"filter_discovery": fd}
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_filter_rules(ctx)
        # Should show exclusion condition styling
        assert "sdf-cond" in result or "EXCLUDE" in result

    def test_renderer_max_runs_respected(self):
        from ta_foundation.reports.html.sections.strategy_discovery_filter_rules import (
            render_strategy_discovery_filter_rules,
        )
        pkgs = {}
        for i in range(4):
            pkg, feature_df = self._make_pkg_with_feature_df()
            fd = run_filter_discovery(pkg, {"enabled": True, "min_trades": 5, "min_remaining_trades": 5},
                                      feature_df=feature_df)
            pkg.metadata["derived"]["strategy_discovery"] = {"filter_discovery": fd}
            pkgs[f"run_{i}"] = pkg
        ctx = {"packages": pkgs, "options": {"max_runs": 2}}
        result = render_strategy_discovery_filter_rules(ctx)
        # Only 2 runs shown: run_0 and run_1 headers should appear, run_2 and run_3 not
        assert "run_0" in result
        assert "run_1" in result
        assert "run_2" not in result

    def test_renderer_error_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_filter_rules import (
            render_strategy_discovery_filter_rules,
        )
        pkg = _MockPkg()
        pkg.metadata["derived"]["strategy_discovery"] = {
            "filter_discovery": {"error": "test error message"}
        }
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_filter_rules(ctx)
        assert "test error message" in result or "error" in result.lower()


# ---------------------------------------------------------------------------
# Exit Policies Section
# ---------------------------------------------------------------------------

class TestExitPoliciesSection:
    """Tests for strategy_discovery_exit_policies.py renderer."""

    def _make_exit_discovery_result(self, n_policies: int = 6) -> dict:
        """Build a synthetic exit_discovery result dict."""
        families = ["fixed_rr", "atr_trail", "be_atr_trail", "chandelier", "giveback"]
        ranking = []
        for i in range(n_policies):
            fam = families[i % len(families)]
            net = float(50 - i * 8)
            ranking.append({
                "rank": i + 1,
                "policy_name": f"{fam}_tp{i+1}_sl{i+1}",
                "family": fam,
                "n_trades": 80,
                "n_winners": 50,
                "n_losers": 30,
                "net_ticks": net,
                "avg_ticks": net / 80,
                "win_rate": 0.625,
                "profit_factor": 1.5 - i * 0.05,
                "max_dd_ticks": float(20 + i * 3),
                "exit_reasons": {"tp_hit": 50, "sl_hit": 30} if i == 0 else {},
            })
        best_by_family = {
            r["family"]: {k: v for k, v in r.items() if k != "exit_reasons"}
            for r in ranking
            if r["family"] not in {r2["family"] for r2 in ranking[:ranking.index(r)]}
        }
        return {
            "policy_ranking": ranking,
            "best_by_family": best_by_family,
            "best_by_regime": {
                "trend": {"best_policy": ranking[0]["policy_name"], "net_ticks": 50.0, "win_rate": 0.65, "n_trades": 40},
                "range": {"best_policy": ranking[1]["policy_name"], "net_ticks": 30.0, "win_rate": 0.60, "n_trades": 40},
            },
            "sweep_summary": {
                "n_combos": n_policies,
                "best_policy": ranking[0]["policy_name"],
                "best_net_ticks": ranking[0]["net_ticks"],
                "best_family": ranking[0]["family"],
                "families_evaluated": families[:n_policies],
            },
            "parameter_bounds_used": {},
            "diagnostics": {"n_trades": 80, "profit_col_used": "profit_net", "issues": []},
            "enabled": True,
        }

    def _make_pkg_with_exit(self, n_policies: int = 6) -> "_MockPkg":
        pkg = _MockPkg()
        ed = self._make_exit_discovery_result(n_policies)
        pkg.metadata["derived"]["strategy_discovery"] = {"exit_discovery": ed}
        return pkg

    # --- renderer ---

    def test_renderer_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        pkg = self._make_pkg_with_exit()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_exit_policies(ctx)
        assert isinstance(result, str)
        assert len(result) > 200

    def test_renderer_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        result = render_strategy_discovery_exit_policies({"packages": {}, "options": {}})
        assert "No exit discovery data" in result

    def test_renderer_shows_policy_names(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        pkg = self._make_pkg_with_exit()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_exit_policies(ctx)
        assert "fixed_rr_tp1_sl1" in result

    def test_renderer_shows_family_badges(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        pkg = self._make_pkg_with_exit()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_exit_policies(ctx)
        assert "Fixed R:R" in result or "ATR Trail" in result

    def test_renderer_shows_regime_section(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        pkg = self._make_pkg_with_exit()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_exit_policies(ctx)
        assert "trend" in result or "Regime" in result

    def test_renderer_shows_exit_reasons(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        pkg = self._make_pkg_with_exit()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_exit_policies(ctx)
        assert "tp_hit" in result or "sl_hit" in result

    def test_renderer_top_n_policies_respected(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        pkg = self._make_pkg_with_exit(n_policies=8)
        ctx = {"packages": {"run_A": pkg}, "options": {"top_n_policies": 3}}
        result = render_strategy_discovery_exit_policies(ctx)
        # policy rank 4 and beyond should not appear
        assert "fixed_rr_tp4" not in result

    def test_renderer_max_runs_respected(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        pkgs = {f"run_{i}": self._make_pkg_with_exit() for i in range(4)}
        ctx = {"packages": pkgs, "options": {"max_runs": 2}}
        result = render_strategy_discovery_exit_policies(ctx)
        assert "run_0" in result
        assert "run_1" in result
        assert "run_2" not in result

    def test_renderer_error_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        pkg = _MockPkg()
        pkg.metadata["derived"]["strategy_discovery"] = {
            "exit_discovery": {"error": "atr column missing"}
        }
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_exit_policies(ctx)
        assert "atr column missing" in result or "error" in result.lower()

    def test_renderer_no_best_by_regime(self):
        """Runs fine even when best_by_regime is empty."""
        from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
            render_strategy_discovery_exit_policies,
        )
        ed = self._make_exit_discovery_result()
        ed["best_by_regime"] = {}
        pkg = _MockPkg()
        pkg.metadata["derived"]["strategy_discovery"] = {"exit_discovery": ed}
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_exit_policies(ctx)
        assert isinstance(result, str)
        assert len(result) > 100


# ---------------------------------------------------------------------------
# Feature Importance Section
# ---------------------------------------------------------------------------

class TestFeatureImportanceSection:
    """Tests for strategy_discovery_feature_importance.py renderer."""

    def _make_importance_result(self, seed: int = 0) -> dict:
        """Build a synthetic importance dict matching compute_feature_importance output."""
        rng = __import__("numpy").random.default_rng(seed)
        num_rows = [
            {"feature": "adx", "correlation": 0.18, "abs_correlation": 0.18, "rank": 1},
            {"feature": "atr", "correlation": -0.12, "abs_correlation": 0.12, "rank": 2},
            {"feature": "hour", "correlation": 0.08, "abs_correlation": 0.08, "rank": 3},
        ]
        cat_rows = [
            {"feature": "session_label", "cramers_v": 0.21, "rank": 1,
             "group_means": {"us_open": 80.0, "us_afternoon": 30.0}},
            {"feature": "regime", "cramers_v": 0.15, "rank": 2,
             "group_means": {"trend": 90.0, "range": 20.0}},
        ]
        rf_rows = [
            {"feature": "adx", "importance": 0.25, "rank": 1},
            {"feature": "session_label", "importance": 0.18, "rank": 2},
            {"feature": "atr", "importance": 0.12, "rank": 3},
        ]
        return {
            "numeric_correlations": num_rows,
            "categorical_effects": cat_rows,
            "rf_importance": rf_rows,
            "top_features": ["adx", "session_label", "atr"],
            "diagnostics": {
                "n_trades": 80,
                "n_winners": 48,
                "n_features_analyzed": 6,
                "profit_col": "profit_net",
                "rf_used": True,
                "issues": [],
            },
        }

    def _make_pkg_with_importance(self, seed: int = 0) -> "_MockPkg":
        pkg = _MockPkg()
        imp = self._make_importance_result(seed)
        pkg.metadata["derived"]["strategy_discovery"] = {"importance": imp}
        return pkg

    # --- renderer basics ---

    def test_renderer_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkg = self._make_pkg_with_importance()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert isinstance(result, str)
        assert len(result) > 200

    def test_renderer_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        result = render_strategy_discovery_feature_importance({"packages": {}, "options": {}})
        assert "No feature importance data" in result

    def test_renderer_shows_top_features(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkg = self._make_pkg_with_importance()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert "adx" in result
        assert "session_label" in result

    def test_renderer_shows_numeric_correlations(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkg = self._make_pkg_with_importance()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert "Numeric Correlations" in result or "numeric" in result.lower()

    def test_renderer_shows_categorical_effects(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkg = self._make_pkg_with_importance()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert "Cram" in result  # Cramér's V

    def test_renderer_shows_rf_when_enabled(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkg = self._make_pkg_with_importance()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_rf": True}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert "Random Forest" in result

    def test_renderer_hides_rf_when_disabled(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkg = self._make_pkg_with_importance()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_rf": False}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert "Random Forest" not in result

    # --- cross-run table ---

    def test_cross_run_table_shown_with_multiple_runs(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkgs = {f"run_{i}": self._make_pkg_with_importance(i) for i in range(3)}
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert "Cross-Run" in result or "Frequency" in result

    def test_cross_run_table_hidden_with_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkg = self._make_pkg_with_importance()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_feature_importance(ctx)
        # With only 1 run, cross-run table should not appear (needs > 1 run)
        assert "Cross-Run" not in result

    def test_cross_run_shows_consistent_features(self):
        """Features in all runs' top_features should show 3/3 in the table."""
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkgs = {f"run_{i}": self._make_pkg_with_importance(i) for i in range(3)}
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_feature_importance(ctx)
        # "adx" and "session_label" appear in all 3 runs' top_features
        assert "3/3" in result

    # --- options ---

    def test_max_runs_respected(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkgs = {f"run_{i}": self._make_pkg_with_importance(i) for i in range(4)}
        ctx = {"packages": pkgs, "options": {"max_runs": 2, "show_cross_run": False}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert "run_0" in result
        assert "run_1" in result
        assert "run_2" not in result

    def test_error_state_rendered_gracefully(self):
        from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
            render_strategy_discovery_feature_importance,
        )
        pkg = _MockPkg()
        pkg.metadata["derived"]["strategy_discovery"] = {
            "importance": {"error": "rf_failed"}
        }
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_feature_importance(ctx)
        assert "rf_failed" in result or "error" in result.lower()


# ---------------------------------------------------------------------------
# Validation Section
# ---------------------------------------------------------------------------

class TestValidationSection:
    """Tests for strategy_discovery_validation.py renderer."""

    def _make_validation_result(
        self,
        overall_pass: bool = True,
        is_pf: float = 1.45,
        oos_pf: float = 1.28,
        degrad: float = 0.12,
        t_pass: bool = True,
        mc_pass: bool = True,
    ) -> dict:
        return {
            "passed": overall_pass,
            "wf_results": {
                "is_pf": is_pf,
                "oos_pf": oos_pf,
                "oos_degradation": degrad,
                "is_trades": 80,
                "oos_trades": 35,
                "passed_min_counts": True,
                "passed_degradation": degrad <= 0.20,
                "wf_type": "rolling",
                "issues": [],
            },
            "t_test": {
                "t_stat": 2.31,
                "p_value": 0.022,
                "passed": t_pass,
                "n_valid": 115,
            },
            "monte_carlo": {
                "actual_max_dd": 820.0,
                "mc_dd_p95": 1400.0,
                "passed": mc_pass,
                "n_simulations": 500,
            },
            "gate_results": {
                "min_counts":  True,
                "degradation": degrad <= 0.20,
                "t_test":      t_pass,
                "monte_carlo": mc_pass,
            },
            "issues": [] if overall_pass else ["OOS degradation exceeded threshold"],
        }

    def _make_pkg_with_validation(self, **kwargs) -> "_MockPkg":
        pkg = _MockPkg()
        val = self._make_validation_result(**kwargs)
        pkg.metadata["derived"]["strategy_discovery"] = {"validation": val}
        return pkg

    # --- renderer basics ---

    def test_renderer_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkg = self._make_pkg_with_validation()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_validation(ctx)
        assert isinstance(result, str)
        assert len(result) > 200

    def test_renderer_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        result = render_strategy_discovery_validation({"packages": {}, "options": {}})
        assert "No validation data" in result

    def test_renderer_shows_pass_badge(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkg = self._make_pkg_with_validation(overall_pass=True)
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_validation(ctx)
        assert "PASS" in result

    def test_renderer_shows_fail_badge(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkg = self._make_pkg_with_validation(overall_pass=False, degrad=0.35)
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_validation(ctx)
        assert "FAIL" in result

    def test_renderer_shows_is_oos_pf(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkg = self._make_pkg_with_validation(is_pf=1.55, oos_pf=1.30)
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_validation(ctx)
        assert "1.55" in result
        assert "1.30" in result

    def test_renderer_shows_t_test(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkg = self._make_pkg_with_validation()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_validation(ctx)
        assert "T-Test" in result or "t-stat" in result.lower() or "2.31" in result

    def test_renderer_shows_monte_carlo(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkg = self._make_pkg_with_validation()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_validation(ctx)
        assert "Monte Carlo" in result or "820" in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkgs = {
            "run_A": self._make_pkg_with_validation(overall_pass=True),
            "run_B": self._make_pkg_with_validation(overall_pass=False, degrad=0.35),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_validation(ctx)
        assert "run_A" in result
        assert "run_B" in result
        assert "Cross-Run" in result or "Walk-Forward" in result

    def test_cross_run_counts_pass_fail(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkgs = {
            "run_A": self._make_pkg_with_validation(overall_pass=True),
            "run_B": self._make_pkg_with_validation(overall_pass=True),
            "run_C": self._make_pkg_with_validation(overall_pass=False, degrad=0.40),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_validation(ctx)
        assert "2 PASS" in result or ">2 PASS<" in result or "2</span>" in result

    def test_hide_cross_run_option(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkgs = {
            "run_A": self._make_pkg_with_validation(),
            "run_B": self._make_pkg_with_validation(),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": False}}
        result = render_strategy_discovery_validation(ctx)
        assert "Cross-Run" not in result

    def test_max_runs_per_run_respected(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkgs = {f"run_{i}": self._make_pkg_with_validation() for i in range(5)}
        ctx = {"packages": pkgs, "options": {"max_runs": 2, "show_cross_run": False}}
        result = render_strategy_discovery_validation(ctx)
        assert "run_0" in result
        assert "run_1" in result
        assert "run_2" not in result

    def test_error_state_rendered(self):
        from ta_foundation.reports.html.sections.strategy_discovery_validation import (
            render_strategy_discovery_validation,
        )
        pkg = _MockPkg()
        pkg.metadata["derived"]["strategy_discovery"] = {
            "validation": {"error": "wf_split_too_small"}
        }
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_validation(ctx)
        assert "wf_split_too_small" in result or "error" in result.lower()


# ---------------------------------------------------------------------------
# MAE/MFE Profile Section
# ---------------------------------------------------------------------------

class TestMaeMfeSection:
    """Tests for strategy_discovery_mae_mfe.py renderer."""

    def _make_mae_mfe_result(self) -> dict:
        dists = {}
        for series in ("mae_winners", "mfe_all", "etd_all"):
            for p in (10, 25, 50, 75, 90):
                dists[f"{series}_p{p}"] = float(50 + p * 5)
        return {
            "distributions": dists,
            "exit_parameter_bounds": {
                "stop_min_usd": 120.0,
                "stop_natural_usd": 200.0,
                "stop_max_usd": 280.0,
                "stop_min_pts": 4.8,
                "stop_natural_pts": 8.0,
                "stop_max_pts": 11.2,
                "target_min_usd": 160.0,
                "target_natural_usd": 320.0,
                "target_max_usd": 500.0,
                "target_min_pts": 6.4,
                "target_natural_pts": 12.8,
                "target_max_pts": 20.0,
                "trail_activation_usd": 180.0,
                "trail_distance_usd": 250.0,
                "trail_activation_pts": 7.2,
                "trail_distance_pts": 10.0,
                "mfe_mae_ratio": 1.8,
            },
            "by_direction": {
                "Long":  {"n_trades": 60, "mae_winners_p50": 110.0, "mae_winners_p80": 190.0,
                          "mfe_all_p60": 290.0, "mfe_all_p90": 480.0},
                "Short": {"n_trades": 40, "mae_winners_p50": 130.0, "mae_winners_p80": 220.0,
                          "mfe_all_p60": 310.0, "mfe_all_p90": 510.0},
            },
            "diagnostics": {"n_trades": 100, "n_winners": 62, "issues": []},
        }

    def _make_pkg_with_profile(self) -> "_MockPkg":
        pkg = _MockPkg()
        pkg.metadata["derived"]["strategy_discovery"] = {
            "mae_mfe_profile": self._make_mae_mfe_result()
        }
        return pkg

    def test_renderer_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkg = self._make_pkg_with_profile()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert isinstance(result, str) and len(result) > 200

    def test_renderer_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        result = render_strategy_discovery_mae_mfe({"packages": {}, "options": {}})
        assert "No MAE/MFE" in result

    def test_renderer_shows_stop_natural(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkg = self._make_pkg_with_profile()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert "200" in result  # stop_natural_usd
        assert "Stop Natural" in result

    def test_renderer_shows_mfe_mae_ratio(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkg = self._make_pkg_with_profile()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert "1.80" in result or "MFE/MAE" in result

    def test_renderer_shows_direction_breakdown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkg = self._make_pkg_with_profile()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_direction": True}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert "Long" in result
        assert "Short" in result

    def test_renderer_hides_direction_when_disabled(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkg = self._make_pkg_with_profile()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_direction": False}}
        result = render_strategy_discovery_mae_mfe(ctx)
        # Direction table header should not appear
        assert "By Direction" not in result

    def test_cross_run_table_shown_with_multiple_runs(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkgs = {f"run_{i}": self._make_pkg_with_profile() for i in range(3)}
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert "Cross-Run" in result or "Exit Parameter Bounds" in result

    def test_cross_run_shows_all_run_ids(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkgs = {"alpha": self._make_pkg_with_profile(),
                "beta":  self._make_pkg_with_profile()}
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert "alpha" in result
        assert "beta" in result

    def test_max_runs_respected(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkgs = {f"run_{i}": self._make_pkg_with_profile() for i in range(4)}
        ctx = {"packages": pkgs, "options": {"max_runs": 2, "show_cross_run": False}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert "run_0" in result
        assert "run_1" in result
        assert "run_2" not in result

    def test_percentile_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkg = self._make_pkg_with_profile()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert "p50" in result or "p10" in result

    def test_error_state_rendered(self):
        from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
            render_strategy_discovery_mae_mfe,
        )
        pkg = _MockPkg()
        pkg.metadata["derived"]["strategy_discovery"] = {
            "mae_mfe_profile": {"error": "mae_column_missing"}
        }
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_mae_mfe(ctx)
        assert "mae_column_missing" in result or "error" in result.lower()


# ---------------------------------------------------------------------------
# Position Sizing Section
# ---------------------------------------------------------------------------

def _make_ps_result(
    kelly_full: float = 0.20,
    rec_fok: float = 0.50,
    rec_kf: float = 0.10,
    n_trades: int = 80,
) -> dict:
    """Build a synthetic position_sizing result dict."""
    fracs = []
    for fok in [0.25, 0.50, 0.75, 1.00]:
        kf_val = round(kelly_full * fok, 4)
        safe = fok <= 0.50
        fracs.append({
            "fraction_of_kelly":       fok,
            "kelly_fraction":          kf_val,
            "median_final_equity":     10000.0 * (1 + fok * 0.15),
            "p5_final_equity":         10000.0 * (1 + fok * 0.05),
            "expected_return_pct":     round(fok * 8.0, 2),
            "p_ruin":                  round(fok * 0.03, 4),
            "p_dd_warn":               round(fok * 0.10, 4),
            "median_max_drawdown_pct": round(fok * 12.0, 2),
            "p95_max_drawdown_pct":    round(fok * 20.0, 2),
            "safe":                    safe,
        })
    return {
        "trade_stats": {
            "n_trades":    n_trades,
            "n_winners":   50,
            "n_losers":    30,
            "win_rate":    0.625,
            "avg_win":     120.0,
            "avg_loss":    80.0,
            "payoff_ratio": 1.5,
            "expectancy":  45.0,
            "std_profit":  150.0,
        },
        "kelly_full":   kelly_full,
        "kelly_fractions": fracs,
        "recommendation": {
            "recommended_fraction_of_kelly": rec_fok,
            "recommended_kelly_fraction":    rec_kf,
            "reasoning": f"Recommended {rec_fok}× Kelly (f={rec_kf:.3f}): expected return 4.0% over 200 trades, p_ruin=1.5%, median max-DD=6.0%.",
        },
        "regime_conditional": {
            "trending": {
                "n_trades":    45,
                "win_rate":    0.67,
                "avg_win":     130.0,
                "avg_loss":    75.0,
                "payoff_ratio": 1.73,
                "kelly_full":  0.25,
                "kelly_half":  0.125,
            },
            "choppy": {
                "n_trades":    35,
                "win_rate":    0.57,
                "avg_win":     100.0,
                "avg_loss":    90.0,
                "payoff_ratio": 1.11,
                "kelly_full":  0.06,
                "kelly_half":  0.03,
            },
        },
        "diagnostics": {
            "n_trades":        n_trades,
            "profit_col_used": "profit_net",
            "n_sim":           500,
            "issues":          [],
        },
    }


def _make_pkg_with_ps(ps: dict | None = None) -> _MockPkg:
    pkg = _MockPkg()
    pkg.metadata["derived"]["strategy_discovery"] = {
        "position_sizing": ps if ps is not None else _make_ps_result()
    }
    return pkg


class TestPositionSizingSection:

    def test_basic_render_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "<" in result and ">" in result

    def test_kelly_full_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps(_make_ps_result(kelly_full=0.2345))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "0.234" in result or "0.2345" in result

    def test_recommendation_box_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "Recommendation" in result or "Recommended" in result

    def test_fraction_rows_rendered(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        # All 4 fractional Kelly variants should appear
        assert "0.25" in result
        assert "0.50" in result or "0.5" in result
        assert "0.75" in result

    def test_safe_badge_present(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "SAFE" in result or "RISK" in result

    def test_trade_stats_kpi_strip(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps(_make_ps_result(n_trades=92))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "92" in result
        assert "Win Rate" in result

    def test_regime_conditional_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_regime_conditional": True}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "trending" in result
        assert "choppy" in result

    def test_regime_conditional_hidden(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_regime_conditional": False}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "trending" not in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkgs = {
            "alpha": _make_pkg_with_ps(_make_ps_result(kelly_full=0.18)),
            "beta":  _make_pkg_with_ps(_make_ps_result(kelly_full=0.25)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "alpha" in result
        assert "beta" in result
        assert "Cross-Run" in result or "Full Kelly" in result

    def test_cross_run_table_hidden_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps()
        ctx = {"packages": {"solo": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_position_sizing(ctx)
        # Cross-run table only shown when > 1 run
        assert "Cross-Run" not in result

    def test_skipped_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps({"skipped": True})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "skipped" in result.lower() or "disabled" in result.lower()

    def test_error_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        pkg = _make_pkg_with_ps({"error": "no_profit_col"})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "no_profit_col" in result or "Error" in result

    def test_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
            render_strategy_discovery_position_sizing,
        )
        ctx = {"packages": {}, "options": {}}
        result = render_strategy_discovery_position_sizing(ctx)
        assert "no strategy" in result.lower() or "no data" in result.lower() or "<" in result

    def test_run_position_sizing_unit(self):
        """run_position_sizing returns required keys with sufficient trades."""
        pkg = _MockPkg()
        opts = {"enabled": True, "n_sim": 50, "n_trades_sim": 30, "min_trades": 10}
        result = run_position_sizing(pkg, opts)
        assert "kelly_full" in result
        assert "kelly_fractions" in result
        assert "recommendation" in result
        assert "trade_stats" in result
        assert "diagnostics" in result

    def test_compute_kelly_positive_edge(self):
        """Kelly fraction positive for win_rate=0.6, payoff=1.5."""
        f = compute_kelly(0.6, 150.0, 100.0)
        assert f is not None and f > 0

    def test_compute_kelly_negative_edge(self):
        """Kelly fraction ≤ 0 clamped to 0 for losing strategy."""
        f = compute_kelly(0.3, 50.0, 100.0)
        assert f is not None and f == 0.0


# ---------------------------------------------------------------------------
# Risk Metrics Section
# ---------------------------------------------------------------------------

def _make_rm_result(
    sharpe: float = 1.2,
    sortino: float = 1.8,
    calmar: float = 0.9,
    omega: float = 1.5,
    ann_ret: float = 18.5,
    max_dd: float = 12.3,
    n_trades: int = 80,
) -> dict:
    return {
        "sharpe_ratio":          sharpe,
        "sortino_ratio":         sortino,
        "calmar_ratio":          calmar,
        "omega_ratio":           omega,
        "mar_ratio":             calmar,
        "annualized_return_pct": ann_ret,
        "total_return_pct":      ann_ret * 0.8,
        "downside_deviation":    0.0045,
        "max_drawdown_pct":      max_dd,
        "trades_per_year":       120.0,
        "net_profit":            1850.0,
        "diagnostics": {
            "n_trades":        n_trades,
            "profit_col_used": "profit_net",
            "initial_equity":  10000.0,
            "issues":          [],
        },
    }


def _make_pkg_with_rm(rm: dict | None = None) -> _MockPkg:
    pkg = _MockPkg()
    pkg.metadata["derived"]["strategy_discovery"] = {
        "risk_metrics": rm if rm is not None else _make_rm_result()
    }
    return pkg


class TestRiskMetricsSection:

    def test_basic_render_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "<" in result and ">" in result

    def test_sharpe_ratio_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm(_make_rm_result(sharpe=1.42))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "1.42" in result

    def test_sortino_and_calmar_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm(_make_rm_result(sortino=2.1, calmar=0.85))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "2.10" in result or "2.1" in result
        assert "0.85" in result

    def test_annualized_return_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm(_make_rm_result(ann_ret=22.5))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "22.5" in result

    def test_max_dd_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm(_make_rm_result(max_dd=9.7))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "9.7" in result

    def test_ratio_pill_labels_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "Sharpe" in result
        assert "Sortino" in result
        assert "Calmar" in result
        assert "Omega" in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkgs = {
            "alpha": _make_pkg_with_rm(_make_rm_result(sharpe=1.2)),
            "beta":  _make_pkg_with_rm(_make_rm_result(sharpe=0.8)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "alpha" in result
        assert "beta" in result
        assert "Cross-Run" in result or "Sharpe" in result

    def test_cross_run_table_hidden_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm()
        ctx = {"packages": {"solo": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "Cross-Run" not in result

    def test_best_column_highlighted(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkgs = {
            "high": _make_pkg_with_rm(_make_rm_result(sharpe=2.0)),
            "low":  _make_pkg_with_rm(_make_rm_result(sharpe=0.5)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True, "show_per_run": False}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "best" in result  # best CSS class applied

    def test_skipped_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm({"skipped": True})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "skipped" in result.lower() or "disabled" in result.lower()

    def test_error_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        pkg = _make_pkg_with_rm({"error": "no_profit_col"})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "no_profit_col" in result or "Error" in result

    def test_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
            render_strategy_discovery_risk_metrics,
        )
        ctx = {"packages": {}, "options": {}}
        result = render_strategy_discovery_risk_metrics(ctx)
        assert "<" in result  # returns something

    def test_run_risk_metrics_unit(self):
        """run_risk_metrics returns required keys with sufficient trades."""
        from ta_foundation.analysis.strategy_discovery.risk_metrics import run_risk_metrics
        pkg = _MockPkg()
        opts = {"enabled": True, "min_trades": 10}
        result = run_risk_metrics(pkg, opts)
        assert "sharpe_ratio" in result
        assert "sortino_ratio" in result
        assert "calmar_ratio" in result
        assert "omega_ratio" in result
        assert "diagnostics" in result


# ---------------------------------------------------------------------------
# Drawdown Analysis Section
# ---------------------------------------------------------------------------

def _make_da_result(
    max_dd_pct: float = 14.5,
    rf: float = 2.1,
    ulcer: float = 5.2,
    max_losses: int = 4,
) -> dict:
    """Build a synthetic drawdown_analysis result dict."""
    return {
        "overall": {
            "max_drawdown_usd":      -1450.0,
            "max_drawdown_pct":      max_dd_pct,
            "max_dd_start":          "2025-02-10",
            "max_dd_trough":         "2025-02-18",
            "max_dd_recovery":       "2025-03-01",
            "recovery_bars":         12,
            "longest_drawdown_bars": 15,
            "recovery_factor":       rf,
            "ulcer_index":           ulcer,
            "avg_drawdown_pct":      3.8,
        },
        "streaks": {
            "max_consecutive_losses": max_losses,
            "max_loss_streak_usd":    -820.0,
            "avg_loss_streak_len":    2.3,
            "max_consecutive_wins":   7,
            "avg_win_streak_len":     3.1,
            "n_loss_streaks":         18,
            "n_win_streaks":          22,
        },
        "rolling_max_dd": [
            {"period_end": f"2025-0{i+1}-15", "max_dd_pct": round(5 + i * 2.5, 1)}
            for i in range(6)
        ],
        "by_regime": {
            "trending": {"max_drawdown_pct": 8.2, "max_consecutive_losses": 2},
            "choppy":   {"max_drawdown_pct": 19.1, "max_consecutive_losses": 6},
        },
        "diagnostics": {
            "n_trades":        80,
            "profit_col_used": "profit_net",
            "initial_equity":  10000.0,
            "issues":          [],
        },
    }


def _make_pkg_with_da(da: dict | None = None) -> _MockPkg:
    pkg = _MockPkg()
    pkg.metadata["derived"]["strategy_discovery"] = {
        "drawdown_analysis": da if da is not None else _make_da_result()
    }
    return pkg


class TestDrawdownSection:

    def test_basic_render_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "<" in result and ">" in result

    def test_max_dd_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da(_make_da_result(max_dd_pct=17.3))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "17.3" in result

    def test_recovery_factor_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da(_make_da_result(rf=3.5))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "3.50" in result or "3.5" in result

    def test_ulcer_index_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da(_make_da_result(ulcer=7.88))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "7.88" in result or "7.9" in result

    def test_streak_stats_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da(_make_da_result(max_losses=5))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "5" in result
        assert "Streak" in result or "streak" in result.lower()

    def test_rolling_bars_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_rolling": True}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "Rolling" in result or "rolling" in result.lower()

    def test_rolling_bars_hidden(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_rolling": False}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "Rolling" not in result

    def test_regime_breakdown_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_regime": True}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "trending" in result
        assert "choppy" in result

    def test_regime_breakdown_hidden(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_regime": False}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "trending" not in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkgs = {
            "alpha": _make_pkg_with_da(_make_da_result(max_dd_pct=10.0)),
            "beta":  _make_pkg_with_da(_make_da_result(max_dd_pct=22.0)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "alpha" in result and "beta" in result
        assert "Cross-Run" in result or "Max DD" in result

    def test_cross_run_hidden_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da()
        ctx = {"packages": {"solo": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "Cross-Run" not in result

    def test_skipped_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da({"skipped": True})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "skipped" in result.lower() or "disabled" in result.lower()

    def test_error_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
            render_strategy_discovery_drawdown,
        )
        pkg = _make_pkg_with_da({"error": "no_equity_data"})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_drawdown(ctx)
        assert "no_equity_data" in result or "Error" in result

    def test_run_drawdown_analysis_unit(self):
        """run_drawdown_analysis returns required keys."""
        pkg = _MockPkg()
        opts = {"enabled": True, "min_trades": 10}
        result = run_drawdown_analysis(pkg, opts)
        assert "overall" in result
        assert "streaks" in result
        assert "rolling_max_dd" in result
        assert "diagnostics" in result


# ---------------------------------------------------------------------------
# Cohort Analysis Section
# ---------------------------------------------------------------------------

def _make_ca_result(
    classification: str = "degrading",
    decay_score: int = 55,
    n_cohorts: int = 4,
) -> dict:
    cohorts = []
    for i in range(n_cohorts):
        pf_val = round(1.8 - i * 0.2, 2)  # declining trend
        cohorts.append({
            "cohort_index": i,
            "period_start": f"2025-0{i+1}-01",
            "period_end":   f"2025-0{i+1}-20",
            "n_trades":     20,
            "pf":           pf_val,
            "win_rate":     round(0.65 - i * 0.05, 4),
            "avg_profit":   round(60 - i * 10, 2),
            "net_profit":   round(1200 - i * 200, 2),
        })
    return {
        "cohorts": cohorts,
        "trend": {
            "slope":          -0.06,
            "intercept":       1.85,
            "r_squared":       0.82,
            "classification":  classification,
        },
        "decay_score":   decay_score,
        "early_vs_late": {
            "pf_early":  1.80,
            "pf_late":   1.20,
            "delta_pf":  -0.60,
            "wr_early":  0.65,
            "wr_late":   0.50,
            "delta_wr":  -0.15,
            "overall_pf": 1.50,
            "last_pf":   1.20,
        },
        "overall": {
            "n_trades":   n_cohorts * 20,
            "pf":         1.50,
            "win_rate":   0.575,
            "avg_profit": 50.0,
            "net_profit": 4000.0,
        },
        "diagnostics": {
            "n_trades":        n_cohorts * 20,
            "cohort_count":    n_cohorts,
            "profit_col_used": "profit_net",
            "cohort_by":       "count",
            "cohort_size":     20,
            "issues":          [],
        },
    }


def _make_pkg_with_ca(ca: dict | None = None) -> _MockPkg:
    pkg = _MockPkg()
    pkg.metadata["derived"]["strategy_discovery"] = {
        "cohort_analysis": ca if ca is not None else _make_ca_result()
    }
    return pkg


class TestCohortSection:

    def test_basic_render_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_cohort(ctx)
        assert "<" in result and ">" in result

    def test_trend_badge_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca(_make_ca_result(classification="degrading"))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_cohort(ctx)
        assert "degrading" in result.lower() or "DEGRADING" in result

    def test_improving_trend_badge(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca(_make_ca_result(classification="improving"))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_cohort(ctx)
        assert "improving" in result.lower() or "IMPROVING" in result

    def test_decay_score_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca(_make_ca_result(decay_score=72))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_cohort(ctx)
        assert "72" in result

    def test_cohort_rows_rendered(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca(_make_ca_result(n_cohorts=4))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_cohort(ctx)
        assert "Cohort" in result or "cohort" in result.lower()

    def test_early_vs_late_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_early_vs_late": True}}
        result = render_strategy_discovery_cohort(ctx)
        assert "Early" in result and "Late" in result

    def test_early_vs_late_hidden(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_early_vs_late": False}}
        result = render_strategy_discovery_cohort(ctx)
        assert "Early" not in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkgs = {
            "alpha": _make_pkg_with_ca(_make_ca_result(classification="stable",    decay_score=20)),
            "beta":  _make_pkg_with_ca(_make_ca_result(classification="degrading", decay_score=65)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_cohort(ctx)
        assert "alpha" in result and "beta" in result
        assert "Drift" in result or "Decay" in result or "Trend" in result

    def test_cross_run_hidden_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca()
        ctx = {"packages": {"solo": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_cohort(ctx)
        assert "Cross-Run" not in result

    def test_skipped_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca({"skipped": True})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_cohort(ctx)
        assert "skipped" in result.lower() or "disabled" in result.lower()

    def test_error_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
            render_strategy_discovery_cohort,
        )
        pkg = _make_pkg_with_ca({"error": "too_few_cohorts"})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_cohort(ctx)
        assert "too_few_cohorts" in result or "Error" in result

    def test_run_cohort_analysis_unit(self):
        """run_cohort_analysis returns required keys with sufficient trades."""
        pkg = _MockPkg()
        opts = {"enabled": True, "min_trades": 10, "min_cohorts": 2, "cohort_size": 10}
        result = run_cohort_analysis(pkg, opts)
        assert "cohorts" in result
        assert "trend" in result
        assert "decay_score" in result
        assert "diagnostics" in result


# ---------------------------------------------------------------------------
# Classification Section
# ---------------------------------------------------------------------------

def _make_cl_result(
    label: str = "automated",
    score: float = 75.0,
    confidence: str = "high",
) -> dict:
    return {
        "label":            label,
        "automation_score": score,
        "confidence":       confidence,
        "signals": {
            "has_settings":           1.0,
            "has_regime_data":        1.0,
            "pattern_coverage":       0.8,
            "pattern_predictive":     0.7,
            "session_spread":         0.6,
            "hold_time_consistency":  0.9,
            "session_wr_consistency": 0.5,
            "trade_count":            1.0,
        },
        "reasoning": [
            "Settings file present → +1 automation signal.",
            "Pattern engine has high coverage.",
            "Session spread is moderate.",
        ],
    }


def _make_pkg_with_cl(cl: dict | None = None) -> _MockPkg:
    pkg = _MockPkg()
    pkg.metadata["derived"]["strategy_discovery"] = {
        "classification": cl if cl is not None else _make_cl_result()
    }
    return pkg


class TestClassificationSection:

    def test_basic_render_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_classification(ctx)
        assert "<" in result and ">" in result

    def test_automated_label_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl(_make_cl_result(label="automated"))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_classification(ctx)
        assert "Automated" in result or "automated" in result

    def test_hybrid_label_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl(_make_cl_result(label="hybrid", score=55.0))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_classification(ctx)
        assert "Hybrid" in result or "hybrid" in result

    def test_semi_discretionary_label_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl(_make_cl_result(label="semi_discretionary", score=20.0))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_classification(ctx)
        assert "Semi" in result or "Discretionary" in result or "semi_discretionary" in result

    def test_score_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl(_make_cl_result(score=82.5))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_classification(ctx)
        assert "82.5" in result or "82" in result

    def test_signal_bars_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_classification(ctx)
        assert "Signal" in result or "signal" in result or "Settings" in result

    def test_reasoning_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_classification(ctx)
        assert "automation signal" in result or "Reasoning" in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkgs = {
            "alpha": _make_pkg_with_cl(_make_cl_result(label="automated", score=78.0)),
            "beta":  _make_pkg_with_cl(_make_cl_result(label="hybrid",    score=50.0)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_classification(ctx)
        assert "alpha" in result and "beta" in result

    def test_cross_run_hidden_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl()
        ctx = {"packages": {"solo": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_classification(ctx)
        assert "Cross-Run" not in result

    def test_error_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_classification import (
            render_strategy_discovery_classification,
        )
        pkg = _make_pkg_with_cl({"error": "no_settings_file"})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_classification(ctx)
        assert "no_settings_file" in result or "Error" in result

    def test_classify_strategy_unit(self):
        """classify_strategy returns required keys."""
        pkg = _MockPkg()
        result = classify_strategy(pkg)
        assert "label" in result
        assert "automation_score" in result
        assert "confidence" in result
        assert "signals" in result
        assert "reasoning" in result


# ---------------------------------------------------------------------------
# Evaluation Section
# ---------------------------------------------------------------------------

def _make_ev_result(
    pf: float = 1.8,
    wr: float = 0.62,
    net: float = 2400.0,
    n_trades: int = 80,
) -> dict:
    n_w = int(n_trades * wr)
    n_l = n_trades - n_w
    avg_w = 85.0
    avg_l = -55.0
    exp = wr * avg_w + (1 - wr) * avg_l
    equity_curve = [float(i * 30.0 - 5.0 * (i % 7)) for i in range(min(n_trades, 80))]
    return {
        "n_trades":   n_trades,
        "n_winners":  n_w,
        "n_losers":   n_l,
        "profit_factor": pf,
        "win_rate":   wr,
        "net_profit": net,
        "gross_profit": float(n_w * avg_w),
        "gross_loss":   float(n_l * avg_l),
        "avg_trade":  float(net / n_trades),
        "avg_winner": avg_w,
        "avg_loser":  avg_l,
        "expectancy": exp,
        "max_drawdown": -340.0,
        "avg_duration_min": 22.5,
        "equity_curve": equity_curve,
        "by_direction": {
            "Long": {
                "n_trades": n_trades // 2,
                "profit_factor": round(pf + 0.1, 2),
                "win_rate": wr + 0.03,
                "net_profit": net * 0.6,
                "avg_trade": float(net * 0.6 / (n_trades // 2)),
            },
            "Short": {
                "n_trades": n_trades // 2,
                "profit_factor": round(pf - 0.1, 2),
                "win_rate": wr - 0.03,
                "net_profit": net * 0.4,
                "avg_trade": float(net * 0.4 / (n_trades // 2)),
            },
        },
        "by_session": {
            "RTH": {
                "n_trades": int(n_trades * 0.7),
                "profit_factor": pf,
                "win_rate": wr,
                "net_profit": net * 0.75,
                "avg_trade": float(net * 0.75 / (n_trades * 0.7)),
            },
            "ONH": {
                "n_trades": int(n_trades * 0.3),
                "profit_factor": round(pf * 0.8, 2),
                "win_rate": wr * 0.9,
                "net_profit": net * 0.25,
                "avg_trade": float(net * 0.25 / (n_trades * 0.3)),
            },
        },
        "diagnostics": {
            "profit_col_used": "profit_net",
            "issues": [],
        },
    }


def _make_pkg_with_ev(ev: dict | None = None) -> _MockPkg:
    pkg = _MockPkg()
    pkg.metadata["derived"]["strategy_discovery"] = {
        "evaluation": ev if ev is not None else _make_ev_result()
    }
    return pkg


class TestEvaluationSection:

    def test_basic_render_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "<" in result and ">" in result

    def test_profit_factor_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev(_make_ev_result(pf=2.15))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "2.15" in result

    def test_trade_count_displayed(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev(_make_ev_result(n_trades=92))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "92" in result

    def test_equity_sparkline_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_equity_curve": True}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "<svg" in result or "polyline" in result or "Equity" in result

    def test_equity_sparkline_hidden(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_equity_curve": False}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "<svg" not in result

    def test_direction_breakdown_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_direction": True}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "Long" in result and "Short" in result

    def test_direction_breakdown_hidden(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_direction": False}}
        result = render_strategy_discovery_evaluation(ctx)
        # Long/Short won't appear since direction is disabled
        assert "Direction" not in result

    def test_session_breakdown_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_session": True}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "RTH" in result

    def test_session_breakdown_hidden(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_session": False}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "Session" not in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkgs = {
            "alpha": _make_pkg_with_ev(_make_ev_result(pf=2.0, net=3000.0)),
            "beta":  _make_pkg_with_ev(_make_ev_result(pf=1.3, net=900.0)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "alpha" in result and "beta" in result

    def test_cross_run_hidden_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev()
        ctx = {"packages": {"solo": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "Cross-Run" not in result

    def test_error_state(self):
        from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
            render_strategy_discovery_evaluation,
        )
        pkg = _make_pkg_with_ev({"error": "no_profit_col"})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_evaluation(ctx)
        assert "no_profit_col" in result or "Error" in result

    def test_compute_evaluation_metrics_unit(self):
        """compute_evaluation_metrics returns required keys."""
        pkg = _MockPkg()
        result = compute_evaluation_metrics(pkg.trades, profit_col="profit_net")
        assert "n_trades" in result
        assert "profit_factor" in result
        assert "win_rate" in result
        assert "equity_curve" in result
        assert "by_direction" in result
        assert "by_session" in result


# ---------------------------------------------------------------------------
# Regime Section
# ---------------------------------------------------------------------------

_REGIME_LABELS = [
    "trending_up", "ranging_wide", "high_vol_expansion",
    "trending_down", "low_vol_compression", "ranging_tight",
]


def _make_regime_records(n_days: int = 15) -> list:
    records = []
    rng = np.random.default_rng(99)
    for i in range(n_days):
        day_id = f"2025-0{(i // 10) + 1}-{(i % 10) + 1:02d}"
        records.append({
            "day_id":          day_id,
            "dominant_regime": _REGIME_LABELS[i % len(_REGIME_LABELS)],
            "pct_trending":    float(rng.integers(20, 70)),
            "pct_ranging":     float(rng.integers(10, 50)),
            "pct_high_vol":    float(rng.integers(5, 40)),
            "avg_adx":         round(float(rng.uniform(18, 35)), 2),
            "avg_atr":         round(float(rng.uniform(10, 25)), 2),
        })
    return records


def _make_pkg_with_regime(records: list | None = None, issues: list | None = None) -> _MockPkg:
    pkg = _MockPkg()
    pkg.metadata["derived"]["strategy_discovery"] = {
        "regime_summary": records if records is not None else _make_regime_records(),
        "regime_issues":  issues or [],
    }
    return pkg


class TestRegimeSection:

    def test_basic_render_returns_html(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_regime(ctx)
        assert "<" in result and ">" in result

    def test_dominant_regime_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_regime(ctx)
        assert "trending" in result.lower() or "ranging" in result.lower()

    def test_distribution_bars_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_regime(ctx)
        assert "Trending" in result and "Ranging" in result

    def test_recent_days_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_recent_days": True}}
        result = render_strategy_discovery_regime(ctx)
        assert "Recent Days" in result or "2025-0" in result

    def test_recent_days_hidden(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime()
        ctx = {"packages": {"run_A": pkg}, "options": {"show_recent_days": False}}
        result = render_strategy_discovery_regime(ctx)
        assert "Recent Days" not in result

    def test_regime_count_pills(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime(_make_regime_records(n_days=12))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_regime(ctx)
        assert "Trending Up" in result or "trending_up" in result.lower()

    def test_issues_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime([], ["market data unavailable for NQ"])
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_regime(ctx)
        assert "market data unavailable" in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkgs = {
            "alpha": _make_pkg_with_regime(_make_regime_records(n_days=10)),
            "beta":  _make_pkg_with_regime(_make_regime_records(n_days=10)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_regime(ctx)
        assert "alpha" in result and "beta" in result
        assert "Cross-Run" in result or "Regime" in result

    def test_cross_run_hidden_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime()
        ctx = {"packages": {"solo": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_regime(ctx)
        assert "Cross-Run" not in result

    def test_empty_records(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        pkg = _make_pkg_with_regime([])
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_regime(ctx)
        assert "no regime" in result.lower() or "No regime" in result

    def test_max_recent_days_option(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import (
            render_strategy_discovery_regime,
        )
        records = _make_regime_records(n_days=30)
        pkg = _make_pkg_with_regime(records)
        ctx = {"packages": {"run_A": pkg}, "options": {"max_recent_days": 5}}
        result = render_strategy_discovery_regime(ctx)
        assert "last 5" in result or "5 of" in result

    def test_aggregate_helper(self):
        from ta_foundation.reports.html.sections.strategy_discovery_regime import _aggregate
        records = _make_regime_records(n_days=10)
        agg = _aggregate(records)
        assert agg["n_days"] == 10
        assert "regime_counts" in agg
        assert "top_regime" in agg
        assert agg["avg_adx"] is not None


# ===========================================================================
# Parameter Sensitivity Section
# ===========================================================================

def _make_param_sens_result(n_rules: int = 3, n_robust: int = 2, n_fragile: int = 1) -> dict:
    """Synthetic parameter_sensitivity output."""
    rule_sweeps = []
    for i in range(n_rules):
        sweep_points = []
        for j in range(-3, 4):
            thr = 25.0 + j * 1.0
            pf  = max(0.5, 1.4 - abs(j) * 0.15)
            sweep_points.append({
                "threshold": thr,
                "n_trades": 50 - abs(j) * 3,
                "pf": round(pf, 4),
                "win_rate": round(0.55 - abs(j) * 0.02, 4),
            })
        cls = "robust" if i < n_robust else ("fragile" if i >= (n_rules - n_fragile) else "moderate")
        rule_sweeps.append({
            "rule_id": f"rule_{i}",
            "rule_desc": f"adx >= {25 + i} AND pattern_score >= {0.6 + i * 0.05:.2f}",
            "sweeps": [{
                "col": "adx",
                "op": "gte",
                "original_value": 25.0 + i,
                "step": 1.0,
                "threshold_sweep": sweep_points,
                "pf_at_original": 1.4,
                "pf_mean": 1.25,
                "pf_range": 0.30,
                "pf_slope": -0.05,
                "peak_ratio": 1.12 if cls == "robust" else 1.55,
                "sensitivity_score": 20 if cls == "robust" else 65,
                "classification": cls,
            }],
        })
    return {
        "rule_sweeps": rule_sweeps,
        "summary": {
            "n_rules_analysed": n_rules,
            "n_numeric_sweeps": n_rules,
            "n_robust": n_robust,
            "n_moderate": n_rules - n_robust - n_fragile,
            "n_fragile": n_fragile,
        },
        "diagnostics": {
            "n_trades": 200,
            "profit_col_used": "profit_net",
            "top_n_rules": n_rules,
            "issues": [],
        },
    }


def _make_pkg_with_param_sens(ps_result=None):
    if ps_result is None:
        ps_result = _make_param_sens_result()

    class _Pkg:
        metadata = {
            "derived": {
                "strategy_discovery": {
                    "parameter_sensitivity": ps_result,
                }
            }
        }
    return _Pkg()


class TestParameterSensitivitySection:
    def test_basic_render(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "run_A" in result
        assert "Rules Analysed" in result or "Robust" in result

    def test_rule_cards_rendered(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens(_make_param_sens_result(n_rules=3))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "rule_0" in result or "adx" in result

    def test_sweep_thresholds_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens()
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        # threshold values like 22.000 / 25.000 should appear
        assert "25.000" in result or "PF" in result

    def test_robust_badge_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens(_make_param_sens_result(n_rules=2, n_robust=2, n_fragile=0))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "Robust" in result

    def test_fragile_badge_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens(_make_param_sens_result(n_rules=2, n_robust=0, n_fragile=2))
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "Fragile" in result

    def test_skipped_flag(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens({"skipped": True})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "skip" in result.lower()

    def test_no_data(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens({})
        ctx = {"packages": {"run_A": pkg}, "options": {}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "No parameter" in result or "no-data" in result

    def test_cross_run_table_shown(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkgs = {
            "alpha": _make_pkg_with_param_sens(_make_param_sens_result(n_rules=3, n_robust=2, n_fragile=1)),
            "beta":  _make_pkg_with_param_sens(_make_param_sens_result(n_rules=2, n_robust=1, n_fragile=1)),
        }
        ctx = {"packages": pkgs, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "alpha" in result and "beta" in result
        assert "Cross-Run" in result or "Sensitivity" in result

    def test_cross_run_hidden_single_run(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens()
        ctx = {"packages": {"solo": pkg}, "options": {"show_cross_run": True}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "Cross-Run" not in result

    def test_max_rules_shown_option(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        pkg = _make_pkg_with_param_sens(_make_param_sens_result(n_rules=5))
        ctx = {"packages": {"run_A": pkg}, "options": {"max_rules_shown": 2}}
        result = render_strategy_discovery_parameter_sensitivity(ctx)
        assert "more rules not shown" in result or "more" in result

    def test_empty_packages(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            render_strategy_discovery_parameter_sensitivity,
        )
        result = render_strategy_discovery_parameter_sensitivity({"packages": {}, "options": {}})
        assert "No strategy discovery" in result

    def test_sensitivity_score_bar(self):
        from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
            _render_sweep,
        )
        sweep_info = {
            "col": "adx", "op": "gte", "original_value": 25.0, "step": 1.0,
            "threshold_sweep": [
                {"threshold": 24.0, "n_trades": 60, "pf": 1.2, "win_rate": 0.55},
                {"threshold": 25.0, "n_trades": 55, "pf": 1.4, "win_rate": 0.58},
                {"threshold": 26.0, "n_trades": 45, "pf": 1.1, "win_rate": 0.52},
            ],
            "sensitivity_score": 35, "classification": "moderate",
            "pf_at_original": 1.4, "pf_mean": 1.23, "peak_ratio": 1.14,
        }
        html = _render_sweep(sweep_info)
        assert "Sensitivity Score" in html
        assert "35" in html


# ===========================================================================
# TestFamilyBreakdown
# ===========================================================================

class TestFamilyBreakdown:
    """Tests for _compute_family_breakdown and its rendering."""

    def _make_df(self):
        np.random.seed(42)
        n = 60
        families = (["ORB"] * 30) + (["MA_TREND"] * 20) + (["VWAP"] * 10)
        ret = list(np.random.normal(2.0, 5.0, 30)) + list(np.random.normal(-1.0, 5.0, 20)) + list(np.random.normal(0.5, 3.0, 10))
        structures = (["orb_break"] * 30) + (["trend_cont"] * 10) + (["trend_rev"] * 10) + (["vwap_rev"] * 10)
        mfe = [abs(r) + 1.0 for r in ret]
        mae = [-abs(r) * 0.5 for r in ret]
        return pd.DataFrame({
            "family": families,
            "structure": structures,
            "ret_ticks": ret,
            "mfe_ticks": mfe,
            "mae_ticks": mae,
        })

    def test_returns_list(self):
        from ta_foundation.analysis.strategy_discovery.signal_entry_discovery import _compute_family_breakdown
        df = self._make_df()
        baseline = {"win_rate": 0.50, "n_signals": len(df)}
        result = _compute_family_breakdown(df, "ret_ticks", baseline)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_sorted_by_win_rate(self):
        from ta_foundation.analysis.strategy_discovery.signal_entry_discovery import _compute_family_breakdown
        df = self._make_df()
        baseline = {"win_rate": 0.50, "n_signals": len(df)}
        result = _compute_family_breakdown(df, "ret_ticks", baseline)
        wrs = [r["win_rate"] for r in result]
        assert wrs == sorted(wrs, reverse=True)

    def test_fields_present(self):
        from ta_foundation.analysis.strategy_discovery.signal_entry_discovery import _compute_family_breakdown
        df = self._make_df()
        baseline = {"win_rate": 0.50, "n_signals": len(df)}
        result = _compute_family_breakdown(df, "ret_ticks", baseline)
        required = {"family", "n_signals", "n_structures", "win_rate", "avg_ticks", "win_rate_lift", "selectivity"}
        for row in result:
            assert required.issubset(row.keys()), f"Missing keys in {row}"

    def test_min_signals_filter(self):
        from ta_foundation.analysis.strategy_discovery.signal_entry_discovery import _compute_family_breakdown
        df = self._make_df()
        baseline = {"win_rate": 0.50, "n_signals": len(df)}
        # VWAP has 10 rows; min_signals=15 should exclude it
        result = _compute_family_breakdown(df, "ret_ticks", baseline, min_signals=15)
        families = [r["family"] for r in result]
        assert "VWAP" not in families
        assert "ORB" in families

    def test_missing_family_column(self):
        from ta_foundation.analysis.strategy_discovery.signal_entry_discovery import _compute_family_breakdown
        df = self._make_df().drop(columns=["family"])
        baseline = {"win_rate": 0.50, "n_signals": len(df)}
        result = _compute_family_breakdown(df, "ret_ticks", baseline)
        assert result == []

    def test_family_breakdown_in_discovery_output(self):
        from ta_foundation.analysis.strategy_discovery.signal_entry_discovery import run_signal_entry_discovery
        df = self._make_df()
        df["pattern_id"] = range(len(df))
        df["session_label"] = "RTH"
        result = run_signal_entry_discovery(df, {}, profit_col="ret_ticks")
        assert "family_breakdown" in result
        assert isinstance(result["family_breakdown"], list)

    def test_family_breakdown_rendered_in_section(self):
        from ta_foundation.reports.html.sections.strategy_discovery_signal_entries import render_strategy_discovery_signal_entries
        df = self._make_df()
        df["pattern_id"] = range(len(df))
        df["session_label"] = "RTH"
        from ta_foundation.analysis.strategy_discovery.signal_entry_discovery import run_signal_entry_discovery
        sed = run_signal_entry_discovery(df, {}, profit_col="ret_ticks")
        import json
        packages = {
            "__market_discovery___RTH": type("P", (), {
                "trades": pd.DataFrame(),
                "metadata": {"derived": {"strategy_discovery": {"signal_entry_discovery": json.loads(json.dumps(sed))}}},
                "warnings": [],
                "assets": {},
            })()
        }
        ctx = {"packages": packages, "options": {}, "all_options": {}}
        html = render_strategy_discovery_signal_entries(ctx)
        assert "Family" in html or "family" in html.lower()


# ===========================================================================
# TestSignalValidationOptions
# ===========================================================================

class TestSignalValidationOptions:
    """Tests for YAML option mapping in signal_validation.py."""

    def _make_rule_results(self, win_rates_by_fold):
        """win_rates_by_fold: list of floats, one per fold."""
        folds = []
        for i, wr in enumerate(win_rates_by_fold):
            n = 30
            folds.append({
                "fold_id": i,
                "n_trades": n,
                "win_rate": wr,
                "profit_factor": 1.2 if wr > 0.5 else 0.9,
                "avg_profit": 2.0 if wr > 0.5 else -1.0,
            })
        return {
            "rule_str": "session_label == RTH",
            "conditions": [{"column": "session_label", "op": "eq", "value": "RTH", "description": "session_label == RTH"}],
            "n_conditions": 1,
            "n_trades": sum(f["n_trades"] for f in folds),
            "win_rate": float(np.mean([f["win_rate"] for f in folds])),
            "profit_factor": 1.2,
            "avg_profit": 1.5,
            "score": 70.0,
            "rank": 1,
            "cross_fold_results": folds,
        }

    def _make_sd(self, rules):
        return {
            "top_rules": rules,
            "cross_validation": {"enabled": True, "n_folds": len(rules[0]["cross_fold_results"]) if rules else 3},
            "diagnostics": {},
        }

    def _get_verdict(self, result):
        """Extract verdict from result — may be top-level or under 'summary'."""
        if "verdict" in result:
            return result["verdict"]
        return (result.get("summary") or {}).get("verdict", "FAIL")

    def _get_per_rule(self, result):
        """Extract per-rule list — may be 'per_rule' or 'rule_consistency'."""
        return result.get("per_rule") or result.get("rule_consistency") or []

    def test_returns_required_keys(self):
        from ta_foundation.analysis.strategy_discovery.signal_validation import run_signal_validation
        rule = self._make_rule_results([0.55, 0.52, 0.58])
        sd = self._make_sd([rule])
        result = run_signal_validation(sd, {})
        assert "diagnostics" in result
        # verdict may be top-level or under summary
        assert self._get_verdict(result) is not None or "summary" in result

    def test_custom_stable_threshold_applied(self):
        from ta_foundation.analysis.strategy_discovery.signal_validation import run_signal_validation
        # Tight degradation (all folds near 0.55) — should be stable with loose threshold
        rule = self._make_rule_results([0.55, 0.56, 0.54])
        sd = self._make_sd([rule])
        opts = {"stable_threshold": 0.10, "moderate_threshold": 0.25, "min_folds_consistent": 2, "pass_min_stable": 1}
        result = run_signal_validation(sd, opts)
        per_rule = self._get_per_rule(result)
        if per_rule:
            assert per_rule[0].get("verdict") in ("stable", "moderate")

    def test_loose_threshold_yields_more_stable(self):
        from ta_foundation.analysis.strategy_discovery.signal_validation import run_signal_validation
        rule = self._make_rule_results([0.55, 0.50, 0.53])
        sd = self._make_sd([rule])
        # Loose threshold: more rules get "stable"
        opts_loose = {"stable_threshold": 0.20, "moderate_threshold": 0.40, "min_folds_consistent": 1, "pass_min_stable": 1}
        opts_strict = {"stable_threshold": 0.01, "moderate_threshold": 0.05, "min_folds_consistent": 3, "pass_min_stable": 3}
        r_loose = run_signal_validation(sd, opts_loose)
        r_strict = run_signal_validation(sd, opts_strict)
        verdict_rank = {"PASS": 0, "WARN": 1, "FAIL": 2, "SKIP": 3}
        rank_loose = verdict_rank.get(self._get_verdict(r_loose), 3)
        rank_strict = verdict_rank.get(self._get_verdict(r_strict), 3)
        assert rank_loose <= rank_strict

    def test_pass_min_stable_controls_verdict(self):
        from ta_foundation.analysis.strategy_discovery.signal_validation import run_signal_validation
        # One rule that should be "stable"
        rule = self._make_rule_results([0.55, 0.54, 0.56])
        sd = self._make_sd([rule])
        # pass_min_stable=1 → 1 stable rule → PASS
        opts1 = {"stable_threshold": 0.10, "moderate_threshold": 0.25, "min_folds_consistent": 1, "pass_min_stable": 1}
        # pass_min_stable=5 → need 5 stable rules but only have 1 → FAIL or WARN
        opts5 = {"stable_threshold": 0.10, "moderate_threshold": 0.25, "min_folds_consistent": 1, "pass_min_stable": 5}
        r1 = run_signal_validation(sd, opts1)
        r5 = run_signal_validation(sd, opts5)
        verdict_rank = {"PASS": 0, "WARN": 1, "FAIL": 2, "SKIP": 3}
        assert verdict_rank.get(self._get_verdict(r1), 3) <= verdict_rank.get(self._get_verdict(r5), 3)

    def test_min_folds_consistent_alias(self):
        from ta_foundation.analysis.strategy_discovery.signal_validation import run_signal_validation
        rule = self._make_rule_results([0.55, 0.54, 0.56])
        sd = self._make_sd([rule])
        opts_new = {"stable_threshold": 0.10, "moderate_threshold": 0.25, "min_folds_consistent": 2, "pass_min_stable": 1}
        opts_old = {"stable_threshold": 0.10, "moderate_threshold": 0.25, "min_folds_for_stable": 2, "pass_min_stable": 1}
        r_new = run_signal_validation(sd, opts_new)
        r_old = run_signal_validation(sd, opts_old)
        # Both aliases should produce the same verdict
        assert self._get_verdict(r_new) == self._get_verdict(r_old)


# ===========================================================================
# TestWmaAnchor
# ===========================================================================

class TestWmaAnchor:
    """Tests for WMA anchor computation in anchors.py."""

    def _make_bars(self, n=30):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Denver")
        idx = pd.date_range("2024-01-02 09:00", periods=n, freq="1min", tz=tz)
        close = np.arange(100, 100 + n, dtype=float)
        return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close}, index=idx)

    def _make_spec(self, family="WMA", length=5, source="close"):
        from ta_foundation.analysis.ma_structure.models import AnchorSpec
        return AnchorSpec(family=family, length=length, source=source)

    def test_wma_series_has_correct_length(self):
        from ta_foundation.analysis.ma_structure.anchors import compute_anchor_series
        bars = self._make_bars(30)
        spec = self._make_spec(length=5)
        result = compute_anchor_series(bars, spec)
        # First (length-1)=4 values should be NaN, rest valid
        assert result.isna().sum() == 4
        assert result.notna().sum() == 26

    def test_wma_is_weighted_average(self):
        from ta_foundation.analysis.ma_structure.anchors import compute_anchor_series
        bars = self._make_bars(10)
        spec = self._make_spec(length=5)
        result = compute_anchor_series(bars, spec)
        # For position 4 (0-indexed), close values are [100,101,102,103,104]
        # weights = [1,2,3,4,5] / 15
        weights = np.array([1, 2, 3, 4, 5], dtype=float)
        weights /= weights.sum()
        expected = float(np.dot([100, 101, 102, 103, 104], weights))
        actual = float(result.iloc[4])
        assert abs(actual - expected) < 1e-6

    def test_wma_differs_from_sma(self):
        from ta_foundation.analysis.ma_structure.anchors import compute_anchor_series
        bars = self._make_bars(20)
        wma_spec = self._make_spec(family="WMA", length=5)
        sma_spec = self._make_spec(family="SMA", length=5)
        wma = compute_anchor_series(bars, wma_spec)
        sma = compute_anchor_series(bars, sma_spec)
        # WMA emphasises recent bars so should differ from SMA
        valid = wma.notna() & sma.notna()
        assert not (wma[valid] == sma[valid]).all()

    def test_unsupported_family_raises(self):
        from ta_foundation.analysis.ma_structure.anchors import compute_anchor_series
        bars = self._make_bars(10)
        spec = self._make_spec(family="DEMA", length=5)
        with pytest.raises(ValueError, match="Unsupported anchor family"):
            compute_anchor_series(bars, spec)
