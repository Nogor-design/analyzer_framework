from __future__ import annotations

import numpy as np
import pandas as pd

from .models import AnchorSpec


REQUIRED_PRICE_COLUMNS = {"open", "high", "low", "close"}


def validate_market_bars(df: pd.DataFrame) -> None:
    missing = REQUIRED_PRICE_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Market bars missing required columns: {sorted(missing)}")
    has_time_col = "timestamp" in df.columns or "dt" in df.columns
    if not has_time_col and not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Market bars must have a tz-aware DatetimeIndex or a 'timestamp'/'dt' column")
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
    elif "timestamp" in df.columns:
        idx = pd.DatetimeIndex(df["timestamp"])
    else:
        idx = pd.DatetimeIndex(df["dt"])
    if idx.tz is None:
        raise ValueError("Naive datetimes are forbidden; market bars must be tz-aware")


def ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        if "timestamp" in out.columns:
            time_col = "timestamp"
        elif "dt" in out.columns:
            time_col = "dt"
            out["timestamp"] = out["dt"]
        else:
            raise ValueError("Market bars must include 'timestamp' or 'dt' for MA anchor analysis")
        out["timestamp"] = pd.to_datetime(out[time_col], utc=False)
        # out["timestamp"] = pd.to_datetime(out["timestamp"], utc=False)
        out = out.set_index("timestamp", drop=False)
    if out.index.tz is None:
        raise ValueError("Naive datetimes are forbidden; market bars must be tz-aware")
    return out.sort_index()


def compute_anchor_series(df: pd.DataFrame, spec: AnchorSpec) -> pd.Series:
    src = spec.source.lower()
    if src not in df.columns:
        raise ValueError(f"Anchor source column not found: {src}")

    s = pd.to_numeric(df[src], errors="coerce")
    family = spec.family.upper()

    if family == "SMA":
        return s.rolling(spec.length, min_periods=spec.length).mean()
    if family == "EMA":
        return s.ewm(span=spec.length, adjust=False, min_periods=spec.length).mean()
    if family == "WMA":
        weights = np.arange(1, spec.length + 1, dtype=float)
        weights /= weights.sum()
        return s.rolling(spec.length, min_periods=spec.length).apply(
            lambda x: float(np.dot(x, weights)), raw=True
        )

    raise ValueError(f"Unsupported anchor family: {family!r}. Supported: SMA, EMA, WMA.")


def build_anchors_table(df: pd.DataFrame, specs: list[AnchorSpec]) -> pd.DataFrame:
    validate_market_bars(df)
    bars = ensure_datetime_index(df)

    out = pd.DataFrame(index=bars.index)
    out["timestamp"] = bars.index
    for spec in specs:
        out[spec.anchor_id] = compute_anchor_series(bars, spec)

    return out.reset_index(drop=True)