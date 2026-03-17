from __future__ import annotations

import pandas as pd

from .models import AnchorSpec


REQUIRED_PRICE_COLUMNS = {"open", "high", "low", "close"}


def validate_market_bars(df: pd.DataFrame) -> None:
    missing = REQUIRED_PRICE_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Market bars missing required columns: {sorted(missing)}")
    if "timestamp" not in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Market bars must have a tz-aware DatetimeIndex or a 'timestamp' column")
    idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.DatetimeIndex(df["timestamp"])
    if idx.tz is None:
        raise ValueError("Naive datetimes are forbidden; market bars must be tz-aware")


def ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=False)
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

    raise ValueError(f"Unsupported anchor family for MVP: {family}")


def build_anchors_table(df: pd.DataFrame, specs: list[AnchorSpec]) -> pd.DataFrame:
    validate_market_bars(df)
    bars = ensure_datetime_index(df)

    out = pd.DataFrame(index=bars.index)
    out["timestamp"] = bars.index
    for spec in specs:
        out[spec.anchor_id] = compute_anchor_series(bars, spec)

    return out.reset_index(drop=True)