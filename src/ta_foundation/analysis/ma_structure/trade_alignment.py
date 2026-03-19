from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _find_col(df: pd.DataFrame, names: list[str]) -> Optional[str]:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        key = str(name).strip().lower()
        if key in lowered:
            return lowered[key]
    return None


def _atr14(bars: pd.DataFrame) -> pd.Series:
    high = pd.to_numeric(bars.get("high"), errors="coerce")
    low = pd.to_numeric(bars.get("low"), errors="coerce")
    close = pd.to_numeric(bars.get("close"), errors="coerce")
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14, min_periods=14).mean()


def _trade_direction(row: pd.Series, market_pos_col: Optional[str]) -> int:
    if market_pos_col:
        val = str(row.get(market_pos_col) or "").strip().lower()
        if "long" in val:
            return 1
        if "short" in val:
            return -1
    entry_price = pd.to_numeric(pd.Series([row.get("entry_price")]), errors="coerce").iloc[0]
    exit_price = pd.to_numeric(pd.Series([row.get("exit_price")]), errors="coerce").iloc[0]
    if pd.notna(entry_price) and pd.notna(exit_price):
        return 1 if exit_price >= entry_price else -1
    return 1


def build_trade_recommendation_alignment(
    trades: Optional[pd.DataFrame],
    recommendations: Optional[pd.DataFrame],
    bars: pd.DataFrame,
) -> pd.DataFrame:
    if trades is None or recommendations is None:
        return pd.DataFrame()
    if trades.empty or recommendations.empty or bars.empty:
        return pd.DataFrame()

    entry_time_col = _find_col(trades, ["entry_time", "Entry time", "Entry Time", "entry_dt"])
    exit_time_col = _find_col(trades, ["exit_time", "Exit time", "Exit Time", "exit_dt"])
    entry_price_col = _find_col(trades, ["entry_price", "Entry price", "Entry Price"])
    exit_price_col = _find_col(trades, ["exit_price", "Exit price", "Exit Price"])
    market_pos_col = _find_col(trades, ["market_pos", "Market pos.", "market position"])
    trade_id_col = _find_col(trades, ["trade_id", "Trade number", "trade number"])

    if not entry_time_col or not exit_time_col or not entry_price_col:
        return pd.DataFrame()

    bars_local = bars.copy().sort_index()
    if "timestamp" not in bars_local.columns:
        bars_local["timestamp"] = bars_local.index
    atr14 = _atr14(bars_local)

    recs = recommendations.copy().reset_index(drop=True)
    trade_rows = []

    for _, trade in trades.iterrows():
        entry_ts = pd.to_datetime(trade.get(entry_time_col), errors="coerce")
        exit_ts = pd.to_datetime(trade.get(exit_time_col), errors="coerce")
        entry_price = pd.to_numeric(pd.Series([trade.get(entry_price_col)]), errors="coerce").iloc[0]
        exit_price = pd.to_numeric(pd.Series([trade.get(exit_price_col)]), errors="coerce").iloc[0] if exit_price_col else np.nan

        if pd.isna(entry_ts) or pd.isna(exit_ts) or pd.isna(entry_price):
            continue
        if entry_ts.tzinfo is None or exit_ts.tzinfo is None:
            continue

        window = bars_local[(bars_local["timestamp"] >= entry_ts) & (bars_local["timestamp"] <= exit_ts)]
        if window.empty:
            continue

        direction = _trade_direction(trade, market_pos_col)
        high = pd.to_numeric(window["high"], errors="coerce")
        low = pd.to_numeric(window["low"], errors="coerce")

        favorable = (high - entry_price) if direction > 0 else (entry_price - low)
        adverse = (entry_price - low) if direction > 0 else (high - entry_price)

        atr_at_entry = atr14.reindex(bars_local.index).loc[window.index[0]] if window.index[0] in atr14.index else np.nan
        if pd.isna(atr_at_entry) or float(atr_at_entry) <= 0:
            continue

        realized_tp_atr = float(favorable.max(skipna=True) / float(atr_at_entry))
        realized_sl_atr = float(adverse.max(skipna=True) / float(atr_at_entry))
        realized_outcome_atr = np.nan
        if pd.notna(exit_price):
            realized_outcome_atr = float(((float(exit_price) - float(entry_price)) * direction) / float(atr_at_entry))

        scored = recs.copy()
        scored["fit_distance"] = (scored["tp_atr"] - realized_tp_atr).abs() + (scored["sl_atr"] - realized_sl_atr).abs()
        best = scored.sort_values(["fit_distance", "stability_score", "robust_score"], ascending=[True, False, False]).iloc[0]

        trade_rows.append(
            {
                "trade_id": trade.get(trade_id_col) if trade_id_col else None,
                "entry_time": entry_ts,
                "exit_time": exit_ts,
                "matched_anchor_id": best.get("anchor_id"),
                "recommended_tp_atr": float(best.get("tp_atr")),
                "recommended_sl_atr": float(best.get("sl_atr")),
                "recommended_stability_score": float(best.get("stability_score")) if pd.notna(best.get("stability_score")) else np.nan,
                "recommended_robust_score": float(best.get("robust_score")) if pd.notna(best.get("robust_score")) else np.nan,
                "recommended_sample_quality_flag": best.get("sample_quality_flag"),
                "realized_tp_atr": realized_tp_atr,
                "realized_sl_atr": realized_sl_atr,
                "realized_outcome_atr": realized_outcome_atr,
                "fit_distance": float(best.get("fit_distance")),
            }
        )

    return pd.DataFrame(trade_rows)
