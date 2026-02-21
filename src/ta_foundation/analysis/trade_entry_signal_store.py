from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ta_foundation.analysis.indicators.registry import IndicatorSpec, DEFAULT_INDICATORS
from ta_foundation.analysis.trade_enrichment import (
    TradeEnrichmentConfig,
    enrich_trades_with_bars_and_indicators,
)


@dataclass(frozen=True)
class EntrySignalStoreConfig:
    bar_tf: str = "5m"
    atr_period: int = 14
    swing_k: int = 2

    # If tick_size exists in cfg/options we can normalize to "ticks" for distance features.
    tick_size: Optional[float] = 0.25

    # how many previous bars to attach (for micro-structure-ish context)
    prev_bars: int = 1


def _norm(s: str) -> str:
    s = (s or "").lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    norm_map = {_norm(c): c for c in df.columns}
    for cand in candidates:
        k = _norm(cand)
        if k in norm_map:
            return norm_map[k]
    return None


def _detect_instrument(trades: pd.DataFrame) -> Optional[str]:
    col = _find_col(trades, ["instrument", "symbol", "market", "contract"])
    if col is None:
        return None
    s = trades[col].dropna().astype(str)
    return str(s.iloc[0]) if len(s) else None


def _get_bars_from_market(market: Any, instrument: str, tf: str) -> Optional[pd.DataFrame]:
    """
    Best-effort adapter to whatever MarketDataStore exposes.
    We try a few common shapes WITHOUT assuming a specific class.
    """
    if market is None or not instrument:
        return None

    # 1) method: get_bars(instrument, tf)
    fn = getattr(market, "get_bars", None)
    if callable(fn):
        try:
            return fn(instrument, tf)
        except Exception:
            pass

    # 2) method: bars(instrument, tf)
    fn = getattr(market, "bars", None)
    if callable(fn):
        try:
            return fn(instrument, tf)
        except Exception:
            pass

    # 3) dict-like: market["bars"][(instrument, tf)] or market.bars_map
    for attr in ("bars_map", "bars_by_key", "bars_store"):
        m = getattr(market, attr, None)
        if isinstance(m, dict):
            for key in ((instrument, tf), f"{instrument}|{tf}", instrument):
                if key in m:
                    try:
                        return m[key]
                    except Exception:
                        pass

    return None


def _safe_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _dist(a: pd.Series, b: pd.Series) -> pd.Series:
    return _safe_num(a) - _safe_num(b)


def _to_ticks(x: pd.Series, tick_size: Optional[float]) -> pd.Series:
    if not tick_size:
        return x
    return x / float(tick_size)


def build_trade_entry_signal_frame(
    packages: Dict[str, Any],
    market: Any,
    cfg: EntrySignalStoreConfig,
) -> pd.DataFrame:
    """
    Build a per-trade feature frame for entry decision discovery.

    - no file IO
    - uses market data from ctx["market"]
    - uses TradeEnrichment join pattern (as-of to entry time)
    - computes distances to prev-day levels / pivots / swings at entry

    Returns:
      DataFrame with at least:
        run_id, entry_dt, pnl, entry_price, atr, pd_* levels, pivot levels, distance features
    """
    rows: list[pd.DataFrame] = []

    for run_id, pkg in (packages or {}).items():
        trades = getattr(pkg, "trades", None)
        if trades is None or trades.empty:
            continue

        inst = _detect_instrument(trades)
        bars = _get_bars_from_market(market, inst, cfg.bar_tf)
        if bars is None or bars.empty:
            continue

        # Indicators to apply on bars BEFORE joining to trades
        specs = [
            IndicatorSpec(name="atr", params={"period": int(cfg.atr_period), "out": f"atr_{int(cfg.atr_period)}"}),
            IndicatorSpec(name="prev_day_ohlc", params={"out_prefix": "pd_"}),
            IndicatorSpec(name="prev_day_pivots", params={"in_prefix": "pd_", "out_prefix": "pd_"}),
            IndicatorSpec(name="swing_points", params={"k": int(cfg.swing_k)}),
        ]

        te_cfg = TradeEnrichmentConfig(
            timeframe=cfg.bar_tf,
            indicator_specs=specs,
            window_prev_bars=int(cfg.prev_bars),
            window_next_bars=0,
        )

        enriched = enrich_trades_with_bars_and_indicators(trades=trades, bars=bars, cfg=te_cfg)
        if enriched is None or enriched.empty:
            continue

        # normalize key trade columns
        entry_dt_col = _find_col(enriched, ["entry_dt", "entry time", "entry_time", "entry date/time"])
        pnl_col = _find_col(enriched, ["pnl", "profit", "profitcurrency", "profit currency", "p&l"])
        entry_px_col = _find_col(enriched, ["entry_price", "entry price", "entryprice"])

        out = pd.DataFrame()
        out["run_id"] = str(run_id)

        if entry_dt_col is not None:
            out["entry_dt"] = pd.to_datetime(enriched[entry_dt_col], errors="coerce")
            out["entry_hour"] = out["entry_dt"].dt.hour

        if pnl_col is not None:
            out["pnl"] = _safe_num(enriched[pnl_col])

        if entry_px_col is not None:
            out["entry_price"] = _safe_num(enriched[entry_px_col])
        else:
            # fall back to entry bar close
            if "entry_bar_close" in enriched.columns:
                out["entry_price"] = _safe_num(enriched["entry_bar_close"])
            else:
                out["entry_price"] = np.nan

        # pull indicator snapshot columns from the entry bar join
        # trade_enrichment prefixes as entry_bar_<col> where <col> is the bar column name
        atr_col = f"entry_bar_atr_{int(cfg.atr_period)}"
        out["atr"] = _safe_num(enriched[atr_col]) if atr_col in enriched.columns else np.nan

        # previous day OHLC levels (broadcast per bar, so should be present in entry snapshot)
        for k in ("pd_open", "pd_high", "pd_low", "pd_close"):
            c = f"entry_bar_{k}"
            out[k] = _safe_num(enriched[c]) if c in enriched.columns else np.nan

        # pivot levels
        for k in ("pd_pp", "pd_r1", "pd_s1", "pd_r2", "pd_s2"):
            c = f"entry_bar_{k}"
            out[k] = _safe_num(enriched[c]) if c in enriched.columns else np.nan

        # swing points (raw at entry bar)
        swing_high_col = f"entry_bar_swing_high_k{int(cfg.swing_k)}"
        swing_low_col = f"entry_bar_swing_low_k{int(cfg.swing_k)}"
        out["swing_high"] = _safe_num(enriched[swing_high_col]) if swing_high_col in enriched.columns else np.nan
        out["swing_low"] = _safe_num(enriched[swing_low_col]) if swing_low_col in enriched.columns else np.nan

        # Distance features (price - level)
        px = out["entry_price"]

        out["d_px_pd_high"] = _dist(px, out["pd_high"])
        out["d_px_pd_low"] = _dist(px, out["pd_low"])
        out["d_px_pd_close"] = _dist(px, out["pd_close"])
        out["d_px_pd_pp"] = _dist(px, out["pd_pp"])
        out["d_px_pd_r1"] = _dist(px, out["pd_r1"])
        out["d_px_pd_s1"] = _dist(px, out["pd_s1"])

        # ATR-normalized distances (robust across regimes)
        atr = out["atr"].replace(0, np.nan)
        out["d_pd_high_atr"] = out["d_px_pd_high"] / atr
        out["d_pd_low_atr"] = out["d_px_pd_low"] / atr
        out["d_pd_pp_atr"] = out["d_px_pd_pp"] / atr

        # Tick-normalized distances (nice for futures intuition)
        out["d_pd_high_ticks"] = _to_ticks(out["d_px_pd_high"], cfg.tick_size)
        out["d_pd_low_ticks"] = _to_ticks(out["d_px_pd_low"], cfg.tick_size)
        out["d_pd_pp_ticks"] = _to_ticks(out["d_px_pd_pp"], cfg.tick_size)

        rows.append(out)

    if not rows:
        return pd.DataFrame()

    res = pd.concat(rows, axis=0, ignore_index=True)

    # keep it deterministic
    if "entry_dt" in res.columns:
        res = res.sort_values(["run_id", "entry_dt"], kind="mergesort")

    return res