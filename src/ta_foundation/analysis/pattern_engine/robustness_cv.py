# ta_foundation/analysis/pattern_engine/robustness_cv.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def _make_day_folds(days: List[pd.Timestamp], n_folds: int) -> List[Tuple[List[pd.Timestamp], List[pd.Timestamp]]]:
    """
    Simple walk-forward:
      fold i uses days[:split_i] train, days[split_i:split_{i+1}] test
    """
    days = list(days)
    if len(days) < max(n_folds, 2):
        n_folds = max(2, min(3, len(days)))
    cuts = np.linspace(0, len(days), n_folds + 1).astype(int)
    folds = []
    for i in range(1, len(cuts) - 0):
        train_end = cuts[i]
        test_end = cuts[i + 1] if i + 1 < len(cuts) else len(days)
        train = days[:train_end]
        test = days[train_end:test_end]
        if len(train) == 0 or len(test) == 0:
            continue
        folds.append((train, test))
    return folds


def compute_purged_walkforward_cv(
    *,
    events_df: pd.DataFrame,
    options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    events_df must include:
      entity_type, entity_id, day_id, horizon, ret_ticks
    """
    if events_df.empty:
        empty_fold = pd.DataFrame(columns=[
            "entity_type","entity_id","fold_id","train_start","train_end","test_start","test_end",
            "horizon","test_n","test_net_ticks","test_avg_ticks","test_win_rate","test_p10","test_p90","sign_positive"
        ])
        empty_oos = pd.DataFrame(columns=[
            "entity_type","entity_id","horizon","oos_n","oos_net_ticks","oos_avg_ticks","oos_win_rate",
            "fold_dispersion","sign_consistency","regime_dispersion","stability_oos_score"
        ])
        return {"cv_fold_stats_df": empty_fold, "oos_stats_df": empty_oos}

    n_folds = int(options.get("n_folds") or 5)
    day_col = str(options.get("day_col") or "day_id")

    df = events_df.copy()
    # normalize day_id to datetime64 (midnight) for ordering
    if not np.issubdtype(df[day_col].dtype, np.datetime64):
        df[day_col] = pd.to_datetime(df[day_col])

    days = sorted(df[day_col].dropna().unique().tolist())
    folds = _make_day_folds(days, n_folds=n_folds)

    fold_rows = []
    for fold_id, (train_days, test_days) in enumerate(folds, start=1):
        train_start, train_end = min(train_days), max(train_days)
        test_start, test_end = min(test_days), max(test_days)

        test_df = df[df[day_col].isin(test_days)].copy()
        g = test_df.groupby(["entity_type","entity_id","horizon"], sort=False)

        for (etype, eid, H), gdf in g:
            r = gdf["ret_ticks"].to_numpy(float)
            r = r[np.isfinite(r)]
            if len(r) == 0:
                continue
            fold_rows.append({
                "entity_type": etype,
                "entity_id": eid,
                "fold_id": int(fold_id),
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "horizon": int(H),
                "test_n": int(len(r)),
                "test_net_ticks": float(np.sum(r)),
                "test_avg_ticks": float(np.mean(r)),
                "test_win_rate": float(np.mean(r > 0)),
                "test_p10": float(np.percentile(r, 10)),
                "test_p90": float(np.percentile(r, 90)),
                "sign_positive": bool(np.mean(r) > 0),
            })

    cv_fold_stats_df = pd.DataFrame(fold_rows) if fold_rows else pd.DataFrame(columns=[
        "entity_type","entity_id","fold_id","train_start","train_end","test_start","test_end",
        "horizon","test_n","test_net_ticks","test_avg_ticks","test_win_rate","test_p10","test_p90","sign_positive"
    ])

    # Collapse into OOS stats per entity/horizon
    oos_rows = []
    if not cv_fold_stats_df.empty:
        for (etype, eid, H), gdf in cv_fold_stats_df.groupby(["entity_type","entity_id","horizon"], sort=False):
            avg = gdf["test_avg_ticks"].to_numpy(float)
            net = gdf["test_net_ticks"].to_numpy(float)
            n = gdf["test_n"].to_numpy(float)
            win = gdf["test_win_rate"].to_numpy(float)

            oos_n = int(np.sum(n))
            oos_net = float(np.sum(net))
            oos_avg = float(np.average(avg, weights=np.maximum(n, 1.0))) if len(avg) else np.nan
            oos_win = float(np.average(win, weights=np.maximum(n, 1.0))) if len(win) else np.nan

            fold_disp = float(np.nanstd(avg)) if len(avg) else np.nan
            sign_cons = float(np.mean(gdf["sign_positive"].to_numpy(bool))) if len(gdf) else np.nan

            # regime_dispersion placeholder: compute later if you include regime in fold stats
            regime_disp = np.nan

            # stability score: reward oos_avg and sign consistency, penalize dispersion
            stability = float(oos_avg * sign_cons) - 0.5 * float(fold_disp if np.isfinite(fold_disp) else 0.0)

            oos_rows.append({
                "entity_type": etype,
                "entity_id": eid,
                "horizon": int(H),
                "oos_n": oos_n,
                "oos_net_ticks": oos_net,
                "oos_avg_ticks": oos_avg,
                "oos_win_rate": oos_win,
                "fold_dispersion": fold_disp,
                "sign_consistency": sign_cons,
                "regime_dispersion": regime_disp,
                "stability_oos_score": stability,
            })

    oos_stats_df = pd.DataFrame(oos_rows) if oos_rows else pd.DataFrame(columns=[
        "entity_type","entity_id","horizon","oos_n","oos_net_ticks","oos_avg_ticks","oos_win_rate",
        "fold_dispersion","sign_consistency","regime_dispersion","stability_oos_score"
    ])

    return {
        "cv_fold_stats_df": cv_fold_stats_df,
        "oos_stats_df": oos_stats_df,
    }