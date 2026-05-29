"""Multi-Symbol Discovery Pipeline (Gap 5)."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pathlib import Path
import json
import pandas as pd
import numpy as np


class SymbolConfig:
    """Configuration for a symbol in multi-symbol discovery."""

    def __init__(
        self,
        symbol: str,
        contract: str = "",
        role: str = "discovery",
        market_data_path: Optional[Path] = None,
        enable_discovery: bool = True,
        enable_validation: bool = True,
    ):
        self.symbol = symbol
        self.contract = contract
        self.role = role
        self.market_data_path = market_data_path
        self.enable_discovery = enable_discovery
        self.enable_validation = enable_validation


class CrossValidationMetrics:
    """Metrics from validating discovery results on another symbol."""

    def __init__(
        self,
        symbol: str,
        n_trades: int,
        profit_factor: float,
        sharpe_ratio: float,
        win_rate: float,
        avg_win_loss_ratio: float,
        max_drawdown_pct: float,
        pf_degradation_pct: float = 0.0,
        sharpe_degradation_pct: float = 0.0,
        is_robust: bool = True,
    ):
        self.symbol = symbol
        self.n_trades = n_trades
        self.profit_factor = profit_factor
        self.sharpe_ratio = sharpe_ratio
        self.win_rate = win_rate
        self.avg_win_loss_ratio = avg_win_loss_ratio
        self.max_drawdown_pct = max_drawdown_pct
        self.pf_degradation_pct = pf_degradation_pct
        self.sharpe_degradation_pct = sharpe_degradation_pct
        self.is_robust = is_robust


class CrossSymbolCandidate:
    """A strategy candidate ranked across multiple symbols."""

    def __init__(
        self,
        strategy_id: str,
        entry_family: str,
        entry_signal: str,
        params: Dict[str, Any],
        discovery_pf: float,
        discovery_sharpe: float,
        discovery_n_trades: int,
        validation_results: Optional[Dict[str, CrossValidationMetrics]] = None,
        cross_symbol_score: float = 0.0,
        is_cross_validated: bool = False,
        robustness_rating: str = "unknown",
    ):
        self.strategy_id = strategy_id
        self.entry_family = entry_family
        self.entry_signal = entry_signal
        self.params = params
        self.discovery_pf = discovery_pf
        self.discovery_sharpe = discovery_sharpe
        self.discovery_n_trades = discovery_n_trades
        self.validation_results = validation_results or {}
        self.cross_symbol_score = cross_symbol_score
        self.is_cross_validated = is_cross_validated
        self.robustness_rating = robustness_rating

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-safe dict."""
        return {
            "strategy_id": self.strategy_id,
            "entry_family": self.entry_family,
            "entry_signal": self.entry_signal,
            "params": self.params,
            "discovery_pf": float(self.discovery_pf),
            "discovery_sharpe": float(self.discovery_sharpe),
            "discovery_n_trades": int(self.discovery_n_trades),
            "validation_results": {
                k: {
                    "symbol": v.symbol,
                    "n_trades": int(v.n_trades),
                    "profit_factor": float(v.profit_factor),
                    "sharpe_ratio": float(v.sharpe_ratio),
                    "win_rate": float(v.win_rate),
                    "pf_degradation_pct": float(v.pf_degradation_pct),
                    "is_robust": v.is_robust,
                }
                for k, v in self.validation_results.items()
            },
            "cross_symbol_score": float(self.cross_symbol_score),
            "is_cross_validated": self.is_cross_validated,
            "robustness_rating": self.robustness_rating,
        }


class CrossSymbolValidator:
    """Validates discovery results on alternative symbols."""

    def __init__(
        self,
        max_degradation_pct: float = 50.0,
        min_trades_validation: int = 20,
        degradation_weight: float = 0.3,
    ):
        self.max_degradation_pct = max_degradation_pct
        self.min_trades_validation = min_trades_validation
        self.degradation_weight = degradation_weight

    def validate_on_symbol(
        self,
        strategy_results: List[Dict[str, Any]],
        validation_symbol: str,
        discovery_symbol: str,
    ) -> CrossValidationMetrics:
        """Validate discovery results on a different symbol."""
        if not strategy_results or len(strategy_results) < self.min_trades_validation:
            return CrossValidationMetrics(
                symbol=validation_symbol,
                n_trades=len(strategy_results) if strategy_results else 0,
                profit_factor=0.0,
                sharpe_ratio=0.0,
                win_rate=0.0,
                avg_win_loss_ratio=0.0,
                max_drawdown_pct=0.0,
                pf_degradation_pct=100.0,
                sharpe_degradation_pct=100.0,
                is_robust=False,
            )

        if isinstance(strategy_results, dict):
            trades_df = pd.DataFrame(strategy_results.get("trades", []))
        else:
            trades_df = pd.DataFrame(strategy_results)

        if trades_df.empty:
            return CrossValidationMetrics(
                symbol=validation_symbol,
                n_trades=0,
                profit_factor=0.0,
                sharpe_ratio=0.0,
                win_rate=0.0,
                avg_win_loss_ratio=0.0,
                max_drawdown_pct=0.0,
                pf_degradation_pct=100.0,
                sharpe_degradation_pct=100.0,
                is_robust=False,
            )

        n_trades = len(trades_df)

        # Get profits column - try profit_ticks first, then pnl_ticks
        if "profit_ticks" in trades_df.columns:
            profits = trades_df["profit_ticks"]
        elif "pnl_ticks" in trades_df.columns:
            profits = trades_df["pnl_ticks"]
        else:
            profits = pd.Series([])

        if isinstance(profits, pd.Series):
            profits = profits.dropna()

        wins = profits[profits > 0] if len(profits) > 0 else []
        losses = profits[profits < 0] if len(profits) > 0 else []

        total_wins = wins.sum() if len(wins) > 0 else 0
        total_losses = abs(losses.sum()) if len(losses) > 0 else 1e-6
        pf = total_wins / total_losses if total_losses > 0 else 0.0
        win_rate = len(wins) / len(profits) if len(profits) > 0 else 0.0

        sharpe = 0.0
        if len(profits) > 1:
            mean_profit = profits.mean()
            std_profit = profits.std()
            sharpe = mean_profit / std_profit if std_profit > 0 else 0.0

        avg_win = wins.mean() if len(wins) > 0 else 0.0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
        avg_wl_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        cumulative = profits.cumsum()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / (running_max.abs() + 1e-6)
        max_dd = abs(drawdown.min()) * 100 if len(drawdown) > 0 else 0.0

        discovery_wr_assumed = 0.55
        pf_degradation = max(0, (discovery_wr_assumed - win_rate) * 100) if discovery_wr_assumed > 0 else 0.0

        is_robust = (
            n_trades >= self.min_trades_validation
            and pf_degradation <= self.max_degradation_pct
        )

        return CrossValidationMetrics(
            symbol=validation_symbol,
            n_trades=n_trades,
            profit_factor=pf,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            avg_win_loss_ratio=avg_wl_ratio,
            max_drawdown_pct=max_dd,
            pf_degradation_pct=pf_degradation,
            sharpe_degradation_pct=0.0,
            is_robust=is_robust,
        )


class MultiSymbolRunner:
    """Orchestrates discovery and validation across multiple symbols."""

    def __init__(
        self,
        primary_symbol: str,
        validation_symbols: List[str],
        output_dir: str | Path = "./multi_symbol_results",
    ):
        self.primary_symbol = primary_symbol
        self.validation_symbols = validation_symbols
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.validator = CrossSymbolValidator()
        self.candidates: List[CrossSymbolCandidate] = []

    def run_multi_symbol_discovery(
        self,
        discovery_results: Dict[str, Any],
        validation_results: Dict[str, Dict[str, Any]],
        top_n: int = 50,
    ) -> List[CrossSymbolCandidate]:
        """Run cross-symbol validation on discovery results."""
        candidates = self._extract_top_candidates(discovery_results, top_n)

        for candidate in candidates:
            for val_symbol in self.validation_symbols:
                if val_symbol not in validation_results:
                    continue

                val_results = validation_results[val_symbol]
                metrics = self.validator.validate_on_symbol(
                    val_results.get("trades", []),
                    validation_symbol=val_symbol,
                    discovery_symbol=self.primary_symbol,
                )
                candidate.validation_results[val_symbol] = metrics

            candidate.cross_symbol_score = self._calculate_cross_symbol_score(candidate)
            candidate.robustness_rating = self._rate_robustness(candidate)
            candidate.is_cross_validated = len(candidate.validation_results) > 0

        self.candidates = sorted(
            candidates, key=lambda x: x.cross_symbol_score, reverse=True
        )

        return self.candidates

    def _extract_top_candidates(
        self,
        discovery_results: Dict[str, Any],
        top_n: int,
    ) -> List[CrossSymbolCandidate]:
        """Extract top N candidates from discovery results."""
        candidates = []
        sweep_results = discovery_results.get("sweep_results", [])
        if not sweep_results:
            return candidates

        sorted_results = sorted(
            sweep_results,
            key=lambda x: x.get("profit_factor", 0),
            reverse=True
        )

        for i, result in enumerate(sorted_results[:top_n]):
            candidate = CrossSymbolCandidate(
                strategy_id=f"{self.primary_symbol}_strategy_{i:03d}",
                entry_family=result.get("entry_family", "unknown"),
                entry_signal=result.get("entry_signal", "unknown"),
                params=result.get("params", {}),
                discovery_pf=result.get("profit_factor", 0.0),
                discovery_sharpe=result.get("sharpe_ratio", 0.0),
                discovery_n_trades=result.get("n_trades", 0),
            )
            candidates.append(candidate)

        return candidates

    def _calculate_cross_symbol_score(
        self,
        candidate: CrossSymbolCandidate,
    ) -> float:
        """Calculate overall cross-symbol robustness score."""
        if not candidate.validation_results:
            return candidate.discovery_pf

        val_pfs = [m.profit_factor for m in candidate.validation_results.values()]
        avg_val_pf = np.mean(val_pfs) if val_pfs else 0.0

        all_robust = all(m.is_robust for m in candidate.validation_results.values())

        score = (0.7 * candidate.discovery_pf) + (0.3 * avg_val_pf)

        if all_robust:
            score *= 1.1

        return score

    def _rate_robustness(self, candidate: CrossSymbolCandidate) -> str:
        """Rate robustness as poor/fair/good/excellent."""
        if not candidate.validation_results:
            return "unknown"

        n_robust = sum(
            1 for m in candidate.validation_results.values() if m.is_robust
        )
        total = len(candidate.validation_results)

        # Rating logic: 0/n = poor, partial = fair, all = excellent
        if n_robust == 0:
            return "poor"
        elif n_robust == total:
            return "excellent"
        else:
            # Partial robustness - return fair
            return "fair"

    def get_cross_validated_leaderboard(
        self,
        min_robustness: str = "good",
    ) -> List[CrossSymbolCandidate]:
        """Get leaderboard filtered by robustness."""
        robustness_order = {"poor": 0, "fair": 1, "good": 2, "excellent": 3}
        min_level = robustness_order.get(min_robustness, 0)

        filtered = [
            c for c in self.candidates
            if robustness_order.get(c.robustness_rating, 0) >= min_level
        ]

        return sorted(
            filtered, key=lambda x: x.cross_symbol_score, reverse=True
        )

    def save_results(self, stage: int = 1) -> Dict[str, Path]:
        """Save leaderboard and analysis to JSON."""
        outputs = {}

        leaderboard_data = [c.to_dict() for c in self.candidates]
        leaderboard_path = self.output_dir / f"stage_{stage:02d}_cross_symbol_leaderboard.json"
        with open(leaderboard_path, "w") as f:
            json.dump(leaderboard_data, f, indent=2, default=str)
        outputs["leaderboard"] = leaderboard_path

        summary = {
            "primary_symbol": self.primary_symbol,
            "validation_symbols": self.validation_symbols,
            "n_candidates": len(self.candidates),
            "robustness_breakdown": {
                "excellent": sum(1 for c in self.candidates if c.robustness_rating == "excellent"),
                "good": sum(1 for c in self.candidates if c.robustness_rating == "good"),
                "fair": sum(1 for c in self.candidates if c.robustness_rating == "fair"),
                "poor": sum(1 for c in self.candidates if c.robustness_rating == "poor"),
                "unknown": sum(1 for c in self.candidates if c.robustness_rating == "unknown"),
            },
        }

        summary_path = self.output_dir / f"stage_{stage:02d}_cross_symbol_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
        outputs["summary"] = summary_path

        return outputs


def print_cross_symbol_leaderboard(
    candidates: List[CrossSymbolCandidate],
    top_n: int = 20,
) -> str:
    """Format leaderboard as text."""
    lines = [
        "=" * 100,
        "CROSS-SYMBOL LEADERBOARD",
        "=" * 100,
        "",
    ]

    header = (
        f"{'Rank':<5} {'Strategy':<25} {'Family':<12} "
        f"{'Discovery PF':<12} {'Avg Val PF':<12} {'Robustness':<12} {'Score':<10}"
    )
    lines.append(header)
    lines.append("-" * 100)

    for i, candidate in enumerate(candidates[:top_n], 1):
        avg_val_pf = (
            np.mean([m.profit_factor for m in candidate.validation_results.values()])
            if candidate.validation_results
            else 0.0
        )

        line = (
            f"{i:<5} {candidate.strategy_id:<25} {candidate.entry_family:<12} "
            f"{candidate.discovery_pf:<12.3f} {avg_val_pf:<12.3f} "
            f"{candidate.robustness_rating:<12} {candidate.cross_symbol_score:<10.3f}"
        )
        lines.append(line)

    lines.append("=" * 100)
    return "\n".join(lines)
