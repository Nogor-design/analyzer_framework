"""
Prop-firm challenge simulation (Apex-style).

This module answers the question: "can this strategy pass a funded-account evaluation?"
It is intentionally separate from edge-validation Monte Carlo (pattern_engine/monte_carlo.py),
which answers: "is the observed expectancy statistically real?"

Public API:
  run_prop_monte_carlo          — baseline path MC, per (entity, horizon)
  run_prop_monte_carlo_regime   — regime-aware + vol-conditioned + slippage surface MC
  apply_microstructure_stress   — deterministic tick-level slippage/fill model
  apply_trade_intake_model      — anti-overtrading day-level intake filter
  build_markov_regime_model     — MLE Markov transition matrix from daily regime sequence
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ======================================================================================
# Core utilities
# ======================================================================================

def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(int(seed))


def _as_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _as_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _safe_str(x: Any, default: str = "UNK") -> str:
    s = str(x) if x is not None else ""
    s = s.strip()
    return s if s else default


def _max_dd_from_pnl_seq(pnl_usd: np.ndarray) -> float:
    if pnl_usd.size == 0:
        return 0.0
    eq = np.cumsum(pnl_usd)
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    return float(np.max(dd)) if dd.size else 0.0


def _intraday_trailing_breach_prob_and_time(
    *,
    paths_breached: np.ndarray,
    breach_steps: np.ndarray,
) -> Tuple[float, float]:
    if paths_breached.size == 0:
        return 0.0, float("nan")
    prob = float(np.mean(paths_breached.astype(float)))
    if np.any(paths_breached):
        exp_t = float(np.nanmean(breach_steps[paths_breached]))
    else:
        exp_t = float("nan")
    return prob, exp_t


# ======================================================================================
# Regime model
# ======================================================================================

@dataclass(frozen=True)
class RegimeModel:
    name: str
    regimes: List[str]
    p: np.ndarray  # shape [K,K], rows sum to 1

    def next_state(self, rng: np.random.Generator, cur_idx: int) -> int:
        return int(rng.choice(len(self.regimes), p=self.p[cur_idx]))


def build_markov_regime_model(
    *,
    daily_regimes: pd.Series,
    model_name: str = "markov_mle",
    alpha: float = 1.0,
) -> RegimeModel:
    """MLE Markov transition with Laplace smoothing alpha."""
    seq = [_safe_str(x) for x in daily_regimes.tolist()]
    regimes = sorted(set(seq)) if seq else ["UNK"]
    idx = {r: i for i, r in enumerate(regimes)}
    k = len(regimes)

    counts = np.full((k, k), float(alpha), dtype=float)
    for a, b in zip(seq[:-1], seq[1:]):
        counts[idx[a], idx[b]] += 1.0

    row_sums = counts.sum(axis=1, keepdims=True)
    p = counts / np.where(row_sums == 0.0, 1.0, row_sums)
    return RegimeModel(name=model_name, regimes=regimes, p=p)


# ======================================================================================
# Volatility bucketing
# ======================================================================================

def _compute_daily_vol_metric(
    *,
    events: pd.DataFrame,
    tick_value_usd: float,
    vol_col: str = "vol_metric",
) -> pd.DataFrame:
    d = events.copy()
    d["pnl_usd"] = pd.to_numeric(d["pnl_ticks"], errors="coerce") * float(tick_value_usd)

    has_vol = vol_col in d.columns and pd.to_numeric(d[vol_col], errors="coerce").notna().any()
    if has_vol:
        d["_v"] = pd.to_numeric(d[vol_col], errors="coerce")
        g = d.groupby("day_id", sort=False)
        out = g.agg(
            day_regime=("regime", lambda s: _safe_str(s.iloc[0] if len(s) else "UNK")),
            day_vol=("_v", lambda s: float(np.nanmedian(s.to_numpy(dtype=float))) if len(s) else float("nan")),
        ).reset_index()
    else:
        g = d.groupby("day_id", sort=False)
        out = g.agg(
            day_regime=("regime", lambda s: _safe_str(s.iloc[0] if len(s) else "UNK")),
            day_vol=("pnl_usd", lambda s: float(np.abs(np.nansum(s.to_numpy(dtype=float))))),
        ).reset_index()

    out["day_vol"] = pd.to_numeric(out["day_vol"], errors="coerce")
    return out


def _assign_vol_buckets(daily: pd.DataFrame, *, q: float = 0.5) -> pd.DataFrame:
    d = daily.copy()
    vals = pd.to_numeric(d["day_vol"], errors="coerce").dropna()
    if len(vals) == 0:
        d["vol_bucket"] = "UNK"
        return d
    thr = float(vals.quantile(q))
    d["vol_bucket"] = np.where(d["day_vol"] > thr, "high", "low")
    return d


# ======================================================================================
# Slippage / microstructure stress
# ======================================================================================

def apply_microstructure_stress(
    *,
    pnl_ticks: np.ndarray,
    rng: np.random.Generator,
    slippage_ticks: int,
    spread_widen_ticks: int,
    fill_prob: float,
) -> np.ndarray:
    """
    Deterministic given rng seed.
    fill_prob: Bernoulli per trade — if not filled pnl = 0.
    slippage + spread cost subtracted per filled trade (conservative).
    """
    pnl = pnl_ticks.astype(float, copy=True)

    if fill_prob < 1.0:
        filled = rng.random(pnl.size) < float(fill_prob)
        pnl = np.where(filled, pnl, 0.0)

    cost = float(max(slippage_ticks, 0) + max(spread_widen_ticks, 0))
    if cost > 0:
        pnl = pnl - cost
    return pnl


# ======================================================================================
# Trade intake model (anti-overtrading)
# ======================================================================================

def apply_trade_intake_model(
    *,
    events_df: pd.DataFrame,
    intake: Dict[str, Any],
    seed: int,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Deterministic down-selection of raw events into executable trades.

    intake knobs (all optional):
      enabled: bool
      max_trades_per_day: int
      cooldown_minutes: int
      selection: 'take_first' | 'random_k'
      random_k: int
      cooldown_scope: 'global' (default) | 'per_entity'
    """
    if events_df is None or not isinstance(events_df, pd.DataFrame) or len(events_df) == 0:
        return pd.DataFrame(), {"enabled": bool(intake.get("enabled", False)), "n_in": 0, "n_out": 0}

    enabled = bool(intake.get("enabled", False))
    if not enabled:
        return events_df.copy(), {"enabled": False, "n_in": int(len(events_df)), "n_out": int(len(events_df))}

    if "dt" not in events_df.columns or "day_id" not in events_df.columns:
        return events_df.copy(), {
            "enabled": True,
            "n_in": int(len(events_df)),
            "n_out": int(len(events_df)),
            "issues": ["missing_required_cols:dt/day_id"],
        }

    max_trades_per_day = _as_int(intake.get("max_trades_per_day", 6), 6)
    max_trades_per_day = max(1, max_trades_per_day)

    cooldown_minutes = _as_int(intake.get("cooldown_minutes", 0), 0)
    cooldown_minutes = max(0, cooldown_minutes)

    selection = _safe_str(intake.get("selection", "take_first"), "take_first").lower()
    random_k = _as_int(intake.get("random_k", max_trades_per_day), max_trades_per_day)
    random_k = max(1, random_k)

    cooldown_scope = _safe_str(intake.get("cooldown_scope", "global"), "global").lower()

    d = events_df.copy()
    d = d.sort_values("dt").reset_index(drop=True)

    rng = _rng(seed)
    kept_idx: List[int] = []

    for day_id, day_df in d.groupby("day_id", sort=False):
        if len(day_df) == 0:
            continue

        cand = day_df.copy()

        if selection == "random_k":
            k0 = min(int(random_k), len(cand))
            pick = rng.choice(cand.index.to_numpy(), size=k0, replace=False)
            cand = cand.loc[np.sort(pick)]
        # else take_first: keep time order

        if cooldown_minutes > 0:
            cooldown = pd.Timedelta(minutes=int(cooldown_minutes))
            last_dt_global = None
            last_dt_by_entity: Dict[str, pd.Timestamp] = {}

            for idx, row in cand.iterrows():
                dt = row["dt"]
                if not isinstance(dt, pd.Timestamp):
                    dt = pd.to_datetime(dt, errors="coerce")
                if pd.isna(dt):
                    kept_idx.append(int(idx))
                    if len([i for i in kept_idx if d.loc[i, "day_id"] == day_id]) >= max_trades_per_day:
                        break
                    continue

                if cooldown_scope == "per_entity" and "entity_id" in cand.columns:
                    eid = _safe_str(row.get("entity_id"), "UNK")
                    last = last_dt_by_entity.get(eid)
                    if last is not None and (dt - last) < cooldown:
                        continue
                    last_dt_by_entity[eid] = dt
                else:
                    if last_dt_global is not None and (dt - last_dt_global) < cooldown:
                        continue
                    last_dt_global = dt

                kept_idx.append(int(idx))
                if len([i for i in kept_idx if d.loc[i, "day_id"] == day_id]) >= max_trades_per_day:
                    break
        else:
            kept_idx.extend([int(i) for i in cand.index[:max_trades_per_day].to_list()])

    kept_idx = sorted(set(kept_idx))
    out = d.loc[kept_idx].sort_values("dt").reset_index(drop=True)

    per_day_counts = (
        out.groupby("day_id", sort=False).size().astype(int).to_dict()
        if "day_id" in out.columns and len(out) else {}
    )
    diag = {
        "enabled": True,
        "selection": selection,
        "max_trades_per_day": int(max_trades_per_day),
        "cooldown_minutes": int(cooldown_minutes),
        "cooldown_scope": cooldown_scope,
        "n_in": int(len(d)),
        "n_out": int(len(out)),
        "days": int(d["day_id"].nunique()) if "day_id" in d.columns else 0,
        "avg_trades_per_day_out": float(len(out) / max(int(d["day_id"].nunique()), 1)) if "day_id" in d.columns else float("nan"),
        "per_day_counts_out": {str(k): int(v) for k, v in list(per_day_counts.items())[:50]},
    }
    return out, diag


# ======================================================================================
# Path simulation: regime-aware day sampling, intraday trailing DD
# ======================================================================================

def _build_day_blocks_for_entity(
    *,
    entity_events: pd.DataFrame,
    tick_value_usd: float,
) -> Dict[str, Dict[str, List[np.ndarray]]]:
    e = entity_events.sort_values("dt").copy()
    e["pnl_usd"] = pd.to_numeric(e["pnl_ticks"], errors="coerce") * float(tick_value_usd)
    e["regime"] = e["regime"].map(lambda x: _safe_str(x, "UNK"))

    daily = _compute_daily_vol_metric(events=e, tick_value_usd=tick_value_usd)
    daily = _assign_vol_buckets(daily, q=0.5)

    dmap = {row["day_id"]: (row["day_regime"], row["vol_bucket"]) for _, row in daily.iterrows()}

    blocks: Dict[str, Dict[str, List[np.ndarray]]] = {}
    for day_id, day_df in e.groupby("day_id", sort=False):
        reg, vb = dmap.get(day_id, ("UNK", "UNK"))
        reg = _safe_str(reg, "UNK")
        vb = _safe_str(vb, "UNK")
        blocks.setdefault(reg, {}).setdefault(vb, []).append(day_df["pnl_usd"].to_numpy(dtype=float))

    return blocks


def _sample_path_regime_aware(
    *,
    rng: np.random.Generator,
    model: RegimeModel,
    blocks: Dict[str, Dict[str, List[np.ndarray]]],
    n_days: int,
    vol_mode: str = "bucketed",
    forced_vol_bucket: Optional[str] = None,
) -> List[np.ndarray]:
    regimes = model.regimes
    k = len(regimes)

    start_weights = np.ones(k, dtype=float) / max(k, 1)
    cur = int(rng.choice(k, p=start_weights))

    out_days: List[np.ndarray] = []
    for _ in range(int(n_days)):
        reg = regimes[cur]
        reg_blocks = blocks.get(reg, {})

        if forced_vol_bucket is not None:
            vb = forced_vol_bucket
            pool = reg_blocks.get(vb, [])
            if not pool:
                pool = [b for vv in reg_blocks.values() for b in vv]
        elif vol_mode == "bucketed":
            vbs = list(reg_blocks.keys())
            if not vbs:
                pool = []
            else:
                counts = np.array([len(reg_blocks[v]) for v in vbs], dtype=float)
                probs = counts / np.where(counts.sum() == 0, 1.0, counts.sum())
                vb = str(rng.choice(vbs, p=probs))
                pool = reg_blocks.get(vb, [])
        else:
            pool = [b for vv in reg_blocks.values() for b in vv]

        if not pool:
            pool = [b for rr in blocks.values() for vv in rr.values() for b in vv]
            if not pool:
                break

        out_days.append(pool[int(rng.integers(0, len(pool)))])
        cur = model.next_state(rng, cur)

    return out_days


def _simulate_intraday_trailing_dd(
    *,
    day_blocks: List[np.ndarray],
    trailing_dd_usd: float,
) -> Tuple[bool, int, float]:
    eq = 0.0
    peak = 0.0
    dd_max = 0.0
    step = 0
    for day in day_blocks:
        for x in day:
            step += 1
            eq += float(x)
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > dd_max:
                dd_max = dd
            if dd > trailing_dd_usd:
                return True, step, float(dd_max)
    return False, 0, float(dd_max)


def _simulate_prop_path(
    *,
    day_blocks: List[np.ndarray],
    start_balance_usd: float,
    trailing_dd_usd: float,
    daily_loss_usd: float,
    profit_target_usd: Optional[float],
    trailing_lock_buffer_usd: float = 100.0,
    dd_mode: str = "global_peak",  # global_peak | session_reset
) -> Dict[str, Any]:
    """
    Apex-style evaluation model.

    Returns JSON-safe dict:
      status: "pass" | "fail" | "survive"
      fail_reason: "daily_loss" | "trailing_dd" | None
      days_to_event: int
      steps_to_trailing_breach: int
      dd_max_usd: float
      end_equity_usd: float
    """
    start = float(start_balance_usd)
    trailing_dd = float(max(trailing_dd_usd, 0.0))
    daily_loss = float(max(daily_loss_usd, 0.0))
    target_abs = None if profit_target_usd is None else start + float(profit_target_usd)
    lock_floor_abs = start + float(trailing_lock_buffer_usd)
    lock_peak_abs = start + trailing_dd + float(trailing_lock_buffer_usd)

    eq_abs = start
    peak_abs = start
    dd_max = 0.0
    trailing_floor_abs = start - trailing_dd
    locked = False
    step = 0
    steps_to_trailing_breach = 0

    for di, day in enumerate(day_blocks, start=1):
        day_open_abs = eq_abs

        if dd_mode == "session_reset" and not locked:
            peak_abs = max(peak_abs, eq_abs)
            trailing_floor_abs = max(start - trailing_dd, eq_abs - trailing_dd)

        day_arr = np.asarray(day, dtype=float)
        for x in day_arr:
            step += 1
            eq_abs += float(x)

            if target_abs is not None and eq_abs >= target_abs:
                return {
                    "status": "pass",
                    "fail_reason": None,
                    "days_to_event": int(di),
                    "steps_to_trailing_breach": 0,
                    "dd_max_usd": float(dd_max),
                    "end_equity_usd": float(eq_abs),
                }

            if daily_loss > 0.0 and (eq_abs - day_open_abs) <= -daily_loss:
                return {
                    "status": "fail",
                    "fail_reason": "daily_loss",
                    "days_to_event": int(di),
                    "steps_to_trailing_breach": 0,
                    "dd_max_usd": float(dd_max),
                    "end_equity_usd": float(eq_abs),
                }

            if eq_abs > peak_abs:
                peak_abs = eq_abs

            if (not locked) and peak_abs >= lock_peak_abs:
                locked = True
                trailing_floor_abs = lock_floor_abs
            elif not locked:
                trailing_floor_abs = max(trailing_floor_abs, peak_abs - trailing_dd)

            dd = peak_abs - eq_abs
            if dd > dd_max:
                dd_max = dd

            if trailing_dd > 0.0 and eq_abs <= trailing_floor_abs:
                if steps_to_trailing_breach == 0:
                    steps_to_trailing_breach = int(step)
                return {
                    "status": "fail",
                    "fail_reason": "trailing_dd",
                    "days_to_event": int(di),
                    "steps_to_trailing_breach": int(steps_to_trailing_breach),
                    "dd_max_usd": float(dd_max),
                    "end_equity_usd": float(eq_abs),
                }

    return {
        "status": "survive",
        "fail_reason": None,
        "days_to_event": 0,
        "steps_to_trailing_breach": 0,
        "dd_max_usd": float(dd_max),
        "end_equity_usd": float(eq_abs),
    }


# ======================================================================================
# Public API
# ======================================================================================

def run_prop_monte_carlo(
    *,
    events_df: pd.DataFrame,
    constraints: Dict[str, Any],
    mc_options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    Baseline MC summary per (entity_type, entity_id, horizon).

    events_df schema (from pattern_engine/engine.py):
      dt, day_id, entity_type, entity_id, horizon, pnl_ticks, regime, vol_metric
    """
    if events_df is None or not isinstance(events_df, pd.DataFrame) or len(events_df) == 0:
        return {"mc_summary_df": pd.DataFrame()}

    tick_value = _as_float(constraints.get("tick_value_usd", 12.5), 12.5)
    trailing_dd_usd = _as_float(constraints.get("trailing_drawdown_usd", 1500), 1500)
    daily_loss_usd = _as_float(constraints.get("daily_loss_limit_usd", 1000), 1000)

    n_paths = _as_int(mc_options.get("n_paths", 2000), 2000)
    seed = _as_int(mc_options.get("seed", 7), 7)

    df = events_df.sort_values("dt").reset_index(drop=True).copy()
    df["pnl_usd"] = pd.to_numeric(df["pnl_ticks"], errors="coerce") * tick_value

    intake_cfg = (mc_options.get("intake") or {}) if isinstance(mc_options.get("intake"), dict) else {}
    df_exec, intake_diag = apply_trade_intake_model(events_df=df, intake=intake_cfg, seed=seed)
    df = df_exec

    rows: List[Dict[str, Any]] = []
    rng = _rng(seed)

    for (etype, eid, horizon), d2 in df.groupby(["entity_type", "entity_id", "horizon"], sort=False):
        day_blocks = [g["pnl_usd"].to_numpy(dtype=float) for _, g in d2.groupby("day_id", sort=False)]
        if not day_blocks:
            continue

        dds = np.zeros(n_paths, dtype=float)
        daily_breach = 0
        trailing_breach = 0
        any_breach = 0

        n_days_hist = len(day_blocks)
        eval_days = _as_int(mc_options.get("eval_days", min(20, n_days_hist)), min(20, n_days_hist))
        eval_days = max(1, min(eval_days, n_days_hist))

        dd_mode = _safe_str(mc_options.get("dd_mode", "global_peak"), "global_peak")

        start_balance_usd = _as_float(
            mc_options.get("start_balance_usd", constraints.get("start_balance_usd", 0.0)),
            _as_float(constraints.get("start_balance_usd", 0.0), 0.0),
        )
        profit_target_usd = mc_options.get("profit_target_usd", constraints.get("profit_target_usd", None))
        profit_target_usd = None if profit_target_usd is None else _as_float(profit_target_usd, float(profit_target_usd))
        trailing_lock_buffer_usd = _as_float(mc_options.get("trailing_lock_buffer_usd", 100.0), 100.0)

        pass_count = 0
        days_to_breach: List[int] = []
        days_to_pass: List[int] = []

        for i in range(n_paths):
            picks = rng.integers(0, n_days_hist, size=eval_days)
            sampled_days = [day_blocks[int(j)] for j in picks]

            sim = _simulate_prop_path(
                day_blocks=sampled_days,
                start_balance_usd=start_balance_usd,
                trailing_dd_usd=trailing_dd_usd,
                daily_loss_usd=daily_loss_usd,
                profit_target_usd=profit_target_usd,
                trailing_lock_buffer_usd=trailing_lock_buffer_usd,
                dd_mode=dd_mode,
            )

            if sim["status"] == "pass":
                pass_count += 1
                days_to_pass.append(int(sim.get("days_to_event") or 0))
            elif sim["status"] == "fail":
                reason = sim.get("fail_reason")
                if reason == "daily_loss":
                    daily_breach += 1
                elif reason == "trailing_dd":
                    trailing_breach += 1
                any_breach += 1
                days_to_breach.append(int(sim.get("days_to_event") or 0))

            dds[i] = float(sim.get("dd_max_usd") or 0.0)

        n_events = len(d2)
        n_days_hist = d2["day_id"].nunique()
        events_per_day = n_events / max(n_days_hist, 1)
        rows.append(
            {
                "entity_type": str(etype),
                "entity_id": str(eid),
                "horizon": int(horizon),
                "n_paths": int(n_paths),
                "dd_p50": float(np.nanpercentile(dds, 50)),
                "dd_p90": float(np.nanpercentile(dds, 90)),
                "dd_p99": float(np.nanpercentile(dds, 99)),
                "daily_loss_breach_prob": float(daily_breach / max(n_paths, 1)),
                "trailing_dd_breach_prob": float(trailing_breach / max(n_paths, 1)),
                "any_breach_prob": float(any_breach / max(n_paths, 1)),
                "median_days_to_breach": float(np.nanmedian(np.asarray(days_to_breach, dtype=float))) if days_to_breach else float("nan"),
                "eval_pass_prob": float(pass_count / max(n_paths, 1)),
                "stress_slip_ticks": int(mc_options.get("stress_slip_ticks", 0)),
                "prop_survival_score": float(1.0 - ((daily_breach + trailing_breach) / max(n_paths, 1))),
            }
        )

    return {"mc_summary_df": pd.DataFrame(rows)}


def run_prop_monte_carlo_regime(
    *,
    events_df: pd.DataFrame,
    constraints: Dict[str, Any],
    mc_options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    Regime-aware + vol-conditioned + slippage surface Monte Carlo.

    Output DataFrames:
      mc_regime_summary_df  — per (entity_id, horizon, vol_bucket)
      mc_slippage_surface_df — per (entity_id, horizon, slip/spread/fill grid point)
    """
    required_cols = {"dt", "day_id", "entity_id", "horizon", "pnl_ticks"}
    if events_df is None or not isinstance(events_df, pd.DataFrame) or len(events_df) == 0:
        return {"mc_regime_summary_df": pd.DataFrame(), "mc_slippage_surface_df": pd.DataFrame()}
    if not required_cols.issubset(set(events_df.columns)):
        return {"mc_regime_summary_df": pd.DataFrame(), "mc_slippage_surface_df": pd.DataFrame()}

    tick_value = _as_float(constraints.get("tick_value_usd", 12.5), 12.5)
    trailing_dd_usd = _as_float(constraints.get("trailing_drawdown_usd", 1500), 1500)
    daily_loss_usd = _as_float(constraints.get("daily_loss_limit_usd", 1000), 1000)

    n_paths = _as_int(mc_options.get("n_paths", 2000), 2000)
    seed = _as_int(mc_options.get("seed", 7), 7)

    dd_mode = _safe_str(mc_options.get("dd_mode", "global_peak"), "global_peak")

    start_balance_usd = _as_float(
        mc_options.get("start_balance_usd", constraints.get("start_balance_usd", 0.0)),
        _as_float(constraints.get("start_balance_usd", 0.0), 0.0),
    )
    profit_target_usd = mc_options.get("profit_target_usd", constraints.get("profit_target_usd", None))
    profit_target_usd = None if profit_target_usd is None else _as_float(profit_target_usd, float(profit_target_usd))
    trailing_lock_buffer_usd = _as_float(mc_options.get("trailing_lock_buffer_usd", 100.0), 100.0)

    stress = (mc_options.get("stress") or {}) if isinstance(mc_options.get("stress"), dict) else {}
    slip_grid = stress.get("slippage_ticks", [0, 1, 2, 4])
    spread_grid = stress.get("spread_widen_ticks", [0, 1, 2])
    fill_grid = stress.get("fill_prob", [1.0, 0.9, 0.8])

    slip_grid = [int(x) for x in (slip_grid if isinstance(slip_grid, list) else [slip_grid])]
    spread_grid = [int(x) for x in (spread_grid if isinstance(spread_grid, list) else [spread_grid])]
    fill_grid = [float(x) for x in (fill_grid if isinstance(fill_grid, list) else [fill_grid])]

    df = events_df.sort_values("dt").reset_index(drop=True).copy()
    df["regime"] = df["regime"].map(lambda x: _safe_str(x, "UNK")) if "regime" in df.columns else "UNK"
    df["vol_metric"] = pd.to_numeric(df["vol_metric"], errors="coerce") if "vol_metric" in df.columns else np.nan

    rows_summary: List[Dict[str, Any]] = []
    rows_surface: List[Dict[str, Any]] = []

    rng_master = _rng(seed)

    for (eid, horizon), e2 in df.groupby(["entity_id", "horizon"], sort=False):
        e2 = e2.sort_values("dt").reset_index(drop=True)

        intake_cfg = (mc_options.get("intake") or {}) if isinstance(mc_options.get("intake"), dict) else {}
        e2_exec, intake_diag = apply_trade_intake_model(events_df=e2, intake=intake_cfg, seed=int(rng_master.integers(0, 2**31 - 1)))
        e2 = e2_exec

        daily_seq = (
            e2.groupby("day_id", sort=False)
            .agg(day_regime=("regime", lambda s: _safe_str(s.iloc[0] if len(s) else "UNK")))
            .reset_index()
        )

        model = build_markov_regime_model(daily_regimes=daily_seq["day_regime"], model_name="markov_mle", alpha=1.0)
        blocks = _build_day_blocks_for_entity(entity_events=e2, tick_value_usd=tick_value)

        all_vol_buckets = sorted({vb for rr in blocks.values() for vb in rr.keys()}) or ["UNK"]

        n_days_hist = int(daily_seq.shape[0]) if daily_seq is not None else 0
        n_days_hist = max(n_days_hist, 1)
        eval_days = _as_int(mc_options.get("eval_days", min(20, n_days_hist)), min(20, n_days_hist))
        eval_days = max(1, min(eval_days, n_days_hist))

        # Regime-aware MC summary per vol bucket
        for vol_bucket in all_vol_buckets:
            paths_breached = np.zeros(n_paths, dtype=bool)
            breach_steps = np.full(n_paths, np.nan, dtype=float)
            dd_maxes = np.zeros(n_paths, dtype=float)
            daily_breached_paths = np.zeros(n_paths, dtype=bool)
            trailing_breached_paths = np.zeros(n_paths, dtype=bool)

            rng_bucket = _rng(int(rng_master.integers(0, 2**31 - 1)))

            for i in range(n_paths):
                day_blocks = _sample_path_regime_aware(
                    rng=rng_bucket,
                    model=model,
                    blocks=blocks,
                    n_days=eval_days,
                    vol_mode="bucketed",
                    forced_vol_bucket=vol_bucket,
                )

                sim = _simulate_prop_path(
                    day_blocks=day_blocks,
                    start_balance_usd=start_balance_usd,
                    trailing_dd_usd=trailing_dd_usd,
                    daily_loss_usd=daily_loss_usd,
                    profit_target_usd=profit_target_usd,
                    trailing_lock_buffer_usd=trailing_lock_buffer_usd,
                    dd_mode=dd_mode,
                )

                breached = bool(sim["status"] == "fail")
                paths_breached[i] = breached
                _fr = sim.get("fail_reason")
                daily_breached_paths[i] = bool(breached and _fr == "daily_loss")
                trailing_breached_paths[i] = bool(breached and _fr == "trailing_dd")

                if sim.get("fail_reason") == "trailing_dd":
                    breach_steps[i] = float(sim.get("steps_to_trailing_breach") or np.nan)

                dd_maxes[i] = float(sim.get("dd_max_usd") or 0.0)

            survived = ~paths_breached
            prop_survival_prob = float(np.mean(survived.astype(float)))
            daily_loss_breach_prob = float(np.mean(daily_breached_paths.astype(float)))
            trailing_dd_breach_prob = float(np.mean(trailing_breached_paths.astype(float)))
            any_breach_prob = float(np.mean(paths_breached.astype(float)))

            trailing_breached = np.isfinite(breach_steps)
            intraday_breach_prob, exp_time = _intraday_trailing_breach_prob_and_time(
                paths_breached=trailing_breached,
                breach_steps=breach_steps,
            )

            rows_summary.append(
                {
                    "entity_id": str(eid),
                    "horizon": int(horizon),
                    "regime_model": model.name,
                    "vol_bucket": str(vol_bucket),
                    "n_paths": int(n_paths),
                    "prop_survival_prob": prop_survival_prob,
                    "daily_loss_breach_prob": float(daily_loss_breach_prob),
                    "trailing_dd_breach_prob": float(trailing_dd_breach_prob),
                    "any_breach_prob": float(any_breach_prob),
                    "dd_p90": float(np.nanpercentile(dd_maxes, 90)) if dd_maxes.size else float("nan"),
                    "intraday_breach_prob": float(intraday_breach_prob),
                    "expected_time_to_breach": float(exp_time),
                }
            )

        # Slippage stress surface
        rng_surface = _rng(int(rng_master.integers(0, 2**31 - 1)))

        base_samples: List[List[np.ndarray]] = []
        for _ in range(n_paths):
            base_samples.append(
                _sample_path_regime_aware(
                    rng=rng_surface,
                    model=model,
                    blocks=blocks,
                    n_days=eval_days,
                    vol_mode="any",
                    forced_vol_bucket=None,
                )
            )

        for slip in slip_grid:
            for spread in spread_grid:
                for fill in fill_grid:
                    rng_point = _rng(int(rng_master.integers(0, 2**31 - 1)))
                    survived_count = 0

                    for i in range(n_paths):
                        day_blocks_usd = base_samples[i]

                        stressed_days: List[np.ndarray] = []
                        for day_usd in day_blocks_usd:
                            day_usd = np.asarray(day_usd, dtype=float)
                            day_ticks = (day_usd / float(tick_value)) if tick_value else (day_usd * 0.0)
                            day_ticks_adj = apply_microstructure_stress(
                                pnl_ticks=day_ticks,
                                rng=rng_point,
                                slippage_ticks=int(slip),
                                spread_widen_ticks=int(spread),
                                fill_prob=float(fill),
                            )
                            stressed_days.append(day_ticks_adj * float(tick_value))

                        sim = _simulate_prop_path(
                            day_blocks=stressed_days,
                            start_balance_usd=start_balance_usd,
                            trailing_dd_usd=trailing_dd_usd,
                            daily_loss_usd=daily_loss_usd,
                            profit_target_usd=profit_target_usd,
                            trailing_lock_buffer_usd=trailing_lock_buffer_usd,
                            dd_mode=dd_mode,
                        )

                        if sim["status"] != "fail":
                            survived_count += 1

                    rows_surface.append(
                        {
                            "entity_id": str(eid),
                            "horizon": int(horizon),
                            "regime_model": model.name,
                            "slippage_ticks": int(slip),
                            "spread_widen_ticks": int(spread),
                            "fill_prob": float(fill),
                            "n_paths": int(n_paths),
                            "prop_survival_prob": float(survived_count / max(n_paths, 1)),
                        }
                    )

    mc_regime_summary_df = pd.DataFrame(
        rows_summary,
        columns=[
            "entity_id", "horizon", "regime_model", "vol_bucket", "n_paths",
            "prop_survival_prob", "daily_loss_breach_prob", "trailing_dd_breach_prob",
            "any_breach_prob", "dd_p90", "intraday_breach_prob", "expected_time_to_breach",
        ],
    )

    mc_slippage_surface_df = pd.DataFrame(
        rows_surface,
        columns=[
            "entity_id", "horizon", "regime_model",
            "slippage_ticks", "spread_widen_ticks", "fill_prob",
            "n_paths", "prop_survival_prob",
        ],
    )

    return {
        "mc_regime_summary_df": mc_regime_summary_df,
        "mc_slippage_surface_df": mc_slippage_surface_df,
    }
