from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from .registry import PatternTemplate, TemplateRegistry


# =============================================================================
# Helpers shared across detectors
# =============================================================================

def _compute_atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _compute_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _day_groups(bars: pd.DataFrame):
    """Yield (day_label, sub_dataframe) grouped by day_id or date."""
    if "day_id" in bars.columns:
        return bars.groupby("day_id", sort=False)
    return bars.groupby(bars["dt"].dt.date, sort=False)


# =============================================================================
# ORB detectors
# =============================================================================

def _orb_break_retest_detect(
    *,
    bars: pd.DataFrame,
    orb_minutes: int,
    retest_bars: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Minimal ORB break + retest detector over 1m bars.
    bars expected columns: ["dt","open","high","low","close","volume","session_id","day_id"]
    Returns boolean mask aligned to bars.index (True when signal fires).
    """
    # Guard: require expected columns
    for c in ("dt", "high", "low", "close"):
        if c not in bars.columns:
            return pd.Series([False] * len(bars), index=bars.index)

    if "day_id" in bars.columns:
        g = bars.groupby("day_id", sort=False)
    else:
        g = bars.groupby(bars["dt"].dt.date, sort=False)

    # compute ORH/ORL per day_id for first orb_minutes rows of each day
    # NOTE: this assumes bars are already filtered to a session (e.g., RTH),
    # or session_id is stable per dt for grouping.
    g = bars.groupby("day_id", sort=False)

    # pre-allocate
    orh = pd.Series(index=bars.index, dtype="float64")
    orl = pd.Series(index=bars.index, dtype="float64")

    for day, idx in g.indices.items():
        day_idx = bars.index[idx]
        first_n = day_idx[:orb_minutes] if len(day_idx) >= orb_minutes else day_idx
        orh.loc[day_idx] = bars.loc[first_n, "high"].max()
        orl.loc[day_idx] = bars.loc[first_n, "low"].min()

    close = bars["close"]

    if direction > 0:
        broke = close > orh
        # retest: within retest_bars after break, close returns near orh (<= orh)
        # minimal: signal when broke now AND any of last retest_bars closes <= orh
        past_retest = (close.shift(1) <= orh.shift(1))
        for k in range(2, retest_bars + 1):
            past_retest = past_retest | (close.shift(k) <= orh.shift(k))
        return broke & past_retest.fillna(False)
    else:
        broke = close < orl
        past_retest = (close.shift(1) >= orl.shift(1))
        for k in range(2, retest_bars + 1):
            past_retest = past_retest | (close.shift(k) >= orl.shift(k))
        return broke & past_retest.fillna(False)


def _orb_direction_detect(
    *,
    bars: pd.DataFrame,
    orb_minutes: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True at bars where the close is above (long) or below (short)
    the opening range established in the first orb_minutes of each session day.
    Useful as a trend-direction filter: is the trade aligned with the ORB breakout?
    """
    for c in ("dt", "high", "low", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    orh = pd.Series(index=bars.index, dtype="float64")
    orl = pd.Series(index=bars.index, dtype="float64")

    for _day, grp in _day_groups(bars):
        first_n = grp.index[:orb_minutes] if len(grp) >= orb_minutes else grp.index
        orh.loc[grp.index] = grp.loc[first_n, "high"].max()
        orl.loc[grp.index] = grp.loc[first_n, "low"].min()

    close = bars["close"]
    if direction > 0:
        return (close > orh).fillna(False)
    else:
        return (close < orl).fillna(False)


# =============================================================================
# MA Trend detectors
# =============================================================================

def _ma_alignment_detect(
    *,
    bars: pd.DataFrame,
    fast_period: int,
    slow_period: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True where the fast EMA is above (long) or below (short) the slow EMA.
    Confirms the trade is in the direction of the prevailing MA trend.
    """
    if "close" not in bars.columns:
        return pd.Series(False, index=bars.index)

    fast = _compute_ema(bars["close"], fast_period)
    slow = _compute_ema(bars["close"], slow_period)

    if direction > 0:
        return (fast > slow).fillna(False)
    else:
        return (fast < slow).fillna(False)


# =============================================================================
# VWAP detectors
# =============================================================================

def _vwap_reclaim_detect(
    *,
    bars: pd.DataFrame,
    hold_bars: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True where price has held above (long) or below (short) the
    daily VWAP for at least hold_bars consecutive bars.
    Signals a reclaim/rejection of VWAP with follow-through.
    """
    for c in ("high", "low", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    vol = bars["volume"].astype(float) if "volume" in bars.columns else pd.Series(1.0, index=bars.index)

    vwap = pd.Series(index=bars.index, dtype="float64")
    for _day, grp in _day_groups(bars):
        tp_grp = typical.loc[grp.index]
        v_grp = vol.loc[grp.index]
        cum_vol = v_grp.cumsum()
        vwap.loc[grp.index] = (tp_grp * v_grp).cumsum() / cum_vol.replace(0.0, np.nan)

    close = bars["close"]
    hold = max(1, int(hold_bars))

    if direction > 0:
        above = (close > vwap).astype(int)
        return (above.rolling(hold, min_periods=hold).min() == 1).fillna(False)
    else:
        below = (close < vwap).astype(int)
        return (below.rolling(hold, min_periods=hold).min() == 1).fillna(False)


# =============================================================================
# Momentum detectors
# =============================================================================

def _rsi_regime_detect(
    *,
    bars: pd.DataFrame,
    period: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True where RSI is above 50 (long) or below 50 (short).
    Confirms the trade is in a momentum-aligned regime.
    """
    if "close" not in bars.columns:
        return pd.Series(False, index=bars.index)

    rsi = _compute_rsi(bars["close"], period)

    if direction > 0:
        return (rsi > 50.0).fillna(False)
    else:
        return (rsi < 50.0).fillna(False)


# =============================================================================
# Volume detectors
# =============================================================================

def _volume_surge_detect(
    *,
    bars: pd.DataFrame,
    multiplier: float,
    lookback: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True where bar volume exceeds multiplier × rolling average volume.
    Direction-neutral confirmation of institutional participation.
    """
    if "volume" not in bars.columns:
        return pd.Series(False, index=bars.index)

    vol = bars["volume"].astype(float)
    rolling_avg = vol.rolling(int(lookback), min_periods=1).mean()
    return (vol > float(multiplier) * rolling_avg).fillna(False)


# =============================================================================
# Session detectors
# =============================================================================

def _open_drive_detect(
    *,
    bars: pd.DataFrame,
    drive_minutes: int,
    min_atr_multiple: float,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True for all bars on a day where the first drive_minutes of the session
    showed a directional move larger than min_atr_multiple × ATR(14).
    Confirms the session opened with a strong directional drive.
    """
    for c in ("open", "high", "low", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    atr14 = _compute_atr(bars, period=14)
    result = pd.Series(False, index=bars.index)

    for _day, grp in _day_groups(bars):
        if len(grp) < drive_minutes:
            continue
        drive_slice = grp.iloc[:drive_minutes]
        drive_open = grp.iloc[0]["open"]
        drive_close = drive_slice.iloc[-1]["close"]
        ref_atr = float(atr14.loc[drive_slice.index[-1]])
        if np.isnan(ref_atr) or ref_atr == 0.0:
            continue
        move = drive_close - drive_open
        threshold = float(min_atr_multiple) * ref_atr
        if direction > 0:
            is_drive = move > threshold
        else:
            is_drive = move < -threshold
        if is_drive:
            result.loc[grp.index] = True

    return result


# =============================================================================
# HTF (Higher Time-Frame) detectors
# =============================================================================

def _htf_trend_alignment_detect(
    *,
    bars: pd.DataFrame,
    htf_minutes: int,
    fast_period: int,
    slow_period: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Resamples the input bars to a higher timeframe (htf_minutes per bar),
    computes fast/slow EMA on the HTF close series, then maps the alignment
    signal back to every original bar via backward merge.

    Long: HTF fast EMA > HTF slow EMA.
    Short: HTF fast EMA < HTF slow EMA.

    Requires bars to have a 'dt' column with comparable (preferably tz-aware)
    timestamps.  Works for any input TF as long as htf_minutes > input TF.
    """
    for c in ("dt", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    dt_series = pd.to_datetime(bars["dt"])
    close_indexed = pd.Series(
        bars["close"].values, index=dt_series, dtype="float64"
    ).sort_index()

    rule = f"{int(htf_minutes)}min"
    htf_close = (
        close_indexed.resample(rule, label="left", closed="left")
        .last()
        .dropna()
    )

    if len(htf_close) < max(fast_period, slow_period) + 1:
        return pd.Series(False, index=bars.index)

    fast_ema = _compute_ema(htf_close, fast_period)
    slow_ema = _compute_ema(htf_close, slow_period)

    htf_signal: pd.Series = fast_ema > slow_ema if direction > 0 else fast_ema < slow_ema

    # Map each input bar to the most recent HTF bar that started at-or-before it
    htf_df   = pd.DataFrame({"htf_dt": htf_signal.index, "signal": htf_signal.values})
    bars_df  = pd.DataFrame({"dt": dt_series.values, "_pos": np.arange(len(bars))})

    merged = pd.merge_asof(
        bars_df.sort_values("dt"),
        htf_df.sort_values("htf_dt"),
        left_on="dt",
        right_on="htf_dt",
        direction="backward",
    ).sort_values("_pos")

    return pd.Series(merged["signal"].fillna(False).values, index=bars.index)


# =============================================================================
# Time-window detectors
# =============================================================================

def _parse_hhmm(s: str) -> int:
    """Return total minutes since midnight for an 'HH:MM' string."""
    parts = str(s).strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _high_prob_window_detect(
    *,
    bars: pd.DataFrame,
    windows: list,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True at bars whose time-of-day falls within any of the
    configured time windows.  Direction-neutral: fires whenever the
    time condition is met, regardless of trade direction.

    windows: list of dicts with 'start' and 'end' keys in 'HH:MM' format.
    Times are compared against the bar's local time (tz stripped).

    Example:
      windows:
        - {start: "08:30", end: "10:30"}
        - {start: "13:00", end: "14:30"}
    """
    if "dt" not in bars.columns:
        return pd.Series(False, index=bars.index)

    dt_parsed   = pd.to_datetime(bars["dt"])
    bar_minutes = dt_parsed.dt.hour * 60 + dt_parsed.dt.minute
    result      = pd.Series(False, index=bars.index)

    for w in (windows or []):
        try:
            start_m = _parse_hhmm(w.get("start", "00:00"))
            end_m   = _parse_hhmm(w.get("end",   "23:59"))
            result  = result | ((bar_minutes >= start_m) & (bar_minutes <= end_m))
        except Exception:
            continue

    return result.fillna(False)


# =============================================================================
# Previous-session level detectors
# =============================================================================

def _prev_level_break_detect(
    *,
    bars: pd.DataFrame,
    lookback_session: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    For each bar, looks up the high and low of the session
    lookback_session days prior (using day_id for grouping), then:

    Long:  returns True where close > prior-session high  (bullish level break)
    Short: returns True where close < prior-session low   (bearish level break)

    Requires bars to have 'day_id', 'high', 'low', and 'close' columns.
    day_id is expected to be sortable chronologically (ISO date strings work).
    """
    for c in ("high", "low", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    if "day_id" not in bars.columns:
        return pd.Series(False, index=bars.index)

    # Build per-day high/low lookup in chronological order
    session_highs: dict = {}
    session_lows:  dict = {}
    for day, grp in _day_groups(bars):
        session_highs[day] = float(grp["high"].max())
        session_lows[day]  = float(grp["low"].min())

    days = sorted(session_highs.keys())
    day_rank = {d: i for i, d in enumerate(days)}

    result = pd.Series(False, index=bars.index)
    n_back  = max(1, int(lookback_session))

    for day, grp in _day_groups(bars):
        rank = day_rank.get(day, -1)
        prev_rank = rank - n_back
        if prev_rank < 0:
            continue
        prev_day  = days[prev_rank]
        prev_high = session_highs[prev_day]
        prev_low  = session_lows[prev_day]
        close_col = bars.loc[grp.index, "close"]

        if direction > 0:
            result.loc[grp.index] = (close_col > prev_high).values
        else:
            result.loc[grp.index] = (close_col < prev_low).values

    return result.fillna(False)


# =============================================================================
# Candle structure detectors
# =============================================================================

def _higher_high_lower_low_detect(
    *,
    bars: pd.DataFrame,
    lookback: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Compares the price range of the most recent `lookback` bars against the
    `lookback` bars immediately prior to them.

    Long  (uptrend): current window high  > prior window high  AND
                     current window low   > prior window low   (HH + HL)

    Short (downtrend): current window low  < prior window low  AND
                       current window high < prior window high  (LL + LH)

    Returns True at bars where the structure condition is met.
    Requires 'high' and 'low' columns.
    """
    for c in ("high", "low"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    n   = max(2, int(lookback))
    hi  = bars["high"]
    lo  = bars["low"]

    curr_hi = hi.rolling(n, min_periods=n).max()
    curr_lo = lo.rolling(n, min_periods=n).min()
    prev_hi = hi.shift(n).rolling(n, min_periods=n).max()
    prev_lo = lo.shift(n).rolling(n, min_periods=n).min()

    if direction > 0:
        return ((curr_hi > prev_hi) & (curr_lo > prev_lo)).fillna(False)
    else:
        return ((curr_lo < prev_lo) & (curr_hi < prev_hi)).fillna(False)


# =============================================================================
# MACD detectors
# =============================================================================

def _macd_signal_detect(
    *,
    bars: pd.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    cross_window: int = 3,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Computes MACD line (fast EMA - slow EMA) and signal line (EMA of MACD).

    Long:  MACD line crossed above signal line within the last cross_window bars.
    Short: MACD line crossed below signal line within the last cross_window bars.

    Requires 'close' column.
    """
    if "close" not in bars.columns:
        return pd.Series(False, index=bars.index)

    close = bars["close"].astype(float)
    fast_ema = _compute_ema(close, int(fast_period))
    slow_ema = _compute_ema(close, int(slow_period))
    macd     = fast_ema - slow_ema
    sig_line = _compute_ema(macd, int(signal_period))

    if direction > 0:
        # MACD crossed above signal: previous bar macd <= signal, current bar macd > signal
        cross = (macd > sig_line) & (macd.shift(1) <= sig_line.shift(1))
    else:
        cross = (macd < sig_line) & (macd.shift(1) >= sig_line.shift(1))

    n = max(1, int(cross_window))
    # Rolling: True if any cross in last n bars
    return cross.rolling(n, min_periods=1).max().fillna(0).astype(bool)


# =============================================================================
# Consecutive-close detectors
# =============================================================================

def _consecutive_closes_detect(
    *,
    bars: pd.DataFrame,
    n_bars: int = 3,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True at bars where the last n_bars bars all closed in the trade
    direction relative to their own open.

    Long:  last n_bars each have close > open  (bullish bars)
    Short: last n_bars each have close < open  (bearish bars)

    Requires 'open' and 'close' columns.
    """
    for c in ("open", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    n = max(1, int(n_bars))
    if direction > 0:
        directional = (bars["close"] > bars["open"]).astype(int)
    else:
        directional = (bars["close"] < bars["open"]).astype(int)

    # True where every one of the last n bars is directional
    return (directional.rolling(n, min_periods=n).sum() == n).fillna(False)


# =============================================================================
# Engulfing candle detectors
# =============================================================================

def _engulfing_detect(
    *,
    bars: pd.DataFrame,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Detects a full-body engulfing candle pattern.

    Bullish engulf (Long):  current bar open <= prior bar close AND
                            current bar close >= prior bar open  (body wraps prior)
                            AND current bar is a bullish bar (close > open)

    Bearish engulf (Short): current bar open >= prior bar close AND
                            current bar close <= prior bar open
                            AND current bar is a bearish bar (close < open)

    Requires 'open' and 'close' columns.
    """
    for c in ("open", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    o = bars["open"].astype(float)
    c = bars["close"].astype(float)
    po = o.shift(1)
    pc = c.shift(1)

    if direction > 0:
        bullish_bar = c > o
        engulf = (o <= pc) & (c >= po) & bullish_bar
    else:
        bearish_bar = c < o
        engulf = (o >= pc) & (c <= po) & bearish_bar

    return engulf.fillna(False)


# =============================================================================
# Gap detectors
# =============================================================================

def _gap_direction_detect(
    *,
    bars: pd.DataFrame,
    min_gap_points: float = 0.0,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True at bars belonging to a session day where today's first bar open
    gapped in the trade direction vs the prior session's last bar close.

    Long:  today open > prior session close + min_gap_points  (gap up)
    Short: today open < prior session close - min_gap_points  (gap down)

    Requires 'dt', 'open', 'close', and 'day_id' columns.
    All bars within a gapped session are marked True.
    """
    for col in ("open", "close", "day_id"):
        if col not in bars.columns:
            return pd.Series(False, index=bars.index)

    min_gap = float(min_gap_points)

    # Build per-day first open and last close
    day_first_open: dict = {}
    day_last_close: dict = {}
    for day, grp in _day_groups(bars):
        day_first_open[day]  = float(grp["open"].iloc[0])
        day_last_close[day]  = float(grp["close"].iloc[-1])

    days = sorted(day_first_open.keys())

    result = pd.Series(False, index=bars.index)
    for i, day in enumerate(days):
        if i == 0:
            continue  # no prior session
        prev_day   = days[i - 1]
        gap_open   = day_first_open[day]
        prior_close = day_last_close[prev_day]
        gap_size   = gap_open - prior_close

        gapped = (
            (gap_size > min_gap)  if direction > 0 else
            (gap_size < -min_gap)
        )
        if gapped:
            day_mask = bars["day_id"] == day
            result.loc[day_mask[day_mask].index] = True

    return result.fillna(False)


# =============================================================================
# Volatility / Bollinger Band detectors
# =============================================================================

def _bb_squeeze_break_detect(
    *,
    bars: pd.DataFrame,
    period: int = 20,
    num_std: float = 2.0,
    squeeze_pct: float = 0.5,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Bollinger Band squeeze + directional breakout.

    A 'squeeze' is when the BB width (upper - lower) is <= squeeze_pct of its
    rolling max over the same period (bands contracted relative to recent max).

    After a squeeze, a breakout fires when:
      Long:  close > upper band  (price breaks out above)
      Short: close < lower band  (price breaks out below)

    Returns True at breakout bars that followed a squeeze (any bar in prior
    period was in squeeze).

    Requires 'close' column.
    """
    if "close" not in bars.columns:
        return pd.Series(False, index=bars.index)

    n   = max(2, int(period))
    k   = float(num_std)
    sqz = float(squeeze_pct)

    close  = bars["close"].astype(float)
    mid    = close.rolling(n, min_periods=n).mean()
    std_   = close.rolling(n, min_periods=n).std(ddof=1)
    upper  = mid + k * std_
    lower  = mid - k * std_
    width  = upper - lower
    max_w  = width.rolling(n, min_periods=1).max()

    in_squeeze = width <= (sqz * max_w)
    # True if any squeeze in prior n bars
    prior_squeeze = in_squeeze.shift(1).rolling(n, min_periods=1).max().fillna(0).astype(bool)

    if direction > 0:
        breakout = close > upper
    else:
        breakout = close < lower

    return (prior_squeeze & breakout).fillna(False)


# =============================================================================
# Session extreme detectors
# =============================================================================

def _new_session_extreme_detect(
    *,
    bars: pd.DataFrame,
    pct_of_range: float = 0.1,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Returns True at bars near the current session's high (Long) or low (Short).

    'Near' means within pct_of_range of the total session range from the
    session extreme.

    Long:  bar close is within pct_of_range * session_range below the session
           high (i.e. close >= session_high - pct_of_range * range)

    Short: bar close is within pct_of_range * session_range above the session
           low  (i.e. close <= session_low + pct_of_range * range)

    Requires 'high', 'low', 'close', and 'day_id' columns.
    """
    for col in ("high", "low", "close", "day_id"):
        if col not in bars.columns:
            return pd.Series(False, index=bars.index)

    pct = float(pct_of_range)
    result = pd.Series(False, index=bars.index)

    for _day, grp in _day_groups(bars):
        sess_high  = float(grp["high"].max())
        sess_low   = float(grp["low"].min())
        sess_range = sess_high - sess_low
        if sess_range == 0.0:
            continue
        tolerance = pct * sess_range
        close_col  = bars.loc[grp.index, "close"]

        if direction > 0:
            result.loc[grp.index] = (close_col >= sess_high - tolerance).values
        else:
            result.loc[grp.index] = (close_col <= sess_low + tolerance).values

    return result.fillna(False)


# =============================================================================
# Entry Candle shared helpers
# =============================================================================

def _resample_ohlcv(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """
    Resample bars to `minutes`-per-bar OHLCV resolution.

    The input bars must have a 'dt' column with comparable timestamps.
    Returns a clean DataFrame with columns: dt, open, high, low, close, volume
    (only the columns present in the input are aggregated).

    Uses left-label / left-closed bucketing consistent with the HTF detector.
    """
    if "dt" not in bars.columns:
        return pd.DataFrame()

    dt_s = pd.to_datetime(bars["dt"])
    indexed = bars.copy()
    indexed.index = dt_s
    indexed = indexed.sort_index()

    rule = f"{int(minutes)}min"
    agg: Dict[str, Any] = {}
    for col, fn in (
        ("open",   "first"),
        ("high",   "max"),
        ("low",    "min"),
        ("close",  "last"),
        ("volume", "sum"),
    ):
        if col in bars.columns:
            agg[col] = fn

    if not agg:
        return pd.DataFrame()

    drop_col = "close" if "close" in agg else next(iter(agg))
    resampled = (
        indexed.resample(rule, label="left", closed="left")
        .agg(agg)
        .dropna(subset=[drop_col])
    )
    resampled.index.name = "dt"
    return resampled.reset_index()


def _tz_to_naive_utc(s: pd.Series) -> pd.Series:
    """
    Normalise any datetime Series to tz-naive UTC for merge_asof key matching.
    If already tz-naive, returns as-is.  Handles both tz-aware pandas and
    numpy-backed datetime columns.
    """
    s = pd.to_datetime(s)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s


def _map_signal_to_bars(
    bars: pd.DataFrame,
    resampled: pd.DataFrame,
    signal_col: str,
) -> pd.Series:
    """
    Map a per-resampled-bar boolean signal column back to every original bar
    via backward merge_asof: each input bar receives the signal of the most
    recent resampled bar whose dt is at-or-before the input bar's dt.

    Both key columns are normalised to tz-naive UTC before merging so that
    tz-aware input bars (America/Denver) can be matched against the resampled
    index without a dtype mismatch error.
    """
    bars_dt    = _tz_to_naive_utc(pd.to_datetime(bars["dt"]))
    resamp_dt  = _tz_to_naive_utc(pd.to_datetime(resampled["dt"]))

    htf_df  = pd.DataFrame({
        "htf_dt": resamp_dt.values,
        "signal": resampled[signal_col].values,
    })
    bars_df = pd.DataFrame({"dt": bars_dt.values, "_pos": np.arange(len(bars))})

    merged = pd.merge_asof(
        bars_df.sort_values("dt"),
        htf_df.sort_values("htf_dt"),
        left_on="dt",
        right_on="htf_dt",
        direction="backward",
    ).sort_values("_pos")

    return pd.Series(merged["signal"].fillna(False).values, index=bars.index)


# =============================================================================
# Entry Candle detectors
# =============================================================================

def _entry_candle_volume_detect(
    *,
    bars: pd.DataFrame,
    resample_minutes: int = 5,
    lookback: int = 20,
    min_volume_ratio: float = 1.2,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Entry candle volume vs rolling N-bar average volume.

    Fires when the resampled entry candle's volume >= avg_volume * min_volume_ratio.

    Direction-neutral: high relative volume confirms conviction in either direction.

    Requires 'dt' and 'volume' columns.

    Params
    ------
    resample_minutes : target bar resolution (2, 5, 15, etc.)
    lookback         : number of prior candles used to compute avg volume
    min_volume_ratio : entry candle volume must exceed avg * this ratio
    """
    for c in ("dt", "volume"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    rs = _resample_ohlcv(bars, int(resample_minutes))
    if rs.empty or "volume" not in rs.columns:
        return pd.Series(False, index=bars.index)

    n     = max(2, int(lookback))
    ratio = float(min_volume_ratio)

    # shift(1): compare entry candle against the avg of the PRIOR n bars (no look-ahead)
    avg_vol = rs["volume"].rolling(n, min_periods=1).mean().shift(1)
    rs["signal"] = (rs["volume"] >= avg_vol * ratio).fillna(False)

    return _map_signal_to_bars(bars, rs, "signal")


def _entry_candle_body_strength_detect(
    *,
    bars: pd.DataFrame,
    resample_minutes: int = 5,
    lookback: int = 20,
    min_body_pct: float = 0.50,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Entry candle body dominance: body size as a fraction of total range,
    with the body in the trade direction.

    Long:  close > open  AND  (close - open) / (high - low) >= min_body_pct
    Short: close < open  AND  (open - close) / (high - low) >= min_body_pct

    A min_body_pct of 0.50 means the body must fill at least half the candle —
    filtering out doji / indecision bars.

    Requires 'dt', 'open', 'high', 'low', 'close' columns.

    Params
    ------
    resample_minutes : target bar resolution
    lookback         : unused (reserved for future average-body comparison)
    min_body_pct     : body / range threshold (0.0–1.0)
    """
    for c in ("dt", "open", "high", "low", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    rs = _resample_ohlcv(bars, int(resample_minutes))
    if rs.empty:
        return pd.Series(False, index=bars.index)

    pct          = float(min_body_pct)
    body_size    = (rs["close"] - rs["open"]).abs()
    candle_range = (rs["high"] - rs["low"]).replace(0.0, np.nan)
    body_pct     = body_size / candle_range

    if direction > 0:
        directional = rs["close"] > rs["open"]
    else:
        directional = rs["close"] < rs["open"]

    rs["signal"] = (directional & (body_pct >= pct)).fillna(False)
    return _map_signal_to_bars(bars, rs, "signal")


def _entry_candle_wick_rejection_detect(
    *,
    bars: pd.DataFrame,
    resample_minutes: int = 5,
    lookback: int = 20,
    max_upper_wick_pct: float = 0.20,
    min_lower_wick_pct: float = 0.10,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Wick quality filter: checks that the entry candle's wick proportions
    are consistent with directional conviction.

    Wick fractions are measured as a fraction of total candle range (high - low):
      upper_wick = (high  - max(open, close)) / range
      lower_wick = (min(open, close) - low)   / range

    Long  (bullish): small upper wick  (≤ max_upper_wick_pct)  — price not
                     rejected at the top — AND meaningful lower tail
                     (≥ min_lower_wick_pct) showing buyers stepped in.

    Short (bearish): meaningful upper wick (≥ min_lower_wick_pct) showing
                     supply overhead — AND small lower tail (≤ max_upper_wick_pct)
                     — no support below.

    Both conditions must pass simultaneously.

    Requires 'dt', 'open', 'high', 'low', 'close' columns.

    Params
    ------
    resample_minutes   : target bar resolution
    lookback           : unused (reserved)
    max_upper_wick_pct : upper wick fraction threshold (long: must be ≤ this)
    min_lower_wick_pct : lower wick fraction threshold (long: must be ≥ this)
    """
    for c in ("dt", "open", "high", "low", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    rs = _resample_ohlcv(bars, int(resample_minutes))
    if rs.empty:
        return pd.Series(False, index=bars.index)

    max_upper    = float(max_upper_wick_pct)
    min_lower    = float(min_lower_wick_pct)
    candle_range = (rs["high"] - rs["low"]).replace(0.0, np.nan)
    body_top     = rs[["open", "close"]].max(axis=1)
    body_bottom  = rs[["open", "close"]].min(axis=1)
    upper_wick   = (rs["high"] - body_top)    / candle_range
    lower_wick   = (body_bottom - rs["low"])  / candle_range

    if direction > 0:
        # Long: no overhead rejection, demand tail present
        rs["signal"] = ((upper_wick <= max_upper) & (lower_wick >= min_lower)).fillna(False)
    else:
        # Short: supply rejection above, no support below
        rs["signal"] = ((upper_wick >= min_lower) & (lower_wick <= max_upper)).fillna(False)

    return _map_signal_to_bars(bars, rs, "signal")


def _entry_candle_size_vs_avg_detect(
    *,
    bars: pd.DataFrame,
    resample_minutes: int = 5,
    lookback: int = 20,
    min_size_ratio: float = 1.1,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Total candle range vs rolling N-bar average range.

    Fires when (high - low) >= rolling_avg_range * min_size_ratio.

    Direction-neutral: an above-average range candle signals commitment from
    one side, regardless of whether it is a bull or bear bar.

    Requires 'dt', 'high', 'low' columns.

    Params
    ------
    resample_minutes : target bar resolution
    lookback         : number of prior candles used to compute avg range
    min_size_ratio   : range must exceed avg * this ratio
    """
    for c in ("dt", "high", "low"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    rs = _resample_ohlcv(bars, int(resample_minutes))
    if rs.empty:
        return pd.Series(False, index=bars.index)

    n     = max(2, int(lookback))
    ratio = float(min_size_ratio)

    candle_range = (rs["high"] - rs["low"]).abs()
    avg_range    = candle_range.rolling(n, min_periods=1).mean().shift(1)
    rs["signal"] = (candle_range >= avg_range * ratio).fillna(False)

    return _map_signal_to_bars(bars, rs, "signal")


def _entry_candle_body_vs_avg_detect(
    *,
    bars: pd.DataFrame,
    resample_minutes: int = 5,
    lookback: int = 20,
    min_body_ratio: float = 1.1,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Directional body size vs rolling N-bar average body size.

    Only counts if the body is in the trade direction, AND its size exceeds
    the average body of the prior `lookback` candles by `min_body_ratio`.

    This is stricter than body_strength (which only checks pct of range):
    the body must also be large relative to recent history, confirming this
    candle is an outsized commitment in the trade direction.

    Long:  close > open  AND  body_size >= avg_body * min_body_ratio
    Short: close < open  AND  body_size >= avg_body * min_body_ratio

    Requires 'dt', 'open', 'close' columns.

    Params
    ------
    resample_minutes : target bar resolution
    lookback         : number of prior candles used to compute avg body
    min_body_ratio   : body must exceed avg_body * this ratio
    """
    for c in ("dt", "open", "close"):
        if c not in bars.columns:
            return pd.Series(False, index=bars.index)

    rs = _resample_ohlcv(bars, int(resample_minutes))
    if rs.empty:
        return pd.Series(False, index=bars.index)

    n     = max(2, int(lookback))
    ratio = float(min_body_ratio)

    body_size = (rs["close"] - rs["open"]).abs()
    avg_body  = body_size.rolling(n, min_periods=1).mean().shift(1)

    if direction > 0:
        directional = rs["close"] > rs["open"]
    else:
        directional = rs["close"] < rs["open"]

    rs["signal"] = (directional & (body_size >= avg_body * ratio)).fillna(False)
    return _map_signal_to_bars(bars, rs, "signal")


# =============================================================================
# Registration
# =============================================================================

def register_builtin_templates(r: TemplateRegistry) -> None:
    # ORB patterns
    r.register(PatternTemplate(
        family="ORB", structure="orb_break_retest",
        detect_fn=_orb_break_retest_detect, requires_ticks=False,
    ))
    r.register(PatternTemplate(
        family="ORB", structure="orb_direction",
        detect_fn=_orb_direction_detect, requires_ticks=False,
    ))
    # MA trend
    r.register(PatternTemplate(
        family="MA_TREND", structure="ma_alignment",
        detect_fn=_ma_alignment_detect, requires_ticks=False,
    ))
    # VWAP
    r.register(PatternTemplate(
        family="VWAP", structure="vwap_reclaim",
        detect_fn=_vwap_reclaim_detect, requires_ticks=False,
    ))
    # Momentum
    r.register(PatternTemplate(
        family="MOMENTUM", structure="rsi_regime",
        detect_fn=_rsi_regime_detect, requires_ticks=False,
    ))
    # Volume
    r.register(PatternTemplate(
        family="VOLUME", structure="volume_surge",
        detect_fn=_volume_surge_detect, requires_ticks=False,
    ))
    # Session
    r.register(PatternTemplate(
        family="SESSION", structure="open_drive",
        detect_fn=_open_drive_detect, requires_ticks=False,
    ))
    # HTF (higher time-frame) alignment
    r.register(PatternTemplate(
        family="HTF", structure="htf_trend_alignment",
        detect_fn=_htf_trend_alignment_detect, requires_ticks=False,
    ))
    # Time windows
    r.register(PatternTemplate(
        family="TIME", structure="high_prob_window",
        detect_fn=_high_prob_window_detect, requires_ticks=False,
    ))
    # Previous-session levels
    r.register(PatternTemplate(
        family="PREV_SESSION", structure="prev_level_break",
        detect_fn=_prev_level_break_detect, requires_ticks=False,
    ))
    # Candle structure
    r.register(PatternTemplate(
        family="CANDLE", structure="higher_high_lower_low",
        detect_fn=_higher_high_lower_low_detect, requires_ticks=False,
    ))
    # MACD signal cross
    r.register(PatternTemplate(
        family="MOMENTUM", structure="macd_signal",
        detect_fn=_macd_signal_detect, requires_ticks=False,
    ))
    # Consecutive directional closes
    r.register(PatternTemplate(
        family="CANDLE", structure="consecutive_closes",
        detect_fn=_consecutive_closes_detect, requires_ticks=False,
    ))
    # Engulfing candle
    r.register(PatternTemplate(
        family="CANDLE", structure="engulfing",
        detect_fn=_engulfing_detect, requires_ticks=False,
    ))
    # Gap direction
    r.register(PatternTemplate(
        family="GAP", structure="gap_direction",
        detect_fn=_gap_direction_detect, requires_ticks=False,
    ))
    # Bollinger Band squeeze break
    r.register(PatternTemplate(
        family="VOLATILITY", structure="bb_squeeze_break",
        detect_fn=_bb_squeeze_break_detect, requires_ticks=False,
    ))
    # New session extreme
    r.register(PatternTemplate(
        family="RANGE", structure="new_session_extreme",
        detect_fn=_new_session_extreme_detect, requires_ticks=False,
    ))
    # Entry candle quality
    r.register(PatternTemplate(
        family="ENTRY_CANDLE", structure="volume",
        detect_fn=_entry_candle_volume_detect, requires_ticks=False,
    ))
    r.register(PatternTemplate(
        family="ENTRY_CANDLE", structure="body_strength",
        detect_fn=_entry_candle_body_strength_detect, requires_ticks=False,
    ))
    r.register(PatternTemplate(
        family="ENTRY_CANDLE", structure="wick_rejection",
        detect_fn=_entry_candle_wick_rejection_detect, requires_ticks=False,
    ))
    r.register(PatternTemplate(
        family="ENTRY_CANDLE", structure="size_vs_avg",
        detect_fn=_entry_candle_size_vs_avg_detect, requires_ticks=False,
    ))
    r.register(PatternTemplate(
        family="ENTRY_CANDLE", structure="body_vs_avg",
        detect_fn=_entry_candle_body_vs_avg_detect, requires_ticks=False,
    ))


def default_template_registry() -> TemplateRegistry:
    r = TemplateRegistry()
    register_builtin_templates(r)
    return r