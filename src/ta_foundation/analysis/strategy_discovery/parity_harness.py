from __future__ import annotations

"""
C#↔Python entry-signal parity harness
=====================================
The make-or-break check for the discovery→NinjaTrader pipeline: prove that
StrategyDiscoveryFilter.cs fires its candle entry on the *same bars* as the
Python discovery detector. Without this, a recipe "confirmation" of a
discovered edge is meaningless — the project has been burned before by C#/Python
divergence (the ORB fill-model bugs).

Workflow
--------
1. Python side (this module / scripts/parity_signal_export.py):
   reproduce the discovery's exact bar construction + feature engine + detector
   and emit the list of signal-bar timestamps for a structure.

2. NinjaTrader side: load StrategyDiscoveryFilter with the matching EntrySignal
   + params + timeframe, set ``EnableDebugPrint = true``, run the same date
   range, and copy the Output window. Each raw signal prints as::

       [SDF-SIGNAL] 2026-02-03T08:35:00 dir=1 sig=EngulfingBullish bar=312

   That print happens BEFORE any regime/session/direction/position gating, so
   it isolates the *entry math* from execution.

3. Diff: ``diff_signals(py_df, parse_nt_signal_log(nt_text))`` reports matched /
   missing / extra bars. A clean run is matched == py_count == nt_count.

Timezone note (load-bearing): NinjaTrader prints ``Time[0]`` in the account
timezone. ta_foundation canonical bars are tz-aware America/Denver (CLAUDE.md).
The harness emits ``nt_time`` as Denver-local wall clock with no offset, so it
lines up with an NT account configured to Denver. If your NT account uses a
different timezone, pass ``account_tz`` to match it.
"""

import re
from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
from ta_foundation.analysis.entry_strategies.candle.patterns import (
    PATTERN_REGISTRY,
    detect_pattern,
)
from ta_foundation.marketdata.resample import ohlcv_resample_from_bars


_NT_LOCAL_FMT = "%Y-%m-%dT%H:%M:%S"
_SIGNAL_LINE_RE = re.compile(
    r"\[SDF-SIGNAL\]\s+(?P<dt>\S+)\s+dir=(?P<dir>-?\d+)"
)


def _to_nt_localstr(dt_series: pd.Series, account_tz: str) -> pd.Series:
    """Render tz-aware datetimes as NT account-local wall clock (no offset)."""
    dt = pd.to_datetime(dt_series)
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_convert(account_tz).dt.tz_localize(None)
    return dt.dt.strftime(_NT_LOCAL_FMT)


def export_signal_bars(
    bars: pd.DataFrame,
    structure: str,
    params: Optional[Dict[str, Any]] = None,
    timeframe_minutes: int = 1,
    tick_size: float = 0.25,
    warmup_bars: int = 50,
    account_tz: str = "America/Denver",
) -> pd.DataFrame:
    """
    Reproduce the discovery detector for one structure and return its signal
    bars, mirroring the StrategyDiscoveryFilter [SDF-SIGNAL] probe.

    Parameters
    ----------
    bars        : OHLCV DataFrame (dt tz-aware, open/high/low/close/volume).
                  Resampled to ``timeframe_minutes`` exactly as discovery does.
    structure   : a key in candle PATTERN_REGISTRY (e.g. "engulfing_bullish").
    params      : detector params (body_multiplier, lookback, etc.). Absent
                  keys fall back to patterns.py defaults — the same defaults
                  StrategyDiscoveryFilter.cs uses.
    timeframe_minutes : primary bar period (matches the seed BarsPeriod).
    tick_size   : instrument tick size (features.py size_ticks).
    warmup_bars : drop signals before this bar index, matching the C#
                  BarsRequiredToTrade gate (default 50).

    Returns
    -------
    DataFrame[dt, direction, structure, nt_time], sorted by dt.
    """
    if structure not in PATTERN_REGISTRY:
        raise ValueError(
            f"Unknown structure '{structure}'. Known: {sorted(PATTERN_REGISTRY)}"
        )

    params = dict(params or {})
    cols = ["dt", "direction", "structure", "nt_time"]

    if bars is None or bars.empty:
        return pd.DataFrame(columns=cols)

    tf = max(1, int(timeframe_minutes or 1))
    work = bars.copy()
    if tf > 1:
        work = ohlcv_resample_from_bars(work, f"{tf}m")
    work = work.sort_values("dt").reset_index(drop=True)

    # Feature config: include the rolling lookback the detector will request so
    # body_vs_roll_N / size_vs_roll_N exist. ATR period 14 matches features.py.
    lookback = int(params.get("lookback", 20))
    cfg = {
        "tick_size": float(tick_size),
        "atr_period": 14,
        "size_lookbacks": sorted({5, 10, 20, lookback}),
    }
    enriched = compute_candle_features(work, cfg)

    sig = detect_pattern(structure, enriched, params)
    if sig is None or sig.empty or "dt" not in sig.columns:
        return pd.DataFrame(columns=cols)

    # Map dt -> positional bar index for the warmup gate.
    pos = {dt: i for i, dt in enumerate(enriched["dt"].tolist())}

    rows: List[tuple] = []
    for _, r in sig.iterrows():
        dt = r["dt"]
        if pos.get(dt, -1) < warmup_bars:
            continue
        rows.append((dt, int(r.get("direction", 0))))

    out = pd.DataFrame(rows, columns=["dt", "direction"])
    out["structure"] = structure
    if not out.empty:
        out["nt_time"] = _to_nt_localstr(out["dt"], account_tz)
    else:
        out["nt_time"] = pd.Series(dtype=str)
    return out.sort_values("dt").reset_index(drop=True)


def parse_nt_signal_log(text: str) -> pd.DataFrame:
    """Parse [SDF-SIGNAL] lines from a NinjaTrader Output-window dump."""
    rows: List[tuple] = []
    for m in _SIGNAL_LINE_RE.finditer(text or ""):
        rows.append((m.group("dt"), int(m.group("dir"))))
    df = pd.DataFrame(rows, columns=["nt_time", "direction"])
    return df


def diff_signals(py_df: pd.DataFrame, nt_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Compare Python signal bars to NT [SDF-SIGNAL] bars on wall-clock time.

    Returns a summary dict. ``missing_in_nt`` = Python fired but NT didn't
    (the dangerous case — usually a C# detector bug or a feature-warmup/tz
    mismatch). ``extra_in_nt`` = NT fired but Python didn't.
    """
    py_times = set(py_df["nt_time"]) if not py_df.empty else set()
    nt_times = set(nt_df["nt_time"]) if not nt_df.empty else set()

    matched = py_times & nt_times
    union = py_times | nt_times
    return {
        "py_count": len(py_times),
        "nt_count": len(nt_times),
        "matched": len(matched),
        "missing_in_nt": sorted(py_times - nt_times),
        "extra_in_nt": sorted(nt_times - py_times),
        "match_rate": (len(matched) / len(union)) if union else 1.0,
        "clean": (py_times == nt_times) and bool(union),
    }
