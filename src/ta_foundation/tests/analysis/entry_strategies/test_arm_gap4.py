"""
Tests for Advanced Risk Management (Gap 4)

Tests volatility-based sizing and Kelly criterion calculations.
"""

import pytest
import pandas as pd
import numpy as np

from ta_foundation.analysis.entry_strategies.advanced_risk_management import (
    VolatilitySizing,
    KellyCriterion,
    compute_kelly_contracts,
    volatility_adjusted_outcome_config,
)


class TestVolatilitySizing:
    """Test volatility-based position sizing."""

    def test_calculate_size_basic(self):
        """Test basic contract sizing with fixed stop."""
        sizing = VolatilitySizing(
            account_size=100000,
            target_risk_pct=1.0,
            tick_size=0.25,
            tick_value=5.0,
        )

        result = sizing.calculate_size(
            entry_price=4500.0,
            stop_price=4450.0,
            atr_value=30.0,
        )

        assert result["contracts"] > 0
        assert result["risk_dollars"] > 0
        assert result["stop_distance_ticks"] > 0
        assert "stop_distance_atr_mult" in result

    def test_calculate_size_different_risks(self):
        """Test sizing scales with target risk."""
        sizing = VolatilitySizing(account_size=100000, target_risk_pct=1.0)

        result_1pct = sizing.calculate_size(4500, 4450, 30.0)
        contracts_1pct = result_1pct["contracts"]

        # 2% risk should give more contracts
        sizing_2pct = VolatilitySizing(account_size=100000, target_risk_pct=2.0)
        result_2pct = sizing_2pct.calculate_size(4500, 4450, 30.0)
        contracts_2pct = result_2pct["contracts"]

        assert contracts_2pct >= contracts_1pct

    def test_calculate_size_invalid_stop(self):
        """Test handling of invalid stops."""
        sizing = VolatilitySizing()

        result = sizing.calculate_size(
            entry_price=4500.0,
            stop_price=4500.0,  # Same as entry (invalid)
            atr_value=30.0,
        )

        assert result["contracts"] == 0
        assert "note" in result

    def test_calculate_size_by_atr_multiple(self):
        """Test sizing with ATR-based stops."""
        sizing = VolatilitySizing()

        result = sizing.calculate_size_by_atr_multiple(
            entry_price=4500.0,
            atr_value=30.0,
            direction=1,
            stop_atr_mult=1.0,
        )

        assert result["contracts"] > 0
        assert result["stop_distance_atr_mult"] == pytest.approx(1.0, rel=0.01)

    def test_contracts_capped(self):
        """Test max contracts cap."""
        sizing = VolatilitySizing(account_size=1000000, target_risk_pct=5.0)

        result = sizing.calculate_size(
            entry_price=4500.0,
            stop_price=4450.0,
            atr_value=30.0,
            max_contracts=10,
        )

        assert result["contracts"] <= 10


class TestKellyCriterion:
    """Test Kelly Criterion calculations."""

    def test_kelly_positive_edge(self):
        """Test Kelly with positive edge (55% win rate, 2:1 reward:risk)."""
        kelly = KellyCriterion()

        result = kelly.calculate(
            win_rate=0.55,
            avg_win_ticks=100.0,
            avg_loss_ticks=50.0,  # 2:1 reward ratio
        )

        assert result["kelly_f"] > 0
        assert result["kelly_f_conservative"] > 0
        assert result["kelly_pct"] > 0
        # Kelly f should be roughly 0.325 (32.5%) for this edge
        # Formula: f = (0.55 * 100 - 0.45 * 50) / 100 = (55 - 22.5) / 100 = 0.325
        assert result["kelly_f"] == pytest.approx(0.325, rel=0.01)

    def test_kelly_no_edge(self):
        """Test Kelly with no edge (50% win rate, 1:1 reward:risk)."""
        kelly = KellyCriterion()

        result = kelly.calculate(
            win_rate=0.50,
            avg_win_ticks=100.0,
            avg_loss_ticks=100.0,
        )

        # No edge means Kelly f = 0
        assert result["kelly_f"] <= 0

    def test_kelly_strong_edge(self):
        """Test Kelly with strong edge (60% win rate, 3:1 reward:risk)."""
        kelly = KellyCriterion()

        result = kelly.calculate(
            win_rate=0.60,
            avg_win_ticks=150.0,
            avg_loss_ticks=50.0,  # 3:1 reward ratio
        )

        assert result["kelly_f"] > 0
        # Stronger edge should give higher kelly_f
        assert result["kelly_f"] > 0.05

    def test_kelly_conservative_scaling(self):
        """Test conservative Kelly scaling."""
        kelly = KellyCriterion()

        result = kelly.calculate(
            win_rate=0.55,
            avg_win_ticks=100.0,
            avg_loss_ticks=50.0,
            max_kelly_fraction=0.25,  # Use only 25% of Kelly
        )

        assert result["kelly_f_conservative"] <= result["kelly_f"] * 0.26

    def test_kelly_invalid_win_rate(self):
        """Test handling of invalid win rates."""
        kelly = KellyCriterion()

        result = kelly.calculate(
            win_rate=1.5,  # Invalid: > 1.0
            avg_win_ticks=100.0,
            avg_loss_ticks=50.0,
        )

        assert result["kelly_f"] == 0.0
        assert "invalid_win_rate" in result.get("edge", "").lower()

    def test_kelly_from_equity_curve(self):
        """Test Kelly calculation from trades DataFrame."""
        kelly = KellyCriterion()

        # Create synthetic trades
        trades_df = pd.DataFrame(
            {
                "profit_ticks": [50, 100, -40, 120, -50, 80, 60, -30, 110, -20] * 3,
            }
        )

        result = kelly.calculate_from_equity_curve(trades_df, min_trades=5)

        assert result["kelly_f"] > 0 or result["kelly_f"] == 0
        if result["kelly_f"] > 0:
            assert "payoff_ratio" in result

    def test_kelly_insufficient_trades(self):
        """Test Kelly with fewer than min_trades."""
        kelly = KellyCriterion()

        trades_df = pd.DataFrame({"profit_ticks": [50, -40]})

        result = kelly.calculate_from_equity_curve(trades_df, min_trades=10)

        assert result["kelly_f"] == 0.0
        assert "fewer than" in result.get("note", "")

    def test_kelly_payoff_ratio(self):
        """Test Kelly payoff ratio calculation."""
        kelly = KellyCriterion()

        result = kelly.calculate(
            win_rate=0.55,
            avg_win_ticks=200.0,
            avg_loss_ticks=100.0,
        )

        assert result["payoff_ratio"] == pytest.approx(2.0, rel=0.01)


class TestKellyContracts:
    """Test Kelly-based contract sizing."""

    def test_compute_kelly_contracts(self):
        """Test computing contracts from Kelly fraction."""
        result = compute_kelly_contracts(
            kelly_fraction=0.05,
            account_size=100000,
            entry_price=4500.0,
            stop_price=4450.0,
            tick_size=0.25,
            tick_value=5.0,
        )

        assert result["contracts"] > 0
        assert result["kelly_fraction_used"] == 0.05
        assert result["kelly_pct_used"] == 5.0

    def test_compute_kelly_zero_fraction(self):
        """Test with zero Kelly fraction."""
        result = compute_kelly_contracts(
            kelly_fraction=0.0,
            account_size=100000,
            entry_price=4500.0,
            stop_price=4450.0,
        )

        assert result["contracts"] == 0

    def test_compute_kelly_capped_by_max(self):
        """Test Kelly contracts capped by max_contracts."""
        result = compute_kelly_contracts(
            kelly_fraction=0.2,
            account_size=1000000,
            entry_price=4500.0,
            stop_price=4450.0,
            max_contracts=5,
        )

        assert result["contracts"] <= 5
        assert result["contracts_limited_by"] == "max_contracts"


class TestVolatilityAdjustedConfig:
    """Test volatility-adjusted outcome configuration."""

    def test_adjust_config_high_volatility(self):
        """Test config adjusts wider stops in high volatility."""
        base_config = {
            "ticks": {
                "enabled": True,
                "take_profit": [50, 100],
                "stop": [25, 40],
            }
        }

        # High volatility (1.5x normal)
        result = volatility_adjusted_outcome_config(
            base_config,
            atr_value=45.0,
            current_regime_volatility=30.0,  # Base vol is 30
        )

        # Should scale TP and SL up by 1.5x
        assert result["ticks"]["take_profit"][0] > base_config["ticks"]["take_profit"][0]
        assert result["ticks"]["stop"][0] > base_config["ticks"]["stop"][0]

    def test_adjust_config_low_volatility(self):
        """Test config tightens in low volatility."""
        base_config = {
            "ticks": {
                "enabled": True,
                "take_profit": [100, 200],
                "stop": [50, 75],
            }
        }

        # Low volatility (0.5x normal)
        result = volatility_adjusted_outcome_config(
            base_config,
            atr_value=15.0,
            current_regime_volatility=30.0,
        )

        # Should scale TP and SL down by 0.5x
        assert result["ticks"]["take_profit"][0] < base_config["ticks"]["take_profit"][0]
        assert result["ticks"]["stop"][0] < base_config["ticks"]["stop"][0]

    def test_adjust_config_no_regime_vol(self):
        """Test returns unchanged config if no regime volatility."""
        base_config = {
            "ticks": {
                "enabled": True,
                "take_profit": [50],
                "stop": [25],
            }
        }

        result = volatility_adjusted_outcome_config(
            base_config,
            atr_value=30.0,
            current_regime_volatility=None,
        )

        # Should be unchanged
        assert result["ticks"]["take_profit"] == base_config["ticks"]["take_profit"]

    def test_adjust_config_vix_stress(self):
        """Test VIX stress adjustment."""
        base_config = {
            "ticks": {
                "enabled": True,
                "take_profit": [50],
                "stop": [25],
            }
        }

        # High VIX (40) should apply stress multiplier
        result = volatility_adjusted_outcome_config(
            base_config,
            atr_value=30.0,
            current_regime_volatility=30.0,
            vix_level=40.0,
        )

        # VIX stress = 40/20 = 2.0
        assert result["ticks"]["take_profit"][0] >= base_config["ticks"]["take_profit"][0] * 2


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_risk_sizing_workflow(self):
        """Test complete workflow: Kelly → contracts → outcome config."""
        # Step 1: Calculate Kelly from historical data
        kelly = KellyCriterion()
        kelly_result = kelly.calculate(
            win_rate=0.55,
            avg_win_ticks=100.0,
            avg_loss_ticks=50.0,
            max_kelly_fraction=0.25,
        )
        kelly_f = kelly_result["kelly_f_conservative"]

        # Step 2: Size contracts
        contracts_result = compute_kelly_contracts(
            kelly_fraction=kelly_f,
            account_size=100000,
            entry_price=4500.0,
            stop_price=4450.0,
        )

        # Step 3: Adjust outcome config by volatility
        base_config = {
            "ticks": {
                "enabled": True,
                "take_profit": [50, 100],
                "stop": [20, 30],
            }
        }
        adjusted_config = volatility_adjusted_outcome_config(
            base_config,
            atr_value=35.0,
            current_regime_volatility=30.0,
        )

        # All steps should complete successfully
        assert kelly_f > 0
        assert contracts_result["contracts"] > 0
        assert adjusted_config["ticks"]["enabled"]

    def test_comparison_fixed_vs_kelly(self):
        """Compare fixed sizing vs Kelly-based sizing."""
        sizing = VolatilitySizing(account_size=100000, target_risk_pct=1.0)

        # Fixed volatility sizing
        fixed = sizing.calculate_size(
            entry_price=4500.0,
            stop_price=4450.0,
            atr_value=30.0,
        )

        # Kelly-based sizing
        kelly = KellyCriterion()
        kelly_result = kelly.calculate(
            win_rate=0.55,
            avg_win_ticks=100.0,
            avg_loss_ticks=50.0,
            max_kelly_fraction=0.25,
        )

        kelly_contracts = compute_kelly_contracts(
            kelly_fraction=kelly_result["kelly_f_conservative"],
            account_size=100000,
            entry_price=4500.0,
            stop_price=4450.0,
        )

        # Both should produce valid contracts
        assert fixed["contracts"] > 0
        