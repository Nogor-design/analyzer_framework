from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from ta_foundation.analysis.indicators.registry import IndicatorSpec, DEFAULT_INDICATORS


def _norm(s: str) -> str:
    s = (s or "").lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    norm_map = {_norm(c): c for c in df.columns}
    for cand in candidates:
        k = _norm(cand)
        if k in norm_map:
            return norm_map[k]
    return None


@dataclass(frozen=True)
class TradeEnrichmentConfig:
    timeframe: str = "5m"         # features computed from 5m bars by default
    indicator_specs: List[IndicatorSpec] = None  # applied to bars before joining
    window_prev_bars: int = 1     # attach previous bar snapshot
    window_next_bars: int = 0     # attach next bar snapshot (optional)


def enrich_trades_with_bars_and_indicators(
    trades: pd.DataFrame,
    bars: pd.DataFrame,
    cfg: TradeEnrichmentConfig,
) -> pd.DataFrame:
    """
    Enrich trades with bar + indicator snapshots at entry/exit times.

    Assumptions:
    - trades dt columns are tz-aware America/Denver (pipeline contract)
    - bars dt is tz-aware America/Denver
    - bars contains OHLCV columns and optionally indicators after apply()

    Output:
    - returns a new trades df with appended columns:
        entry_bar_close, entry_bar_atr_14, prev1_bar_close, ...
        exit_bar_close, ...
    """
    if trades is None or trades.empty:
        return trades
    if bars is None or bars.empty:
        return trades.copy()

    # Column detection (tolerant)
    entry_dt_col = _find_col(trades, ["entry_dt", "entry time", "entry_time", "entrydatetime", "entry date/time"])
    exit_dt_col = _find_col(trades, ["exit_dt", "exit time", "exit_time", "exitdatetime", "exit date/time"])
    if entry_dt_col is None:
        # can't enrich without entry timestamps
        return trades.copy()

    out = trades.copy()

    # Ensure dt types
    out[entry_dt_col] = pd.to_datetime(out[entry_dt_col], errors="coerce")
    if exit_dt_col is not None:
        out[exit_dt_col] = pd.to_datetime(out[exit_dt_col], errors="coerce")

    b = bars.copy()
    b["dt"] = pd.to_datetime(b["dt"], errors="coerce")
    b = b.dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)

    # Apply indicators (optional)
    specs = cfg.indicator_specs or []
    if specs:
        b = DEFAULT_INDICATORS.apply(b, specs)

    # For asof joins, we need index on dt
    b2 = b.set_index("dt")

    def _snapshot_at(ts: pd.Series, prefix: str) -> pd.DataFrame:
        # asof: nearest bar at or before trade time
        idx = pd.DatetimeIndex(ts)
        # Reindex via asof-like behavior:
        # pandas doesn't have vectorized asof for DF easily; do merge_asof
        tmp = pd.DataFrame({"_ts": idx}).dropna().sort_values("_ts")
        merged = pd.merge_asof(
            tmp,
            b,
            left_on="_ts",
            right_on="dt",
            direction="backward",
            allow_exact_matches=True,
        )
        merged = merged.set_index(tmp.index)
        merged = merged.drop(columns=["_ts"])
        # Prefix all bar columns except dt
        rename = {}
        for c in merged.columns:
            if c == "dt":
                rename[c] = f"{prefix}bar_dt"
            else:
                rename[c] = f"{prefix}bar_{c}"
        return merged.rename(columns=rename)

    entry_snap = _snapshot_at(out[entry_dt_col], prefix="entry_")
    out = pd.concat([out, entry_snap], axis=1)

    if cfg.window_prev_bars and cfg.window_prev_bars > 0:
        for k in range(1, cfg.window_prev_bars + 1):
            # previous k bars relative to the matched entry bar time
            prev_dt_col = "entry_bar_dt"
            if prev_dt_col not in out.columns:
                break
            prev_ts = pd.to_datetime(out[prev_dt_col], errors="coerce") - pd.Timedelta(minutes=0)
            # shift by k bars in the bar table
            # compute using bar index position: build a mapping dt->pos then pos-k
            dt_to_pos = {d: i for i, d in enumerate(b["dt"].tolist())}
            prev_dts = []
            for d in pd.to_datetime(out[prev_dt_col], errors="coerce"):
                if pd.isna(d) or d not in dt_to_pos:
                    prev_dts.append(pd.NaT)
                    continue
                pos = dt_to_pos[d]
                pos2 = pos - k
                prev_dts.append(b["dt"].iloc[pos2] if pos2 >= 0 else pd.NaT)

            prev_ts_series = pd.Series(prev_dts, index=out.index)
            prev_snap = _snapshot_at(prev_ts_series, prefix=f"prev{k}_")
            out = pd.concat([out, prev_snap], axis=1)

    if exit_dt_col is not None:
        exit_snap = _snapshot_at(out[exit_dt_col], prefix="exit_")
        out = pd.concat([out, exit_snap], axis=1)

    return out
