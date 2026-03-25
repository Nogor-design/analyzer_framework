from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple, Optional

import numpy as np
import pandas as pd


def _true_range(df: pd.DataFrame) -> pd.Series:
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr1 = high - low
    tr2 = np.abs(high - prev_close)
    tr3 = np.abs(low - prev_close)
    return pd.Series(np.maximum(tr1, np.maximum(tr2, tr3)), index=df.index)


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = _true_range(df)
    return tr.rolling(period, min_periods=period).mean()


def _ensure_day_session(bars: pd.DataFrame) -> pd.DataFrame:
    bars = bars.copy()
    if "dt" not in bars.columns:
        raise ValueError("bars must contain dt")
    bars["dt"] = pd.to_datetime(bars["dt"])
    if "day_id" not in bars.columns:
        bars["day_id"] = bars["dt"].dt.date
    if "session_id" not in bars.columns:
        # Minimal session identity: per-day bucket. Upgrade later to true session segmentation.
        bars["session_id"] = bars["day_id"].astype(str)
    return bars


def _indexer_by_dt(bars: pd.DataFrame) -> pd.Index:
    return pd.Index(pd.to_datetime(bars["dt"]))


def _safe_ir(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if x.size < 3:
        return float("nan")
    mu = float(np.mean(x))
    sd = float(np.std(x, ddof=1))
    if sd <= 1e-12:
        return float("nan")
    return mu / sd


def compute_market_discovery(
    *,
    bars: pd.DataFrame,
    signals_df: pd.DataFrame,
    options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    Market-derived discovery metrics ONLY (no backtest PnL, no trade exits).

    Inputs:
      - bars: dt/open/high/low/close/volume (+ optional day_id/session_id)
      - signals_df: must include dt, pattern_id, direction (±1), signal_id (optional)

    Outputs:
      - discovery_events_df: per-signal per-horizon forward outcomes (ticks, ATR adj, MFE/MAE)
      - discovery_stats_df: aggregated per pattern/horizon
      - discovery_regime_stats_df: per pattern/horizon/vol_regime
      - discovery_stability_df: simple fold stability + sign consistency
    """
    tick_size = float(options.get("tick_size", 0.25))
    horizons = [int(x) for x in (options.get("horizons") or [])]
    if not horizons:
        horizons = [10, 20, 40]

    atr_period = int((options.get("discovery") or {}).get("atr_period", 14))
    n_folds = int((options.get("cv") or {}).get("n_folds", 5))
    n_regimes = int((options.get("discovery") or {}).get("n_regimes", 4))

    bars = _ensure_day_session(bars)
    bars = bars.sort_values("dt").reset_index(drop=True)
    # print(bars)
    for c in ("open", "high", "low", "close"):
        if c not in bars.columns:
            raise ValueError(f"bars missing required column: {c}")

    dt_idx = _indexer_by_dt(bars)

    atr = _compute_atr(bars, period=atr_period)
    # if ATR not available at beginning, that's okay; those events will be NaN for atr_adj
    close = bars["close"].astype(float).values
    high = bars["high"].astype(float).values
    low = bars["low"].astype(float).values

    if signals_df is None or not isinstance(signals_df, pd.DataFrame) or signals_df.empty:
        return {
            "discovery_events_df": pd.DataFrame(),
            "discovery_stats_df": pd.DataFrame(),
            "discovery_regime_stats_df": pd.DataFrame(),
            "discovery_stability_df": pd.DataFrame(),
        }


    sig = signals_df.copy()
    sig["dt"] = pd.to_datetime(sig["dt"])
    if "direction" not in sig.columns:
        raise ValueError("signals_df must contain direction")
    if "pattern_id" not in sig.columns:
        raise ValueError("signals_df must contain pattern_id")
    if "signal_id" not in sig.columns:
        sig["signal_id"] = np.arange(len(sig), dtype=int)

    # Map signal dt -> bar index
    ix = dt_idx.get_indexer(sig["dt"].values)
    sig["bar_i"] = ix
    sig = sig[sig["bar_i"] >= 0].copy()
    if sig.empty:
        empty = pd.DataFrame()
        return {
            "discovery_events_df": empty,
            "discovery_stats_df": empty,
            "discovery_regime_stats_df": empty,
            "discovery_stability_df": empty,
        }

    # Attach day/session from bars
    sig["day_id"] = bars.loc[sig["bar_i"].values, "day_id"].values
    sig["session_id"] = bars.loc[sig["bar_i"].values, "session_id"].values
    sig["atr"] = atr.loc[sig["bar_i"].values].values

    # Volatility regime by ATR quantile (computed on signal rows to avoid session bias)
    atr_vals = sig["atr"].astype(float).values
    finite_atr = atr_vals[np.isfinite(atr_vals)]
    if finite_atr.size >= max(20, n_regimes * 5):
        qs = np.quantile(finite_atr, np.linspace(0, 1, n_regimes + 1))
        # make bins unique
        qs = np.unique(qs)
        if qs.size >= 3:
            sig["vol_regime"] = np.digitize(atr_vals, qs[1:-1], right=True)
        else:
            sig["vol_regime"] = 0
    else:
        sig["vol_regime"] = 0

    # Fold id by day (purged WF later; for discovery we keep it simple & stable)
    days = pd.Series(sig["day_id"].astype(str).values)
    uniq_days = days.drop_duplicates().tolist()
    day_to_fold = {d: (i % max(1, n_folds)) for i, d in enumerate(uniq_days)}
    sig["fold_id"] = days.map(day_to_fold).astype(int).values

    # Build per-signal per-horizon events
    events = []
    for h in horizons:
        i0 = sig["bar_i"].values.astype(int)
        i1 = i0 + h
        ok = i1 < len(bars)
        if not np.any(ok):
            continue

        i0_ok = i0[ok]
        i1_ok = i1[ok]
        dir_ok = sig.loc[ok, "direction"].astype(int).values
        entry = close[i0_ok]
        exitc = close[i1_ok]
        ret_price_signed = (exitc - entry) * dir_ok
        ret_ticks_signed = ret_price_signed / tick_size

        # MFE/MAE over window [i0, i1]
        mfe_ticks = np.full_like(ret_ticks_signed, np.nan, dtype=float)
        mae_ticks = np.full_like(ret_ticks_signed, np.nan, dtype=float)
        for k in range(len(i0_ok)):
            a = int(i0_ok[k])
            b = int(i1_ok[k])
            wh = float(np.max(high[a : b + 1]))
            wl = float(np.min(low[a : b + 1]))
            if dir_ok[k] > 0:
                mfe = wh - entry[k]
                mae = entry[k] - wl
            else:
                mfe = entry[k] - wl
                mae = wh - entry[k]
            mfe_ticks[k] = mfe / tick_size
            mae_ticks[k] = mae / tick_size

        atr0 = sig.loc[ok, "atr"].astype(float).values
        atr_ticks = atr0 / tick_size
        atr_adj_ret = np.where(np.isfinite(atr_ticks) & (atr_ticks > 1e-9), ret_ticks_signed / atr_ticks, np.nan)

        # Probabilities relative to ATR
        p_up_1atr = np.where(np.isfinite(atr_ticks), ret_ticks_signed >= atr_ticks, False)
        p_dn_1atr = np.where(np.isfinite(atr_ticks), ret_ticks_signed <= -atr_ticks, False)

        part = sig.loc[ok, ["signal_id", "pattern_id", "dt", "day_id", "session_id", "direction", "vol_regime", "fold_id"]].copy()
        part["horizon"] = int(h)
        part["ret_ticks"] = ret_ticks_signed
        part["atr_ticks"] = atr_ticks
        part["atr_adj_ret"] = atr_adj_ret
        part["mfe_ticks"] = mfe_ticks
        part["mae_ticks"] = mae_ticks
        part["hit_pos_1atr"] = p_up_1atr.astype(int)
        part["hit_neg_1atr"] = p_dn_1atr.astype(int)
        events.append(part)

    discovery_events_df = pd.concat(events, ignore_index=True) if events else pd.DataFrame()

    if discovery_events_df.empty:
        empty = pd.DataFrame()
        return {
            "discovery_events_df": empty,
            "discovery_stats_df": empty,
            "discovery_regime_stats_df": empty,
            "discovery_stability_df": empty,
        }

    # Aggregate stats per pattern/horizon
    g = discovery_events_df.groupby(["pattern_id", "horizon"], sort=False)
    rows = []
    for (pid, h), d in g:
        x = d["ret_ticks"].astype(float).values
        x = x[np.isfinite(x)]
        if x.size == 0:
            continue
        rows.append(
            {
                "pattern_id": pid,
                "horizon": int(h),
                "n_signals": int(len(d)),
                "avg_ticks": float(np.mean(x)),
                "win_rate": float(np.mean(x > 0)),
                "p10": float(np.quantile(x, 0.10)),
                "p50": float(np.quantile(x, 0.50)),
                "p90": float(np.quantile(x, 0.90)),
                "ir_ticks": _safe_ir(x),
                "p_hit_pos_1atr": float(np.mean(d["hit_pos_1atr"].astype(int).values)),
                "p_hit_neg_1atr": float(np.mean(d["hit_neg_1atr"].astype(int).values)),
                "avg_atr_adj_ret": float(np.nanmean(d["atr_adj_ret"].astype(float).values)),
                "ir_atr_adj_ret": _safe_ir(d["atr_adj_ret"].astype(float).values),
                "avg_mfe_ticks": float(np.nanmean(d["mfe_ticks"].astype(float).values)),
                "avg_mae_ticks": float(np.nanmean(d["mae_ticks"].astype(float).values)),
            }
        )
    discovery_stats_df = pd.DataFrame(rows)

    # Regime stats by vol_regime
    rg = discovery_events_df.groupby(["pattern_id", "horizon", "vol_regime"], sort=False)
    rrows = []
    for (pid, h, r), d in rg:
        x = d["ret_ticks"].astype(float).values
        x = x[np.isfinite(x)]
        if x.size == 0:
            continue
        rrows.append(
            {
                "pattern_id": pid,
                "horizon": int(h),
                "vol_regime": int(r),
                "n": int(len(d)),
                "avg_ticks": float(np.mean(x)),
                "win_rate": float(np.mean(x > 0)),
                "ir_ticks": _safe_ir(x),
                "avg_atr_adj_ret": float(np.nanmean(d["atr_adj_ret"].astype(float).values)),
            }
        )
    discovery_regime_stats_df = pd.DataFrame(rrows)

    # Stability: fold sign consistency + fold dispersion
    fg = discovery_events_df.groupby(["pattern_id", "horizon", "fold_id"], sort=False)
    frows = []
    for (pid, h, f), d in fg:
        x = d["ret_ticks"].astype(float).values
        x = x[np.isfinite(x)]
        if x.size == 0:
            continue
        frows.append({"pattern_id": pid, "horizon": int(h), "fold_id": int(f), "avg_ticks": float(np.mean(x)), "n": int(len(d))})
    fold_df = pd.DataFrame(frows)

    srows = []
    if not fold_df.empty:
        sg = fold_df.groupby(["pattern_id", "horizon"], sort=False)
        for (pid, h), d in sg:
            av = d["avg_ticks"].astype(float).values
            if av.size < 2:
                continue
            sign_consistency = float(np.mean(np.sign(av) == np.sign(np.nanmean(av))))
            fold_dispersion = float(np.nanstd(av, ddof=1)) if av.size > 2 else float(np.nan)
            srows.append(
                {
                    "pattern_id": pid,
                    "horizon": int(h),
                    "n_folds": int(d["fold_id"].nunique()),
                    "sign_consistency": sign_consistency,
                    "fold_dispersion": fold_dispersion,
                }
            )
    # print(pd.DataFrame(srows))
    discovery_stability_df = pd.DataFrame(srows)

    return {
        "discovery_events_df": discovery_events_df,
        "discovery_stats_df": discovery_stats_df,
        "discovery_regime_stats_df": discovery_regime_stats_df,
        "discovery_stability_df": discovery_stability_df,
    }