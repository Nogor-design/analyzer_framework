from __future__ import annotations

import pandas as pd


TREND_UP = "up"
TREND_DOWN = "down"
TREND_FLAT = "flat"
VOL_LOW = "low"
VOL_MID = "mid"
VOL_HIGH = "high"


def _compute_atr14(bars: pd.DataFrame) -> pd.Series:
    high = pd.to_numeric(bars.get("high"), errors="coerce")
    low = pd.to_numeric(bars.get("low"), errors="coerce")
    close = pd.to_numeric(bars.get("close"), errors="coerce")

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=14).mean()


def _compute_trend_regime(close: pd.Series, span: int = 21, slope_lookback: int = 3) -> pd.Series:
    ema = close.ewm(span=span, adjust=False, min_periods=span).mean()
    slope = ema - ema.shift(slope_lookback)

    out = pd.Series(index=close.index, dtype="object")
    out.loc[slope > 0] = TREND_UP
    out.loc[slope < 0] = TREND_DOWN
    out.loc[slope.notna() & out.isna()] = TREND_FLAT
    return out


def _compute_vol_regime(atr: pd.Series) -> pd.Series:
    out = pd.Series(index=atr.index, dtype="object")
    valid = atr.dropna()
    if valid.empty:
        return out

    q33 = float(valid.quantile(0.33))
    q67 = float(valid.quantile(0.67))

    out.loc[valid.index] = VOL_MID
    out.loc[valid.index[valid <= q33]] = VOL_LOW
    out.loc[valid.index[valid >= q67]] = VOL_HIGH
    return out


def attach_regime_context(segments: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    if segments.empty:
        return segments

    enriched = segments.copy()
    close = pd.to_numeric(bars.get("close"), errors="coerce")
    atr14 = _compute_atr14(bars)
    trend = _compute_trend_regime(close)
    vol = _compute_vol_regime(atr14)

    entry_idx = pd.to_numeric(enriched.get("entry_bar_index"), errors="coerce")

    def _safe_pick(series: pd.Series, idx: float, default: str) -> str:
        if pd.isna(idx):
            return default
        i = int(idx)
        if i < 0 or i >= len(series):
            return default
        val = series.iloc[i]
        if pd.isna(val):
            return default
        return str(val)

    enriched["trend_regime_at_entry"] = [
        _safe_pick(trend, i, "unknown") for i in entry_idx
    ]
    enriched["vol_regime_at_entry"] = [
        _safe_pick(vol, i, "unknown") for i in entry_idx
    ]
    return enriched
