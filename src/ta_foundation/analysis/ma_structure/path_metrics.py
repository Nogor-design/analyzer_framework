from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def compute_path_metrics(
    bars: pd.DataFrame,
    anchors_df: pd.DataFrame,
    segments: pd.DataFrame,
) -> pd.DataFrame:
    if segments.empty:
        return pd.DataFrame()

    bars = bars.reset_index(drop=True).copy()
    anchors_df = anchors_df.reset_index(drop=True).copy()

    close = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    atr14 = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14, min_periods=14).mean()

    rows: List[Dict] = []

    for _, seg in segments.iterrows():
        i0 = int(seg["entry_bar_index"])
        i1 = int(seg["exit_bar_index"])
        anchor_id = str(seg["anchor_id"])
        direction = 1 if str(seg["direction"]).lower() == "long" else -1

        win_close = close.iloc[i0:i1 + 1].reset_index(drop=True)
        win_high = high.iloc[i0:i1 + 1].reset_index(drop=True)
        win_low = low.iloc[i0:i1 + 1].reset_index(drop=True)
        win_anchor = pd.to_numeric(anchors_df.loc[i0:i1, anchor_id], errors="coerce").reset_index(drop=True)
        win_ts = pd.to_datetime(bars.loc[i0:i1, "timestamp"]).reset_index(drop=True)
        entry_price = float(seg["entry_price"])

        favorable = (win_high - entry_price) if direction > 0 else (entry_price - win_low)
        adverse = (entry_price - win_low) if direction > 0 else (win_high - entry_price)

        mfe_idx = int(favorable.idxmax())
        mae_idx = int(adverse.idxmax())

        mfe_price = float(favorable.iloc[mfe_idx])
        mae_price = float(adverse.iloc[mae_idx])
        net_outcome_price = float((float(seg["exit_price"]) - entry_price) * direction)
        etd_price = float(max(0.0, mfe_price - net_outcome_price))
        etd_ratio = float(etd_price / mfe_price) if mfe_price > 0 else np.nan

        anchor_dist = (win_close - win_anchor).abs()
        path_length_abs = float(win_close.diff().abs().sum(skipna=True))
        net_displacement = float(abs(win_close.iloc[-1] - win_close.iloc[0]))
        path_efficiency = float(net_displacement / path_length_abs) if path_length_abs > 0 else np.nan

        entry_atr = atr14.iloc[i0]
        mfe_atr = float(mfe_price / entry_atr) if pd.notna(entry_atr) and entry_atr > 0 else np.nan
        mae_atr = float(mae_price / entry_atr) if pd.notna(entry_atr) and entry_atr > 0 else np.nan

        anchor_move = abs(float(win_anchor.iloc[-1] - win_anchor.iloc[0])) if len(win_anchor) > 1 else 0.0
        price_move = abs(float(win_close.iloc[-1] - win_close.iloc[0])) if len(win_close) > 1 else 0.0
        denom = anchor_move + price_move
        anchor_drift_burden = float(anchor_move / denom) if denom > 0 else np.nan

        rows.append({
            "segment_id": seg["segment_id"],
            "mfe_price": mfe_price,
            "mae_price": mae_price,
            "mfe_atr": mfe_atr,
            "mae_atr": mae_atr,
            "net_outcome_price": net_outcome_price,
            "mfe_ts": win_ts.iloc[mfe_idx],
            "mae_ts": win_ts.iloc[mae_idx],
            "time_to_mfe_bars": int(mfe_idx),
            "time_to_mae_bars": int(mae_idx),
            "mfe_before_mae": bool(mfe_idx <= mae_idx),
            "etd_price": etd_price,
            "etd_ratio": etd_ratio,
            "max_anchor_distance": float(anchor_dist.max(skipna=True)),
            "mean_anchor_distance": float(anchor_dist.mean(skipna=True)),
            "anchor_distance_auc": float(anchor_dist.sum(skipna=True)),
            "path_length_abs": path_length_abs,
            "path_efficiency": path_efficiency,
            "anchor_drift_burden": anchor_drift_burden,
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        out["mfe_ts"] = pd.to_datetime(out["mfe_ts"])
        out["mae_ts"] = pd.to_datetime(out["mae_ts"])
    return out