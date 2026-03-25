from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ta_foundation.analysis.indicators.registry import IndicatorSpec
from ta_foundation.analysis.trade_enrichment import (
    TradeEnrichmentConfig,
    enrich_trades_with_bars_and_indicators,
)


@dataclass(frozen=True)
class EntrySignalStoreConfig:
    bar_tf: str = "5m"
    atr_period: int = 14
    swing_k: int = 2

    # Session structure (America/Denver, bars tz-aware on ingest)
    session_start: str = "07:30"
    globex_start: str = "16:00"
    opening_range_minutes: int = 15

    tick_size: Optional[float] = 0.25
    prev_bars: int = 1

    bars_source: str = "auto"


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


def infer_instrument_contract_from_trades(trades: pd.DataFrame) -> Optional[Tuple[str, str]]:
    if trades is None or trades.empty:
        return None
    c = _find_col(trades, ["instrument"])
    if c is None:
        return None
    v = trades[c].dropna()
    if v.empty:
        return None
    parts = str(v.iloc[0]).strip().split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def _get_bars_from_market(market: Any, instrument: str, contract: str, tf: str, source: str) -> Optional[pd.DataFrame]:
    if market is None or not instrument or not contract:
        return None

    fn = getattr(market, "get_bars", None)
    if callable(fn):
        try:
            return fn(instrument, contract, timeframe=tf, source=source)
        except TypeError:
            try:
                return fn(instrument, contract, tf, source)
            except Exception:
                return None
        except Exception:
            return None

    bars_cache = getattr(market, "bars_cache", None)
    if isinstance(bars_cache, dict):
        for src in (source, "auto", "ticks", "bars"):
            key = (instrument, contract, tf, src)
            if key in bars_cache:
                try:
                    return bars_cache[key]
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
    rows: list[pd.DataFrame] = []

    for run_id, pkg in (packages or {}).items():
        trades = getattr(pkg, "trades", None)
        if trades is None or trades.empty:
            continue

        ic = infer_instrument_contract_from_trades(trades)
        if ic is None:
            continue
        instrument, contract = ic

        bars = _get_bars_from_market(market, instrument, contract, cfg.bar_tf, cfg.bars_source)
        if bars is None or bars.empty:
            continue

        specs = [
            IndicatorSpec(name="atr", params={"period": int(cfg.atr_period), "out": f"atr_{int(cfg.atr_period)}"}),
            IndicatorSpec(name="prev_day_ohlc", params={"out_prefix": "pd_"}),
            IndicatorSpec(name="prev_day_pivots", params={"in_prefix": "pd_", "out_prefix": "pd_"}),
            IndicatorSpec(name="swing_points", params={"k": int(cfg.swing_k)}),

            IndicatorSpec(
                name="opening_range",
                params={
                    "session_start": str(cfg.session_start),
                    "globex_start": str(cfg.globex_start),
                    "minutes": int(cfg.opening_range_minutes),
                    "out_prefix": "or_",
                },
            ),
            IndicatorSpec(
                name="overnight_levels",
                params={
                    "session_start": str(cfg.session_start),
                    "globex_start": str(cfg.globex_start),
                    "out_prefix": "on_",
                },
            ),
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

        entry_dt_col = _find_col(enriched, ["entry_dt", "entry time", "entry_time", "entry date/time", "entrydatetime"])
        pnl_col = _find_col(enriched, ["pnl", "profit", "net profit", "netprofit", "profitcurrency", "profit currency", "p&l"])
        entry_px_col = _find_col(enriched, ["entry_price", "entry price", "entryprice"])

        # CRITICAL FIX: seed index so scalar assignments broadcast to rows
        out = pd.DataFrame(index=enriched.index)

        out["run_id"] = str(run_id)
        out["instrument"] = instrument
        out["contract"] = contract

        if entry_dt_col is not None:
            out["entry_dt"] = pd.to_datetime(enriched[entry_dt_col], errors="coerce")
            out["entry_hour"] = out["entry_dt"].dt.hour

        if pnl_col is not None:
            out["pnl"] = _safe_num(enriched[pnl_col])

        if entry_px_col is not None:
            out["entry_price"] = _safe_num(enriched[entry_px_col])
        else:
            out["entry_price"] = _safe_num(enriched["entry_bar_close"]) if "entry_bar_close" in enriched.columns else np.nan

        atr_col = f"entry_bar_atr_{int(cfg.atr_period)}"
        out["atr"] = _safe_num(enriched[atr_col]) if atr_col in enriched.columns else np.nan

        for k in ("pd_open", "pd_high", "pd_low", "pd_close"):
            c = f"entry_bar_{k}"
            out[k] = _safe_num(enriched[c]) if c in enriched.columns else np.nan

        for k in ("pd_pp", "pd_r1", "pd_s1", "pd_r2", "pd_s2"):
            c = f"entry_bar_{k}"
            out[k] = _safe_num(enriched[c]) if c in enriched.columns else np.nan

        sh = f"entry_bar_swing_high_k{int(cfg.swing_k)}"
        sl = f"entry_bar_swing_low_k{int(cfg.swing_k)}"
        out["swing_high"] = _safe_num(enriched[sh]) if sh in enriched.columns else np.nan
        out["swing_low"] = _safe_num(enriched[sl]) if sl in enriched.columns else np.nan

        for k in ("or_high", "or_low", "on_high", "on_low"):
            c = f"entry_bar_{k}"
            out[k] = _safe_num(enriched[c]) if c in enriched.columns else np.nan

        px = out["entry_price"]
        atr = out["atr"].replace(0, np.nan)

        out["d_px_pd_high"] = _dist(px, out["pd_high"])
        out["d_px_pd_low"] = _dist(px, out["pd_low"])
        out["d_px_pd_close"] = _dist(px, out["pd_close"])
        out["d_px_pd_pp"] = _dist(px, out["pd_pp"])

        out["d_px_or_high"] = _dist(px, out["or_high"])
        out["d_px_or_low"] = _dist(px, out["or_low"])
        out["d_px_on_high"] = _dist(px, out["on_high"])
        out["d_px_on_low"] = _dist(px, out["on_low"])

        out["d_pd_high_atr"] = out["d_px_pd_high"] / atr
        out["d_pd_low_atr"] = out["d_px_pd_low"] / atr
        out["d_pd_pp_atr"] = out["d_px_pd_pp"] / atr

        out["d_or_high_atr"] = out["d_px_or_high"] / atr
        out["d_or_low_atr"] = out["d_px_or_low"] / atr
        out["d_on_high_atr"] = out["d_px_on_high"] / atr
        out["d_on_low_atr"] = out["d_px_on_low"] / atr

        out["d_pd_high_ticks"] = _to_ticks(out["d_px_pd_high"], cfg.tick_size)
        out["d_pd_low_ticks"] = _to_ticks(out["d_px_pd_low"], cfg.tick_size)
        out["d_pd_pp_ticks"] = _to_ticks(out["d_px_pd_pp"], cfg.tick_size)

        out["d_or_high_ticks"] = _to_ticks(out["d_px_or_high"], cfg.tick_size)
        out["d_or_low_ticks"] = _to_ticks(out["d_px_or_low"], cfg.tick_size)
        out["d_on_high_ticks"] = _to_ticks(out["d_px_on_high"], cfg.tick_size)
        out["d_on_low_ticks"] = _to_ticks(out["d_px_on_low"], cfg.tick_size)

        rows.append(out.reset_index(drop=True))

    if not rows:
        return pd.DataFrame()

    res = pd.concat(rows, axis=0, ignore_index=True)
    if "entry_dt" in res.columns:
        res = res.sort_values(["run_id", "entry_dt"], kind="mergesort")
    return res