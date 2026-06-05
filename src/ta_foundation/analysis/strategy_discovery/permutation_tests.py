from __future__ import annotations

"""
Permutation null tests for strategy-discovery candidates.

The null used here is a trade-level sign permutation: keep each realised
per-trade return magnitude and randomly assign its sign. This preserves the
candidate's trade-size / volatility distribution while destroying directional
edge. Under the null hypothesis that the strategy has no directional signal,
positive and negative labels are exchangeable at the trade level.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ta_foundation.analysis.strategy_discovery.evaluation import (
    compute_evaluation_metrics,
    compute_profit_factor,
)
from ta_foundation.analysis.strategy_discovery.risk_metrics import sharpe_ratio
from ta_foundation.analysis.strategy_discovery.validation import (
    DEFAULT_WF_CONFIG,
    extract_oos_pool,
)


MIN_OOS_TRADES = 30
SUPPORTED_METRICS = {"expectancy", "profit_factor", "sharpe"}


@dataclass(frozen=True)
class PermutationResult:
    metric: str
    observed: float
    p_value: float
    n: int
    null_mean: float
    null_std: float
    null_quantiles: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "metric": self.metric,
            "observed": _json_float(self.observed),
            "p_value": _json_float(self.p_value),
            "n": int(self.n),
            "null_mean": _json_float(self.null_mean),
            "null_std": _json_float(self.null_std),
            "null_quantiles": {
                str(k): _json_float(v) for k, v in self.null_quantiles.items()
            },
        }


def permutation_test_returns(
    returns: "pd.Series | np.ndarray",
    *,
    metric: str = "expectancy",
    n: int = 1000,
    seed: int = 17,
) -> PermutationResult:
    """
    Run a one-sided trade-level sign-permutation null test.

    Each null sample keeps the original absolute per-trade return magnitudes
    and randomly flips each sign with probability 0.5. That preserves the
    trade magnitude / volatility distribution while destroying any directional
    edge. This is the standard label/sign-flip null for trade P&L vectors:
    if the signal has no true directional information, the realised sign labels
    are exchangeable conditional on the magnitudes.

    The empirical p-value is one-sided upper-tail:
    ``(# null_metric >= observed_metric + 1) / (n + 1)``.
    Supported metrics are ``expectancy``, ``profit_factor``, and ``sharpe``.
    """
    metric_name = str(metric).lower()
    if metric_name not in SUPPORTED_METRICS:
        raise ValueError(
            f"unsupported metric '{metric}'; expected one of {sorted(SUPPORTED_METRICS)}"
        )
    if n < 1:
        raise ValueError("n must be >= 1")

    arr = _coerce_returns(returns)
    if arr.size == 0:
        raise ValueError("returns must contain at least one finite value")

    observed = _metric_value(arr, metric_name)
    magnitudes = np.abs(arr)
    rng = np.random.default_rng(seed)

    null_values = np.empty(int(n), dtype=float)
    for i in range(int(n)):
        signs = rng.choice(np.array([-1.0, 1.0]), size=magnitudes.size)
        null_values[i] = _metric_value(magnitudes * signs, metric_name)

    p_value = float((np.sum(null_values >= observed) + 1.0) / (int(n) + 1.0))
    finite_null = null_values[np.isfinite(null_values)]
    if finite_null.size == 0:
        null_mean = float("inf")
        null_std = float("nan")
        quantiles = {"0.5": float("inf"), "0.95": float("inf"), "0.99": float("inf")}
    else:
        null_mean = float(np.mean(finite_null))
        null_std = float(np.std(finite_null, ddof=1)) if finite_null.size > 1 else 0.0
        quantiles = {
            "0.5": float(np.quantile(finite_null, 0.50)),
            "0.95": float(np.quantile(finite_null, 0.95)),
            "0.99": float(np.quantile(finite_null, 0.99)),
        }

    return PermutationResult(
        metric=metric_name,
        observed=float(observed),
        p_value=p_value,
        n=int(n),
        null_mean=null_mean,
        null_std=null_std,
        null_quantiles=quantiles,
    )


def permutation_test_for_discovery(
    sd: dict[str, Any],
    *,
    n: int = 1000,
    seed: int = 17,
    metrics: tuple[str, ...] = ("expectancy", "profit_factor"),
) -> dict[str, Any]:
    """
    Run permutation null tests on a strategy-discovery result's OOS returns.

    The function prefers explicitly supplied OOS trades/returns in ``sd``. If
    only full cost-normalized trades are available, it uses
    ``validation.extract_oos_pool`` with the strategy's walk-forward config so
    the tested slice matches ranking's OOS evaluation convention. It returns
    ``{"status": "insufficient_trades"}`` when fewer than 30 OOS returns are
    available.
    """
    returns = _extract_oos_returns(sd)
    if returns.size < MIN_OOS_TRADES:
        return {
            "status": "insufficient_trades",
            "n_oos_trades": int(returns.size),
            "min_oos_trades": MIN_OOS_TRADES,
        }

    out: dict[str, Any] = {}
    for metric_name in metrics:
        result = permutation_test_returns(
            returns,
            metric=metric_name,
            n=n,
            seed=seed,
        )
        out[result.metric] = result.to_dict()
    return out


def _coerce_returns(returns: "pd.Series | np.ndarray | list[Any]") -> np.ndarray:
    series = pd.Series(returns)
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    return values[np.isfinite(values)]


def _metric_value(returns: np.ndarray, metric: str) -> float:
    series = pd.Series(returns, dtype=float)
    if metric == "expectancy":
        metrics = compute_evaluation_metrics(pd.DataFrame({"profit_net": series}))
        value = metrics.get("expectancy")
        return _finite_or(value, 0.0)

    if metric == "profit_factor":
        pf = compute_profit_factor(series)
        if pf is not None:
            return float(pf)
        gross_profit = float(series[series > 0].sum())
        gross_loss = abs(float(series[series < 0].sum()))
        if gross_loss == 0.0 and gross_profit > 0.0:
            return float("inf")
        return 0.0

    if metric == "sharpe":
        sr = sharpe_ratio(series.to_numpy(dtype=float), rf_per_trade=0.0, ann_factor=1.0)
        return _finite_or(sr, 0.0)

    raise ValueError(f"unsupported metric '{metric}'")


def _extract_oos_returns(sd: dict[str, Any]) -> np.ndarray:
    direct_keys = ("oos_returns", "oos_trade_returns", "returns_oos")
    for key in direct_keys:
        if key in sd:
            return _coerce_returns(sd[key])

    oos_trade_keys = ("oos_trades", "trades_oos", "oos_pool", "cost_normalized_oos_trades")
    for key in oos_trade_keys:
        returns = _returns_from_trade_like(sd.get(key))
        if returns.size:
            return returns

    full_trade_candidates = [
        sd.get("cost_normalized_trades"),
        sd.get("trades"),
        (sd.get("validation") or {}).get("cost_normalized"),
    ]
    wf_config = _resolve_wf_config(sd)
    for candidate in full_trade_candidates:
        df = _to_dataframe(candidate)
        if df is None or len(df) == 0:
            continue
        oos = extract_oos_pool(df, wf_config=wf_config)
        returns = _returns_from_dataframe(oos)
        if returns.size:
            return returns

    return np.array([], dtype=float)


def _returns_from_trade_like(value: Any) -> np.ndarray:
    df = _to_dataframe(value)
    if df is None:
        return np.array([], dtype=float)
    return _returns_from_dataframe(df)


def _to_dataframe(value: Any) -> pd.DataFrame | None:
    if value is None:
        return None
    if isinstance(value, pd.DataFrame):
        return value
    if isinstance(value, list):
        try:
            return pd.DataFrame(value)
        except Exception:
            return None
    return None


def _returns_from_dataframe(df: pd.DataFrame) -> np.ndarray:
    for col in ("profit_net", "return", "returns", "profit", "pnl"):
        if col in df.columns:
            return _coerce_returns(df[col])
    return np.array([], dtype=float)


def _resolve_wf_config(sd: dict[str, Any]) -> dict[str, Any]:
    raw = (
        sd.get("wf_config")
        or sd.get("walk_forward")
        or (sd.get("validation") or {}).get("wf_config")
        or {}
    )
    return {**DEFAULT_WF_CONFIG, **raw}


def _finite_or(value: Any, default: float) -> float:
    try:
        f = float(value)
        return f if np.isfinite(f) else default
    except Exception:
        return default


def _json_float(value: Any) -> float | None:
    try:
        f = float(value)
        return f if np.isfinite(f) else None
    except Exception:
        return None
