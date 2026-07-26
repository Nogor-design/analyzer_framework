"""
Tests for dynamic parameter discovery module.
"""

import pytest
from ta_foundation.analysis.entry_strategies.dynamic_params import (
    rank_results,
    identify_top_by_family,
    get_param_ranges_in_results,
    identify_varying_params,
    expand_numeric_list,
    recommend_param_expansion,
)


def _make_result(
    signal_id: str,
    pf: float = 1.2,
    n_trades: int = 50,
    params: dict = None,
) -> dict:
    """Helper to create a mock result dict."""
    return {
        "signal_id": signal_id,
        "n_trades": n_trades,
        "metrics": {"profit_factor": pf, "sharpe": 1.0},
        "params": params or {"tp_ticks": 20, "sl_ticks": 10},
    }


class TestRankResults:
    def test_rank_by_pf(self):
        results = [
            _make_result("candle", pf=1.1),
            _make_result("candle", pf=1.5),
            _make_result("candle", pf=1.3),
        ]
        ranked = rank_results(results, metric="profit_factor")
        assert len(ranked) == 3
        assert ranked[0]["metrics"]["profit_factor"] == 1.5
        assert ranked[-1]["metrics"]["profit_factor"] == 1.1

    def test_filter_by_min_trades(self):
        results = [
            _make_result("candle", pf=1.5, n_trades=50),
            _make_result("candle", pf=1.4, n_trades=10),  # too few
        ]
        ranked = rank_results(results, min_trades=20)
        assert len(ranked) == 1
        assert ranked[0]["metrics"]["profit_factor"] == 1.5


class TestIdentifyTopByFamily:
    def test_group_by_family(self):
        results = [
            _make_result("candle", pf=1.5),
            _make_result("candle", pf=1.4),
            _make_result("ma", pf=1.3),
            _make_result("ma", pf=1.2),
        ]
        by_fam = identify_top_by_family(results, top_n=1)
        assert len(by_fam) == 2
        assert by_fam["candle"][0]["metrics"]["profit_factor"] == 1.5
        assert by_fam["ma"][0]["metrics"]["profit_factor"] == 1.3

    def test_limit_per_family(self):
        results = [
            _make_result("candle", pf=1.5),
            _make_result("candle", pf=1.4),
            _make_result("candle", pf=1.3),
        ]
        by_fam = identify_top_by_family(results, top_n=2)
        assert len(by_fam["candle"]) == 2


class TestParamRanges:
    def test_extract_ranges(self):
        results = [
            _make_result("candle", params={"tp_ticks": 20, "sl_ticks": 10}),
            _make_result("candle", params={"tp_ticks": 40, "sl_ticks": 10}),
            _make_result("candle", params={"tp_ticks": 60, "sl_ticks": 20}),
        ]
        ranges = get_param_ranges_in_results(results)
        assert ranges["tp_ticks"] == {20, 40, 60}
        assert ranges["sl_ticks"] == {10, 20}

    def test_identify_varying(self):
        results = [
            _make_result("candle", params={"tp_ticks": 20, "const": 1}),
            _make_result("candle", params={"tp_ticks": 40, "const": 1}),
        ]
        varying = identify_varying_params(results)
        assert "tp_ticks" in varying
        assert "const" not in varying  # single value
        assert varying["tp_ticks"] == (20, 40)


class TestExpandNumericList:
    def test_expand_simple(self):
        vals = [20, 40, 60]
        expanded = expand_numeric_list(vals, factor=2.0)
        assert min(expanded) < 20
        assert max(expanded) > 60
        assert len(expanded) > len(vals)

    def test_respect_bounds(self):
        vals = [20, 40, 60]
        expanded = expand_numeric_list(vals, min_val=15, max_val=100, factor=2.0)
        assert min(expanded) >= 15
        assert max(expanded) <= 100

    def test_single_value(self):
        vals = [30]
        expanded = expand_numeric_list(vals, factor=1.5)
        assert len(expanded) > 1
        assert 30 in expanded

    def test_preserve_integers(self):
        vals = [10, 20, 30]
        expanded = expand_numeric_list(vals, factor=1.5)
        assert all(float(v).is_integer() for v in expanded)


class TestRecommendParamExpansion:
    def test_identify_winners(self):
        results = [
            _make_result("candle", pf=1.5, params={"tp_ticks": 20, "sl_ticks": 10}),
            _make_result("candle", pf=1.4, params={"tp_ticks": 40, "sl_ticks": 10}),
            _make_result("ma", pf=0.9, params={"fast": 9}),  # below threshold
        ]
        rec = recommend_param_expansion(
            results,
            min_pf_threshold=1.2,
        )
        recs = rec["recommendations"]
        assert "candle" in recs
        assert "ma" not in recs
        assert "tp_ticks" in recs["candle"]["expand"]

    def test_no_winners(self):
        results = [
            _make_result("candle", pf=1.0),
            _make_result("ma", pf=0.9),
        ]
        rec = recommend_param_expansion(results, min_pf_threshold=1.2)
        assert len(rec["recommendations"]) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
