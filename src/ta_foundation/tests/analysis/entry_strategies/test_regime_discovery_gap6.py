"""
Tests for Gap 6: Regime-Aware Discovery (Phases 1-2)

Tests regime discovery configuration and regime-based candidate analysis.
"""

import pytest
import pandas as pd
import numpy as np

from ta_foundation.analysis.entry_strategies.regime_discovery_config import (
    RegimeDiscoveryConfig,
    RegimeParamSet,
    create_default_regime_discovery_config,
)
from ta_foundation.analysis.entry_strategies.regime_candidate_analyzer import (
    RegimeCandidateAnalyzer,
)


class TestRegimeParamSet:
    """Test RegimeParamSet configuration class."""

    def test_regime_param_set_creation(self):
        """Test creating a RegimeParamSet."""
        param_set = RegimeParamSet(
            regime_id="trend_up",
            enabled_families=["candle", "ma"],
            param_overrides={"candle": {"patterns": ["large_body"]}},
        )

        assert param_set.regime_id == "trend_up"
        assert "candle" in param_set.enabled_families
        assert param_set.min_trades_per_regime == 20

    def test_apply_to_family_config_enabled_families(self):
        """Test applying enabled families filter."""
        param_set = RegimeParamSet(
            regime_id="trend_up",
            enabled_families=["candle", "ma"],
        )

        base_config = {"enabled": False, "patterns": ["a", "b"]}
        result = param_set.apply_to_family_config("candle", base_config)

        assert result["enabled"] is True

    def test_apply_to_family_config_disabled_families(self):
        """Test applying disabled families filter."""
        param_set = RegimeParamSet(
            regime_id="trend_up",
            disabled_families=["level"],
        )

        base_config = {"enabled": True}
        result = param_set.apply_to_family_config("level", base_config)

        assert result["enabled"] is False

    def test_apply_param_overrides(self):
        """Test applying parameter overrides."""
        param_set = RegimeParamSet(
            regime_id="trend_up",
            param_overrides={
                "candle": {
                    "patterns": ["large_body", "pin_bar"],
                },
            },
        )

        base_config = {"patterns": ["a", "b", "c"]}
        result = param_set.apply_to_family_config("candle", base_config)

        assert result["patterns"] == ["large_body", "pin_bar"]

    def test_apply_pattern_filtering(self):
        """Test enabling specific patterns."""
        param_set = RegimeParamSet(
            regime_id="trend_up",
            enabled_patterns={
                "candle": ["large_body", "pin_bar"],
            },
        )

        base_config = {"patterns": ["large_body", "pin_bar", "engulfing"]}
        result = param_set.apply_to_family_config("candle", base_config)

        # Should keep only enabled patterns
        assert set(result["patterns"]) == {"large_body", "pin_bar"}


class TestRegimeDiscoveryConfig:
    """Test RegimeDiscoveryConfig orchestration."""

    def test_config_creation(self):
        """Test creating a RegimeDiscoveryConfig."""
        config = RegimeDiscoveryConfig(
            enabled=True,
            regime_types=["trend_up", "trend_down", "range"],
        )

        assert config.enabled is True
        assert len(config.regime_types) == 3
        assert len(config.regime_params) == 3

    def test_get_regime_config(self):
        """Test retrieving regime-specific config."""
        config = RegimeDiscoveryConfig()
        regime_cfg = config.get_regime_config("trend_up")

        assert regime_cfg.regime_id == "trend_up"
        assert regime_cfg.min_trades_per_regime == 20

    def test_get_regime_config_nonexistent(self):
        """Test retrieving config for non-registered regime."""
        config = RegimeDiscoveryConfig()
        regime_cfg = config.get_regime_config("unknown_regime")

        # Should return default
        assert regime_cfg.regime_id == "unknown_regime"
        assert regime_cfg.min_trades_per_regime == 20

    def test_set_regime_config(self):
        """Test setting regime-specific config."""
        config = RegimeDiscoveryConfig()
        new_param_set = RegimeParamSet(
            regime_id="trend_up",
            enabled_families=["candle"],
        )

        config.set_regime_config("trend_up", new_param_set)
        retrieved = config.get_regime_config("trend_up")

        assert retrieved.enabled_families == ["candle"]

    def test_apply_regime_to_discovery_config(self):
        """Test applying regime overrides to discovery config."""
        config = RegimeDiscoveryConfig(enabled=True)

        # Set up trend_up config
        trend_up = RegimeParamSet(
            regime_id="trend_up",
            disabled_families=["level"],
        )
        config.set_regime_config("trend_up", trend_up)

        base_config = {
            "discovery_families": {
                "candle": {"enabled": True},
                "level": {"enabled": True},
            }
        }

        result = config.apply_regime_to_discovery_config(base_config, "trend_up")

        # Level should be disabled
        assert result["discovery_families"]["level"]["enabled"] is False
        # Candle should remain enabled
        assert result["discovery_families"]["candle"]["enabled"] is True

    def test_apply_regime_disabled_returns_base_config(self):
        """Test that disabled regime discovery returns base config."""
        config = RegimeDiscoveryConfig(enabled=False)

        base_config = {
            "discovery_families": {
                "candle": {"enabled": True},
            }
        }

        result = config.apply_regime_to_discovery_config(base_config, "trend_up")

        assert result == base_config

    def test_to_dict_and_from_dict(self):
        """Test serialization and deserialization."""
        config = RegimeDiscoveryConfig(
            enabled=True,
            regime_types=["trend_up", "range"],
        )

        data = config.to_dict()
        restored = RegimeDiscoveryConfig.from_dict(data)

        assert restored.enabled == config.enabled
        assert restored.regime_types == config.regime_types
        assert len(restored.regime_params) == len(config.regime_params)

    def test_default_regime_config(self):
        """Test default regime discovery configuration."""
        config = create_default_regime_discovery_config()

        assert config.enabled is True
        assert len(config.regime_types) >= 3

        # Check trend_up config
        trend_up = config.get_regime_config("trend_up")
        assert trend_up.enabled_families is not None
        assert "candle" in trend_up.enabled_families

        # Check range config
        range_cfg = config.get_regime_config("range")
        assert "level" in range_cfg.enabled_families


class TestRegimeCandidateAnalyzer:
    """Test regime-based candidate analysis."""

    def test_analyzer_creation(self):
        """Test creating a RegimeCandidateAnalyzer."""
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": -50},
            {"profit_ticks": 80},
        ]
        regimes = ["trend_up", "trend_up", "range"]

        analyzer = RegimeCandidateAnalyzer(trades, regimes)

        assert analyzer.results == trades
        assert analyzer.regime_labels == regimes

    def test_analyzer_creation_mismatched_lengths(self):
        """Test error when results and regimes have different lengths."""
        trades = [{"profit_ticks": 100}]
        regimes = ["trend_up", "trend_down"]

        with pytest.raises(ValueError, match="must have same length"):
            RegimeCandidateAnalyzer(trades, regimes)

    def test_partition_by_regime(self):
        """Test partitioning results by regime."""
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": -50},
            {"profit_ticks": 80},
            {"profit_ticks": -30},
        ]
        regimes = ["trend_up", "trend_up", "range", "range"]

        analyzer = RegimeCandidateAnalyzer(trades, regimes)

        assert len(analyzer.results_by_regime["trend_up"]) == 2
        assert len(analyzer.results_by_regime["range"]) == 2

    def test_get_regime_performance_good_regime(self):
        """Test performance metrics for a profitable regime."""
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": 120},
            {"profit_ticks": -50},
            {"profit_ticks": 80},
        ]
        regimes = ["trend_up"] * 4

        analyzer = RegimeCandidateAnalyzer(trades, regimes)
        perf = analyzer.get_regime_performance("trend_up")

        assert perf["n_trades"] == 4
        assert perf["profit_factor"] > 1.0
        assert perf["win_rate"] > 0

    def test_get_regime_performance_empty_regime(self):
        """Test performance metrics for empty regime."""
        trades = [{"profit_ticks": 100}]
        regimes = ["trend_up"]

        analyzer = RegimeCandidateAnalyzer(trades, regimes)
        perf = analyzer.get_regime_performance("range")

        assert perf["n_trades"] == 0
        assert perf["profit_factor"] == 0.0

    def test_get_multi_regime_performance(self):
        """Test performance across all regimes."""
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": -50},
            {"profit_ticks": 80},
            {"profit_ticks": -30},
        ]
        regimes = ["trend_up", "trend_up", "range", "range"]

        analyzer = RegimeCandidateAnalyzer(trades, regimes)
        perf = analyzer.get_multi_regime_performance()

        assert "trend_up" in perf
        assert "range" in perf

    def test_get_multi_regime_robustness_excellent(self):
        """Test robustness rating for excellent multi-regime performance."""
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": 120},
            {"profit_ticks": -50},
        ] * 20  # 60 trades total

        regimes = []
        for regime in ["trend_up", "trend_down", "range"]:
            regimes.extend([regime] * 20)

        analyzer = RegimeCandidateAnalyzer(trades, regimes)
        robustness = analyzer.get_multi_regime_robustness(min_trades_per_regime=15)

        assert robustness == "excellent"

    def test_get_multi_regime_robustness_good(self):
        """Test robustness rating for good multi-regime performance."""
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": 120},
            {"profit_ticks": -50},
        ] * 15  # 45 trades

        regimes = []
        for regime in ["trend_up", "trend_down", "range"]:
            regimes.extend([regime] * 15)

        # Make third regime unprofitable
        trades = trades[:30] + [
            {"profit_ticks": -100},
            {"profit_ticks": -120},
            {"profit_ticks": 50},
        ] * 5

        analyzer = RegimeCandidateAnalyzer(trades, regimes)
        robustness = analyzer.get_multi_regime_robustness(
            min_trades_per_regime=10,
            min_profitable_regimes=1,
        )

        # Should be fair or good depending on how many regimes are profitable
        assert robustness in ["good", "fair"]

    def test_get_multi_regime_robustness_poor(self):
        """Test robustness rating for poor multi-regime performance."""
        trades = [{"profit_ticks": -100}] * 5
        regimes = ["trend_up"] * 5

        analyzer = RegimeCandidateAnalyzer(trades, regimes)
        robustness = analyzer.get_multi_regime_robustness(min_trades_per_regime=3)

        assert robustness == "poor"

    def test_calculate_regime_degradation(self):
        """Test degradation calculation from best to other regimes."""
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": 120},
            {"profit_ticks": -40},
        ] * 10 + [
            {"profit_ticks": 50},
            {"profit_ticks": 60},
            {"profit_ticks": -30},
        ] * 10

        regimes = ["trend_up"] * 30 + ["range"] * 30

        analyzer = RegimeCandidateAnalyzer(trades, regimes)
        degradations = analyzer.calculate_regime_degradation("trend_up")

        # All degradations should be >= 0
        assert all(d >= 0 for d in degradations.values())

    def test_get_regime_summary(self):
        """Test comprehensive regime summary."""
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": 120},
            {"profit_ticks": -50},
        ] * 10

        regimes = ["trend_up"] * 30

        analyzer = RegimeCandidateAnalyzer(trades, regimes)
        summary = analyzer.get_regime_summary(min_trades_per_regime=20)

        assert "robustness" in summary
        assert "n_regimes_tested" in summary
        assert "best_regime" in summary
        assert "recommendations" in summary


class TestIntegrationRegimeDiscovery:
    """Integration tests for regime-aware discovery."""

    def test_config_to_analyzer_workflow(self):
        """Test workflow from config creation through analysis."""
        # Create discovery config
        config = create_default_regime_discovery_config()

        # Simulate discovery results in trend_up regime
        trades = [
            {"profit_ticks": 100},
            {"profit_ticks": 120},
            {"profit_ticks": -50},
        ] * 15

        regimes = ["trend_up"] * 45

        # Create analyzer
        analyzer = RegimeCandidateAnalyzer(trades, regimes)

        # Get summary
        summary = analyzer.get_regime_summary(min_trades_per_regime=30)

        assert summary["n_regimes_valid"] == 1
        assert summary["best_regime"] == "trend_up"

    def test_multi_regime_discovery_workflow(self):
        """Test discovering across multiple regimes."""
        # Simulate discovery on 3 regimes
        trades_trend_up = [
            {"profit_ticks": 100},
            {"profit_ticks": 120},
            {"profit_ticks": -50},
        ] * 10
        trades_trend_down = [
            {"profit_ticks": 90},
            {"profit_ticks": 110},
            {"profit_ticks": -60},
        ] * 10
        trades_range = [
            {"profit_ticks": 50},
            {"profit_ticks": -40},
        ] * 15

        all_trades = trades_trend_up + trades_trend_down + trades_range
        all_regimes = (
            ["trend_up"] * 30 +
            ["trend_down"] * 30 +
            ["range"] * 30
        )

        analyzer = RegimeCandidateAnalyzer(all_trades, all_regimes)

        # Should show multi-regime robustness
        robustness = analyzer.get_multi_regime_robustness(min_trades_per_regime=20)
        assert robustness in ["excellent", "good", "fair"]

        # Summary should reflect all regimes
        summary = analyzer.get_regime_summary(min_trades_per_regime=20)
        assert summary["n_regimes_valid"] >= 2
