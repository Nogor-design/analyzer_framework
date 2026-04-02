from __future__ import annotations

"""
Walk-Forward IS/OOS Validation
================================
Computes in-sample vs out-of-sample profit-factor degradation for a set
of trades produced by a single parameter combination.

The split is purely chronological (no randomisation) to respect the
time-series nature of trading data.

  IS  = first `is_fraction` of trades (sorted by entry_time)
  OOS = remaining trades

Degradation formula:
  degradation = (pf_is - pf_oos) / pf_is

  0.0  = identical performance (no degradation)
  0.3  = OOS PF is 30% below IS PF
  1.0  = OOS PF is zero (total breakdown)
  > 1.0 = OOS PF is negative (rare, indicates mean-reversion in OOS period)

Returns None when there are insufficient trades in either half to compute
a reliable PF (< min_trades_per_half in either window).
"""

from typing import Optional, Tuple

import numpy as np
import pandas as pd


DEFAULT_VALIDATION_CONFIG = {
    "is_fraction":        0.70,   # IS = first 70% of trades
    "min_trades_per_half": 10,    # require at least this many in each half
}


def _pf_from_trades(trades: pd.DataFrame, profit_col: str = "profit_net") -> Optional[float]:
    """Compute profit factor from a trades slice."""
    profits = pd.to_numeric(trades[profit_col], errors="coerce").dropna()
    if len(profits) == 0:
        return None
    gross_profit = float(profits[profits > 0].sum())
    gross_loss   = abs(float(profits[profits < 0].sum()))
    if gross_loss == 0:
        return None  # can't divide, avoid infinite PF (likely insufficient data)
    return round(gross_profit / gross_loss, 4)


def compute_is_oos_degradation(
    trades_df: pd.DataFrame,
    profit_col: str = "profit_net",
    is_fraction: float = 0.70,
    min_trades_per_half: int = 10,
) -> Optional[float]:
    """
    Compute chronological IS/OOS profit-factor degradation.

    Parameters
    ----------
    trades_df           : trades produced by a single combo (must have entry_time, profit_col)
    profit_col          : column name for net profit per trade
    is_fraction         : fraction of trades to use as in-sample  default 0.70
    min_trades_per_half : minimum trades required in each half     default 10

    Returns
    -------
    float  — degradation in [0, ∞) where 0 = no degradation, None = insufficient data
    """
    if trades_df is None or len(trades_df) < min_trades_per_half * 2:
        return None

    if "entry_time" not in trades_df.columns:
        return None

    sorted_t  = trades_df.sort_values("entry_time").reset_index(drop=True)
    split_idx = int(len(sorted_t) * is_fraction)

    if split_idx < min_trades_per_half or (len(sorted_t) - split_idx) < min_trades_per_half:
        return None

    is_trades  = sorted_t.iloc[:split_idx]
    oos_trades = sorted_t.iloc[split_idx:]

    pf_is  = _pf_from_trades(is_trades,  profit_col)
    pf_oos = _pf_from_trades(oos_trades, profit_col)

    if pf_is is None or pf_oos is None or pf_is <= 0:
        return None

    degradation = (pf_is - pf_oos) / pf_is
    return round(float(degradation), 4)


def compute_is_oos_full(
    trades_df: pd.DataFrame,
    profit_col: str = "profit_net",
    is_fraction: float = 0.70,
    min_trades_per_half: int = 10,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Returns (pf_is, pf_oos, degradation) — useful for diagnostics.
    Any element may be None if insufficient data.
    """
    if trades_df is None or len(trades_df) < min_trades_per_half * 2:
        return None, None, None

    if "entry_time" not in trades_df.columns:
        return None, None, None

    sorted_t  = trades_df.sort_values("entry_time").reset_index(drop=True)
    split_idx = int(len(sorted_t) * is_fraction)

    if split_idx < min_trades_per_half or (len(sorted_t) - split_idx) < min_trades_per_half:
        return None, None, None

    pf_is  = _pf_from_trades(sorted_t.iloc[:split_idx],  profit_col)
    pf_oos = _pf_from_trades(sorted_t.iloc[split_idx:],  profit_col)

    if pf_is is None or pf_oos is None or pf_is <= 0:
        return pf_is, pf_oos, None

    return pf_is, pf_oos, round((pf_is - pf_oos) / pf_is, 4)
