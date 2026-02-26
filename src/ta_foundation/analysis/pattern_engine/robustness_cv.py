from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def compute_purged_walkforward_cv(
    *,
    events_df: pd.DataFrame,
    options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    events_df: one row per signal per horizon, must include:
      - dt, horizon, ret_ticks
      - entity_type ("pattern"/"cluster"), entity_id
      - day_id (optional)
    """
    if events_df is None or len(events_df) == 0:
        empty = pd.DataFrame()
        return {"cv_fold_stats_df": empty, "oos_stats_df": empty}

    n_folds = int(options.get("n_folds", 5))
    n_folds = max(2, n_folds)

    # ensure dt sorted time series
    if "dt" not in events_df.columns:
        empty = pd.DataFrame()
        return {"cv_fold_stats_df": empty, "oos_stats_df": empty}

    df = events_df.sort_values("dt").reset_index(drop=True)

    # naive time-based folds (anchored). Extend with purge/embargo using horizon later.
    # split indices into n_folds contiguous test chunks
    idx = np.arange(len(df))
    fold_edges = np.array_split(idx, n_folds)

    fold_rows: List[Dict[str, Any]] = []

    for fold_id, test_idx in enumerate(fold_edges):
        if len(test_idx) == 0:
            continue

        test = df.loc[test_idx]
        # "train" = prior data only (anchored)
        train_end = test["dt"].min()
        train = df[df["dt"] < train_end]

        # per entity/horizon stats on test only
        g = test.groupby(["entity_type", "entity_id", "horizon"], sort=False)

        for (etype, eid, H), d2 in g:
            rets = d2["ret_ticks"].astype(float)
            n = int(len(rets))
            fold_rows.append(
                {
                    "entity_type": str(etype),
                    "entity_id": str(eid),
                    "fold_id": int(fold_id),
                    "train_start": train["dt"].min() if len(train) else pd.NaT,
                    "train_end": train["dt"].max() if len(train) else pd.NaT,
                    "test_start": d2["dt"].min(),
                    "test_end": d2["dt"].max(),
                    "horizon": int(H),
                    "test_n": n,
                    "test_net_ticks": float(rets.sum()),
                    "test_avg_ticks": float(rets.mean()) if n else 0.0,
                    "test_win_rate": float((rets > 0).mean()) if n else 0.0,
                    "test_p10": float(np.nanpercentile(rets, 10)) if n else np.nan,
                    "test_p90": float(np.nanpercentile(rets, 90)) if n else np.nan,
                    "sign_positive": bool(float(rets.mean()) > 0) if n else False,
                }
            )

    cv_fold_stats_df = pd.DataFrame(fold_rows)

    # collapse to OOS metrics
    if len(cv_fold_stats_df) == 0:
        empty = pd.DataFrame()
        return {"cv_fold_stats_df": empty, "oos_stats_df": empty}

    oos_rows: List[Dict[str, Any]] = []
    g2 = cv_fold_stats_df.groupby(["entity_type", "entity_id", "horizon"], sort=False)
    for (etype, eid, H), d2 in g2:
        n_total = int(d2["test_n"].sum())
        net = float(d2["test_net_ticks"].sum())
        avg = float(net / max(n_total, 1))
        win = float(np.average(d2["test_win_rate"], weights=np.maximum(d2["test_n"], 1)))

        fold_disp = float(d2["test_avg_ticks"].std(ddof=0)) if len(d2) > 1 else 0.0
        sign_cons = float(d2["sign_positive"].mean()) if len(d2) else 0.0

        # placeholders for regime dispersion
        regime_disp = float(options.get("regime_dispersion_default", 0.0))

        # stability score: penalize dispersion, require sign consistency
        stability = float(avg) - 0.5 * fold_disp
        stability = stability * sign_cons

        oos_rows.append(
            {
                "entity_type": str(etype),
                "entity_id": str(eid),
                "horizon": int(H),
                "oos_n": n_total,
                "oos_net_ticks": net,
                "oos_avg_ticks": avg,
                "oos_win_rate": win,
                "fold_dispersion": fold_disp,
                "sign_consistency": sign_cons,
                "regime_dispersion": regime_disp,
                "stability_oos_score": stability,
            }
        )

    oos_stats_df = pd.DataFrame(oos_rows)
    return {"cv_fold_stats_df": cv_fold_stats_df, "oos_stats_df": oos_stats_df}