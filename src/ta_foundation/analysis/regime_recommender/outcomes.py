from __future__ import annotations

from typing import Any, Dict

import pandas as pd


DENVER_TZ = "America/Denver"


def _safe_num_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def summarize_trade_outcomes(trades: pd.DataFrame, baseline: Dict[str, Any] | None = None) -> Dict[str, Any]:
    baseline = baseline or {}
    if trades is None or trades.empty:
        return {
            "trades_count": 0,
            "net_pnl": 0.0,
            "max_drawdown": 0.0,
            "mae_p50": 0.0,
            "mae_p95": 0.0,
            "mfe_p50": 0.0,
            "etd_mean": 0.0,
            "baseline_net_pnl": float(baseline.get("net_pnl", 0.0) or 0.0),
            "baseline_max_drawdown": float(baseline.get("max_drawdown", 0.0) or 0.0),
        }

    pnl = _safe_num_series(trades.get("profit", pd.Series(dtype=float))).fillna(0.0)
    mae = _safe_num_series(trades.get("mae", pd.Series(dtype=float))).abs().fillna(0.0)
    mfe = _safe_num_series(trades.get("mfe", pd.Series(dtype=float))).abs().fillna(0.0)

    equity = pnl.cumsum()
    running_peak = equity.cummax()
    drawdown = equity - running_peak

    etd = (mfe - pnl).fillna(0.0)

    return {
        "trades_count": int(len(trades)),
        "net_pnl": float(pnl.sum()),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "mae_p50": float(mae.quantile(0.5)) if len(mae) else 0.0,
        "mae_p95": float(mae.quantile(0.95)) if len(mae) else 0.0,
        "mfe_p50": float(mfe.quantile(0.5)) if len(mfe) else 0.0,
        "etd_mean": float(etd.mean()) if len(etd) else 0.0,
        "baseline_net_pnl": float(baseline.get("net_pnl", 0.0) or 0.0),
        "baseline_max_drawdown": float(baseline.get("max_drawdown", 0.0) or 0.0),
    }


def attach_outcome_snapshot(pkg, baseline: Dict[str, Any] | None = None) -> Dict[str, Any]:
    md = getattr(pkg, "metadata", None)
    if md is None:
        pkg.metadata = {}
        md = pkg.metadata
    if md.get("derived") is None:
        md["derived"] = {}

    rr = md["derived"].get("regime_recommender") or {}
    trades = getattr(pkg, "trades", None)
    summary = summarize_trade_outcomes(trades=trades, baseline=baseline)

    rr["outcomes"] = {
        "captured_at": pd.Timestamp.now(tz=DENVER_TZ).isoformat(),
        "summary": summary,
    }
    md["derived"]["regime_recommender"] = rr
    return rr["outcomes"]
