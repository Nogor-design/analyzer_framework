# ta_foundation/analysis/pattern_engine/monte_carlo.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(int(seed))


def _make_blocks(df: pd.DataFrame, block_unit: str, window_minutes: int = 60) -> List[pd.DataFrame]:
    if df.empty:
        return []
    if block_unit == "day":
        return [g for _, g in df.groupby("day_id", sort=True)]
    if block_unit == "session":
        return [g for _, g in df.groupby("session_id", sort=True)]
    if block_unit == "window":
        # window by time; assumes df sorted by dt
        out = []
        g = df.sort_values("dt")
        start = g["dt"].iloc[0]
        cur = []
        for _, r in g.iterrows():
            if (r["dt"] - start).total_seconds() >= window_minutes * 60 and cur:
                out.append(pd.DataFrame(cur))
                cur = []
                start = r["dt"]
            cur.append(r)
        if cur:
            out.append(pd.DataFrame(cur))
        return out
    raise ValueError(f"Unknown block_unit: {block_unit}")


def _apply_slippage_ticks(pnl_ticks: np.ndarray, slip_ticks: int) -> np.ndarray:
    if slip_ticks <= 0:
        return pnl_ticks
    # conservative: subtract slip from every trade
    return pnl_ticks - float(slip_ticks)


def _simulate_path(
    blocks: List[pd.DataFrame],
    *,
    n_blocks: int,
    rng: np.random.Generator,
    constraints: Dict[str, Any],
    slip_ticks: int,
) -> Dict[str, Any]:
    """
    Simulate one equity path by sampling blocks with replacement.
    Implements:
      - trailing drawdown (peak-to-valley) in USD
      - daily loss limit in USD (based on day PnL)
    """
    if not blocks:
        return {
            "max_dd_usd": 0.0,
            "daily_loss_breach": False,
            "trailing_dd_breach": False,
            "days_to_breach": np.nan,
            "final_pnl_usd": 0.0,
        }

    td_usd = float(constraints["trailing_drawdown_usd"])
    dll_usd = float(constraints["daily_loss_limit_usd"])
    tick_value = float(constraints["tick_value_usd"])

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    day_pnl = 0.0
    cur_day = None
    days = 0

    daily_breach = False
    trailing_breach = False

    for _ in range(int(n_blocks)):
        b = blocks[int(rng.integers(0, len(blocks)))]
        # ensure time order inside block
        b = b.sort_values("dt")
        pnl_ticks = b["pnl_ticks"].to_numpy(float)
        pnl_ticks = _apply_slippage_ticks(pnl_ticks, slip_ticks=slip_ticks)
        # compute daily breach by day change
        for dt, pt, day_id in zip(b["dt"].values, pnl_ticks, b["day_id"].values):
            if cur_day is None:
                cur_day = day_id
                day_pnl = 0.0
                days = 1
            elif day_id != cur_day:
                # finalize previous day
                if day_pnl <= -dll_usd:
                    daily_breach = True
                    return {
                        "max_dd_usd": float(max_dd),
                        "daily_loss_breach": True,
                        "trailing_dd_breach": bool(trailing_breach),
                        "days_to_breach": float(days),
                        "final_pnl_usd": float(equity),
                    }
                cur_day = day_id
                day_pnl = 0.0
                days += 1

            pnl_usd = float(pt * tick_value)
            equity += pnl_usd
            day_pnl += pnl_usd

            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
            if dd >= td_usd:
                trailing_breach = True
                return {
                    "max_dd_usd": float(max_dd),
                    "daily_loss_breach": bool(daily_breach),
                    "trailing_dd_breach": True,
                    "days_to_breach": float(days),
                    "final_pnl_usd": float(equity),
                }

    # finalize last day
    if day_pnl <= -dll_usd:
        daily_breach = True

    return {
        "max_dd_usd": float(max_dd),
        "daily_loss_breach": bool(daily_breach),
        "trailing_dd_breach": bool(trailing_breach),
        "days_to_breach": float(days),
        "final_pnl_usd": float(equity),
    }


def run_prop_monte_carlo(
    *,
    equity_events_df: pd.DataFrame,
    constraints: Dict[str, Any],
    mc_options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    equity_events_df columns:
      dt, entity_type, entity_id, pnl_ticks, day_id, session_id, regime
    """
    if equity_events_df.empty:
        empty = pd.DataFrame(columns=[
            "entity_type","entity_id","horizon","n_paths",
            "dd_p50","dd_p90","dd_p99",
            "daily_loss_breach_prob","trailing_dd_breach_prob",
            "median_days_to_breach","eval_pass_prob",
            "stress_slip_ticks","prop_survival_score"
        ])
        return {"mc_summary_df": empty}

    n_paths = int(mc_options.get("n_paths") or 2000)
    block_unit = str(mc_options.get("block_unit") or "day")
    window_minutes = int(mc_options.get("block_window_minutes") or 60)
    slip_list = list(mc_options.get("stress", {}).get("slippage_ticks", [0, 1, 2, 4]))
    seed = int(mc_options.get("seed") or 7)

    rng = _rng(seed)

    df = equity_events_df.sort_values("dt").copy()

    # Ensure required cols exist
    for c in ("day_id", "session_id", "pnl_ticks", "entity_type", "entity_id"):
        if c not in df.columns:
            raise ValueError(f"equity_events_df missing column: {c}")

    # blocks per entity
    summary_rows = []
    for (etype, eid), edf in df.groupby(["entity_type","entity_id"], sort=False):
        blocks = _make_blocks(edf, block_unit=block_unit, window_minutes=window_minutes)
        n_blocks = max(1, len(blocks))  # sample same count as history by default

        for slip in slip_list:
            sims = []
            for _ in range(n_paths):
                sims.append(_simulate_path(
                    blocks=blocks,
                    n_blocks=n_blocks,
                    rng=rng,
                    constraints=constraints,
                    slip_ticks=int(slip),
                ))
            max_dd = np.array([s["max_dd_usd"] for s in sims], dtype=float)
            daily_b = np.array([s["daily_loss_breach"] for s in sims], dtype=bool)
            trail_b = np.array([s["trailing_dd_breach"] for s in sims], dtype=bool)
            days_to = np.array([s["days_to_breach"] for s in sims], dtype=float)

            dd_p50 = float(np.percentile(max_dd, 50))
            dd_p90 = float(np.percentile(max_dd, 90))
            dd_p99 = float(np.percentile(max_dd, 99))

            daily_prob = float(np.mean(daily_b))
            trail_prob = float(np.mean(trail_b))
            med_days = float(np.nanmedian(days_to)) if np.any(np.isfinite(days_to)) else np.nan

            # Basic prop survival score:
            # lower breach probs + lower dd tail is better. (Expectancy handled upstream via OOS avg ticks.)
            prop_score = - (0.75 * dd_p90) - (1000.0 * trail_prob) - (750.0 * daily_prob)

            summary_rows.append({
                "entity_type": etype,
                "entity_id": eid,
                "horizon": int(edf.get("horizon", pd.Series([0])).iloc[0]) if "horizon" in edf.columns else 0,
                "n_paths": int(n_paths),
                "dd_p50": dd_p50,
                "dd_p90": dd_p90,
                "dd_p99": dd_p99,
                "daily_loss_breach_prob": daily_prob,
                "trailing_dd_breach_prob": trail_prob,
                "median_days_to_breach": med_days,
                "eval_pass_prob": np.nan,
                "stress_slip_ticks": int(slip),
                "prop_survival_score": float(prop_score),
            })

    mc_summary_df = pd.DataFrame(summary_rows)
    return {"mc_summary_df": mc_summary_df}