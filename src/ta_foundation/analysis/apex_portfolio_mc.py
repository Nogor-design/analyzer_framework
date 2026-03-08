from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Union

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ApexPortfolioParams:
    starting_balance: float
    trailing_drawdown: float
    lock_profit: float


SamplingMode = Literal["iid", "correlated"]


def _simulate_path(daily_pnl: np.ndarray, apex: ApexPortfolioParams) -> Dict[str, float]:
    """
    Apply Apex-style trailing drawdown on a daily PnL path.

    Returns:
      final_balance
      min_buffer (min(balance - trail_floor))
      breach_day (1-indexed day where buffer first < 0, NaN if never)
    """
    balance = float(apex.starting_balance)
    peak = balance
    trail_floor = balance - float(apex.trailing_drawdown)
    locked = False

    min_buffer = float("inf")
    breach_day: Optional[int] = None

    for i, pnl in enumerate(daily_pnl):
        balance += float(pnl)

        if not locked:
            if balance - apex.starting_balance >= float(apex.lock_profit):
                locked = True
                trail_floor = apex.starting_balance + float(apex.lock_profit) - float(apex.trailing_drawdown)

        peak = max(peak, balance)

        if not locked:
            trail_floor = peak - float(apex.trailing_drawdown)

        buffer = balance - trail_floor
        min_buffer = min(min_buffer, buffer)

        if buffer < 0 and breach_day is None:
            breach_day = i + 1  # 1-indexed

    return {
        "final_balance": float(balance),
        "min_buffer": float(min_buffer),
        "breach_day": float(breach_day) if breach_day is not None else float("nan"),
    }


def run_portfolio_monte_carlo(
    daily: Union[pd.Series, pd.DataFrame],
    apex: ApexPortfolioParams,
    *,
    n_paths: int = 2000,
    seed: int = 42,
    mode: SamplingMode = "iid",
) -> Dict[str, Any]:
    """
    Portfolio MC across bots.

    Inputs:
      - mode="iid":
          daily must be a pd.Series of portfolio daily PnL (index = dates).
          We shuffle portfolio daily pnl values (IID days).
      - mode="correlated":
          daily must be a pd.DataFrame of per-bot daily PnL (index = dates, columns = bots).
          We shuffle the DAY ORDER (same permutation applied to all bots),
          then sum across bots per day. This preserves cross-bot correlation structure.

    Deterministic via RNG seed.

    Returns JSON-safe summary.
    """
    if isinstance(daily, pd.Series):
        if daily.empty:
            return {"ok": False, "reason": "No daily PnL data", "mode": mode}
        if mode != "iid":
            return {"ok": False, "reason": f"mode='{mode}' requires DataFrame daily bot matrix", "mode": mode}

        values = daily.astype(float).values
        n_days = int(len(values))

        rng = np.random.default_rng(int(seed))
        results = []
        for _ in range(int(n_paths)):
            sampled = rng.permutation(values)
            results.append(_simulate_path(sampled, apex))

    else:
        # DataFrame case (correlated)
        if daily.empty:
            return {"ok": False, "reason": "No daily bot matrix data", "mode": mode}
        if mode != "correlated":
            # Allow iid on DataFrame too: sum first then shuffle portfolio.
            summed = daily.fillna(0.0).sum(axis=1)
            return run_portfolio_monte_carlo(
                summed,
                apex,
                n_paths=n_paths,
                seed=seed,
                mode="iid",
            )

        mat = daily.fillna(0.0).astype(float)
        n_days = int(len(mat))
        arr = mat.values  # shape (n_days, n_bots)

        rng = np.random.default_rng(int(seed))
        results = []
        idx = np.arange(n_days, dtype=int)

        for _ in range(int(n_paths)):
            perm = rng.permutation(idx)
            # same perm applied to all bots => correlation preserved across bots
            sampled_portfolio = arr[perm, :].sum(axis=1)  # shape (n_days,)
            results.append(_simulate_path(sampled_portfolio, apex))

    df = pd.DataFrame(results)

    survival_prob = float((df["min_buffer"] >= 0).mean())
    # p10 of min_buffer distribution (10th percentile) = conservative tail
    min_buffer_p10 = float(np.percentile(df["min_buffer"].values, 10))
    expected_final_balance = float(df["final_balance"].mean())

    breach_days = df["breach_day"].dropna()
    breach_days = breach_days[~np.isnan(breach_days.values)]
    breach_day_p90 = float(np.percentile(breach_days.values, 90)) if len(breach_days) else None

    return {
        "ok": True,
        "mode": mode,
        "n_paths": int(n_paths),
        "n_days": int(n_days),
        "survival_probability": survival_prob,
        "min_buffer_p10": min_buffer_p10,
        "expected_final_balance": expected_final_balance,
        "breach_day_p90": breach_day_p90,
    }