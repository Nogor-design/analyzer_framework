from __future__ import annotations

import numpy as np
import pandas as pd


def _pct(s: pd.Series, q: float) -> float:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float(s.quantile(q))


def build_summary_by_anchor(segments: pd.DataFrame, path_stats: pd.DataFrame, *, sample_floor: int) -> pd.DataFrame:
    if segments.empty or path_stats.empty:
        return pd.DataFrame()

    df = segments.merge(path_stats, on="segment_id", how="left")

    rows = []
    for anchor_id, g in df.groupby("anchor_id", dropna=False):
        n = len(g)
        rows.append({
            "anchor_id": anchor_id,
            "n_segments": int(n),
            "n_uncensored": int((~g["censored"]).sum()),
            "pct_censored": float(g["censored"].mean()) if n else np.nan,
            "immediate_failure_rate": float(g["immediate_failure"].mean()) if n else np.nan,
            "median_mfe_atr": _pct(g["mfe_atr"], 0.50),
            "median_mae_atr": _pct(g["mae_atr"], 0.50),
            "median_etd_ratio": _pct(g["etd_ratio"], 0.50),
            "median_minutes_held": _pct(g["minutes_held"], 0.50),
            "median_path_efficiency": _pct(g["path_efficiency"], 0.50),
            "mfe_atr_p10": _pct(g["mfe_atr"], 0.10),
            "mfe_atr_p25": _pct(g["mfe_atr"], 0.25),
            "mfe_atr_p50": _pct(g["mfe_atr"], 0.50),
            "mfe_atr_p75": _pct(g["mfe_atr"], 0.75),
            "mfe_atr_p90": _pct(g["mfe_atr"], 0.90),
            "mae_atr_p10": _pct(g["mae_atr"], 0.10),
            "mae_atr_p25": _pct(g["mae_atr"], 0.25),
            "mae_atr_p50": _pct(g["mae_atr"], 0.50),
            "mae_atr_p75": _pct(g["mae_atr"], 0.75),
            "mae_atr_p90": _pct(g["mae_atr"], 0.90),
            "etd_ratio_p10": _pct(g["etd_ratio"], 0.10),
            "etd_ratio_p25": _pct(g["etd_ratio"], 0.25),
            "etd_ratio_p50": _pct(g["etd_ratio"], 0.50),
            "etd_ratio_p75": _pct(g["etd_ratio"], 0.75),
            "etd_ratio_p90": _pct(g["etd_ratio"], 0.90),
            "minutes_held_p10": _pct(g["minutes_held"], 0.10),
            "minutes_held_p25": _pct(g["minutes_held"], 0.25),
            "minutes_held_p50": _pct(g["minutes_held"], 0.50),
            "minutes_held_p75": _pct(g["minutes_held"], 0.75),
            "minutes_held_p90": _pct(g["minutes_held"], 0.90),
            "meets_sample_floor": bool(n >= sample_floor),
        })

    return pd.DataFrame(rows).sort_values(["n_segments", "anchor_id"], ascending=[False, True]).reset_index(drop=True)


def build_summary_by_anchor_regime(
    segments: pd.DataFrame,
    path_stats: pd.DataFrame,
    *,
    regime_sample_floor: int,
) -> pd.DataFrame:
    if segments.empty or path_stats.empty:
        return pd.DataFrame()

    df = segments.merge(path_stats, on="segment_id", how="left")
    trend = df.get("trend_regime_at_entry")
    vol = df.get("vol_regime_at_entry")
    df["trend_regime_at_entry"] = trend.fillna("unknown") if trend is not None else "unknown"
    df["vol_regime_at_entry"] = vol.fillna("unknown") if vol is not None else "unknown"

    rows = []
    grouped = df.groupby(["anchor_id", "trend_regime_at_entry", "vol_regime_at_entry"], dropna=False)
    for (anchor_id, trend_regime, vol_regime), g in grouped:
        n = len(g)
        rows.append({
            "anchor_id": anchor_id,
            "trend_regime_at_entry": trend_regime,
            "vol_regime_at_entry": vol_regime,
            "n_segments": int(n),
            "median_mfe_atr": _pct(g["mfe_atr"], 0.50),
            "median_mae_atr": _pct(g["mae_atr"], 0.50),
            "median_etd_ratio": _pct(g["etd_ratio"], 0.50),
            "minutes_held_p50": _pct(g["minutes_held"], 0.50),
            "meets_sample_floor": bool(n >= regime_sample_floor),
        })

    return pd.DataFrame(rows).sort_values(
        ["anchor_id", "trend_regime_at_entry", "vol_regime_at_entry"]
    ).reset_index(drop=True)