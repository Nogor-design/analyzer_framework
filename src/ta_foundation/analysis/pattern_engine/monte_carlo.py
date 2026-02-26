from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def _simulate_path(
    *,
    blocks: List[np.ndarray],
    n_blocks: int,
    rng: np.random.Generator,
) -> np.ndarray:
    picks = rng.integers(0, len(blocks), size=n_blocks)
    seq = np.concatenate([blocks[i] for i in picks]) if picks.size else np.array([], dtype=float)
    return seq


def _max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    return float(np.max(dd))


def _trailing_dd_breach(
    *,
    equity: np.ndarray,
    trailing_dd: float,
) -> bool:
    """
    Simplified trailing drawdown: breach if equity falls more than trailing_dd from its running peak.
    (Path-dependent; good enough for baseline; refine for Apex-style rules later.)
    """
    if equity.size == 0:
        return False
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    return bool(np.any(dd > trailing_dd))


def run_prop_monte_carlo(
    *,
    equity_events_df: pd.DataFrame,
    constraints: Dict[str, Any],
    mc_options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    equity_events_df columns (minimum):
      dt, entity_type, entity_id, pnl_ticks, day_id (or session_id)
    constraints:
      trailing_drawdown_usd, daily_loss_limit_usd, tick_value_usd
    mc_options:
      n_paths, block_unit
    """
    if equity_events_df is None or len(equity_events_df) == 0:
        empty = pd.DataFrame()
        return {"mc_summary_df": empty}

    tick_value = float(constraints.get("tick_value_usd", 12.5))
    trailing_dd_usd = float(constraints.get("trailing_drawdown_usd", 1500))
    daily_loss_usd = float(constraints.get("daily_loss_limit_usd", 1000))

    n_paths = int(mc_options.get("n_paths", 2000))
    block_unit = str(mc_options.get("block_unit", "day"))

    df = equity_events_df.sort_values("dt").reset_index(drop=True)
    df["pnl_usd"] = df["pnl_ticks"].astype(float) * tick_value

    # build blocks
    key = "day_id" if block_unit == "day" else "session_id"
    if key not in df.columns:
        # fallback: one block = all
        blocks_by_entity = {(t, i): [df["pnl_usd"].to_numpy(dtype=float)] for t, i in df[["entity_type", "entity_id"]].drop_duplicates().itertuples(index=False)}
    else:
        blocks_by_entity: Dict[Tuple[str, str], List[np.ndarray]] = {}
        for (etype, eid), d2 in df.groupby(["entity_type", "entity_id"], sort=False):
            blocks = []
            for _, b in d2.groupby(key, sort=False):
                blocks.append(b["pnl_usd"].to_numpy(dtype=float))
            if len(blocks) == 0:
                blocks = [d2["pnl_usd"].to_numpy(dtype=float)]
            blocks_by_entity[(str(etype), str(eid))] = blocks

    rng = np.random.default_rng(7)

    rows: List[Dict[str, Any]] = []
    for (etype, eid), blocks in blocks_by_entity.items():
        # n_blocks per path ~ historical count of blocks
        n_blocks = len(blocks)
        if n_blocks <= 0:
            continue

        dds = []
        daily_breach = 0
        trailing_breach = 0
        days_to_breach = []

        for _ in range(n_paths):
            pnl = _simulate_path(blocks=blocks, n_blocks=n_blocks, rng=rng)
            equity = np.cumsum(pnl)

            dd = _max_drawdown(equity)
            dds.append(dd)

            # daily loss breach: any block pnl < -daily_loss
            if any(float(b.sum()) < -daily_loss_usd for b in blocks):
                # This uses historical blocks; for synthetic paths we should compute on sampled blocks.
                # Approx baseline: compute on sampled pnl partitioned by original block sizes.
                pass

            # compute breaches on synthetic path by reconstructing sampled block totals
            # (we can do it more simply: daily breach if any sampled block total < -daily_loss)
            # Build sampled block totals directly:
            picks = rng.integers(0, len(blocks), size=n_blocks)
            sampled_totals = [float(blocks[i].sum()) for i in picks]
            if any(t < -daily_loss_usd for t in sampled_totals):
                daily_breach += 1

            if _trailing_dd_breach(equity=equity, trailing_dd=trailing_dd_usd):
                trailing_breach += 1
                # days-to-breach: approximate by first sampled block index that triggers
                # (coarse but useful)
                peak = -1e18
                eq = 0.0
                breach_at = None
                for j, tot in enumerate(sampled_totals):
                    eq += tot
                    peak = max(peak, eq)
                    if (peak - eq) > trailing_dd_usd:
                        breach_at = j + 1
                        break
                days_to_breach.append(float(breach_at) if breach_at is not None else float("nan"))
            else:
                days_to_breach.append(float("nan"))

        dds_np = np.asarray(dds, dtype=float)
        dd_p50 = float(np.nanpercentile(dds_np, 50))
        dd_p90 = float(np.nanpercentile(dds_np, 90))
        dd_p99 = float(np.nanpercentile(dds_np, 99))

        rows.append(
            {
                "entity_type": etype,
                "entity_id": eid,
                "horizon": int(df["horizon"].iloc[0]) if "horizon" in df.columns else -1,
                "n_paths": int(n_paths),
                "dd_p50": dd_p50,
                "dd_p90": dd_p90,
                "dd_p99": dd_p99,
                "daily_loss_breach_prob": float(daily_breach / max(n_paths, 1)),
                "trailing_dd_breach_prob": float(trailing_breach / max(n_paths, 1)),
                "median_days_to_breach": float(np.nanmedian(np.asarray(days_to_breach, dtype=float))),
                "eval_pass_prob": np.nan,
                "stress_slip_ticks": int(mc_options.get("stress_slip_ticks", 0)),
                "prop_survival_score": np.nan,
            }
        )

    mc_summary_df = pd.DataFrame(rows)
    return {"mc_summary_df": mc_summary_df}