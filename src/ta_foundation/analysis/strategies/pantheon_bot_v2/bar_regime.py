"""
Per-bar regime feature computation that mirrors PantheonBotV2.cs.

Outputs the same intermediate values that EnableDebugPrint reports — slope,
distATR, and ATR percentile rank — so that test_bar_regime_parity can
check the Python feature pipeline against the NT runtime bar-by-bar.

Source the test parity targets from PantheonBotV2.cs:
  - UpdateTrendRegimeFromHigherTimeFrame  (lines 608-630)
  - UpdateSessionVwap                     (lines 632-648)
  - UpdateVwapRegimeFromPrimary           (lines 650-667)
  - UpdateVolatilityRegimeFromPrimary     (lines 669-681)
  - RollingPercentileRank                 (lines 138-179)

Key parity facts:
  - NinjaTrader EMA seeds at Input[0] with adjust=False recursion. Pandas
    ewm(span=N, adjust=False, min_periods=1) reproduces this bit-for-bit
    once both sides have processed the same warm-up window.
  - NinjaTrader ATR is Wilder smoothing of True Range. Pandas
    ewm(alpha=1/N, adjust=False, min_periods=1) on TR matches.
  - HTF EMA/ATR are computed only on completed HTF bars; for primary bars
    between HTF closes, NT carries the last completed value forward. This
    module forward-fills the HTF-derived slope onto primary timestamps.
  - The rolling ATR percentile uses (less + 0.5 * equal) / count, exactly
    matching RollingPercentileRank.GetPercentileRank.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BarRegimeConfig:
    trend_htf_minutes: int = 15
    trend_ema_period: int = 34
    trend_slope_lookback_bars: int = 3
    trend_atr_period: int = 14
    trend_flat_threshold: float = 0.03
    trend_slope_mode_int: int = 1   # 0 = RawPoints, 1 = AtrNormalized

    vwap_atr_period: int = 14
    vwap_near_threshold_atr: float = 0.20
    use_close_for_vwap_price: bool = True

    volatility_atr_period: int = 14
    volatility_lookback_window: int = 100
    volatility_low_percentile: float = 0.33
    volatility_high_percentile: float = 0.67

    # NQ daily session reset in America/Denver. Empirically, NinjaTrader's
    # IsFirstBarOfSession for NQ ETH fires at 16:00 MT (the first 5m bar of
    # the new session is at 16:05). This corresponds to the daily session
    # start specified by the chart's instrument session template. Override
    # if you observe a different reset boundary in the EnableDebugPrint
    # output (look for the bar where vwap drops back to ≈close after a gap).
    session_reset_hour: int = 16
    session_reset_minute: int = 0

    # If set, the FIRST session VWAP accumulator starts at this timestamp
    # (used to match NinjaTrader's backtest behavior where the strategy
    # begins accumulating VWAP at the first bar of the loaded data, not
    # at the prior calendar session anchor). Bars before this timestamp
    # contribute nothing to VWAP. Subsequent sessions still reset at the
    # configured calendar reset.
    vwap_anchor_dt: Optional[pd.Timestamp] = None

    primary_tf: str = "5m"


def config_from_settings(settings: dict) -> BarRegimeConfig:
    return BarRegimeConfig(
        trend_htf_minutes=int(settings.get("TrendHigherTimeFrameMinutes", 15)),
        trend_ema_period=int(settings.get("TrendEmaPeriod", 34)),
        trend_slope_lookback_bars=int(settings.get("TrendSlopeLookbackBars", 3)),
        trend_atr_period=int(settings.get("TrendAtrPeriod", 14)),
        trend_flat_threshold=float(settings.get("TrendFlatThreshold", 0.03)),
        trend_slope_mode_int=int(settings.get("TrendSlopeModeInt", 1)),
        vwap_atr_period=int(settings.get("VwapAtrPeriod", 14)),
        vwap_near_threshold_atr=float(settings.get("VwapNearThresholdAtr", 0.20)),
        use_close_for_vwap_price=bool(settings.get("UseCloseForVwapPrice", True)),
        volatility_atr_period=int(settings.get("VolatilityAtrPeriod", 14)),
        volatility_lookback_window=int(settings.get("VolatilityLookbackWindow", 100)),
        volatility_low_percentile=float(settings.get("VolatilityLowPercentile", 0.33)),
        volatility_high_percentile=float(settings.get("VolatilityHighPercentile", 0.67)),
    )


def _ema_nt(series: pd.Series, period: int) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return s.ewm(span=int(period), adjust=False, min_periods=1).mean()


def _atr_wilder_nt(bars: pd.DataFrame, period: int) -> pd.Series:
    h = pd.to_numeric(bars["high"], errors="coerce")
    l = pd.to_numeric(bars["low"], errors="coerce")
    c = pd.to_numeric(bars["close"], errors="coerce")
    prev_c = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    # NT seeds TR[0] = H[0] - L[0], then Wilder recursion.
    tr.iloc[0] = (h.iloc[0] - l.iloc[0]) if len(tr) else np.nan
    return tr.ewm(alpha=1.0 / int(period), adjust=False, min_periods=1).mean()


def _rolling_percentile_rank_nt(series: pd.Series, window: int) -> pd.Series:
    """
    Mirrors RollingPercentileRank.GetPercentileRank:

        rank = (less + 0.5 * equal) / count

    where `count` is the size of the rolling window (capped at `capacity`),
    `less` is the count of prior values strictly below the current value,
    and `equal` is the count of values within 1e-10 of the current value.
    """
    arr = pd.to_numeric(series, errors="coerce").to_numpy()
    out = np.full(len(arr), np.nan, dtype=float)
    n = int(window)
    eps = 1e-10
    for i in range(len(arr)):
        cur = arr[i]
        if np.isnan(cur):
            continue
        lo = max(0, i + 1 - n)   # NT adds-then-ranks; current value is in the window
        window_slice = arr[lo : i + 1]
        valid = window_slice[~np.isnan(window_slice)]
        if valid.size == 0:
            continue
        less = int((valid < cur - eps).sum())
        equal = int((np.abs(valid - cur) < eps).sum())
        out[i] = (less + 0.5 * equal) / valid.size
    return pd.Series(out, index=series.index)


def _assign_session_id(bars: pd.DataFrame, reset_hour: int, reset_minute: int) -> pd.Series:
    """Each bar maps to a session_id where the session resets daily at the
    configured local time. Bars at or after reset belong to that day's
    session; bars before reset belong to the previous day's session."""
    dt = pd.to_datetime(bars["dt"])
    reset_seconds = reset_hour * 3600 + reset_minute * 60
    seconds_of_day = dt.dt.hour * 3600 + dt.dt.minute * 60 + dt.dt.second
    session_date = dt.dt.normalize()
    before_reset = seconds_of_day < reset_seconds
    session_date = session_date.where(~before_reset, session_date - pd.Timedelta(days=1))
    return session_date.dt.strftime("%Y-%m-%d")


def _session_vwap(
    bars: pd.DataFrame,
    reset_hour: int,
    reset_minute: int,
    use_close: bool,
    anchor_dt: Optional[pd.Timestamp] = None,
) -> pd.Series:
    # NinjaTrader's session VWAP accumulator ALWAYS uses typical price
    # (H+L+C)/3 regardless of the UseCloseForVwapPrice flag. That flag only
    # affects which `price` is compared against vwap for the dist_atr
    # calculation in UpdateVwapRegimeFromPrimary. See PantheonBotV2.cs
    # UpdateSessionVwap (lines ~632-648).
    del use_close  # accepted for backward-compatible callers; ignored here
    typical = (
        pd.to_numeric(bars["high"], errors="coerce")
        + pd.to_numeric(bars["low"], errors="coerce")
        + pd.to_numeric(bars["close"], errors="coerce")
    ) / 3.0
    price = typical
    vol = pd.to_numeric(bars["volume"], errors="coerce").fillna(0.0)
    sess = _assign_session_id(bars, reset_hour, reset_minute)
    dt = pd.to_datetime(bars["dt"])

    # If an anchor is set, zero out contributions from bars before the anchor
    # so the first session accumulator starts at the anchor bar. Subsequent
    # sessions still reset at the configured calendar boundary because they
    # have distinct session_ids.
    if anchor_dt is not None:
        anchor = pd.Timestamp(anchor_dt)
        before_anchor = dt < anchor
        # Tag pre-anchor bars in their own ignored session so they don't
        # contaminate the first real session's cumulative sums.
        sess = sess.where(~before_anchor, "__pre_anchor__")

    pv = price * vol
    cum_pv = pv.groupby(sess).cumsum()
    cum_vol = vol.groupby(sess).cumsum()
    vwap = cum_pv.div(cum_vol).replace([np.inf, -np.inf], np.nan)
    vwap = vwap.where(cum_vol > 0, price)

    if anchor_dt is not None:
        # Pre-anchor VWAP values are not meaningful — set to NaN so callers
        # don't accidentally use them.
        vwap = vwap.where(dt >= anchor_dt, np.nan)
    return vwap


def resample_nt_right_labeled(bars_1m: pd.DataFrame, period_minutes: int) -> pd.DataFrame:
    """
    Resample minute bars to a multi-minute timeframe matching NT's bar
    boundary convention exactly.

    NinjaTrader's bar at Time[0]=T covers the half-open interval (T-period, T].
    The exported 1m bars use right-edge timestamps (the bar's close time), so
    we need closed='right', label='right' to group them correctly:

        5m bar at 00:05 = 1m bars with dt in {00:01, 00:02, 00:03, 00:04, 00:05}

    The shared `ohlcv_resample_from_bars` defaults to left/left which is
    appropriate elsewhere in the codebase but produces a one-minute alignment
    error when matching NT runtime bar values.
    """
    df = bars_1m[["dt", "open", "high", "low", "close", "volume"]].copy()
    df = df.sort_values("dt").set_index("dt")
    rule = f"{int(period_minutes)}min"
    kw = dict(rule=rule, closed="right", label="right")
    o = df["open"].resample(**kw).first()
    h = df["high"].resample(**kw).max()
    l = df["low"].resample(**kw).min()
    c = df["close"].resample(**kw).last()
    v = df["volume"].resample(**kw).sum(min_count=1)
    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["open", "high", "low", "close", "volume"]
    return out.dropna(subset=["open", "high", "low", "close"], how="any").reset_index()


def compute_htf_slope_series(htf_bars: pd.DataFrame, cfg: BarRegimeConfig) -> pd.DataFrame:
    """Compute EMA, slope, and ATR per HTF bar."""
    ema = _ema_nt(htf_bars["close"], cfg.trend_ema_period)
    atr = _atr_wilder_nt(htf_bars, cfg.trend_atr_period)
    lb = cfg.trend_slope_lookback_bars
    ema_then = ema.shift(lb)
    if cfg.trend_slope_mode_int == 1:
        slope = (ema - ema_then) / (lb * atr.where(atr > 0, np.nan))
    else:
        slope = (ema - ema_then) / lb
    out = pd.DataFrame(
        {
            "dt": pd.to_datetime(htf_bars["dt"]),
            "ema_htf": ema.values,
            "atr_htf": atr.values,
            "slope": slope.values,
        }
    )
    return out


def compute_bar_regime(
    primary_bars: pd.DataFrame,
    htf_bars: pd.DataFrame,
    cfg: Optional[BarRegimeConfig] = None,
) -> pd.DataFrame:
    """
    Build the per-primary-bar regime feature frame.

    primary_bars and htf_bars must each contain tz-aware `dt` and
    open/high/low/close/volume columns. `dt` is the bar's close time (right
    label) — matching NinjaTrader's `Time[0]` convention.
    """
    cfg = cfg or BarRegimeConfig()
    pb = primary_bars.copy().sort_values("dt").reset_index(drop=True)
    hb = htf_bars.copy().sort_values("dt").reset_index(drop=True)

    htf_features = compute_htf_slope_series(hb, cfg)

    # At primary T = HTF-boundary, NinjaTrader's behavior splits in two:
    #   - `emaTrendHtf[0]` and `atrTrendHtf[0]` READ the just-closed HTF bar
    #     directly (exact-match HTF lookup).
    #   - `currentTrendSlopeValue` is CACHED from the previous HTF close
    #     because primary OnBarUpdate fires before the same-timestamp HTF
    #     OnBarUpdate. So slope at primary T uses the HTF bar strictly
    #     before T (no exact match).
    # Hence two separate merge_asof joins below.
    pb_dt = pd.to_datetime(pb["dt"])
    primary_key = pd.DataFrame({"dt": pb_dt}).sort_values("dt")

    htf_exact = pd.merge_asof(
        primary_key,
        htf_features[["dt", "ema_htf", "atr_htf"]].sort_values("dt"),
        on="dt",
        direction="backward",
        allow_exact_matches=True,
    )
    htf_cached_slope = pd.merge_asof(
        primary_key,
        htf_features[["dt", "slope"]].sort_values("dt"),
        on="dt",
        direction="backward",
        allow_exact_matches=False,
    )
    htf_for_primary = htf_exact.assign(slope=htf_cached_slope["slope"].values)

    atr_primary = _atr_wilder_nt(pb, cfg.vwap_atr_period)
    atr_vol = _atr_wilder_nt(pb, cfg.volatility_atr_period)
    vwap = _session_vwap(
        pb,
        cfg.session_reset_hour,
        cfg.session_reset_minute,
        cfg.use_close_for_vwap_price,
        anchor_dt=cfg.vwap_anchor_dt,
    )

    price = (
        pd.to_numeric(pb["close"], errors="coerce")
        if cfg.use_close_for_vwap_price
        else (
            pd.to_numeric(pb["high"], errors="coerce")
            + pd.to_numeric(pb["low"], errors="coerce")
            + pd.to_numeric(pb["close"], errors="coerce")
        ) / 3.0
    )
    dist_atr = (price - vwap) / atr_primary.where(atr_primary > 0, np.nan)
    dist_atr = dist_atr.fillna(0.0)

    pct = _rolling_percentile_rank_nt(atr_vol, cfg.volatility_lookback_window)

    out = pd.DataFrame(
        {
            "ema_htf": htf_for_primary["ema_htf"].values,
            "atr_htf": htf_for_primary["atr_htf"].values,
            "slope": htf_for_primary["slope"].values,
            "vwap": vwap.values,
            "atr_primary": atr_primary.values,
            "dist_atr": dist_atr.values,
            "atr_vol": atr_vol.values,
            "pct": pct.values,
        }
    )
    out.insert(0, "dt", pb["dt"].reset_index(drop=True))
    return out
