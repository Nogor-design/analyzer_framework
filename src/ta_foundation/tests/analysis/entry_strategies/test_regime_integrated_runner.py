"""
Tests for Regime-Integrated Discovery Runner (Gap 6 Phase 3-4)
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path

from ta_foundation.analysis.entry_strategies.regime_integrated_discovery_runner import (
    RegimeIntegratedDiscoveryRunner,
)
from ta_foundation.analysis.entry_strategies.regime_discovery_config import (
    RegimeDiscoveryConfig,
    create_default_regime_discovery_config,
)


class TestRegimeIntegratedDiscoveryRunner:
    """Test regime-integrated discovery orchestration."""

    def test_runner_creation(self):
        """Test creating a RegimeIntegratedDiscoveryRunner."""
        runner = RegimeIntegratedDiscoveryRunner(
            output_dir="./test_results",
            num_stages=2,
            regime_discovery_config=create_default_regime_discovery_config(),
        )

        assert runner.regime_config is not None
        assert runner.num_stages == 2

    def test_classify_bars_to_regimes(self, tmp_path):
        """Test regime classification of bars."""
        runner = RegimeIntegratedDiscoveryRunner(output_dir=tmp_path)

        # Create synthetic bars
        bars_1m = pd.DataFrame({
            "open": [100, 101, 102, 103, 104],
            "high": [101, 102, 103, 104, 105],
            "low": [99, 100, 101, 102, 103],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
        })

        # Simple classifier that returns trend_up for all
        def simple_classifier(window):
            return "trend_up"

        regimes = runner.classify_bars_to_regimes(bars_1m, simple_classifier)

        assert len(regimes) == len(bars_1m)
        assert all(r == "trend_up" for r in regimes)

    def test_classify_bars_with_errors(self, tmp_path):
        """Test regime classification with errors."""
        runner = RegimeIntegratedDiscoveryRunner(output_dir=tmp_path)

        bars_1m = pd.DataFrame({
            "open": [100, 101],
            "high": [101, 102],
            "low": [99, 100],
            "close": [100.5, 101.5],
        })

        def failing_classifier(window):
            raise ValueError("Test error")

        regimes = runner.classify_bars_to_regimes(bars_1m, failing_classifier)

        # Should default to "range" on error
        assert all(r == "range" for r in regimes)

    def test_partition_discovery_results_by_regime(self, tmp_path):
        """Test partitioning results by regime."""
        runner = RegimeIntegratedDiscoveryRunner(output_dir=tmp_path)

        results = [
            {"profit_ticks": 100},
            {"profit_ticks": -50},
            {"profit_ticks": 80},
            {"profit_ticks": -30},
        ]
        regimes = ["trend_up", "trend_up", "range", "range"]

        partitioned = runner.partition_discovery_results_by_regime(results, regimes)

        assert len(partitioned["trend_up"]) == 2
        assert len(partitioned["range"]) == 2

    def test_apply_regime_specific_discovery(self, tmp_path):
        """Test applying regime-specific overrides."""
        config = create_default_regime_discovery_config()
        runner = RegimeIntegratedDiscoveryRunner(
            output_dir=tmp_path,
            regime_discovery_config=config,
        )

        base_config = {
            "discovery_families": {
                "candle": {"enabled": True, "patterns": ["all"]},
                "level": {"enabled": True},
            }
        }

        # Apply trend_up config (should disable level)
        result = runner.apply_regime_specific_discovery(base_config, "trend_up")

        # Level should be disabled in trend_up
        assert result["discovery_families"]["level"]["enabled"] is False

    def test_apply_regime_specific_discovery_disabled(self, tmp_path):
        """Test that disabled regime discovery returns base config."""
        config = RegimeDiscoveryConfig(enabled=False)
        runner = RegimeIntegratedDiscoveryRunner(
            output_dir=tmp_path,
            regime_discovery_config=config,
        )

        base_config = {
            "discovery_families": {
                "candle": {"enabled": True},
            }
        }

        result = runner.apply_regime_specific_discovery(base_config, "trend_up")

        # Should return base config unchanged
        assert result == base_config

    def test_save_regime_config(self, tmp_path):
        """Test saving regime configuration."""
        runner = RegimeIntegratedDiscoveryRunner(output_dir=tmp_path)
        filepath = runner.save_regime_config()

        assert filepath.exists()
        assert filepath.name == "regime_discovery_config.json"

    def test_regime_analysis_results_storage(self, tmp_path):
        """Test storing regime analysis results."""
        runner = RegimeIntegratedDiscoveryRunner(output_dir=tmp_path)

        sample_analysis = {
            "n_regimes_tested": 3,
            "robustness": "excellent",
            "best_regime": "trend_up",
        }

        runner._save_regime_analysis(1, sample_analysis)

        filepath = tmp_path / "stage_01_regime_analysis.json"
        assert filepath.exists()

    def test_process_stage_results_with_regime_analysis(self, tmp_path):
        """Test processing stage results with regime analysis."""
        runner = RegimeIntegratedDiscoveryRunner(output_dir=tmp_path)

        family_results = {
            "candle": {
                "sweep_results": [
                    {
                        "entry_family": "candle",
                        "profit_factor": 1.5,
                        "sharpe_ratio": 1.0,
                        "n_trades": 100,
                    }
                ]
            }
        }

        discovery_results = [{"profit_ticks": 100}, {"profit_ticks": -50}] * 20
        regime_labels = ["trend_up"] * 20 + ["range"] * 20

        analysis = runner.process_stage_results_with_regime_analysis(
            stage=1,
            family_results=family_results,
            discovery_results=discovery_results,
            regime_labels=regime_labels,
        )

        assert "regime_analysis" in analysis
        assert analysis["regime_analysis"]["n_regimes_tested"] >= 1

    def test_get_summary(self, tmp_path):
        """Test getting runner summary."""
        runner = RegimeIntegratedDiscoveryRunner(output_dir=tmp_path)

        runner.stage_results[1] = {"focus_families": ["candle"]}

        summary = runner.get_summary()

        assert summary["stages_processed"] == 1
        assert 1 in summary["stages"]


class TestRegimeDiscoveryIntegration:
    """Integration tests for regime discovery."""

    def test_end_to_end_regime_classification_and_analysis(self, tmp_path):
        """Test full workflow from classification through analysis."""
        runner = RegimeIntegratedDiscoveryRunner(
            output_dir=tmp_path,
            regime_discovery_config=create_default_regime_discovery_config(),
        )

        # Create synthetic bars
        np.random.seed(42)
        n_bars = 100
        returns = np.random.randn(n_bars) * 0.01
        close = 100 * np.exp(np.cumsum(returns))

        bars_1m = pd.DataFrame({
            "open": close - np.abs(np.random.randn(n_bars) * 0.5),
            "high": close + np.abs(np.random.randn(n_bars) * 0.5),
            "low": close - np.abs(np.random.randn(n_bars) * 0.5),
            "close": close,
        })

        # Simple classifier
        def classifier(window):
            if len(window) < 2:
                return "range"
            slope = (window["close"].iloc[-1] - window["close"].iloc[-10]) / 10 if len(window) >= 10 else 0
            if slope > 0.1:
                return "trend_up"
            elif slope < -0.1:
                return "trend_down"
            else:
                return "range"

        regimes = runner.classify_bars_to_regimes(bars_1m, classifier)

        assert len(regimes) == len(bars_1m)
        assert any(r in ["trend_up", "trend_down", "range"] for r in regimes)

    def test_regime_config_with_discovery_simulation(self, tmp_path):
        """Test regime config integration with discovery simulation."""
        config = create_default_regime_discovery_config()
        base_discovery_config = {
            "discovery_families": {
                "candle": {
                    "enabled": True,
                    "patterns": ["large_body", "pin_bar", "engulfing"],
                },
                "ma": {
                    "enabled": True,
                    "fast_range": [9, 20],
                    "slow_range": [30, 100],
                },
                "level": {
                    "enabled": True,
                    "touch_tolerance": [3, 8],
                },
            }
        }

        # Apply to different regimes
        trend_up_config = config.apply_regime_to_discovery_config(
            base_discovery_config,
            "trend_up",
        )
        range_config = config.apply_regime_to_discovery_config(
            base_discovery_config,
            "range",
        )

        # Trend_up should have candle enabled, level disabled
        assert trend_up_config["discovery_families"]["candle"]["enabled"] is True

        # Range should have level enabled
        assert range_config["discovery_families"]["level"]["enabled"] is True
