from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .models import EngineConfig


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _crossed(prev_diff: float, curr_diff: float) -> bool:
    if np.isnan(prev_diff) or np.isnan(curr_diff):
        return False
    return _sign(prev_diff) != _sign(curr_diff)


def _touch_return(low: float, high: float, anchor_value: float, band: float) -> bool:
    lower = anchor_value - band
    upper = anchor_value + band
    return (low <= upper) and (high >= lower)


def detect_segments(
    bars: pd.DataFrame,
    anchors_df: pd.DataFrame,
    config: EngineConfig,
    *,
    run_id: str,
) -> pd.DataFrame:
    rows: List[Dict] = []
    bars = bars.copy().reset_index(drop=True)
    anchors_df = anchors_df.copy().reset_index(drop=True)

    close = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    atr14 = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14, min_periods=14).mean()

    for anchor_col in [c for c in anchors_df.columns if c != "timestamp"]:
        anchor = pd.to_numeric(anchors_df[anchor_col], errors="coerce")
        active = None
        seg_n = 0

        for i in range(1, len(bars)):
            a_prev = anchor.iloc[i - 1]
            a_now = anchor.iloc[i]
            c_prev = close.iloc[i - 1]
            c_now = close.iloc[i]
            if np.isnan(a_prev) or np.isnan(a_now) or np.isnan(c_prev) or np.isnan(c_now):
                continue

            prev_diff = c_prev - a_prev
            curr_diff = c_now - a_now

            if active is None:
                if _crossed(prev_diff, curr_diff):
                    direction = 1 if curr_diff > 0 else -1
                    entry_gap_cross = abs(prev_diff) > 0 and abs(curr_diff) > 0 and _sign(prev_diff) != _sign(curr_diff)
                    active = {
                        "segment_id": f"{run_id}::{anchor_col}::{seg_n}",
                        "run_id": run_id,
                        "instrument": config.instrument,
                        "anchor_id": anchor_col,
                        "direction": "long" if direction > 0 else "short",
                        "direction_sign": direction,
                        "entry_ts": bars.loc[i, "timestamp"],
                        "entry_bar_index": i,
                        "entry_price": float(c_now),
                        "entry_anchor_value": float(a_now),
                        "entry_cross_mode": config.cross_mode,
                        "exit_mode": config.exit_mode,
                        "gap_cross":  bool(entry_gap_cross),
                        "re_cross_count": 0,
                        "censored": True,
                        "anchor_slope_at_entry": float(a_now - a_prev),
                    }
                    seg_n += 1
                continue

            active["re_cross_count"] += int(_crossed(prev_diff, curr_diff))

            min_bars_ok = (i - active["entry_bar_index"]) >= config.min_bars_after_entry
            band = 0.0
            if config.return_band_atr > 0 and not np.isnan(atr14.iloc[i]):
                band = float(atr14.iloc[i] * config.return_band_atr)

            if config.return_band_ticks > 0:
                band = max(band, config.return_band_ticks)

            should_exit = False
            if min_bars_ok:
                if config.exit_mode == "touch":
                    should_exit = _touch_return(
                        float(low.iloc[i]),
                        float(high.iloc[i]),
                        float(a_now),
                        band,
                    )
                else:
                    should_exit = abs(curr_diff) <= band if band > 0 else (_sign(curr_diff) == 0 or _crossed(prev_diff, curr_diff))

            if should_exit:
                entry_i = active["entry_bar_index"]
                window_close = close.iloc[entry_i : i + 1]
                entry_price = float(active["entry_price"])
                direction = int(active["direction_sign"])

                adverse_first_bar = window_close.iloc[min(1, len(window_close) - 1)]
                immediate_failure = (
                    (direction > 0 and adverse_first_bar < entry_price) or
                    (direction < 0 and adverse_first_bar > entry_price)
                )

                rows.append({
                    **active,
                    "exit_ts": bars.loc[i, "timestamp"],
                    "exit_bar_index": i,
                    "exit_price": float(c_now),
                    "exit_anchor_value": float(a_now),
                    "bars_held": int(i - entry_i),
                    "minutes_held": float((bars.loc[i, "timestamp"] - active["entry_ts"]).total_seconds() / 60.0),
                    "censored": False,
                    "immediate_failure": bool(immediate_failure),
                    "trend_regime_at_entry": None,
                    "vol_regime_at_entry": None,
                    "anchor_slope_at_entry": float(active.get("anchor_slope_at_entry", np.nan)),
                    "gap_cross": bool(active.get("gap_cross", False)),
                })
                active = None

        if active is not None:
            i = len(bars) - 1
            rows.append({
                **active,
                "exit_ts": bars.loc[i, "timestamp"],
                "exit_bar_index": i,
                "exit_price": float(close.iloc[i]),
                "exit_anchor_value": float(anchor.iloc[i]) if not np.isnan(anchor.iloc[i]) else np.nan,
                "bars_held": int(i - active["entry_bar_index"]),
                "minutes_held": float((bars.loc[i, "timestamp"] - active["entry_ts"]).total_seconds() / 60.0),
                "censored": True,
                "immediate_failure": False,
                "trend_regime_at_entry": None,
                "vol_regime_at_entry": None,
                "anchor_slope_at_entry": float(active.get("anchor_slope_at_entry", np.nan)),
            })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # Convert to DatetimeTZDtype preserving timezone.
    # utc=True converts all tz-aware objects to UTC first (consistent dtype),
    # then tz_convert restores America/Denver.  This avoids older-pandas
    # behaviour where pd.to_datetime() on an object column silently strips tz.
    for col in ("entry_ts", "exit_ts"):
        try:
            out[col] = pd.to_datetime(out[col], utc=True).dt.tz_convert("America/Denver")
        except Exception:
            out[col] = pd.to_datetime(out[col], errors="coerce")
    return out