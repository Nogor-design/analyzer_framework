from __future__ import annotations

"""
Trade Pattern Audit
===================
For each trade in pkg.trades, locates the entry bar in market bars and
evaluates whether configured patterns fired in the trade's direction at or
near that bar.  Results are attached to:

  pkg.assets["trade_pattern_audit"]["audit_df"]      – per-trade DataFrame
  pkg.metadata["derived"]["trade_pattern_audit"]     – JSON-safe summary + diagnostics

YAML config lives under pattern_engine.trade_pattern_audit:

  trade_pattern_audit:
    enabled: true
    instrument: "NQ"          # overrides pattern_engine.instrument if set
    contract:   "H25"         # overrides pattern_engine.contract if set
    timeframe:  "5m"          # bar resolution for pattern detection
    lookback_bars: 200        # bars loaded before entry for context
                              # (needs to cover full session for VWAP/open_drive)
    signal_window_bars: 3     # 0 = exact entry bar only; N = fire within N bars before entry
    verdict_threshold: 2.0    # weighted confirming score needed for CONFIRMED verdict
    patterns:
      - family: "MA_TREND"
        structure: "ma_alignment"
        enabled: true
        weight: 2.0
        params:
          fast_period: 8
          slow_period: 21
      - family: "VWAP"
        structure: "vwap_reclaim"
        enabled: true
        weight: 1.5
        params:
          hold_bars: 3
      - family: "ORB"
        structure: "orb_direction"
        enabled: true
        weight: 1.0
        params:
          orb_minutes: 15
      - family: "MOMENTUM"
        structure: "rsi_regime"
        enabled: true
        weight: 1.0
        params:
          period: 14
      - family: "VOLUME"
        structure: "volume_surge"
        enabled: true
        weight: 1.0
        params:
          multiplier: 1.5
          lookback: 20
      - family: "SESSION"
        structure: "open_drive"
        enabled: true
        weight: 1.5
        params:
          drive_minutes: 30
          min_atr_multiple: 0.5
"""

import json
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .templates.builtins import default_template_registry


# ---------------------------------------------------------------------------
# NaN-safe stat helpers
# ---------------------------------------------------------------------------

def _safe_mean(series: pd.Series) -> Optional[float]:
    """Mean over non-NaN values.  Returns None when no valid values exist."""
    valid = series.dropna()
    return float(valid.mean()) if len(valid) > 0 else None


def _safe_sum(series: pd.Series) -> Optional[float]:
    """Sum over non-NaN values.  Returns None when no valid values exist."""
    valid = series.dropna()
    return float(valid.sum()) if len(valid) > 0 else None


def _safe_winrate(series: pd.Series) -> Optional[float]:
    """Win rate computed only over non-NaN values so NaN trades aren't counted as losses."""
    valid = series.dropna()
    return float((valid > 0).mean()) if len(valid) > 0 else None


def _to_json_safe(v: Optional[float]) -> Optional[float]:
    """Convert nan/inf to None so json.dumps never receives a non-finite float."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (not np.isfinite(f)) else f
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_bars(
    market: Any,
    instrument: str,
    contract: str,
    timeframe: str,
) -> Optional[pd.DataFrame]:
    try:
        bars = market.get_bars(
            instrument_root=instrument,
            contract=contract,
            timeframe=timeframe,
        )
        return bars if bars is not None and len(bars) > 0 else None
    except Exception:
        return None


def _find_entry_bar_idx(bars: pd.DataFrame, entry_time: Any) -> Optional[int]:
    """
    Return the integer position (iloc index) of the last bar whose dt is
    <= entry_time.  Returns None if no bar qualifies.
    """
    try:
        dt_col = bars["dt"]
        mask = dt_col <= entry_time
        if not mask.any():
            return None
        positions = np.flatnonzero(mask.values)
        return int(positions[-1])
    except Exception:
        return None


def _trade_direction(row: pd.Series) -> int:
    """Return +1 for long, -1 for short."""
    mp = row.get("market_pos", 1)
    if isinstance(mp, str):
        return 1 if mp.strip().lower() in ("long", "1") else -1
    try:
        v = int(float(mp))
        return 1 if v >= 0 else -1
    except Exception:
        return 1


def _run_pattern_at_entry(
    *,
    bars: pd.DataFrame,
    entry_idx: int,
    direction: int,
    lookback_bars: int,
    signal_window_bars: int,
    family: str,
    structure: str,
    params: Dict[str, Any],
    registry: Any,
) -> bool:
    """
    Slice bars to [entry_idx - lookback_bars + 1 .. entry_idx] (inclusive),
    run the pattern's detect_fn, then check whether the signal fired within
    the last signal_window_bars bars of that slice.
    """
    try:
        tmpl = registry.get(family, structure)
    except KeyError:
        return False

    start = max(0, entry_idx - lookback_bars + 1)
    slice_bars = bars.iloc[start : entry_idx + 1].copy()

    if len(slice_bars) == 0:
        return False

    try:
        signal = tmpl.detect_fn(bars=slice_bars, direction=direction, **params)
    except Exception:
        return False

    if not isinstance(signal, pd.Series) or len(signal) == 0:
        return False

    # signal_window_bars=0 means only the exact entry bar
    window = max(1, int(signal_window_bars)) if signal_window_bars > 0 else 1
    tail = signal.iloc[-window:]
    return bool(tail.any())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_trade_pattern_audit(
    *,
    pkg: Any,
    market: Any,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Evaluate each trade in pkg.trades against configured market patterns.

    Returns
    -------
    dict with keys:
      audit_df   : pd.DataFrame – one row per audited trade
      summary    : dict – JSON-safe aggregate stats + pattern correlation
      diagnostics: dict – JSON-safe diagnostic counters and issues
    """
    audit_cfg: Dict[str, Any] = dict(options.get("trade_pattern_audit") or {})

    # Config: resolve instrument/contract from audit block or parent PE block
    instrument = str(audit_cfg.get("instrument") or options.get("instrument") or "").strip()
    contract   = str(audit_cfg.get("contract")   or options.get("contract")   or "").strip()
    timeframe  = str(audit_cfg.get("timeframe")  or options.get("timeframe")  or "5m").strip()

    lookback_bars      = int(audit_cfg.get("lookback_bars", 200))
    signal_window_bars = int(audit_cfg.get("signal_window_bars", 3))
    verdict_threshold  = float(audit_cfg.get("verdict_threshold", 2.0))
    pattern_cfgs: List[Dict[str, Any]] = list(audit_cfg.get("patterns") or [])

    diag: Dict[str, Any] = {"ok": True, "issues": [], "counts": {}}

    # ── guard: trades ────────────────────────────────────────────────────────
    trades = getattr(pkg, "trades", None)
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        diag["ok"] = False
        diag["issues"].append("no trades available in package")
        return {"audit_df": pd.DataFrame(), "summary": {}, "diagnostics": diag}

    # ── guard: instrument / contract ─────────────────────────────────────────
    if not instrument or not contract:
        diag["ok"] = False
        diag["issues"].append(
            "instrument and contract are required (set in trade_pattern_audit or pattern_engine block)"
        )
        return {"audit_df": pd.DataFrame(), "summary": {}, "diagnostics": diag}

    # ── load bars ─────────────────────────────────────────────────────────────
    bars = _get_bars(market, instrument, contract, timeframe)
    if bars is None:
        diag["ok"] = False
        diag["issues"].append(f"no bars found for {instrument} {contract} @ {timeframe}")
        return {"audit_df": pd.DataFrame(), "summary": {}, "diagnostics": diag}

    if "dt" not in bars.columns:
        diag["ok"] = False
        diag["issues"].append("bars DataFrame is missing required 'dt' column")
        return {"audit_df": pd.DataFrame(), "summary": {}, "diagnostics": diag}

    # Ensure sorted and day_id present
    bars = bars.sort_values("dt").reset_index(drop=True)
    if "day_id" not in bars.columns:
        bars["day_id"] = bars["dt"].dt.date.astype(str)

    # ── build enabled pattern list ────────────────────────────────────────────
    registry = default_template_registry()
    enabled_patterns: List[Dict[str, Any]] = []
    for pc in pattern_cfgs:
        if not pc.get("enabled", True):
            continue
        fam    = str(pc.get("family", "")).strip()
        struct = str(pc.get("structure", "")).strip()
        if not fam or not struct:
            continue
        enabled_patterns.append({
            "family":  fam,
            "structure": struct,
            "key":     f"{fam}::{struct}",
            "weight":  float(pc.get("weight", 1.0)),
            "params":  dict(pc.get("params") or {}),
        })

    if not enabled_patterns:
        diag["issues"].append("no enabled patterns configured — audit will run but no scoring is possible")

    # ── per-trade audit ───────────────────────────────────────────────────────
    audit_rows: List[Dict[str, Any]] = []
    n_entry_miss = 0

    for _, trade in trades.iterrows():
        entry_time = trade.get("entry_time")
        trade_id   = trade.get("trade_id", "")
        raw_profit = trade.get("profit")
        # Guard against np.nan / None from parse_money returning None for blank cells
        profit: Optional[float] = None if pd.isna(raw_profit) else float(raw_profit)
        direction  = _trade_direction(trade)

        entry_idx = _find_entry_bar_idx(bars, entry_time)
        if entry_idx is None:
            n_entry_miss += 1
            continue

        # ── score each pattern ────────────────────────────────────────────────
        pattern_detail: Dict[str, bool] = {}
        confirming_score = 0.0
        confirming_names: List[str] = []

        for pc in enabled_patterns:
            fired = _run_pattern_at_entry(
                bars=bars,
                entry_idx=entry_idx,
                direction=direction,
                lookback_bars=lookback_bars,
                signal_window_bars=signal_window_bars,
                family=pc["family"],
                structure=pc["structure"],
                params=pc["params"],
                registry=registry,
            )
            pattern_detail[pc["key"]] = fired
            if fired:
                confirming_score += pc["weight"]
                confirming_names.append(pc["key"])

        n_possible = sum(pc["weight"] for pc in enabled_patterns if pc["weight"] > 0)
        pct_confirmed = round(100.0 * confirming_score / n_possible, 1) if n_possible > 0 else 0.0

        if confirming_score >= verdict_threshold:
            verdict = "CONFIRMED"
        elif confirming_score > 0:
            verdict = "PARTIAL"
        else:
            verdict = "REJECTED"

        audit_rows.append({
            "trade_id":           trade_id,
            "entry_time":         entry_time,
            "direction":          "Long" if direction > 0 else "Short",
            "profit":             profit,
            "confirming_score":   round(confirming_score, 2),
            "max_possible_score": round(n_possible, 2),
            "pct_confirmed":      pct_confirmed,
            "n_patterns_fired":   len(confirming_names),
            "confirming_patterns": ", ".join(confirming_names) if confirming_names else "—",
            "verdict":            verdict,
            "pattern_detail":     json.dumps(pattern_detail),
        })

    audit_df = pd.DataFrame(audit_rows)

    diag["counts"] = {
        "n_trades":      int(len(trades)),
        "n_audited":     int(len(audit_df)),
        "n_entry_miss":  int(n_entry_miss),
        "n_patterns":    int(len(enabled_patterns)),
        "instrument":    instrument,
        "contract":      contract,
        "timeframe":     timeframe,
    }

    # ── summary stats ─────────────────────────────────────────────────────────
    summary: Dict[str, Any] = {}
    if len(audit_df) > 0:
        total = len(audit_df)
        vc    = audit_df["verdict"].value_counts().to_dict()

        summary["n_confirmed"] = int(vc.get("CONFIRMED", 0))
        summary["n_partial"]   = int(vc.get("PARTIAL", 0))
        summary["n_rejected"]  = int(vc.get("REJECTED", 0))
        summary["pct_confirmed"] = round(100.0 * summary["n_confirmed"] / total, 1)
        summary["pct_partial"]   = round(100.0 * summary["n_partial"]   / total, 1)
        summary["pct_rejected"]  = round(100.0 * summary["n_rejected"]  / total, 1)

        for verdict in ("CONFIRMED", "PARTIAL", "REJECTED"):
            sub = audit_df[audit_df["verdict"] == verdict]
            key = verdict.lower()
            profit_col = sub["profit"] if "profit" in sub.columns else pd.Series(dtype=float)
            n_valid = int(profit_col.dropna().__len__())
            n_nan   = int(len(sub)) - n_valid
            summary[f"avg_profit_{key}"]   = _to_json_safe(_safe_mean(profit_col))
            summary[f"win_rate_{key}"]     = _to_json_safe(_safe_winrate(profit_col))
            summary[f"total_profit_{key}"] = _to_json_safe(_safe_sum(profit_col))
            summary[f"n_valid_{key}"]      = n_valid
            summary[f"n_nan_{key}"]        = n_nan

        # ── pattern-level correlation ─────────────────────────────────────────
        pattern_correlation: Dict[str, Any] = {}
        for pc in enabled_patterns:
            key = pc["key"]
            try:
                fired_mask = audit_df["pattern_detail"].apply(
                    lambda d: json.loads(d).get(key, False)
                )
                yes = audit_df[fired_mask]
                no  = audit_df[~fired_mask]
                yp = yes["profit"] if "profit" in yes.columns else pd.Series(dtype=float)
                np_ = no["profit"] if "profit" in no.columns else pd.Series(dtype=float)
                pattern_correlation[key] = {
                    "n_fired":               int(len(yes)),
                    "win_rate_fired":        _to_json_safe(_safe_winrate(yp)),
                    "avg_profit_fired":      _to_json_safe(_safe_mean(yp)),
                    "n_not_fired":           int(len(no)),
                    "win_rate_not_fired":    _to_json_safe(_safe_winrate(np_)),
                    "avg_profit_not_fired":  _to_json_safe(_safe_mean(np_)),
                    "weight":                pc["weight"],
                }
            except Exception:
                pattern_correlation[key] = {"error": "computation failed"}

        summary["pattern_correlation"] = pattern_correlation

    return {
        "audit_df":   audit_df,
        "summary":    summary,
        "diagnostics": diag,
    }
