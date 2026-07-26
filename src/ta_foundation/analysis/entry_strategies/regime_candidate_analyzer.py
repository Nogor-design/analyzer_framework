"""Regime-Based Candidate Analysis (Gap 6 Phase 2)."""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


class RegimeCandidateAnalyzer:
    """Analyze strategy candidate performance across market regimes."""

    def __init__(
        self,
        discovery_results: List[Dict[str, Any]],
        regime_labels: List[str],
        regime_types: Optional[List[str]] = None,
    ):
        """Initialize regime candidate analyzer.

        Args:
            discovery_results: List of trade result dicts with keys like profit_ticks, entry_time, etc.
            regime_labels: List of regime IDs (same length as discovery_results)
            regime_types: List of expected regime IDs (optional)
        """
        self.results = discovery_results
        self.regime_labels = regime_labels
        self.regime_types = regime_types or list(set(regime_labels))

        # Validate lengths match
        if len(discovery_results) != len(regime_labels):
            raise ValueError(
                f"discovery_results ({len(discovery_results)}) and "
                f"regime_labels ({len(regime_labels)}) must have same length"
            )

        # Build regime-indexed results
        self.results_by_regime = self._partition_by_regime()

    def _partition_by_regime(self) -> Dict[str, List[Dict[str, Any]]]:
        """Partition results by regime."""
        by_regime = {regime: [] for regime in self.regime_types}
        for result, regime in zip(self.results, self.regime_labels):
            by_regime[regime].append(result)
        return by_regime

    def get_regime_performance(self, regime: str) -> Dict[str, Any]:
        """Calculate performance metrics for a specific regime.

        Args:
            regime: Regime identifier

        Returns:
            Dict with keys: n_trades, profit_factor, sharpe_ratio, win_rate,
            avg_win, avg_loss, max_drawdown_pct, cumulative_pnl
        """
        trades = self.results_by_regime.get(regime, [])

        if not trades or len(trades) == 0:
            return {
                "regime": regime,
                "n_trades": 0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "max_drawdown_pct": 0.0,
                "cumulative_pnl": 0.0,
            }

        # Extract profits
        trades_df = pd.DataFrame(trades)
        profits = None

        if "profit_ticks" in trades_df.columns:
            profits = trades_df["profit_ticks"]
        elif "pnl_ticks" in trades_df.columns:
            profits = trades_df["pnl_ticks"]
        elif "profit_loss" in trades_df.columns:
            profits = trades_df["profit_loss"]
        else:
            profits = pd.Series([])

        profits = profits.dropna()

        if len(profits) == 0:
            return {
                "regime": regime,
                "n_trades": 0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "max_drawdown_pct": 0.0,
                "cumulative_pnl": 0.0,
            }

        # Calculate metrics
        wins = profits[profits > 0]
        losses = profits[profits < 0]

        total_wins = wins.sum() if len(wins) > 0 else 0.0
        total_losses = abs(losses.sum()) if len(losses) > 0 else 1e-6
        pf = float(total_wins / total_losses) if total_losses > 0 else 0.0

        win_rate = float(len(wins) / len(profits)) if len(profits) > 0 else 0.0
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0

        # Sharpe ratio
        sharpe = 0.0
        if len(profits) > 1:
            mean_profit = profits.mean()
            std_profit = profits.std()
            sharpe = float(mean_profit / std_profit) if std_profit > 0 else 0.0

        # Max drawdown
        cumulative = profits.cumsum()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / (running_max.abs() + 1e-6)
        max_dd = float(abs(drawdown.min()) * 100) if len(drawdown) > 0 else 0.0

        cumulative_pnl = float(cumulative.iloc[-1]) if len(cumulative) > 0 else 0.0

        return {
            "regime": regime,
            "n_trades": int(len(profits)),
            "profit_factor": pf,
            "sharpe_ratio": sharpe,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_drawdown_pct": max_dd,
            "cumulative_pnl": cumulative_pnl,
        }

    def get_multi_regime_performance(self) -> Dict[str, Dict[str, Any]]:
        """Get performance across all regimes.

        Returns:
            Dict mapping regime_id to performance metrics
        """
        return {
            regime: self.get_regime_performance(regime)
            for regime in self.regime_types
        }

    def get_multi_regime_robustness(
        self,
        min_trades_per_regime: int = 20,
        min_profitable_regimes: int = 1,
    ) -> str:
        """Rate overall robustness across regimes.

        Args:
            min_trades_per_regime: Minimum trades to consider regime valid
            min_profitable_regimes: Minimum regimes with positive PF

        Returns:
            Rating: 'excellent' (works in 3+ regimes), 'good' (2 regimes),
            'fair' (1 regime), 'poor' (0 regimes or insufficient trades)
        """
        perf = self.get_multi_regime_performance()

        # Count regimes with sufficient trades and positive PF
        profitable_regimes = 0
        for regime_id in self.regime_types:
            regime_perf = perf.get(regime_id, {})
            n_trades = regime_perf.get("n_trades", 0)
            pf = regime_perf.get("profit_factor", 0.0)

            if n_trades >= min_trades_per_regime and pf > 1.0:
                profitable_regimes += 1

        if profitable_regimes < min_profitable_regimes:
            return "poor"
        elif profitable_regimes >= 3:
            return "excellent"
        elif profitable_regimes == 2:
            return "good"
        else:  # profitable_regimes == 1
            return "fair"

    def calculate_regime_degradation(
        self,
        best_regime: str,
    ) -> Dict[str, float]:
        """Calculate performance degradation from best regime to others.

        Args:
            best_regime: Regime with best performance (baseline)

        Returns:
            Dict mapping regime_id to degradation percentage
        """
        perf = self.get_multi_regime_performance()
        best_pf = perf.get(best_regime, {}).get("profit_factor", 0.0)

        degradations = {}
        for regime in self.regime_types:
            regime_pf = perf.get(regime, {}).get("profit_factor", 0.0)
            if best_pf > 0:
                degradation = ((best_pf - regime_pf) / best_pf) * 100
                degradations[regime] = max(0.0, degradation)
            else:
                degradations[regime] = 0.0

        return degradations

    def get_regime_summary(
        self,
        min_trades_per_regime: int = 20,
    ) -> Dict[str, Any]:
        """Generate a comprehensive summary of regime performance.

        Args:
            min_trades_per_regime: Minimum trades to include regime in summary

        Returns:
            Summary dict with performance, robustness, and recommendations
        """
        perf = self.get_multi_regime_performance()

        # Find best and worst performing regimes
        valid_regimes = {
            r: p for r, p in perf.items()
            if p.get("n_trades", 0) >= min_trades_per_regime
        }

        if not valid_regimes:
            return {
                "n_regimes_tested": len(self.regime_types),
                "n_regimes_valid": 0,
                "n_total_trades": len(self.results),
                "robustness": "poor",
                "best_regime": None,
                "worst_regime": None,
                "avg_profit_factor": 0.0,
                "recommendations": ["Insufficient trades in any regime to assess robustness"],
            }

        best_regime = max(valid_regimes, key=lambda r: valid_regimes[r].get("profit_factor", 0))
        worst_regime = min(valid_regimes, key=lambda r: valid_regimes[r].get("profit_factor", 0))

        # Calculate statistics
        pfs = [p.get("profit_factor", 0) for p in valid_regimes.values()]
        sharpes = [p.get("sharpe_ratio", 0) for p in valid_regimes.values()]

        robustness = self.get_multi_regime_robustness(min_trades_per_regime)

        degradations = self.calculate_regime_degradation(best_regime)

        # Generate recommendations
        recommendations = []
        if len(valid_regimes) >= 3:
            recommendations.append(f"Strategy works across {len(valid_regimes)} regimes - excellent robustness")
        elif len(valid_regimes) == 2:
            recommendations.append(f"Strategy works in {len(valid_regimes)} regimes - good multi-regime performance")
        else:
            recommendations.append(f"Strategy works in only {len(valid_regimes)} regime(s) - consider regime-specific tuning")

        if degradations[worst_regime] > 50:
            recommendations.append(
                f"Large degradation from {best_regime} to {worst_regime} ({degradations[worst_regime]:.1f}%) - "
                f"may need regime-specific parameters"
            )

        avg_pf = float(np.mean(pfs)) if pfs else 0.0
        if avg_pf < 1.05:
            recommendations.append("Average PF across regimes is low - consider stronger pattern selection")

        return {
            "n_regimes_tested": len(self.regime_types),
            "n_regimes_valid": len(valid_regimes),
            "n_total_trades": len(self.results),
            "robustness": robustness,
            "best_regime": best_regime,
            "worst_regime": worst_regime,
            "best_regime_pf": float(valid_regimes[best_regime].get("profit_factor", 0)),
            "worst_regime_pf": float(valid_regimes[worst_regime].get("profit_factor", 0)),
            "avg_profit_factor": avg_pf,
            "avg_sharpe_ratio": float(np.mean(sharpes)) if sharpes else 0.0,
            "max_degradation_pct": float(max(degradations.values())) if degradations else 0.0,
            "regime_performance": {r: valid_regimes[r] for r in valid_regimes},
            "recommendations": recommendations,
        }
