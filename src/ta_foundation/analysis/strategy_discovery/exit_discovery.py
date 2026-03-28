from __future__ import annotations

"""
Exit Policy Discovery
=====================
Phase 5 of the Strategy Discovery Engine.

Sweeps exit parameter space using MAE/MFE-derived bounds from mae_mfe.py,
re-using the existing tick-path simulation engine from analysis/exits/simulate.py.

Outputs a ranked list of exit policy candidates + regime-conditional
recommendations — not a single winner.

Entry point: run_exit_discovery(pkg, market, options, bars_with_regime=None)

Attaches results to:
  pkg.metadata["derived"]["strategy_discovery"]["exit_discovery"]  (JSON-safe)

Config lives under strategy_discovery.exit_discovery in report.yaml:

  strategy_discovery:
    exit_discovery:
      enabled: true
      tick_size: 0.25
      atr_tf: "5m"
      atr_period: 14
      top_n: 10          # how many top combos to store in ranking
      max_combos: 80     # hard cap on grid size
      families:          # which families to sweep (default: all)
        - fixed_rr
        - atr_trail
        - be_atr_trail
        - chandelier
        - giveback
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ta_foundation.analysis.exits.simulate import ExitSimConfig, simulate_exit_policies_for_run
from ta_foundation.analysis.exits.policies import (
    FixedStopTargetPolicy,
    AtrTrailPolicy,
    BreakEvenAtrTrailPolicy,
    ChandelierAtrTrailPolicy,
    GivebackAfterMfePolicy,
)


# ---------------------------------------------------------------------------
# JSON-safety helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _make_json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if not np.isfinite(v) else v
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    return obj


# ---------------------------------------------------------------------------
# Instrument/contract inference (mirrors exit_policy_simulation.py)
# ---------------------------------------------------------------------------

def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    cols = list(df.columns)
    lower = {str(c).lower(): c for c in cols}
    for c in candidates:
        if c in cols:
            return c
        lc = str(c).lower()
        if lc in lower:
            return lower[lc]
    for cand in candidates:
        lc = str(cand).lower()
        for col in cols:
            if lc in str(col).lower():
                return col
    return None


def _infer_ic_from_trades(trades: pd.DataFrame) -> Optional[Tuple[str, str]]:
    inst_col = _find_col(trades, ["instrument", "Instrument"])
    if not inst_col:
        return None
    v = trades[inst_col].dropna()
    if v.empty:
        return None
    parts = str(v.iloc[0]).strip().split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


# ---------------------------------------------------------------------------
# Parameter grid builders
# ---------------------------------------------------------------------------

def _linspace_ticks(lo: Optional[float], hi: Optional[float], n: int,
                    fallback_lo: float, fallback_hi: float) -> List[float]:
    """Return n evenly-spaced tick values between lo and hi (or fallbacks)."""
    lo = lo if lo is not None and lo > 0 else fallback_lo
    hi = hi if hi is not None and hi > lo else fallback_hi
    if hi <= lo:
        hi = lo * 2.0
    return [round(float(v), 4) for v in np.linspace(lo, hi, n)]


def _arange_mult(lo: float, hi: float, step: float) -> List[float]:
    """Return list of ATR multiples from lo to hi (inclusive) by step."""
    vals = []
    v = lo
    while v <= hi + 1e-9:
        vals.append(round(float(v), 3))
        v += step
    return vals or [lo]


def build_policy_grid(
    mae_mfe_profile: Dict[str, Any],
    options: Dict[str, Any],
    tick_size: float = 0.25,
) -> List[Tuple[str, Any]]:
    """
    Build a list of (unique_name, policy_object) pairs spanning all exit families.
    Parameter ranges are constrained by MAE/MFE-derived bounds when available.

    Returns at most options["max_combos"] entries.
    """
    bounds = (mae_mfe_profile or {}).get("exit_parameter_bounds") or {}
    families_cfg = options.get("families") or ["fixed_rr", "atr_trail", "be_atr_trail", "chandelier", "giveback"]
    max_combos = int(options.get("max_combos", 80))

    # Convert USD bounds → ticks
    tick_value = float(options.get("tick_value", 5.0))

    def _usd_to_ticks(usd: Optional[float]) -> Optional[float]:
        if usd is None or tick_value == 0:
            return None
        return _safe_float(usd / tick_value)

    stop_min_tks = _usd_to_ticks(bounds.get("stop_min_usd")) or bounds.get("stop_min_pts")
    stop_nat_tks = _usd_to_ticks(bounds.get("stop_natural_usd")) or bounds.get("stop_natural_pts")
    stop_max_tks = _usd_to_ticks(bounds.get("stop_max_usd")) or bounds.get("stop_max_pts")
    tgt_min_tks  = _usd_to_ticks(bounds.get("target_min_usd")) or bounds.get("target_min_pts")
    tgt_nat_tks  = _usd_to_ticks(bounds.get("target_natural_usd")) or bounds.get("target_natural_pts")
    tgt_max_tks  = _usd_to_ticks(bounds.get("target_max_usd")) or bounds.get("target_max_pts")
    trail_act_tks = _usd_to_ticks(bounds.get("trail_activation_usd")) or bounds.get("trail_activation_pts")
    trail_dist_tks = _usd_to_ticks(bounds.get("trail_distance_usd")) or bounds.get("trail_distance_pts")

    # Per-family budget ensures diversity when max_combos is tight
    n_families = len(families_cfg)
    per_family_budget = max(4, max_combos // max(n_families, 1))

    families_out: Dict[str, List[Tuple[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Family: fixed_rr — FixedStopTargetPolicy (tick-based)
    # ------------------------------------------------------------------
    if "fixed_rr" in families_cfg:
        fam: List[Tuple[str, Any]] = []
        stop_vals = _linspace_ticks(stop_min_tks, stop_max_tks, 3, fallback_lo=12, fallback_hi=32)
        tgt_vals  = _linspace_ticks(tgt_min_tks, tgt_max_tks,  4, fallback_lo=20, fallback_hi=60)
        for s in stop_vals:
            for t in tgt_vals:
                if t >= s:  # require at least 1:1 R:R
                    name = f"fixed_rr_s{int(s)}_t{int(t)}"
                    fam.append((name, FixedStopTargetPolicy(name=name, stop_ticks=s, target_ticks=t)))
        families_out["fixed_rr"] = fam

    # ------------------------------------------------------------------
    # Family: atr_trail — AtrTrailPolicy
    # ------------------------------------------------------------------
    if "atr_trail" in families_cfg:
        fam = []
        stop_mults  = _arange_mult(0.75, 1.5, 0.25)
        trail_mults = _arange_mult(1.25, 2.5, 0.25)
        for sm in stop_mults:
            for tm in trail_mults:
                if tm > sm:
                    name = f"atr_trail_s{sm}_t{tm}"
                    fam.append((name, AtrTrailPolicy(name=name, stop_atr_mult=sm, trail_atr_mult=tm)))
        families_out["atr_trail"] = fam

    # ------------------------------------------------------------------
    # Family: be_atr_trail — BreakEvenAtrTrailPolicy
    # ------------------------------------------------------------------
    if "be_atr_trail" in families_cfg:
        fam = []
        stop_mults    = [0.75, 1.0, 1.25]
        trigger_mults = _arange_mult(0.5, 1.25, 0.25)
        trail_mults   = _arange_mult(1.25, 2.0, 0.25)
        for sm in stop_mults:
            for trig in trigger_mults:
                for tm in trail_mults:
                    name = f"be_trail_s{sm}_trig{trig}_t{tm}"
                    fam.append((name, BreakEvenAtrTrailPolicy(
                        name=name,
                        stop_atr_mult=sm,
                        trail_atr_mult=tm,
                        be_trigger_atr_mult=trig,
                        be_offset_ticks=2.0,
                    )))
        families_out["be_atr_trail"] = fam

    # ------------------------------------------------------------------
    # Family: chandelier — ChandelierAtrTrailPolicy
    # ------------------------------------------------------------------
    if "chandelier" in families_cfg:
        fam = []
        stop_mults  = _arange_mult(0.75, 1.5, 0.25)
        trail_mults = _arange_mult(1.5, 2.5, 0.25)
        for sm in stop_mults:
            for tm in trail_mults:
                if tm > sm:
                    name = f"chandelier_s{sm}_t{tm}"
                    fam.append((name, ChandelierAtrTrailPolicy(name=name, stop_atr_mult=sm, trail_atr_mult=tm)))
        families_out["chandelier"] = fam

    # ------------------------------------------------------------------
    # Family: giveback — GivebackAfterMfePolicy (tick-based)
    # arm = MFE level at which giveback is armed (use target bounds)
    # giveback = retrace allowed from peak (use ETD/trail bounds)
    # ------------------------------------------------------------------
    if "giveback" in families_cfg:
        fam = []
        # Arm: sweep MFE target range (when to arm giveback)
        # Giveback: fixed fractions of arm (25%, 40%, 55%)
        arm_base = tgt_min_tks if tgt_min_tks and tgt_min_tks > 4 else 15.0
        arm_vals = _linspace_ticks(arm_base * 0.6, arm_base * 2.0, 4, 10.0, 40.0)
        gb_fracs = [0.25, 0.40, 0.55]  # giveback = arm * frac
        for arm in arm_vals:
            for frac in gb_fracs:
                gb = round(arm * frac, 1)
                if gb >= 2.0:  # minimum 2 ticks giveback
                    name = f"giveback_arm{int(arm)}_gb{int(gb)}"
                    fam.append((name, GivebackAfterMfePolicy(name=name, arm_mfe_ticks=arm, giveback_ticks=gb)))
        families_out["giveback"] = fam

    # Assemble with per-family cap, then enforce total max_combos
    policies: List[Tuple[str, Any]] = []
    for fam_name, fam_list in families_out.items():
        # Sample evenly within family if over budget
        if len(fam_list) > per_family_budget:
            step = max(1, len(fam_list) // per_family_budget)
            fam_list = fam_list[::step][:per_family_budget]
        policies.extend(fam_list)

    # Final hard cap
    if len(policies) > max_combos:
        policies = policies[:max_combos]

    return policies


# ---------------------------------------------------------------------------
# Metrics aggregation from simulation DataFrame
# ---------------------------------------------------------------------------

def _aggregate_policy_metrics(sim_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Aggregate per-policy metrics from the simulation output DataFrame.

    sim_df columns: policy, pnl_ticks, mae_ticks, mfe_ticks, exit_reason, ...

    Returns list of dicts sorted by net_ticks descending.
    """
    if sim_df is None or sim_df.empty or "policy" not in sim_df.columns:
        return []

    # Drop diagnostic rows
    df = sim_df[sim_df["policy"] != "DIAGNOSTIC"].copy()
    if df.empty:
        return []

    df["pnl_ticks"] = pd.to_numeric(df["pnl_ticks"], errors="coerce")
    df["mae_ticks"] = pd.to_numeric(df.get("mae_ticks"), errors="coerce") if "mae_ticks" in df.columns else np.nan
    df["mfe_ticks"] = pd.to_numeric(df.get("mfe_ticks"), errors="coerce") if "mfe_ticks" in df.columns else np.nan

    rows: List[Dict[str, Any]] = []
    for policy_name, grp in df.groupby("policy"):
        pnl = grp["pnl_ticks"].dropna()
        n = int(len(pnl))
        if n == 0:
            continue
        n_win = int((pnl > 0).sum())
        n_loss = int((pnl <= 0).sum())
        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = abs(float(pnl[pnl < 0].sum()))
        pf = _safe_float(gross_profit / gross_loss) if gross_loss > 0 else None
        win_rate = _safe_float(n_win / n) if n > 0 else None
        net_ticks = _safe_float(pnl.sum())
        avg_ticks = _safe_float(pnl.mean())

        # Max drawdown on cumulative tick P&L
        cumsum = pnl.cumsum().values
        running_max = np.maximum.accumulate(cumsum)
        max_dd = _safe_float(float(np.max(running_max - cumsum)))

        # Exit reason distribution
        exit_reasons: Dict[str, int] = {}
        if "exit_reason" in grp.columns:
            for reason, cnt in grp["exit_reason"].value_counts().items():
                exit_reasons[str(reason)] = int(cnt)

        rows.append({
            "policy_name": str(policy_name),
            "n_trades": n,
            "n_winners": n_win,
            "n_losers": n_loss,
            "net_ticks": net_ticks,
            "avg_ticks": avg_ticks,
            "win_rate": win_rate,
            "profit_factor": pf,
            "max_dd_ticks": max_dd,
            "exit_reasons": exit_reasons,
        })

    rows.sort(key=lambda r: (r.get("net_ticks") or -1e9), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Regime-conditional breakdown
# ---------------------------------------------------------------------------

def _regime_breakdown(
    sim_df: pd.DataFrame,
    bars_with_regime: Optional[pd.DataFrame],
) -> Dict[str, Dict[str, Any]]:
    """
    Join sim_df to bars_with_regime by entry_dt (merge_asof backward),
    then find the best-performing policy per regime.

    Returns dict keyed by regime label with keys:
      best_policy, net_ticks, win_rate, n_trades
    """
    if bars_with_regime is None or bars_with_regime.empty:
        return {}
    if sim_df is None or sim_df.empty:
        return {}
    if "entry_dt" not in sim_df.columns or "dt" not in bars_with_regime.columns:
        return {}
    if "regime" not in bars_with_regime.columns:
        return {}

    try:
        # Normalize timestamps to naive UTC for merge_asof
        def _to_naive_utc(s: pd.Series) -> pd.Series:
            s = pd.to_datetime(s, errors="coerce")
            if getattr(s.dt, "tz", None) is not None:
                s = s.dt.tz_convert("UTC").dt.tz_localize(None)
            return s

        work = sim_df[sim_df["policy"] != "DIAGNOSTIC"].copy()
        if work.empty:
            return {}

        work["_entry_utc"] = _to_naive_utc(pd.to_datetime(work["entry_dt"]))
        work = work.sort_values("_entry_utc").reset_index(drop=True)

        bars = bars_with_regime.copy()
        bars["_dt_utc"] = _to_naive_utc(pd.to_datetime(bars["dt"]))
        bars = bars.sort_values("_dt_utc").reset_index(drop=True)

        merged = pd.merge_asof(
            work,
            bars[["_dt_utc", "regime"]],
            left_on="_entry_utc",
            right_on="_dt_utc",
            direction="backward",
        )

        merged["pnl_ticks"] = pd.to_numeric(merged["pnl_ticks"], errors="coerce")

        result: Dict[str, Dict[str, Any]] = {}
        for regime_label, reg_grp in merged.groupby("regime"):
            best_policy = None
            best_net = -1e9

            for policy_name, pol_grp in reg_grp.groupby("policy"):
                pnl = pol_grp["pnl_ticks"].dropna()
                if len(pnl) < 5:  # skip tiny samples
                    continue
                net = float(pnl.sum())
                if net > best_net:
                    best_net = net
                    n = int(len(pnl))
                    n_win = int((pnl > 0).sum())
                    gross_profit = float(pnl[pnl > 0].sum())
                    gross_loss = abs(float(pnl[pnl < 0].sum()))
                    pf = _safe_float(gross_profit / gross_loss) if gross_loss > 0 else None
                    best_policy = {
                        "policy_name": str(policy_name),
                        "n_trades": n,
                        "net_ticks": _safe_float(net),
                        "win_rate": _safe_float(n_win / n) if n > 0 else None,
                        "profit_factor": pf,
                    }

            if best_policy is not None:
                result[str(regime_label)] = best_policy

        return result

    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Policy family label helper
# ---------------------------------------------------------------------------

def _family_of(policy_name: str) -> str:
    name = str(policy_name).lower()
    if name.startswith("fixed_rr"):
        return "fixed_rr"
    if name.startswith("be_trail"):
        return "be_atr_trail"
    if name.startswith("chandelier"):
        return "chandelier"
    if name.startswith("atr_trail"):
        return "atr_trail"
    if name.startswith("giveback"):
        return "giveback"
    return "other"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_exit_discovery(
    pkg: Any,
    market: Any,
    options: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Run exit policy discovery for a single AnalysisPackage.

    Parameters
    ----------
    pkg             : AnalysisPackage
    market          : MarketDataStore
    options         : strategy_discovery.exit_discovery block from YAML
    bars_with_regime: bars DataFrame with regime column (from Phase 0)

    Returns
    -------
    JSON-safe dict with keys:
      policy_ranking   : top-N combos sorted by net_ticks
      best_by_regime   : best policy per regime label
      sweep_summary    : aggregate sweep stats
      parameter_bounds_used : what MAE/MFE bounds were used
      diagnostics      : coverage, issues
    """
    diag: Dict[str, Any] = {
        "instrument": None,
        "contract": None,
        "n_trades": 0,
        "n_combos_evaluated": 0,
        "issues": [],
    }

    if not options.get("enabled", True):
        return {"enabled": False, "diagnostics": diag}

    trades = getattr(pkg, "trades", None)
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        diag["issues"].append("no trades on package")
        return {"policy_ranking": [], "best_by_regime": {}, "diagnostics": diag}

    diag["n_trades"] = int(len(trades))

    # Infer instrument/contract
    ic = _infer_ic_from_trades(trades)
    if ic is None:
        diag["issues"].append("could not infer instrument/contract from trades")
        return {"policy_ranking": [], "best_by_regime": {}, "diagnostics": diag}

    instrument, contract = ic
    diag["instrument"] = instrument
    diag["contract"] = contract

    if market is None:
        diag["issues"].append("no market data store available")
        return {"policy_ranking": [], "best_by_regime": {}, "diagnostics": diag}

    # Pull MAE/MFE profile from pkg metadata
    mae_mfe_profile: Dict[str, Any] = {}
    try:
        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        sd = derived.get("strategy_discovery") or {}
        mae_mfe_profile = sd.get("mae_mfe_profile") or {}
    except Exception:
        pass

    # Build parameter bounds summary for output
    bounds = mae_mfe_profile.get("exit_parameter_bounds") or {}
    tick_size = float(options.get("tick_size", 0.25))
    atr_tf = str(options.get("atr_tf", "5m"))
    atr_period = int(options.get("atr_period", 14))
    top_n = int(options.get("top_n", 10))

    parameter_bounds_used = {
        "stop_min_pts": _safe_float(bounds.get("stop_min_pts")),
        "stop_natural_pts": _safe_float(bounds.get("stop_natural_pts")),
        "stop_max_pts": _safe_float(bounds.get("stop_max_pts")),
        "target_min_pts": _safe_float(bounds.get("target_min_pts")),
        "target_natural_pts": _safe_float(bounds.get("target_natural_pts")),
        "target_max_pts": _safe_float(bounds.get("target_max_pts")),
        "trail_activation_pts": _safe_float(bounds.get("trail_activation_pts")),
        "trail_distance_pts": _safe_float(bounds.get("trail_distance_pts")),
        "mfe_mae_ratio": _safe_float(bounds.get("mfe_mae_ratio")),
        "source": "mae_mfe_profile" if bounds else "defaults",
    }

    # Build policy grid
    policy_grid = build_policy_grid(mae_mfe_profile, options, tick_size=tick_size)
    if not policy_grid:
        diag["issues"].append("policy grid is empty — check family config")
        return {"policy_ranking": [], "best_by_regime": {}, "diagnostics": diag}

    policy_objects = [pol for _, pol in policy_grid]
    diag["n_combos_evaluated"] = len(policy_objects)

    # Simulation config
    sim_cfg = ExitSimConfig(
        tick_size=tick_size,
        atr_tf=atr_tf,
        atr_period=atr_period,
        bounded_to_original_exit=True,
        use_bid_ask_triggers=True,
    )

    # Run tick-path simulation for all combos at once
    run_id = str(getattr(pkg, "run_id", "unknown"))
    try:
        sim_df = simulate_exit_policies_for_run(
            run_id=run_id,
            trades=trades,
            market=market,
            instrument=instrument,
            contract=contract,
            policies=policy_objects,
            cfg=sim_cfg,
        )
    except Exception as exc:
        diag["issues"].append(f"simulation failed: {exc}")
        return {
            "policy_ranking": [],
            "best_by_regime": {},
            "sweep_summary": {},
            "parameter_bounds_used": parameter_bounds_used,
            "diagnostics": diag,
        }

    # Check for diagnostic-only result (simulation engine returns DIAGNOSTIC rows on failure)
    if sim_df is None or sim_df.empty:
        diag["issues"].append("simulation returned empty result")
        return {
            "policy_ranking": [],
            "best_by_regime": {},
            "sweep_summary": {},
            "parameter_bounds_used": parameter_bounds_used,
            "diagnostics": diag,
        }

    if "policy" in sim_df.columns and (sim_df["policy"] == "DIAGNOSTIC").all():
        first_reason = sim_df["exit_reason"].iloc[0] if not sim_df.empty else "unknown"
        diag["issues"].append(f"simulation diagnostic: {first_reason}")
        return {
            "policy_ranking": [],
            "best_by_regime": {},
            "sweep_summary": {},
            "parameter_bounds_used": parameter_bounds_used,
            "diagnostics": diag,
        }

    # Aggregate per-policy metrics
    policy_metrics = _aggregate_policy_metrics(sim_df)

    # Add family and rank
    for rank, row in enumerate(policy_metrics, start=1):
        row["rank"] = rank
        row["family"] = _family_of(row["policy_name"])

    # Top-N ranking
    policy_ranking = policy_metrics[:top_n]

    # Best-by-family summary (best net_ticks per family)
    best_by_family: Dict[str, Any] = {}
    for row in policy_metrics:
        fam = row["family"]
        if fam not in best_by_family:
            best_by_family[fam] = {k: v for k, v in row.items() if k != "exit_reasons"}

    # Regime-conditional breakdown
    best_by_regime = _regime_breakdown(sim_df, bars_with_regime)

    # Sweep summary
    sweep_summary: Dict[str, Any] = {
        "n_combos": len(policy_metrics),
        "best_policy": policy_ranking[0]["policy_name"] if policy_ranking else None,
        "best_net_ticks": policy_ranking[0]["net_ticks"] if policy_ranking else None,
        "best_family": policy_ranking[0]["family"] if policy_ranking else None,
        "families_evaluated": sorted({r["family"] for r in policy_metrics}),
    }

    return _make_json_safe({
        "policy_ranking": policy_ranking,
        "best_by_family": best_by_family,
        "best_by_regime": best_by_regime,
        "sweep_summary": sweep_summary,
        "parameter_bounds_used": parameter_bounds_used,
        "diagnostics": diag,
    })
