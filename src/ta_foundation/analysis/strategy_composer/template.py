from __future__ import annotations

"""
Strategy Template Interpreter
==============================
Maps a strategy JSON template to callable signal functions and outcome
configuration usable by StrategyComposer.

Supported entry_signal types
------------------------------
  candle_pattern  — existing PATTERN_REGISTRY (large_body, engulfing_bullish, …)
  ma_cross        — fast MA crosses above/below slow MA
  ma_touch        — price dips to MA and closes back on the correct side
  breakout        — close beyond N-bar high/low
  orb             — Opening Range Breakout using existing opening_range indicator
  rsi_threshold   — RSI crosses a level
  price_level     — price touches VWAP / OR level / prev-day level and bounces

Supported entry_filters
------------------------
  trend       — price above/below SMA or EMA
  volatility  — ATR above/below rolling median or percentile
  rsi         — RSI above/below a threshold
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# MA helpers (no import dependency on indicators module)
# ---------------------------------------------------------------------------

def _sma(series: pd.Series, period: int) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").rolling(period, min_periods=period).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").ewm(span=period, adjust=False, min_periods=period).mean()


def _ma(series: pd.Series, ma_type: str, period: int) -> pd.Series:
    return _ema(series, period) if str(ma_type).lower() == "ema" else _sma(series, period)


def _rsi(series: pd.Series, period: int) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(bars: pd.DataFrame, period: int) -> pd.Series:
    h = pd.to_numeric(bars["high"], errors="coerce")
    lo = pd.to_numeric(bars["low"], errors="coerce")
    c = pd.to_numeric(bars["close"], errors="coerce")
    tr = pd.concat([(h - lo).abs(), (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _dir_val(direction: str) -> int:
    return 1 if str(direction).lower() == "long" else -1


def _to_signals(bars: pd.DataFrame, mask: pd.Series, direction: int) -> pd.DataFrame:
    """Return a signals DataFrame from a boolean mask on bars."""
    matched = bars[mask].copy()
    if matched.empty:
        return pd.DataFrame()
    matched["direction"] = direction
    return matched.reset_index(drop=True)


def _concat_directions(*dfs: pd.DataFrame) -> pd.DataFrame:
    parts = [d for d in dfs if d is not None and not d.empty]
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).sort_values("dt").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Signal generators — one per entry_signal type
# ---------------------------------------------------------------------------

def _gen_candle_pattern(bars: pd.DataFrame, cfg: dict, direction: str) -> pd.DataFrame:
    from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
    from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern, PATTERN_REGISTRY

    pattern = cfg.get("pattern", "engulfing_bullish")
    if pattern not in PATTERN_REGISTRY:
        raise ValueError(
            f"Unknown candle pattern: {pattern!r}. "
            f"Available: {sorted(PATTERN_REGISTRY)}"
        )

    params = {k: v for k, v in cfg.items() if not k.startswith("_") and k not in ("type", "pattern", "timing")}
    dir_val = 0 if direction == "both" else _dir_val(direction)
    params["direction"] = dir_val

    enriched = compute_candle_features(bars.copy(), params)
    return detect_pattern(pattern, enriched, params)


def _gen_ma_cross(bars: pd.DataFrame, cfg: dict, direction: str) -> pd.DataFrame:
    close = pd.to_numeric(bars["close"], errors="coerce")
    fast = _ma(close, cfg.get("fast_type", "ema"), int(cfg.get("fast_period", 9)))
    slow = _ma(close, cfg.get("slow_type", "sma"), int(cfg.get("slow_period", 21)))

    parts = []
    if direction in ("long", "both"):
        cross = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        parts.append(_to_signals(bars, cross, 1))
    if direction in ("short", "both"):
        cross = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        parts.append(_to_signals(bars, cross, -1))
    return _concat_directions(*parts)


def _gen_ma_touch(bars: pd.DataFrame, cfg: dict, direction: str) -> pd.DataFrame:
    close = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    period = int(cfg.get("period", 20))
    tolerance_pct = float(cfg.get("touch_tolerance_pct", 0.1))
    ma = _ma(close, cfg.get("ma_type", "ema"), period)
    bar_range = (high - low).replace(0, np.nan)
    tol = bar_range * tolerance_pct

    parts = []
    if direction in ("long", "both"):
        # Low touches the MA within tolerance, close is back above MA
        touched = low <= (ma + tol)
        bounced = close > ma
        parts.append(_to_signals(bars, touched & bounced, 1))
    if direction in ("short", "both"):
        touched = high >= (ma - tol)
        bounced = close < ma
        parts.append(_to_signals(bars, touched & bounced, -1))
    return _concat_directions(*parts)


def _gen_breakout(bars: pd.DataFrame, cfg: dict, direction: str) -> pd.DataFrame:
    lookback = int(cfg.get("lookback_bars", 20))
    confirmation = int(cfg.get("confirmation_bars", 1))
    close = pd.to_numeric(bars["close"], errors="coerce")

    # Use shift(1) so we never look at current bar in the rolling window
    rolling_high = close.shift(1).rolling(lookback, min_periods=lookback).max()
    rolling_low = close.shift(1).rolling(lookback, min_periods=lookback).min()

    # confirmation_bars: close must be above/below for N consecutive bars
    def _consecutive(cond: pd.Series, n: int) -> pd.Series:
        if n <= 1:
            return cond
        return cond.rolling(n, min_periods=n).sum() == n

    parts = []
    if direction in ("long", "both"):
        above = close > rolling_high
        parts.append(_to_signals(bars, _consecutive(above, confirmation), 1))
    if direction in ("short", "both"):
        below = close < rolling_low
        parts.append(_to_signals(bars, _consecutive(below, confirmation), -1))
    return _concat_directions(*parts)


def _gen_orb(bars: pd.DataFrame, cfg: dict, direction: str) -> pd.DataFrame:
    from ta_foundation.analysis.indicators.basic import opening_range

    params = {
        "session_start": cfg.get("session_start", "07:30"),
        "minutes": int(cfg.get("orb_minutes", 15)),
        "out_prefix": "or_",
    }
    enriched = opening_range(bars.copy(), params)

    if "or_high" not in enriched.columns or "or_low" not in enriched.columns:
        return pd.DataFrame()

    close = pd.to_numeric(enriched["close"], errors="coerce")
    or_high = pd.to_numeric(enriched["or_high"], errors="coerce")
    or_low = pd.to_numeric(enriched["or_low"], errors="coerce")

    # Only signal after the OR period has closed (or_high/or_low are populated)
    valid = or_high.notna() & or_low.notna()
    retest = int(cfg.get("retest_bars", 0))

    parts = []
    if direction in ("long", "both"):
        broke_above = (close > or_high) & valid
        if retest > 0:
            # Allow a pullback: price came back near OR high then re-broke
            prev_low = pd.to_numeric(enriched["low"], errors="coerce").shift(1).rolling(retest).min()
            broke_above = broke_above & (prev_low <= or_high * 1.001)
        parts.append(_to_signals(enriched, broke_above, 1))
    if direction in ("short", "both"):
        broke_below = (close < or_low) & valid
        if retest > 0:
            prev_high = pd.to_numeric(enriched["high"], errors="coerce").shift(1).rolling(retest).max()
            broke_below = broke_below & (prev_high >= or_low * 0.999)
        parts.append(_to_signals(enriched, broke_below, -1))
    return _concat_directions(*parts)


def _gen_rsi_threshold(bars: pd.DataFrame, cfg: dict, direction: str) -> pd.DataFrame:
    period = int(cfg.get("period", 14))
    threshold = float(cfg.get("threshold", 30))
    cross_dir = cfg.get("cross_direction", "cross_above")
    rsi_vals = _rsi(pd.to_numeric(bars["close"], errors="coerce"), period)

    parts = []
    if cross_dir == "cross_above" and direction in ("long", "both"):
        cross = (rsi_vals > threshold) & (rsi_vals.shift(1) <= threshold)
        parts.append(_to_signals(bars, cross, 1))
    if cross_dir == "cross_below" and direction in ("short", "both"):
        cross = (rsi_vals < threshold) & (rsi_vals.shift(1) >= threshold)
        parts.append(_to_signals(bars, cross, -1))
    return _concat_directions(*parts)


def _gen_price_level(bars: pd.DataFrame, cfg: dict, direction: str) -> pd.DataFrame:
    from ta_foundation.analysis.indicators.basic import (
        session_vwap, opening_range, prev_day_ohlc, overnight_levels
    )

    level_type = cfg.get("level_type", "vwap")
    touch_dir = cfg.get("touch_direction", "bounce_up")
    tol_ticks = float(cfg.get("touch_tolerance_ticks", 4))
    tick_size = float(cfg.get("tick_size", 0.25))
    tol = tol_ticks * tick_size

    enriched = bars.copy()

    # Build the level series
    if level_type == "vwap":
        if "volume" not in enriched.columns:
            return pd.DataFrame()
        enriched = session_vwap(enriched, {"out": "_level"})
        level_col = "_level"
    elif level_type in ("or_high", "or_low"):
        orb_min = int(cfg.get("orb_minutes", 15))
        enriched = opening_range(enriched, {"minutes": orb_min, "out_prefix": "or_"})
        level_col = "or_high" if level_type == "or_high" else "or_low"
    elif level_type in ("pd_high", "pd_low"):
        enriched = prev_day_ohlc(enriched, {"out_prefix": "pd_"})
        level_col = "pd_high" if level_type == "pd_high" else "pd_low"
    elif level_type in ("overnight_high", "overnight_low"):
        enriched = overnight_levels(enriched, {"out_prefix": "on_"})
        level_col = "on_high" if level_type == "overnight_high" else "on_low"
    else:
        raise ValueError(f"Unknown level_type: {level_type!r}")

    if level_col not in enriched.columns:
        return pd.DataFrame()

    level = pd.to_numeric(enriched[level_col], errors="coerce")
    close = pd.to_numeric(enriched["close"], errors="coerce")
    low = pd.to_numeric(enriched["low"], errors="coerce")
    high = pd.to_numeric(enriched["high"], errors="coerce")

    parts = []
    if touch_dir == "bounce_up" and direction in ("long", "both"):
        touched = low <= (level + tol)
        bounced = close > level
        parts.append(_to_signals(enriched, touched & bounced & level.notna(), 1))
    if touch_dir == "bounce_down" and direction in ("short", "both"):
        touched = high >= (level - tol)
        bounced = close < level
        parts.append(_to_signals(enriched, touched & bounced & level.notna(), -1))
    return _concat_directions(*parts)


_SIGNAL_GENERATORS = {
    "candle_pattern": _gen_candle_pattern,
    "ma_cross":       _gen_ma_cross,
    "ma_touch":       _gen_ma_touch,
    "breakout":       _gen_breakout,
    "orb":            _gen_orb,
    "rsi_threshold":  _gen_rsi_threshold,
    "price_level":    _gen_price_level,
}


# ---------------------------------------------------------------------------
# Filter applicators
# ---------------------------------------------------------------------------

def _filter_trend(bars: pd.DataFrame, signals: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    period = int(cfg.get("period", 50))
    indicator = cfg.get("indicator", "sma")
    condition = cfg.get("condition", "price_above")

    ma = _ma(pd.to_numeric(bars["close"], errors="coerce"), indicator, period)
    close = pd.to_numeric(bars["close"], errors="coerce")

    keep = (close > ma) if condition == "price_above" else (close < ma)
    keep_at_signal = keep.reindex(bars.index)

    # Map keep mask to signal rows via dt
    keep_dt = set(bars.loc[keep_at_signal[keep_at_signal].index, "dt"])
    return signals[signals["dt"].isin(keep_dt)].copy()


def _filter_volatility(bars: pd.DataFrame, signals: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    period = int(cfg.get("period", 14))
    lookback = int(cfg.get("lookback", 20))
    condition = cfg.get("condition", "above_median")

    atr_vals = _atr(bars, period)
    if "above" in condition:
        threshold = atr_vals.shift(1).rolling(lookback, min_periods=lookback).median()
        keep = atr_vals > threshold
    else:
        threshold = atr_vals.shift(1).rolling(lookback, min_periods=lookback).median()
        keep = atr_vals < threshold

    keep_dt = set(bars.loc[keep[keep].index, "dt"])
    return signals[signals["dt"].isin(keep_dt)].copy()


def _filter_rsi(bars: pd.DataFrame, signals: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    period = int(cfg.get("period", 14))
    threshold = float(cfg.get("threshold", 60))
    condition = cfg.get("condition", "below")

    rsi_vals = _rsi(pd.to_numeric(bars["close"], errors="coerce"), period)
    keep = (rsi_vals < threshold) if condition == "below" else (rsi_vals > threshold)
    keep_dt = set(bars.loc[keep[keep].index, "dt"])
    return signals[signals["dt"].isin(keep_dt)].copy()


_FILTERS = {
    "trend":      _filter_trend,
    "volatility": _filter_volatility,
    "rsi":        _filter_rsi,
}


def _apply_session_filter(signals: pd.DataFrame, session_rules: dict) -> pd.DataFrame:
    if signals.empty:
        return signals
    session_start = session_rules.get("session_start", "08:30:00")
    session_end = session_rules.get("session_end", "15:00:00")

    dt_col = "signal_dt" if "signal_dt" in signals.columns else "dt"
    dt = pd.to_datetime(signals[dt_col])

    start_h, start_m = int(session_start[:2]), int(session_start[3:5])
    end_h, end_m = int(session_end[:2]), int(session_end[3:5])

    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m
    bar_mins = dt.dt.hour * 60 + dt.dt.minute

    in_session = (bar_mins >= start_mins) & (bar_mins < end_mins)
    return signals[in_session].copy()


# ---------------------------------------------------------------------------
# StrategyTemplate
# ---------------------------------------------------------------------------

class StrategyTemplate:
    """
    Parsed and validated strategy template.

    Parameters
    ----------
    d : dict
        Raw template dict (from JSON file or LLM output).

    Usage
    -----
        tmpl = StrategyTemplate.from_file("my_strategy.json")
        signals = tmpl.generate_signals(bars_5m)
        outcome_cfg = tmpl.to_outcome_config()
    """

    def __init__(self, d: dict) -> None:
        # Strip internal documentation keys
        self._d = {k: v for k, v in d.items() if not k.startswith("_")}
        self._strip_doc_keys(self._d)
        self._validate()

    @classmethod
    def from_file(cls, path: str | Path) -> "StrategyTemplate":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyTemplate":
        return cls(d)

    def _strip_doc_keys(self, obj: Any) -> None:
        if isinstance(obj, dict):
            for k in list(obj.keys()):
                if k.startswith("_"):
                    del obj[k]
                else:
                    self._strip_doc_keys(obj[k])
        elif isinstance(obj, list):
            for item in obj:
                self._strip_doc_keys(item)

    def _validate(self) -> None:
        if "entry_signal" not in self._d:
            raise ValueError("Template missing required field: entry_signal")
        sig_type = self._d["entry_signal"].get("type")
        if sig_type not in _SIGNAL_GENERATORS:
            raise ValueError(
                f"entry_signal.type {sig_type!r} is not supported. "
                f"Choose from: {sorted(_SIGNAL_GENERATORS)}"
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._d.get("template_name", "unnamed")

    @property
    def hypothesis(self) -> str:
        return self._d.get("hypothesis", "")

    @property
    def instrument(self) -> str:
        return self._d.get("instrument", "")

    @property
    def contract(self) -> str:
        return self._d.get("contract", "")

    @property
    def timeframe(self) -> str:
        return self._d.get("timeframe", "5m")

    @property
    def direction(self) -> str:
        return self._d.get("direction", "both")

    @property
    def timing(self) -> str:
        return self._d.get("entry_signal", {}).get("timing", "next_open")

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def generate_signals(self, bars_tf: pd.DataFrame) -> pd.DataFrame:
        """
        Generate entry signals from resampled bars.

        Parameters
        ----------
        bars_tf
            OHLCV DataFrame resampled to ``self.timeframe``.
            Must contain: dt, open, high, low, close.

        Returns
        -------
        Signals DataFrame with at minimum: dt, direction columns.
        """
        sig_cfg = self._d["entry_signal"]
        sig_type = sig_cfg["type"]
        direction = self.direction

        gen_fn = _SIGNAL_GENERATORS[sig_type]
        raw = gen_fn(bars_tf, sig_cfg, direction)

        if raw.empty:
            return pd.DataFrame()

        # Apply entry filters
        for flt in self._d.get("entry_filters", []):
            flt_type = flt.get("type")
            if flt_type in _FILTERS:
                raw = _FILTERS[flt_type](bars_tf, raw, flt)
                if raw.empty:
                    return pd.DataFrame()

        # Apply session rules
        session_rules = self._d.get("session_rules", {})
        if session_rules.get("enabled"):
            raw = _apply_session_filter(raw, session_rules)

        return raw.reset_index(drop=True)

    def to_outcome_config(self) -> dict:
        """
        Build an outcome simulator config from the template's exit rules.
        """
        stop_ticks = int(self._d.get("hard_stop_ticks_cap", 44))
        target_ticks = int(self._d.get("initial_target_ticks", 30))
        max_hold = int(self._d.get("max_hold_bars", 20))

        tp_list = [target_ticks]
        partial = self._d.get("partial_rules", {})
        if partial.get("enabled") and "partial_target_ticks" in partial:
            tp_list = sorted(set([int(partial["partial_target_ticks"]), target_ticks]))

        return {
            "atr":  {"enabled": False},
            "ticks": {
                "enabled": True,
                "take_profit": tp_list,
                "stop": [stop_ticks],
            },
            "max_bars_timeout": max_hold,
            "timeout_result": "loss",
            "tick_size": 0.25,
            "tick_value": 5.00,
            "commission_per_side": 2.09,
            "slippage_ticks": 1,
        }

    def to_dict(self) -> dict:
        return dict(self._d)
