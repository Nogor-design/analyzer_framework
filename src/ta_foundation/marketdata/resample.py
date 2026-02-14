from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd


Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def _tf_to_pandas_rule(timeframe: str) -> str:
    tf = (timeframe or "").strip().lower()
    mapping = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1H",
        "4h": "4H",
        "1d": "1D",
    }
    if tf not in mapping:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return mapping[tf]


def ohlcv_resample_from_ticks(
    ticks: pd.DataFrame,
    timeframe: str,
    price_col: str = "last",
    volume_col: str = "volume",
) -> pd.DataFrame:
    """
    Build OHLCV bars from tick data.

    ticks columns required:
      - dt (tz-aware)
      - price_col (default last)
      - volume_col (default volume)
    """
    if ticks is None or ticks.empty:
        return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])

    rule = _tf_to_pandas_rule(timeframe)
    df = ticks[["dt", price_col, volume_col]].copy()
    df = df.sort_values("dt")
    df = df.set_index("dt")

    o = df[price_col].resample(rule).first()
    h = df[price_col].resample(rule).max()
    l = df[price_col].resample(rule).min()
    c = df[price_col].resample(rule).last()
    v = df[volume_col].resample(rule).sum(min_count=1)

    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["open", "high", "low", "close", "volume"]
    out = out.dropna(subset=["open", "high", "low", "close"], how="any")
    out = out.reset_index().rename(columns={"dt": "dt"})
    return out


def ohlcv_resample_from_bars(
    bars: pd.DataFrame,
    timeframe: str,
) -> pd.DataFrame:
    """
    Resample OHLCV bars (e.g., 1m -> 5m/15m/1h).

    bars columns required:
      - dt (tz-aware)
      - open, high, low, close, volume
    """
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])

    rule = _tf_to_pandas_rule(timeframe)
    df = bars[["dt", "open", "high", "low", "close", "volume"]].copy()
    df = df.sort_values("dt").set_index("dt")

    o = df["open"].resample(rule).first()
    h = df["high"].resample(rule).max()
    l = df["low"].resample(rule).min()
    c = df["close"].resample(rule).last()
    v = df["volume"].resample(rule).sum(min_count=1)

    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["open", "high", "low", "close", "volume"]
    out = out.dropna(subset=["open", "high", "low", "close"], how="any")
    out = out.reset_index()
    return out


def slice_dt(df: pd.DataFrame, start: Optional[pd.Timestamp], end: Optional[pd.Timestamp]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df
    if start is not None:
        out = out[out["dt"] >= start]
    if end is not None:
        out = out[out["dt"] <= end]
    return out
