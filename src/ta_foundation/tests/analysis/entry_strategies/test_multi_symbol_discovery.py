"""
Tests for Multi-Symbol Discovery (Gap 5)

Tests cross-symbol validation, leaderboarding, and robustness scoring.
"""

import pytest
import pandas as pd
import numpy as np

from ta_foundation.analysis.entry_strategies.multi_symbol_discovery import (
    CrossSymbolValidator,
    CrossSymbolCandidate,
    MultiSymbolRunner,
    CrossValidationMetrics,
)


class TestCrossSymbolValidator:
    """Test cross-symbol validation."""

    def test_validate_robust_strategy(self):
        """Test validation of strategy with low degradation."""
        validator = CrossSymbolValidator(
            max_degradation_pct=50.0,
            min_trades_validation=10,
        )

        # Create synthetic trades (good performance)
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": 120},
            {"profit_ticks": -50},
            {"profit_ticks": 80},
            {"profit_ticks": -40},
            {"profit_ticks": 150},
            {"profit_ticks": -30},
            {"profit_ticks": 90},
            {"profit_ticks": 110},
            {"profit_ticks": -45},
            {"profit_ticks": 70},
            {"profit_ticks": 85},
        ]

        result = validator.validate_on_symbol(
            trades,
            validation_symbol="ES",
            discovery_symbol="NQ",
        )

        assert result.symbol == "ES"
        assert result.n_trades == 12
        assert result.profit_factor > 0
        assert result.win_rate > 0

    def test_validate_weak_strategy(self):
        """Test validation of strategy with high degradation."""
        validator = CrossSymbolValidator(
            max_degradation_pct=30.0,
            min_trades_validation=10,
        )

        # Create synthetic trades (poor performance)
        trades = [
            {"profit_ticks": 30},
            {"profit_ticks": -100},
            {"profit_ticks": -80},
            {"profit_ticks": 20},
            {"profit_ticks": -90},
            {"profit_ticks": 40},
            {"profit_ticks": -70},
        ]

        result = validator.validate_on_symbol(
            trades,
            validation_symbol="ES",
            discovery_symbol="NQ",
        )

        assert result.symbol == "ES"
        assert result.n_trades == 7
        assert result.profit_factor < 1.0

    def test_validate_insufficient_trades(self):
        """Test validation fails with too few trades."""
        validator = CrossSymbolValidator(
            max_degradation_pct=50.0,
            min_trades_validation=20,
        )

        trades = [{"profit_ticks": 50}, {"profit_ticks": -30}]

        result = validator.validate_on_symbol(
            trades,
            validation_symbol="ES",
            discovery_symbol="NQ",
        )

        assert result.n_trades == 2
        assert result.is_robust is False

    def test_validate_empty_trades(self):
        """Test validation with empty trades."""
        validator = CrossSymbolValidator()

        result = validator.validate_on_symbol([], "ES", "NQ")

        assert result.n_trades == 0
        assert result.profit_factor == 0.0
        assert result.is_robust is False

    def test_sharpe_calculation(self):
        """Test Sharpe ratio calculation."""
        validator = CrossSymbolValidator()

        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": 110},
            {"profit_ticks": 90},
            {"profit_ticks": 95},
            {"profit_ticks": 105},
        ]

        result = validator.validate_on_symbol(trades, "ES", "NQ")

        # Sharpe should be positive for consistent wins
        assert result.sharpe_ratio > 0

    def test_win_rate_calculation(self):
        """Test win rate calculation."""
        validator = CrossSymbolValidator()

        trades = [
            {"profit_ticks": 100},   # Win
            {"profit_ticks": -50},   # Loss
            {"profit_ticks": 80},    # Win
            {"profit_ticks": -40},   # Loss
            {"profit_ticks": 120},   # Win
        ]

        result = validator.validate_on_symbol(trades, "ES", "NQ")

        # 3 wins out of 5 = 60%
        assert result.win_rate == pytest.approx(0.6, rel=0.01)


class TestCrossSymbolCandidate:
    """Test CrossSymbolCandidate data structure."""

    def test_candidate_creation(self):
        """Test creating a candidate."""
        candidate = CrossSymbolCandidate(
            strategy_id="NQ_001",
            entry_family="candle",
            entry_signal="large_body",
            params={"body_mult": 1.5},
            discovery_pf=1.45,
            discovery_sharpe=1.2,
            discovery_n_trades=150,
        )

        assert candidate.strategy_id == "NQ_001"
        assert candidate.entry_family == "candle"
        assert candidate.discovery_pf == 1.45

    def test_candidate_to_dict(self):
        """Test converting candidate to dict."""
        candidate = CrossSymbolCandidate(
            strategy_id="NQ_001",
            entry_family="candle",
            entry_signal="pin_bar",
            params={},
            discovery_pf=1.3,
            discovery_sharpe=0.9,
            discovery_n_trades=100,
        )

        candidate_dict = candidate.to_dict()

        assert isinstance(candidate_dict, dict)
        assert candidate_dict["strategy_id"] == "NQ_001"
        assert candidate_dict["discovery_pf"] == 1.3

    def test_candidate_with_validation(self):
        """Test candidate with validation results."""
        candidate = CrossSymbolCandidate(
            strategy_id="NQ_001",
            entry_family="ma",
            entry_signal="ma_cross",
            params={},
            discovery_pf=1.4,
            discovery_sharpe=1.1,
            discovery_n_trades=200,
        )

        # Add validation result
        val_metric = CrossValidationMetrics(
            symbol="ES",
            n_trades=180,
            profit_factor=1.3,
            sharpe_ratio=1.0,
            win_rate=0.54,
            avg_win_loss_ratio=2.0,
            max_drawdown_pct=15.0,
            pf_degradation_pct=7.1,
            is_robust=True,
        )

        candidate.validation_results["ES"] = val_metric
        candidate.is_cross_validated = True

        assert "ES" in candidate.validation_results
        assert candidate.is_cross_validated


class TestMultiSymbolRunner:
    """Test MultiSymbolRunner orchestration."""

    def test_runner_creation(self):
        """Test creating a MultiSymbolRunner."""
        runner = MultiSymbolRunner(
            primary_symbol="NQ",
            validation_symbols=["ES", "RTY"],
        )

        assert runner.primary_symbol == "NQ"
        assert "ES" in runner.validation_symbols

    def test_extract_top_candidates(self):
        """Test extracting top N candidates."""
        runner = MultiSymbolRunner("NQ", ["ES"])

        # Create synthetic discovery results
        discovery_results = {
            "sweep_results": [
                {
                    "entry_family": "candle",
                    "entry_signal": "large_body",
                    "params": {"mult": 1.5},
                    "profit_factor": 1.45,
                    "sharpe_ratio": 1.2,
                    "n_trades": 150,
                },
                {
                    "entry_family": "ma",
                    "entry_signal": "ma_cross",
                    "params": {"fast": 9, "slow": 20},
                    "profit_factor": 1.32,
                    "sharpe_ratio": 0.95,
                    "n_trades": 120,
                },
                {
                    "entry_family": "orb",
                    "entry_signal": "orb_break",
                    "params": {"period": 60},
                    "profit_factor": 1.15,
                    "sharpe_ratio": 0.7,
                    "n_trades": 80,
                },
            ]
        }

        candidates = runner._extract_top_candidates(discovery_results, top_n=2)

        assert len(candidates) == 2
        assert candidates[0].entry_family == "candle"
        assert candidates[1].entry_family == "ma"

    def test_calculate_cross_symbol_score(self):
        """Test cross-symbol score calculation."""
        runner = MultiSymbolRunner("NQ", ["ES"])

        candidate = CrossSymbolCandidate(
            strategy_id="NQ_001",
            entry_family="candle",
            entry_signal="large_body",
            params={},
            discovery_pf=1.40,
            discovery_sharpe=1.2,
            discovery_n_trades=150,
        )

        # Add validation results
        candidate.validation_results["ES"] = CrossValidationMetrics(
            symbol="ES",
            n_trades=140,
            profit_factor=1.30,
            sharpe_ratio=1.1,
            win_rate=0.54,
            avg_win_loss_ratio=2.0,
            max_drawdown_pct=12.0,
            pf_degradation_pct=7.1,
            is_robust=True,
        )

        score = runner._calculate_cross_symbol_score(candidate)

        # Score should be: 0.7 * 1.4 + 0.3 * 1.3 = 0.98 + 0.39 = 1.37
        # Plus 10% bonus for being robust
        expected = (0.7 * 1.40 + 0.3 * 1.30) * 1.1
        assert score == pytest.approx(expected, rel=0.01)

    def test_rate_robustness(self):
        """Test robustness rating."""
        runner = MultiSymbolRunner("NQ", ["ES", "RTY"])

        # Excellent: all symbols robust
        candidate_excellent = CrossSymbolCandidate(
            strategy_id="NQ_001",
            entry_family="candle",
            entry_signal="large_body",
            params={},
            discovery_pf=1.4,
            discovery_sharpe=1.2,
            discovery_n_trades=150,
        )
        candidate_excellent.validation_results["ES"] = CrossValidationMetrics(
            symbol="ES", n_trades=140, profit_factor=1.3, sharpe_ratio=1.1,
            win_rate=0.54, avg_win_loss_ratio=2.0, max_drawdown_pct=12.0,
            pf_degradation_pct=7.0, is_robust=True,
        )
        candidate_excellent.validation_results["RTY"] = CrossValidationMetrics(
            symbol="RTY", n_trades=130, profit_factor=1.28, sharpe_ratio=1.05,
            win_rate=0.53, avg_win_loss_ratio=1.95, max_drawdown_pct=13.0,
            pf_degradation_pct=8.5, is_robust=True,
        )

        rating = runner._rate_robustness(candidate_excellent)
        assert rating == "excellent"

        # Fair: only 1 symbol robust
        candidate_fair = CrossSymbolCandidate(
            strategy_id="NQ_002",
            entry_family="ma",
            entry_signal="ma_cross",
            params={},
            discovery_pf=1.3,
            discovery_sharpe=1.0,
            discovery_n_trades=120,
        )
        candidate_fair.validation_results["ES"] = CrossValidationMetrics(
            symbol="ES", n_trades=140, profit_factor=1.25, sharpe_ratio=1.0,
            win_rate=0.54, avg_win_loss_ratio=2.0, max_drawdown_pct=12.0,
            pf_degradation_pct=7.0, is_robust=True,
        )
        candidate_fair.validation_results["RTY"] = CrossValidationMetrics(
            symbol="RTY", n_trades=80, profit_factor=0.95, sharpe_ratio=0.3,
            win_rate=0.48, avg_win_loss_ratio=1.1, max_drawdown_pct=25.0,
            pf_degradation_pct=45.0, is_robust=False,
        )

        rating = runner._rate_robustness(candidate_fair)
        assert rating == "fair"

    def test_get_cross_validated_leaderboard(self):
        """Test filtering leaderboard by robustness."""
        runner = MultiSymbolRunner("NQ", ["ES"])

        # Create candidates with different robustness ratings
        c1 = CrossSymbolCandidate(
            strategy_id="NQ_001",
            entry_family="candle",
            entry_signal="large_body",
            params={},
            discovery_pf=1.4,
            discovery_sharpe=1.2,
            discovery_n_trades=150,
        )
        c1.robustness_rating = "excellent"
        c1.cross_symbol_score = 1.4

        c2 = CrossSymbolCandidate(
            strategy_id="NQ_002",
            entry_family="ma",
            entry_signal="ma_cross",
            params={},
            discovery_pf=1.3,
            discovery_sharpe=1.0,
            discovery_n_trades=120,
        )
        c2.robustness_rating = "fair"
        c2.cross_symbol_score = 1.2

        runner.candidates = [c1, c2]

        # Filter by "good" or better
        leaderboard = runner.get_cross_validated_leaderboard(min_robustness="good")

        assert len(leaderboard) == 1
        assert leaderboard[0].strategy_id == "NQ_001"


class TestIntegration:
    """Integration tests for multi-symbol discovery."""

    def test_full_multi_symbol_workflow(self):
        """Test complete multi-symbol discovery workflow."""
        runner = MultiSymbolRunner(
            primary_symbol="NQ",
            validation_symbols=["ES"],
        )

        # Synthetic discovery results
        discovery_results = {
            "sweep_results": [
                {
                    "entry_family": "candle",
                    "entry_signal": "large_body",
                    "params": {"mult": 1.5},
                    "profit_factor": 1.45,
                    "sharpe_ratio": 1.2,
                    "n_trades": 150,
                },
                {
                    "entry_family": "ma",
                    "entry_signal": "ma_cross",
                    "params": {"fast": 9},
                    "profit_factor": 1.32,
                    "sharpe_ratio": 0.95,
                    "n_trades": 120,
                },
            ]
        }

        # Synthetic validation results
        validation_results = {
            "ES": {
                "trades": [
                    {"profit_ticks": 100},
                    {"profit_ticks": -50},
                    {"profit_ticks": 110},
                    {"profit_ticks": -40},
                    {"profit_ticks": 120},
                    {"profit_ticks": -45},
                ] * 5,  # 30 trades
            }
        }

        # Run multi-symbol discovery
        candidates = runner.run_multi_symbol_discovery(
            discovery_results,
            validation_results,
            top_n=2,
        )

        assert len(candidates) == 2
        assert candidates[0].is_cross_validated
        assert candidates[0].cross_symbol_score > 0

    def test_leaderboard_sorting(self):
        """Test that candidates are sorted correctly."""
        runner = MultiSymbolRunner("NQ", ["ES"])

        c1 = CrossSymbolCandidate(
            strategy_id="NQ_001",
            entry_family="candle",
            entry_signal="large_body",
            params={},
            discovery_pf=1.4,
            discovery_sharpe=1.2,
            discovery_n_trades=150,
        )
        c1.cross_symbol_score = 1.4

        c2 = CrossSymbolCandidate(
            strategy_id="NQ_002",
            entry_family="ma",
            entry_signal="ma_cross",
            params={},
            discovery_pf=1.3,
            discovery_sharpe=1.0,
            discovery_n_trades=120,
        )
        c2.cross_symbol_score = 1.2

        runner.candidates = [c2, c1]  # Out of order

        leaderboard = runner.get_cross_validated_leaderboard(min_robustness="poor")

        # Should be sorted by score
        assert leaderboard[0].cross_symbol_score >= leaderboard[1].cross_symbol_score
