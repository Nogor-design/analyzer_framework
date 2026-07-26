"""
Advanced Risk Management for Strategy Discovery

Implements sophisticated position sizing techniques:
  1. Volatility-Based Sizing (VBS) — size contracts based on ATR
  2. Kelly Criterion — optimal fractional f based on win rate and payoff ratio
  3. Volatility-Adjusted Stops — dynamically adjust stop/target based on regime ATR

This enables discovery to optimize not just entry/exit logic but also risk management.

YAML Configuration:
  outcome:
    volatility_sizing:
      enabled: true
      target_risk_pct: 1.0              # Risk 1% of account per trade
      atr_period: 14
      account_size: 100000              # Default account size

    kelly_criterion:
      enabled: true
      max_kelly_fraction: 0.25           # Use max 25% of Kelly to be conservative
      min_kelly_fraction: 0.01           # Don't size below 1% Kelly
      smoothing_factor: 0.5              # Average Kelly over rolling window

Example:
    from ta_foundation.analysis.entry_strategies.advanced_risk_management import (
        VolatilitySizing,
        KellyCriterion,
        compute_kelly_contracts,
    )

    # Volatility-based sizing
    sizing = VolatilitySizing(account_size=100000, target_risk_pct=1.0)
    contracts = sizing.calculate_size(
        entry_price=4500.0,
        stop_price=4450.0,
        atr_value=25.0,
    )

    # Kelly criterion
    kelly = KellyCriterion()
    kelly_f = kelly.calculate(
        win_rate=0.55,
        avg_win=250.0,
        avg_loss=100.0,
    )
    # kelly_f might be 0.085 (8.5% of account)

    # Conservative Kelly (use 50% of theoretical Kelly)
    kelly_f_conservative = kelly_f * 0.5
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import math
import numpy as np
import pandas as pd


@dataclass
class VolatilitySizing:
    """Calculate position size based on volatility (ATR) and target risk percentage."""

    account_size: float = 100000.0      # Base account size
    target_risk_pct: float = 1.0        # Risk X% of account per trade
    tick_size: float = 0.25             # ES / NQ tick size
    tick_value: float = 5.0             # $ value per tick per contract

    def calculate_size(
        self,
        entry_price: float,
        stop_price: float,
        atr_value: float,
        max_contracts: int = 50,
    ) -> Dict[str, Any]:
        """
        Calculate position size using volatility-based risk management.

        Parameters
        ----------
        entry_price     : entry level
        stop_price      : stop loss level
        atr_value       : current ATR in points
        max_contracts   : maximum contracts to size

        Returns
        -------
        Dict with:
          - contracts: position size in contracts
          - risk_dollars: risk amount in dollars
          - stop_distance_ticks: distance from entry to stop
          - stop_distance_atr_mult: how many ATR is the stop
        """
        # Calculate stop distance
        stop_distance_ticks = abs(entry_price - stop_price) / self.tick_size
        if stop_distance_ticks <= 0 or math.isnan(stop_distance_ticks):
            return {
                "contracts": 0,
                "risk_dollars": 0.0,
                "stop_distance_ticks": 0.0,
                "stop_distance_atr_mult": 0.0,
                "note": "invalid_stop_distance",
            }

        # Risk amount based on target_risk_pct
        target_risk_dollars = self.account_size * (self.target_risk_pct / 100.0)

        # How many contracts can we trade at this risk level?
        dollars_per_tick = self.tick_value
        dollars_at_risk_per_contract = stop_distance_ticks * dollars_per_tick

        if dollars_at_risk_per_contract <= 0:
            return {
                "contracts": 0,
                "risk_dollars": 0.0,
                "stop_distance_ticks": stop_distance_ticks,
                "stop_distance_atr_mult": atr_value / atr_value if atr_value > 0 else 0.0,
                "note": "no_risk_at_this_stop",
            }

        # Calculate contracts needed to achieve target risk
        contracts = target_risk_dollars / dollars_at_risk_per_contract
        contracts = min(int(contracts), max_contracts)
        contracts = max(1, contracts)  # At least 1 contract

        # ATR multiple calculation
        atr_mult = stop_distance_ticks / (atr_value / self.tick_size) if atr_value > 0 else 0.0

        return {
            "contracts": int(contracts),
            "risk_dollars": dollars_at_risk_per_contract * contracts,
            "stop_distance_ticks": stop_distance_ticks,
            "stop_distance_atr_mult": atr_mult,
            "dollars_per_contract_at_risk": dollars_at_risk_per_contract,
        }

    def calculate_size_by_atr_multiple(
        self,
        entry_price: float,
        atr_value: float,
        direction: int,
        stop_atr_mult: float = 1.0,
        max_contracts: int = 50,
    ) -> Dict[str, Any]:
        """
        Calculate position size when stop is defined as ATR multiple.

        Parameters
        ----------
        entry_price     : entry level
        atr_value       : ATR in points
        direction       : 1 for long, -1 for short
        stop_atr_mult   : stop distance as multiple of ATR
        max_contracts   : maximum contracts

        Returns
        -------
        Dict with contract size and risk metrics
        """
        stop_price = entry_price - (atr_value * stop_atr_mult * direction)
        return self.calculate_size(entry_price, stop_price, atr_value, max_contracts)


@dataclass
class KellyCriterion:
    """Calculate Kelly Criterion for position sizing."""

    def calculate(
        self,
        win_rate: float,
        avg_win_ticks: float,
        avg_loss_ticks: float,
        max_kelly_fraction: float = 0.25,
        min_kelly_fraction: float = 0.01,
    ) -> Dict[str, Any]:
        """
        Calculate Kelly Criterion for optimal position sizing.

        Kelly f = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win

        Parameters
        ----------
        win_rate            : historical win rate (0-1)
        avg_win_ticks       : average winning trade size in ticks
        avg_loss_ticks      : average losing trade size in ticks (positive)
        max_kelly_fraction  : apply fraction to kelly f (e.g., 0.25 = use 25% of Kelly)
        min_kelly_fraction  : minimum kelly fraction to use

        Returns
        -------
        Dict with:
          - kelly_f: theoretical optimal fraction
          - kelly_f_conservative: kelly_f * max_kelly_fraction
          - kelly_pct: kelly_f as percentage
          - kelly_pct_conservative: conservative % to risk
          - edge: win_rate and payoff ratio summary
        """
        if not (0 < win_rate < 1):
            return {
                "kelly_f": 0.0,
                "kelly_f_conservative": 0.0,
                "kelly_pct": 0.0,
                "kelly_pct_conservative": 0.0,
                "edge": "invalid_win_rate",
                "note": "win_rate must be between 0 and 1",
            }

        if avg_win_ticks <= 0 or avg_loss_ticks <= 0:
            return {
                "kelly_f": 0.0,
                "kelly_f_conservative": 0.0,
                "kelly_pct": 0.0,
                "kelly_pct_conservative": 0.0,
                "edge": "invalid_payoff",
                "note": "avg_win and avg_loss must be positive",
            }

        loss_rate = 1.0 - win_rate

        # Kelly formula: f = (p * b - q) / b
        # where p = win_rate, q = 1 - p, b = b/a (payoff ratio)
        # Simplified: f = (win_rate * avg_win - loss_rate * avg_loss) / avg_win

        numerator = (win_rate * avg_win_ticks) - (loss_rate * avg_loss_ticks)
        denominator = avg_win_ticks

        kelly_f = numerator / denominator

        # Kelly f should be positive for an edge; negative means no edge
        if kelly_f <= 0:
            return {
                "kelly_f": max(0.0, kelly_f),
                "kelly_f_conservative": 0.0,
                "kelly_pct": max(0.0, kelly_f * 100),
                "kelly_pct_conservative": 0.0,
                "edge": "no_positive_edge",
                "note": f"win_rate={win_rate:.2%}, avg_win={avg_win_ticks}, avg_loss={avg_loss_ticks}",
            }

        # Apply conservative scaling
        kelly_f_conservative = kelly_f * max_kelly_fraction
        kelly_f_conservative = max(kelly_f_conservative, min_kelly_fraction)

        # Payoff ratio for edge analysis
        payoff_ratio = avg_win_ticks / avg_loss_ticks

        return {
            "kelly_f": kelly_f,
            "kelly_f_conservative": kelly_f_conservative,
            "kelly_pct": kelly_f * 100,
            "kelly_pct_conservative": kelly_f_conservative * 100,
            "payoff_ratio": payoff_ratio,
            "edge": f"WR={win_rate:.1%}, RR={payoff_ratio:.2f}",
        }

    def calculate_from_equity_curve(
        self,
        trades_df: pd.DataFrame,
        max_kelly_fraction: float = 0.25,
        min_kelly_fraction: float = 0.01,
        min_trades: int = 10,
    ) -> Dict[str, Any]:
        """
        Calculate Kelly from a trades DataFrame.

        Parameters
        ----------
        trades_df       : DataFrame with 'profit_ticks' or 'pnl_ticks' column
        max_kelly_fraction : conservative scaling factor
        min_kelly_fraction : minimum to use
        min_trades      : minimum trades required

        Returns
        -------
        Dict with Kelly metrics
        """
        if len(trades_df) < min_trades:
            return {
                "kelly_f": 0.0,
                "kelly_f_conservative": 0.0,
                "note": f"fewer than {min_trades} trades",
            }

        # Find profit column
        profit_col = None
        for col in ["profit_ticks", "pnl_ticks", "profit_net"]:
            if col in trades_df.columns:
                profit_col = col
                break

        if profit_col is None:
            return {
                "kelly_f": 0.0,
                "note": "no profit column found",
            }

        profits = trades_df[profit_col].dropna()
        if len(profits) < min_trades:
            return {
                "kelly_f": 0.0,
                "note": f"fewer than {min_trades} trades with data",
            }

        # Calculate win rate and average win/loss
        wins = profits[profits > 0]
        losses = profits[profits < 0]

        if len(wins) == 0 or len(losses) == 0:
            return {
                "kelly_f": 0.0,
                "note": "no wins or no losses in sample",
            }

        win_rate = len(wins) / len(profits)
        avg_win = wins.mean()
        avg_loss = abs(losses.mean())

        return self.calculate(
            win_rate=win_rate,
            avg_win_ticks=avg_win,
            avg_loss_ticks=avg_loss,
            max_kelly_fraction=max_kelly_fraction,
            min_kelly_fraction=min_kelly_fraction,
        )


def compute_kelly_contracts(
    kelly_fraction: float,
    account_size: float,
    entry_price: float,
    stop_price: float,
    tick_size: float = 0.25,
    tick_value: float = 5.0,
    max_contracts: int = 50,
) -> Dict[str, Any]:
    """
    Compute position size from Kelly fraction.

    Parameters
    ----------
    kelly_fraction  : Kelly f (e.g., 0.1 for 10%)
    account_size    : total account equity
    entry_price     : entry level
    stop_price      : stop loss level
    tick_size       : contract tick size
    tick_value      : $ per tick per contract
    max_contracts   : cap on contracts

    Returns
    -------
    Dict with contracts, risk_dollars, etc.
    """
    if kelly_fraction <= 0:
        return {"contracts": 0, "kelly_fraction_used": 0.0}

    # Risk X% of account
    risk_amount = account_size * kelly_fraction

    # Calculate stop distance and contracts
    stop_distance_ticks = abs(entry_price - stop_price) / tick_size
    if stop_distance_ticks <= 0:
        return {"contracts": 0, "kelly_fraction_used": 0.0}

    dollars_at_risk_per_contract = stop_distance_ticks * tick_value
    contracts = risk_amount / dollars_at_risk_per_contract

    contracts = min(int(contracts), max_contracts)
    contracts = max(1, contracts)

    return {
        "contracts": contracts,
        "kelly_fraction_used": kelly_fraction,
        "kelly_pct_used": kelly_fraction * 100,
        "risk_amount_dollars": risk_amount,
        "stop_distance_ticks": stop_distance_ticks,
        "contracts_limited_by": "max_contracts" if contracts == max_contracts else "kelly",
    }


def volatility_adjusted_outcome_config(
    base_config: Dict[str, Any],
    atr_value: float,
    current_regime_volatility: Optional[float] = None,
    vix_level: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Adjust outcome (TP/SL) configuration based on current volatility.

    When volatility is high (high ATR), widen stops and targets.
    When volatility is low, tighten stops and targets.

    Parameters
    ----------
    base_config                 : base outcome config
    atr_value                   : current ATR
    current_regime_volatility   : regime average ATR (for scaling)
    vix_level                   : VIX level (optional, for stress adjustment)

    Returns
    -------
    Modified config with volatility-adjusted parameters
    """
    import copy
    cfg = copy.deepcopy(base_config)

    if current_regime_volatility is None or current_regime_volatility <= 0:
        return cfg

    # Volatility multiplier
    vol_mult = atr_value / current_regime_volatility

    # Adjust ticks-based outcomes
    if "ticks" in cfg and cfg["ticks"].get("enabled"):
        tick_cfg = cfg["ticks"]

        # Scale TP and SL by volatility
        if "take_profit" in tick_cfg and isinstance(tick_cfg["take_profit"], list):
            tick_cfg["take_profit"] = [int(tp * vol_mult) for tp in tick_cfg["take_profit"]]

        if "stop" in tick_cfg and isinstance(tick_cfg["stop"], list):
            tick_cfg["stop"] = [int(sl * vol_mult) for sl in tick_cfg["stop"]]

    # If VIX provided, apply stress multiplier
    if vix_level is not None and vix_level > 20:
        vix_stress = min(vix_level / 20.0, 2.0)  # Cap at 2x
        if "ticks" in cfg:
            if "take_profit" in cfg["ticks"] and isinstance(cfg["ticks"]["take_profit"], list):
                cfg["ticks"]["take_profit"] = [
                    int(tp * vix_stress) for tp in cfg["ticks"]["take_profit"]
                ]

    return cfg


# Example YAML configuration for advanced risk management:
EXAMPLE_YAML = """
outcome:
  volatility_sizing:
    enabled: true
    target_risk_pct: 1.0              # Risk 1% per trade
    account_size: 100000
    max_contracts: 20

  kelly_criterion:
    enabled: true
    max_kelly_fraction: 0.25          # Use 25% of Kelly (conservative)
    min_kelly_fraction: 0.01
    smoothing_window: 20              # Rolling 20-trade window

  ticks:
    enabled: true
    take_profit: [30, 60, 100]
    stop: [20, 30, 40]

  # Volatility adjustment
  volatility_regime:
    bull_atr: 25.0                    # Bull regime ATR
    bear_atr: 35.0                    # Bear regime ATR
    flat_atr: 15.0                    # Flat regime ATR

discovery:
  stages: 3
  stage1:
    name: "quick_scan"
    outcome_mode: "ticks"
  stage2:
    name: "dynamic_sizing"
    outcome_mode: "kelly_criterion"
  stage3:
    name: "final_validation"
    outcome_mode: "volatility_adjusted"
"""
