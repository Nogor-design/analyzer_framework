from __future__ import annotations

"""
Strategy Classification
=======================
Phase 3 — Labels each strategy candidate as one of:

  automated         — fully rule-based, no discretionary input required at run-time
  hybrid            — rule-based entry + discretionary filter (e.g. news, session selection)
  semi_discretionary — entry is discretionary; system only manages position

Classification is derived from observable metadata signals, not ML:

  Signal                         Evidence                       Weight toward "automated"
  ─────────────────────────────  ─────────────────────────────  ──────────────────────────
  NT8 backtest origin            settings.csv present           strong
  Regime-conditional performance regime_summary populated       moderate
  Pattern engine coverage        n_patterns_fired > 0           moderate
  Session concentration          trades spread across sessions  weak
  Trade count adequacy           n_trades >= 50                 weak
  Consistent hold time           std(duration) low              weak
  Consistent win-rate by session std(session_wr) low            weak

Heuristic tiers (score 0–100):
  >= 70 → automated
  40–69 → hybrid
  <  40 → semi_discretionary

Entry point: classify_strategy(pkg) → dict
Attaches to pkg.metadata["derived"]["strategy_discovery"]["classification"]
"""

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return default if not np.isfinite(f) else f
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Signal extractors
# ---------------------------------------------------------------------------

def _has_settings(pkg: Any) -> bool:
    """NT8 settings.csv present → strategy is parameterized and automatable."""
    settings = getattr(pkg, "settings", None)
    if settings is None:
        return False
    if isinstance(settings, pd.DataFrame):
        return len(settings) > 0
    return bool(settings)


def _has_regime_data(sd: Dict[str, Any]) -> bool:
    """Regime summary was populated (market context was considered)."""
    regime_summary = sd.get("regime_summary")
    return bool(regime_summary and len(regime_summary) > 0)


def _has_pattern_coverage(sd: Dict[str, Any]) -> bool:
    """Pattern engine fired on at least some trades."""
    imp = sd.get("importance") or {}
    diag = imp.get("diagnostics") or {}
    # Use feature matrix presence as a proxy
    n_features = diag.get("n_features_analyzed", 0)
    return n_features > 0


def _pattern_score_predictive(sd: Dict[str, Any]) -> bool:
    """pattern_score is among the top features → system uses rule-based pattern conditions."""
    imp = sd.get("importance") or {}
    top = imp.get("top_features") or []
    return "pattern_score" in top or any(str(f).startswith("pat_") for f in top[:5])


def _session_spread(evaluation: Dict[str, Any]) -> float:
    """
    How spread out trades are across sessions. Returns 0.0–1.0.
    1.0 = trades perfectly distributed; 0.0 = all in one session.
    """
    by_session = evaluation.get("by_session") or {}
    counts = [v.get("n_trades", 0) for v in by_session.values() if isinstance(v, dict)]
    if not counts or sum(counts) == 0:
        return 0.5  # neutral
    total = sum(counts)
    fracs = [c / total for c in counts]
    # Normalized entropy: max_entropy = log(n), actual = -sum(p*log(p))
    n = len(fracs)
    if n < 2:
        return 0.0
    entropy = -sum(p * np.log(p + 1e-12) for p in fracs)
    max_entropy = np.log(n)
    return float(entropy / max_entropy) if max_entropy > 0 else 0.5


def _hold_time_consistency(trades: Optional[pd.DataFrame]) -> float:
    """
    Low coefficient of variation in trade durations → consistent automated logic.
    Returns 0.0–1.0 (1.0 = perfectly consistent).
    """
    if trades is None or trades.empty:
        return 0.5

    # Try to find duration column
    dur_col = None
    for candidate in ["duration_min", "duration", "hold_min", "hold_time"]:
        if candidate in trades.columns:
            dur_col = candidate
            break

    if dur_col is None:
        # Derive from entry/exit times if present
        entry_col = next((c for c in trades.columns if "entry" in str(c).lower() and "time" in str(c).lower()), None)
        exit_col  = next((c for c in trades.columns if "exit" in str(c).lower() and "time" in str(c).lower()), None)
        if entry_col and exit_col:
            try:
                entry_dt = pd.to_datetime(trades[entry_col], errors="coerce")
                exit_dt  = pd.to_datetime(trades[exit_col], errors="coerce")
                dur_min  = (exit_dt - entry_dt).dt.total_seconds() / 60.0
            except Exception:
                return 0.5
        else:
            return 0.5
    else:
        dur_min = pd.to_numeric(trades[dur_col], errors="coerce")

    dur_min = dur_min.dropna()
    if len(dur_min) < 5:
        return 0.5
    mean_dur = float(dur_min.mean())
    std_dur  = float(dur_min.std())
    if mean_dur <= 0:
        return 0.5
    cv = std_dur / mean_dur  # coefficient of variation
    # CV < 0.5 = consistent, CV > 2.0 = highly variable
    score = 1.0 - min(1.0, cv / 2.0)
    return float(score)


def _session_winrate_consistency(evaluation: Dict[str, Any]) -> float:
    """
    Low variance in win rate across sessions → strategy works mechanically.
    Returns 0.0–1.0.
    """
    by_session = evaluation.get("by_session") or {}
    wrs = [
        v.get("win_rate", None)
        for v in by_session.values()
        if isinstance(v, dict) and v.get("win_rate") is not None
    ]
    if len(wrs) < 2:
        return 0.5
    std_wr = float(np.std(wrs))
    # std < 0.05 = consistent, std > 0.20 = inconsistent
    score = 1.0 - min(1.0, std_wr / 0.20)
    return float(score)


# ---------------------------------------------------------------------------
# Scoring and classification
# ---------------------------------------------------------------------------

def _compute_automation_score(pkg: Any, sd: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute automation score (0–100) from observable signals.
    Returns score breakdown dict.
    """
    evaluation = sd.get("evaluation") or {}
    trades = getattr(pkg, "trades", None)

    # Signal scores (each 0–1)
    signals: Dict[str, float] = {}

    # Strong signals (weight 25 each → 50 total)
    signals["has_settings"]    = 1.0 if _has_settings(pkg) else 0.0
    signals["has_regime_data"] = 1.0 if _has_regime_data(sd) else 0.5

    # Moderate signals (weight 15 each → 30 total)
    signals["pattern_coverage"]  = 1.0 if _has_pattern_coverage(sd) else 0.3
    signals["pattern_predictive"] = 1.0 if _pattern_score_predictive(sd) else 0.4

    # Weak signals (weight 5 each → 20 total)
    signals["session_spread"]     = _session_spread(evaluation)
    signals["hold_time_consistency"] = _hold_time_consistency(trades)
    signals["session_wr_consistency"] = _session_winrate_consistency(evaluation)

    n_trades = _safe_float(evaluation.get("n_trades"), 0.0)
    signals["trade_count"] = min(1.0, n_trades / 100.0)  # 100+ trades = full score

    # Weighted sum
    weights = {
        "has_settings":           25.0,
        "has_regime_data":        15.0,
        "pattern_coverage":       15.0,
        "pattern_predictive":     15.0,
        "session_spread":          7.0,
        "hold_time_consistency":   7.0,
        "session_wr_consistency":  8.0,
        "trade_count":             8.0,
    }

    total_weight = sum(weights.values())
    score = sum(signals[k] * w for k, w in weights.items()) / total_weight * 100.0
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 2),
        "signals": {k: round(v, 3) for k, v in signals.items()},
    }


def _label_from_score(score: float) -> str:
    if score >= 70:
        return "automated"
    if score >= 40:
        return "hybrid"
    return "semi_discretionary"


def _confidence_from_score(score: float) -> str:
    """How confident we are in the classification."""
    distance_from_boundary = min(
        abs(score - 70),  # distance from hybrid/automated boundary
        abs(score - 40),  # distance from semi/hybrid boundary
    )
    if distance_from_boundary >= 15:
        return "high"
    if distance_from_boundary >= 7:
        return "moderate"
    return "low"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def classify_strategy(pkg: Any) -> Dict[str, Any]:
    """
    Classify a strategy's automation level from observable metadata signals.

    Parameters
    ----------
    pkg : AnalysisPackage

    Returns
    -------
    JSON-safe dict:
      label             : "automated" | "hybrid" | "semi_discretionary"
      automation_score  : float 0–100
      confidence        : "high" | "moderate" | "low"
      signals           : dict of signal scores
      reasoning         : list of human-readable reasons
    """
    meta = getattr(pkg, "metadata", None)
    if not isinstance(meta, dict):
        return {"label": "hybrid", "automation_score": 50.0, "confidence": "low",
                "signals": {}, "reasoning": ["no metadata available"]}

    sd = meta.get("derived", {}).get("strategy_discovery") or {}
    score_data = _compute_automation_score(pkg, sd)
    score = score_data["score"]
    signals = score_data["signals"]
    label = _label_from_score(score)
    confidence = _confidence_from_score(score)

    # Human-readable reasoning
    reasons = []
    if signals.get("has_settings", 0) >= 0.9:
        reasons.append("NinjaTrader settings file present → parameterized strategy")
    else:
        reasons.append("No settings file found → automation level uncertain")

    if signals.get("pattern_predictive", 0) >= 0.9:
        reasons.append("Pattern score is a top feature → rule-based entry signal detected")
    elif signals.get("pattern_coverage", 0) >= 0.9:
        reasons.append("Pattern engine fired on trades → entry has systematic component")
    else:
        reasons.append("No pattern engine signal detected → entry may be discretionary")

    if signals.get("hold_time_consistency", 0) >= 0.7:
        reasons.append("Consistent hold times suggest rule-based exit logic")
    else:
        reasons.append("Variable hold times suggest discretionary or time-in-market exit")

    if signals.get("session_wr_consistency", 0) >= 0.7:
        reasons.append("Win rate is consistent across sessions → strategy not session-dependent")
    else:
        reasons.append("Win rate varies significantly by session → possible manual session filter")

    return {
        "label": label,
        "automation_score": score,
        "confidence": confidence,
        "signals": signals,
        "reasoning": reasons,
    }
