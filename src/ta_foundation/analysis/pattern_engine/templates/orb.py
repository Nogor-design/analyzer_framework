# src/ta_foundation/analysis/pattern_engine/templates/orb.py
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def _parse_hhmm(s: str) -> tuple[int, int]:
    s = (s or "").strip()
    if ":" not in s:
        return (0, 0)
    hh, mm = s.split(":", 1)
    return (int(hh), int(mm))


def _minutes_since_midnight(dt: pd.Series) -> pd.Series:
    return (dt.dt.hour.astype("int64") * 60 + dt.dt.minute.astype("int64")).astype("int64")


def _session_day_date(dt: pd.Series, globex_start: str) -> pd.Series:
    """
    Futures session_day convention:
      - if time >= globex_start (e.g., 16:00) => belongs to NEXT calendar day
      - else                                 => belongs to current calendar day
    """
    gh, gm = _parse_hhmm(globex_start)
    globex_min = gh * 60 + gm

    dt = pd.to_datetime(dt, errors="coerce")
    mins = _minutes_since_midnight(dt)
    cal_day = dt.dt.floor("D")
    sess_day = cal_day.where(mins < globex_min, cal_day + pd.Timedelta(days=1))
    return pd.to_datetime(sess_day).dt.date


def detect_orb_break_retest(
    *,
    bars_df: pd.DataFrame,
    ticks_df: Optional[pd.DataFrame],
    params: Dict[str, Any],
    market_ctx: Dict[str, Any],
    options: Dict[str, Any],
) -> pd.DataFrame:
    """
    Emits signals on retest confirmation.

    Required bars_df columns: dt, open, high, low, close
    Output columns: dt, direction, entry_ref_price, features_json (optional)

    Params:
      orb_minutes (int): opening range window length from session_start
      session_start (str): "07:30" for RTH open (Denver local)
      globex_start (str): "16:00" for CME globex start (Denver local)
      retest_bars (int): max bars after breakout to accept a retest-confirm
      buffer_ticks (int): how close to OR boundary qualifies as retest touch
      tick_size (float): used to translate buffer_ticks into price buffer
    """
    if bars_df is None or bars_df.empty:
        return pd.DataFrame(columns=["dt", "direction", "entry_ref_price", "features_json"])

    df = bars_df.sort_values("dt").copy()

    orb_minutes = int(params.get("orb_minutes", 5))
    retest_bars = int(params.get("retest_bars", 2))
    session_start = str(params.get("session_start", "07:30"))
    globex_start = str(params.get("globex_start", "16:00"))
    buffer_ticks = int(params.get("buffer_ticks", 2))

    tick_size = float(options.get("tick_size") or params.get("tick_size") or 0.0)
    if tick_size <= 0:
        tick_size = 0.25  # fallback; better to pass explicitly
    buf = buffer_ticks * tick_size

    sh, sm = _parse_hhmm(session_start)
    start_min = sh * 60 + sm

    df["day_id"] = _session_day_date(df["dt"], globex_start=globex_start)
    df["min_of_day"] = _minutes_since_midnight(df["dt"])
    df["in_orb"] = (df["min_of_day"] >= start_min) & (df["min_of_day"] < start_min + orb_minutes)

    out_rows = []

    for day_id, g in df.groupby("day_id", sort=True):
        g = g.sort_values("dt").copy()
        orb = g[g["in_orb"]]
        if orb.empty:
            continue

        or_high = float(pd.to_numeric(orb["high"], errors="coerce").max())
        or_low = float(pd.to_numeric(orb["low"], errors="coerce").min())
        if not np.isfinite(or_high) or not np.isfinite(or_low) or or_high <= or_low:
            continue

        # after ORB window
        post = g[~g["in_orb"]].copy()
        if post.empty:
            continue

        closes = pd.to_numeric(post["close"], errors="coerce").to_numpy(float)
        highs = pd.to_numeric(post["high"], errors="coerce").to_numpy(float)
        lows = pd.to_numeric(post["low"], errors="coerce").to_numpy(float)
        dts = post["dt"].to_numpy()

        # Identify breakout bar index:
        # long breakout: close > or_high
        # short breakout: close < or_low
        brk_long_idx = np.argmax(closes > or_high) if np.any(closes > or_high) else -1
        brk_short_idx = np.argmax(closes < or_low) if np.any(closes < or_low) else -1

        # Process each direction independently (can emit both; your sweep can filter later)
        def process_direction(direction: int, brk_idx: int):
            if brk_idx < 0:
                return
            # search retest confirm within next retest_bars
            end = min(len(post), brk_idx + 1 + max(1, retest_bars))
            if end <= brk_idx + 1:
                return

            # retest condition:
            # long: low <= or_high + buf and then a close back above or_high
            # short: high >= or_low - buf and then a close back below or_low
            for j in range(brk_idx + 1, end):
                if direction > 0:
                    touched = np.isfinite(lows[j]) and (lows[j] <= or_high + buf)
                    confirmed = np.isfinite(closes[j]) and (closes[j] > or_high)
                else:
                    touched = np.isfinite(highs[j]) and (highs[j] >= or_low - buf)
                    confirmed = np.isfinite(closes[j]) and (closes[j] < or_low)

                if touched and confirmed:
                    entry_price = float(closes[j])
                    out_rows.append({
                        "dt": pd.Timestamp(dts[j]),
                        "direction": int(direction),
                        "entry_ref_price": entry_price,
                        "features_json": "{}",
                    })
                    return

        process_direction(+1, brk_long_idx)
        process_direction(-1, brk_short_idx)

    if not out_rows:
        return pd.DataFrame(columns=["dt", "direction", "entry_ref_price", "features_json"])

    out = pd.DataFrame(out_rows)
    out["dt"] = pd.to_datetime(out["dt"])
    return out