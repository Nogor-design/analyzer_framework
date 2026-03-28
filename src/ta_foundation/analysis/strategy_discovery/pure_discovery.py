from __future__ import annotations

"""
Pure Discovery Mode
===================
Disciplined market-data-first strategy discovery pipeline that extends the
existing strategy discovery engine without replacing it.

Pipeline stages:
  1) Discovery universe definition (archetypes + template library)
  2) Controlled candidate assembly (context + trigger [+ confirmation])
  3) Synthetic trade generation (baseline exits)
  4) Early pruning
  5) Full validation via existing validation/evaluation modules
  6) Selection + clustering + NinjaScript-ready export payloads
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .evaluation import compute_evaluation_metrics
from .validation import run_validation


DEFAULT_PURE_DISCOVERY_OPTIONS: Dict[str, Any] = {
    "enabled": False,
    "objective": "high_quality",
    "max_final_strategies": 5,
    "max_archetypes_per_run": 6,
    "max_candidates_per_archetype": 40,
    "max_rule_depth": 3,
    "min_trades": 60,
    "require_walk_forward_pass": True,
    "require_robust_or_moderate_sensitivity": True,
    "require_distinct_cluster_representatives": True,
    "sessions": ["London", "NY_open", "Midday", "Power_hour", "Asian", "All"],
    "direction_mode": "both",  # both | long_only | short_only
    "pruning": {
        "min_profit_factor": 1.02,
        "max_top_window_pnl_share": 0.55,
        "max_single_session_share": 0.80,
        "max_long_short_pf_ratio": 3.0,
    },
    "baseline_exit": {
        "stop_ticks": 80,
        "target_ticks": 120,
        "max_holding_bars": 36,
    },
}


SESSION_WINDOWS: Dict[str, Tuple[int, int]] = {
    "Asian": (18, 0),
    "London": (1, 6),
    "NY_premarket": (6, 8),
    "NY_open": (8, 10),
    "Midday": (10, 13),
    "Power_hour": (13, 15),
    "All": (0, 24),
}


@dataclass(frozen=True)
class CandidateTemplate:
    name: str
    archetype: str
    session: str
    direction: str
    context: str
    trigger: str
    confirmation: Optional[str] = None


def _safe_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except Exception:
        return None


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy().sort_values("dt").reset_index(drop=True)
    if "day_id" not in df.columns:
        df["day_id"] = pd.to_datetime(df["dt"]).dt.date.astype(str)

    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()
    df["ema50_slope"] = df["ema50"].diff(3)
    df["atr14"] = (high - low).rolling(14, min_periods=2).mean()
    df["atr14_med50"] = df["atr14"].rolling(50, min_periods=10).median()
    df["atr14_pct20"] = df["atr14"].rolling(100, min_periods=20).quantile(0.2)
    df["atr14_pct80"] = df["atr14"].rolling(100, min_periods=20).quantile(0.8)
    df["session_high_24"] = high.rolling(24, min_periods=12).max().shift(1)
    df["session_low_24"] = low.rolling(24, min_periods=12).min().shift(1)
    df["rolling_high_12"] = high.rolling(12, min_periods=8).max().shift(1)
    df["rolling_low_12"] = low.rolling(12, min_periods=8).min().shift(1)
    df["range_10"] = (high.rolling(10, min_periods=5).max() - low.rolling(10, min_periods=5).min())
    df["range_40_med"] = (high.rolling(40, min_periods=20).max() - low.rolling(40, min_periods=20).min()).rolling(40, min_periods=10).median()
    df["bar_body"] = (pd.to_numeric(df["close"], errors="coerce") - pd.to_numeric(df["open"], errors="coerce")).abs()
    df["bar_range"] = (high - low).replace(0, np.nan)
    df["close_near_high"] = ((high - close) / df["bar_range"]) < 0.2
    df["close_near_low"] = ((close - low) / df["bar_range"]) < 0.2
    return df


def _session_mask(df: pd.DataFrame, session: str) -> pd.Series:
    dt = pd.to_datetime(df["dt"])
    hours = dt.dt.hour
    start, end = SESSION_WINDOWS.get(session, (0, 24))
    if start < end:
        return (hours >= start) & (hours < end)
    return (hours >= start) | (hours < end)


def _template_library(enabled_sessions: List[str], direction_mode: str) -> List[CandidateTemplate]:
    dirs = ["long", "short"]
    if direction_mode == "long_only":
        dirs = ["long"]
    elif direction_mode == "short_only":
        dirs = ["short"]

    templates: List[CandidateTemplate] = []
    primary = [s for s in enabled_sessions if s in {"London", "NY_open", "Power_hour", "Midday", "All"}] or ["All"]
    for session in primary:
        for direction in dirs:
            side = "up" if direction == "long" else "down"
            templates.extend([
                CandidateTemplate(
                    name=f"session_breakout_{session}_{direction}",
                    archetype="session_breakout",
                    session=session,
                    direction=direction,
                    context=f"session:{session}|regime:high_vol_expansion",
                    trigger=f"break_session_{side}",
                    confirmation="atr_expansion",
                ),
                CandidateTemplate(
                    name=f"trend_pullback_{session}_{direction}",
                    archetype="trend_pullback",
                    session=session,
                    direction=direction,
                    context=f"session:{session}|regime:trending_{side}",
                    trigger=f"pullback_to_ema20_{side}",
                    confirmation=f"close_through_prior_{side}",
                ),
                CandidateTemplate(
                    name=f"mean_reversion_{session}_{direction}",
                    archetype="mean_reversion",
                    session=session,
                    direction=direction,
                    context=f"session:{session}|regime:ranging",
                    trigger=f"distance_from_ema50_extreme_{'low' if direction == 'long' else 'high'}",
                    confirmation=f"reversal_close_{side}",
                ),
                CandidateTemplate(
                    name=f"compression_expansion_{session}_{direction}",
                    archetype="compression_expansion",
                    session=session,
                    direction=direction,
                    context=f"session:{session}|regime:low_vol_compression",
                    trigger=f"compression_break_{side}",
                    confirmation="atr_expansion",
                ),
            ])
    return templates


def _regime_mask(df: pd.DataFrame, context: str) -> pd.Series:
    regime = str(context).split("|regime:")[-1]
    if "regime" not in df.columns:
        return pd.Series(True, index=df.index)
    reg = df["regime"].fillna("unknown").astype(str)
    if regime == "ranging":
        return reg.str.contains("ranging")
    return reg == regime


def _trigger_mask(df: pd.DataFrame, trigger: str, direction: str) -> pd.Series:
    c = pd.to_numeric(df["close"], errors="coerce")
    o = pd.to_numeric(df["open"], errors="coerce")
    h = pd.to_numeric(df["high"], errors="coerce")
    l = pd.to_numeric(df["low"], errors="coerce")

    if trigger.startswith("break_session_up"):
        return c > pd.to_numeric(df["session_high_24"], errors="coerce")
    if trigger.startswith("break_session_down"):
        return c < pd.to_numeric(df["session_low_24"], errors="coerce")
    if trigger.startswith("pullback_to_ema20_up"):
        return (c > df["ema50"]) & (l <= df["ema20"]) & (df["ema50_slope"] > 0)
    if trigger.startswith("pullback_to_ema20_down"):
        return (c < df["ema50"]) & (h >= df["ema20"]) & (df["ema50_slope"] < 0)
    if trigger.startswith("distance_from_ema50_extreme_low"):
        return (c < (df["ema50"] - 1.5 * df["atr14"]))
    if trigger.startswith("distance_from_ema50_extreme_high"):
        return (c > (df["ema50"] + 1.5 * df["atr14"]))
    if trigger.startswith("compression_break_up"):
        return (df["range_10"] < 0.65 * df["range_40_med"]) & (c > df["rolling_high_12"])
    if trigger.startswith("compression_break_down"):
        return (df["range_10"] < 0.65 * df["range_40_med"]) & (c < df["rolling_low_12"])
    return pd.Series(False, index=df.index)


def _confirmation_mask(df: pd.DataFrame, confirmation: Optional[str], direction: str) -> pd.Series:
    if not confirmation:
        return pd.Series(True, index=df.index)
    c = pd.to_numeric(df["close"], errors="coerce")
    if confirmation == "atr_expansion":
        return pd.to_numeric(df["atr14"], errors="coerce") > pd.to_numeric(df["atr14_med50"], errors="coerce")
    if confirmation.startswith("close_through_prior_up"):
        return c > pd.to_numeric(df["high"], errors="coerce").shift(1)
    if confirmation.startswith("close_through_prior_down"):
        return c < pd.to_numeric(df["low"], errors="coerce").shift(1)
    if confirmation.startswith("reversal_close_up"):
        return (c > pd.to_numeric(df["open"], errors="coerce")) & df["close_near_high"].fillna(False)
    if confirmation.startswith("reversal_close_down"):
        return (c < pd.to_numeric(df["open"], errors="coerce")) & df["close_near_low"].fillna(False)
    return pd.Series(True, index=df.index)


def _assemble_candidates(df: pd.DataFrame, opts: Dict[str, Any]) -> List[Dict[str, Any]]:
    templates = _template_library(
        enabled_sessions=list(opts.get("sessions") or []),
        direction_mode=str(opts.get("direction_mode") or "both"),
    )
    max_per_arch = int(opts.get("max_candidates_per_archetype", 40))

    out: List[Dict[str, Any]] = []
    per_arch_count: Dict[str, int] = {}
    for t in templates:
        if per_arch_count.get(t.archetype, 0) >= max_per_arch:
            continue
        session_name = str(t.context).split("session:")[-1].split("|")[0]
        context_mask = _session_mask(df, session_name) & _regime_mask(df, t.context)
        trigger_mask = _trigger_mask(df, t.trigger, t.direction)
        confirmation_mask = _confirmation_mask(df, t.confirmation, t.direction)
        signal_mask = (context_mask & trigger_mask & confirmation_mask).fillna(False)

        if int(signal_mask.sum()) <= 0:
            continue
        candidate = {
            "strategy_name": t.name,
            "archetype": t.archetype,
            "direction": t.direction,
            "session": t.session,
            "context": t.context,
            "trigger": t.trigger,
            "confirmation": t.confirmation,
            "rule_depth": 2 if not t.confirmation else 3,
            "signal_mask": signal_mask,
        }
        out.append(candidate)
        per_arch_count[t.archetype] = per_arch_count.get(t.archetype, 0) + 1
    return out


def _simulate_trades_for_candidate(
    df: pd.DataFrame,
    candidate: Dict[str, Any],
    opts: Dict[str, Any],
    cost_model: Dict[str, Any],
    tick_size: float,
) -> pd.DataFrame:
    baseline = dict(opts.get("baseline_exit") or {})
    stop_ticks = int(baseline.get("stop_ticks", 80))
    target_ticks = int(baseline.get("target_ticks", 120))
    max_hold = int(baseline.get("max_holding_bars", 36))

    tick_value = float(cost_model.get("tick_value", 5.0))
    commission_per_side = float(cost_model.get("commission_per_side", 2.09))
    slippage_ticks = float(cost_model.get("slippage_ticks", 1.0))
    cost_per_rt = 2.0 * commission_per_side + 2.0 * slippage_ticks * tick_value

    signals = candidate["signal_mask"]
    idxs = np.where(signals.values)[0]
    if len(idxs) == 0:
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    direction = candidate["direction"]
    pos_mult = 1.0 if direction == "long" else -1.0

    for idx in idxs:
        entry_i = idx + 1
        if entry_i >= len(df):
            continue
        entry_px = float(df.iloc[entry_i]["open"])
        if not np.isfinite(entry_px):
            continue

        stop_px = entry_px - stop_ticks * tick_size * pos_mult
        target_px = entry_px + target_ticks * tick_size * pos_mult
        end_i = min(entry_i + max_hold, len(df) - 1)

        exit_i = end_i
        exit_reason = "time_stop"
        mae_ticks = 0.0
        mfe_ticks = 0.0

        for j in range(entry_i, end_i + 1):
            hi = float(df.iloc[j]["high"])
            lo = float(df.iloc[j]["low"])
            if direction == "long":
                mfe_ticks = max(mfe_ticks, (hi - entry_px) / tick_size)
                mae_ticks = max(mae_ticks, (entry_px - lo) / tick_size)
                if lo <= stop_px:
                    exit_i = j
                    exit_reason = "stop"
                    break
                if hi >= target_px:
                    exit_i = j
                    exit_reason = "target"
                    break
            else:
                mfe_ticks = max(mfe_ticks, (entry_px - lo) / tick_size)
                mae_ticks = max(mae_ticks, (hi - entry_px) / tick_size)
                if hi >= stop_px:
                    exit_i = j
                    exit_reason = "stop"
                    break
                if lo <= target_px:
                    exit_i = j
                    exit_reason = "target"
                    break

        exit_px = float(df.iloc[exit_i]["close"])
        pnl_ticks = (exit_px - entry_px) / tick_size * pos_mult
        pnl = pnl_ticks * tick_value
        pnl_net = pnl - cost_per_rt

        rows.append({
            "entry_time": pd.to_datetime(df.iloc[entry_i]["dt"]),
            "exit_time": pd.to_datetime(df.iloc[exit_i]["dt"]),
            "entry_price": entry_px,
            "exit_price": exit_px,
            "direction": direction,
            "market_pos": "Long" if direction == "long" else "Short",
            "initial_stop_basis": f"fixed_ticks:{stop_ticks}",
            "initial_target_basis": f"fixed_ticks:{target_ticks}",
            "mae": mae_ticks * tick_value,
            "mfe": mfe_ticks * tick_value,
            "profit": pnl,
            "profit_net": pnl_net,
            "result_after_cost_model": pnl_net,
            "exit_reason": exit_reason,
            "session": candidate.get("session"),
            "day_id": str(pd.to_datetime(df.iloc[entry_i]["dt"]).date()),
            "archetype": candidate.get("archetype"),
            "strategy_name": candidate.get("strategy_name"),
        })

    out = pd.DataFrame(rows)
    if len(out) == 0:
        return out
    return out.sort_values("entry_time").reset_index(drop=True)


def _profit_factor(profit: pd.Series) -> Optional[float]:
    s = pd.to_numeric(profit, errors="coerce").dropna()
    if len(s) == 0:
        return None
    gp = float(s[s > 0].sum())
    gl = float(s[s < 0].sum())
    if gl == 0:
        return None
    return gp / abs(gl)


def _early_prune(trades: pd.DataFrame, candidate: Dict[str, Any], opts: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    reasons: List[str] = []
    prune = dict(opts.get("pruning") or {})
    min_trades = int(opts.get("min_trades", 60))

    n_trades = int(len(trades))
    pf = _profit_factor(trades.get("profit_net", pd.Series(dtype=float)))
    if n_trades < min_trades:
        reasons.append(f"too_few_trades:{n_trades}<{min_trades}")
    if pf is None or pf < float(prune.get("min_profit_factor", 1.02)):
        reasons.append("weak_pf_after_costs")

    if "day_id" in trades.columns and len(trades) > 0:
        by_day = trades.groupby("day_id")["profit_net"].sum().sort_values(ascending=False)
        top_share = float(by_day.iloc[0] / max(by_day.sum(), 1e-9)) if len(by_day) else 0.0
        if top_share > float(prune.get("max_top_window_pnl_share", 0.55)):
            reasons.append("single_window_dependency")

    side = str(candidate.get("direction", "both"))
    if side == "both" and "market_pos" in trades.columns and len(trades):
        pf_long = _profit_factor(trades.loc[trades["market_pos"] == "Long", "profit_net"])
        pf_short = _profit_factor(trades.loc[trades["market_pos"] == "Short", "profit_net"])
        ratio = None
        if pf_long and pf_short and min(pf_long, pf_short) > 0:
            ratio = max(pf_long, pf_short) / min(pf_long, pf_short)
        if ratio is not None and ratio > float(prune.get("max_long_short_pf_ratio", 3.0)):
            reasons.append("long_short_asymmetry")

    diagnostics = {
        "trade_count": n_trades,
        "profit_factor": _safe_float(pf),
        "gross_net": _safe_float(pd.to_numeric(trades.get("profit_net"), errors="coerce").sum() if len(trades) else 0.0),
    }
    return (len(reasons) == 0), reasons, diagnostics


def _sensitivity_class(df: pd.DataFrame) -> str:
    if df is None or len(df) < 40:
        return "unknown"
    pf_base = _profit_factor(df["profit_net"])
    if pf_base is None:
        return "fragile"

    q = np.linspace(0.1, 0.9, 5)
    pfs: List[float] = []
    for qq in q:
        cut = int(len(df) * qq)
        if cut < 10:
            continue
        sub = df.iloc[cut:]
        pf = _profit_factor(sub["profit_net"])
        if pf is not None:
            pfs.append(pf)
    if len(pfs) < 2:
        return "unknown"
    spread = (max(pfs) - min(pfs)) / max(np.mean(pfs), 1e-9)
    if spread < 0.20:
        return "robust"
    if spread < 0.45:
        return "moderate"
    return "fragile"


def _cluster_key(candidate: Dict[str, Any]) -> str:
    return f"{candidate.get('archetype')}|{candidate.get('session')}|{candidate.get('direction')}"


def _to_ninja_spec(candidate: Dict[str, Any], best_exit: Dict[str, Any], baseline_exit: Dict[str, Any]) -> Dict[str, Any]:
    entry_logic = [
        candidate.get("context", ""),
        candidate.get("trigger", ""),
    ]
    if candidate.get("confirmation"):
        entry_logic.append(candidate["confirmation"])

    return {
        "strategy_name": candidate.get("strategy_name"),
        "archetype": candidate.get("archetype"),
        "direction": candidate.get("direction"),
        "session_filter": candidate.get("session"),
        "regime_filter": [str(candidate.get("context", "")).split("|regime:")[-1]],
        "entry_logic": entry_logic,
        "baseline_stop_model": {
            "type": "fixed_ticks",
            "value": int(baseline_exit.get("stop_ticks", 80)),
        },
        "baseline_target_model": {
            "type": "fixed_ticks",
            "value": int(baseline_exit.get("target_ticks", 120)),
        },
        "recommended_exit_model": best_exit,
    }


def run_pure_discovery(
    *,
    pkg: Any,
    bars_with_regime: Optional[pd.DataFrame],
    options: Dict[str, Any],
    wf_config: Dict[str, Any],
    cost_model: Dict[str, Any],
    tick_size: float,
) -> Dict[str, Any]:
    cfg = {**DEFAULT_PURE_DISCOVERY_OPTIONS, **dict(options or {})}
    if not cfg.get("enabled"):
        return {"enabled": False, "status": "disabled"}

    if bars_with_regime is None or len(bars_with_regime) < 300:
        return {
            "enabled": True,
            "status": "failed",
            "reason": "insufficient_market_bars",
            "leaderboard": [],
            "rejections": [],
            "strategies": [],
        }

    bars = _prepare_bars(bars_with_regime)
    candidates = _assemble_candidates(bars, cfg)

    rejections: List[Dict[str, Any]] = []
    survivors: List[Dict[str, Any]] = []

    for c in candidates:
        trades = _simulate_trades_for_candidate(bars, c, cfg, cost_model, tick_size)
        keep, reasons, diag = _early_prune(trades, c, cfg)
        if not keep:
            rejections.append({
                "strategy_name": c["strategy_name"],
                "archetype": c["archetype"],
                "session": c["session"],
                "direction": c["direction"],
                "reasons": reasons,
                "diagnostics": diag,
            })
            continue

        validation = run_validation(trades, wf_config=wf_config, cost_model=cost_model)
        eval_metrics = compute_evaluation_metrics(validation.get("cost_normalized", trades))
        sensitivity = _sensitivity_class(validation.get("cost_normalized", trades))
        wf_pass = bool(validation.get("passed", False))
        if cfg.get("require_walk_forward_pass", True) and not wf_pass:
            rejections.append({
                "strategy_name": c["strategy_name"],
                "archetype": c["archetype"],
                "session": c["session"],
                "direction": c["direction"],
                "reasons": ["failed_walk_forward"],
                "diagnostics": {"walk_forward": validation.get("walk_forward")},
            })
            continue
        if cfg.get("require_robust_or_moderate_sensitivity", True) and sensitivity not in {"robust", "moderate"}:
            rejections.append({
                "strategy_name": c["strategy_name"],
                "archetype": c["archetype"],
                "session": c["session"],
                "direction": c["direction"],
                "reasons": ["fragile_thresholds"],
                "diagnostics": {"sensitivity_class": sensitivity},
            })
            continue

        pf = _safe_float(eval_metrics.get("profit_factor")) or 0.0
        sharpe = _safe_float(eval_metrics.get("sharpe_approx")) or 0.0
        sortino = _safe_float(eval_metrics.get("sortino_approx")) or 0.0
        max_dd = _safe_float(eval_metrics.get("max_drawdown")) or 0.0
        score = (pf * 35.0) + (sharpe * 20.0) + (sortino * 15.0) - (max_dd / 500.0)

        survivors.append({
            "candidate": c,
            "trades": validation.get("cost_normalized", trades),
            "validation": validation,
            "evaluation": eval_metrics,
            "sensitivity_class": sensitivity,
            "score": float(score),
            "cluster_key": _cluster_key(c),
        })

    survivors = sorted(survivors, key=lambda x: x.get("score", 0.0), reverse=True)

    # De-duplicate similar candidates by archetype/session/direction cluster key.
    picked: List[Dict[str, Any]] = []
    seen_clusters: set[str] = set()
    for row in survivors:
        key = row["cluster_key"]
        if cfg.get("require_distinct_cluster_representatives", True) and key in seen_clusters:
            rejections.append({
                "strategy_name": row["candidate"]["strategy_name"],
                "archetype": row["candidate"]["archetype"],
                "session": row["candidate"]["session"],
                "direction": row["candidate"]["direction"],
                "reasons": ["too_correlated_to_better_candidate"],
                "diagnostics": {"cluster_key": key},
            })
            continue
        seen_clusters.add(key)
        picked.append(row)
        if len(picked) >= int(cfg.get("max_final_strategies", 5)):
            break

    leaderboard: List[Dict[str, Any]] = []
    strategies: List[Dict[str, Any]] = []
    baseline_exit = dict(cfg.get("baseline_exit") or {})

    for i, row in enumerate(picked, start=1):
        c = row["candidate"]
        eval_metrics = row["evaluation"]
        validation = row["validation"]
        trades = row["trades"]

        best_exit = {
            "type": "ATR_Trail",
            "atr_multiple": 1.5,
        }

        complexity = "low" if (c.get("confirmation") is None) else "medium"
        leader = {
            "rank": i,
            "strategy_name": c["strategy_name"],
            "archetype": c["archetype"],
            "direction": c["direction"],
            "session": c["session"],
            "trade_count": int(len(trades)),
            "PF": _safe_float(eval_metrics.get("profit_factor")),
            "WR": _safe_float(eval_metrics.get("win_rate")),
            "Sharpe": _safe_float(eval_metrics.get("sharpe_approx")),
            "Sortino": _safe_float(eval_metrics.get("sortino_approx")),
            "max_DD": _safe_float(eval_metrics.get("max_drawdown")),
            "walk_forward_result": "PASS" if validation.get("passed") else "FAIL",
            "decay_score": _safe_float(validation.get("walk_forward", {}).get("oos_degradation")),
            "sensitivity_class": row.get("sensitivity_class"),
            "cluster_badge": row.get("cluster_key"),
            "implementation_complexity": complexity,
            "score": round(float(row.get("score", 0.0)), 3),
        }
        leaderboard.append(leader)

        strategies.append({
            "strategy_name": c["strategy_name"],
            "archetype": c["archetype"],
            "market_side": c["direction"],
            "primary_session": c["session"],
            "required_regime": str(c.get("context", "")).split("|regime:")[-1],
            "entry_conditions": [c.get("context")],
            "trigger_conditions": [c.get("trigger")],
            "confirmation_conditions": [c.get("confirmation")] if c.get("confirmation") else [],
            "baseline_stop_target": {
                "stop_ticks": int(baseline_exit.get("stop_ticks", 80)),
                "target_ticks": int(baseline_exit.get("target_ticks", 120)),
            },
            "best_discovered_exit_family": best_exit,
            "key_metrics": leader,
            "walk_forward_verdict": leader["walk_forward_result"],
            "robustness_verdict": row.get("sensitivity_class"),
            "ninjatrader_mapping": _to_ninja_spec(c, best_exit, baseline_exit),
        })

    return {
        "enabled": True,
        "status": "ok",
        "objective": cfg.get("objective"),
        "search_summary": {
            "candidate_count": len(candidates),
            "survivor_count": len(survivors),
            "selected_count": len(strategies),
            "max_rule_depth": int(cfg.get("max_rule_depth", 3)),
        },
        "leaderboard": leaderboard,
        "strategies": strategies,
        "rejections": rejections,
        "ninja_export": [s["ninjatrader_mapping"] for s in strategies],
        "config_snapshot": cfg,
    }
