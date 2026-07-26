from __future__ import annotations

"""
Slippage and Latency Stress Sweep (T8 — P1-7)
==============================================
Re-evaluates a candidate's trades under a grid of execution-cost regimes so
reviewers can see how much of the apparent edge survives realistic friction.

The baseline cost model (``commission_per_side``, ``slippage_ticks``,
``tick_value``) bakes in only one slippage assumption. Real execution will be
worse: queue position varies, market orders can pay 2–3 ticks on fast moves,
and any latency between signal and fill costs additional ticks. This module
re-prices each trade across a Cartesian product of:

  slippage_ticks  — extra round-trip slippage in ticks per side
  entry_delays    — bars of latency between signal and fill, modelled as an
                    additional per-bar cost in ticks (config:
                    ``delay_cost_per_bar_ticks``).

The result is a matrix of expectancy / profit_factor / net_profit cells, plus
a single ``stress_cell`` (default ``[slip=2, delay=1]``) used as the
rejection gate. A candidate is flagged when the stress cell's expectancy
falls more than ``max_expectancy_loss_pct`` below the most-optimistic baseline
cell (the lowest slip in the grid combined with zero delay).

This module does **not** re-simulate exits — it perturbs the cost line on
already-realised trades. That captures the slippage / latency hit on entry
and exit fills correctly, which is the dominant friction in a backtest
comparison. Tick-replay intra-bar resolution is T11's job.
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


DEFAULT_SLIPPAGE_STRESS_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "slippage_ticks": [1, 2, 3],
    "entry_delays": [0, 1, 2],
    "delay_cost_per_bar_ticks": 1.0,   # per-bar cost of entry delay, in ticks/side
    "max_expectancy_loss_pct": 40.0,
    "stress_cell": [2, 1],             # (slip_ticks, delay_bars) used for the gate
}


def _safe_finite(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _profit_factor(profits: pd.Series) -> Optional[float]:
    valid = pd.to_numeric(profits, errors="coerce").dropna()
    if len(valid) == 0:
        return None
    gross_profit = float(valid[valid > 0].sum())
    gross_loss = float(valid[valid < 0].sum())
    if gross_loss == 0:
        return None
    return _safe_finite(gross_profit / abs(gross_loss))


def run_slippage_stress(
    trades: Optional[pd.DataFrame],
    *,
    baseline_cost_model: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Re-evaluate ``trades`` under a grid of (slippage, entry-delay) execution
    regimes and return a JSON-safe summary.

    Parameters
    ----------
    trades              : raw trade DataFrame (must contain a ``profit`` column;
                          ``profit_net`` is ignored — we re-derive it per cell).
    baseline_cost_model : dict with ``commission_per_side`` and ``tick_value``;
                          used as the fixed commission / tick value across all
                          cells. The cost model's own ``slippage_ticks`` is
                          ignored — we sweep over the grid instead.
    options             : merged on top of ``DEFAULT_SLIPPAGE_STRESS_CONFIG``.

    Returns
    -------
    dict with keys:
      enabled               whether the sweep ran
      stress_matrix         list of per-cell metric dicts
      baseline_expectancy   expectancy at the most-optimistic cell (min slip,
                            delay=0); used as the reference for loss_pct
      baseline_n_trades     trade count entering the sweep
      baseline_cell         {slip_ticks, delay_bars} of the reference cell
      stress_cell           per-cell dict at config['stress_cell']; the gate
                            evaluates this row's ``expectancy_loss_pct``
      max_expectancy_loss_pct  the configured rejection threshold
      passed                True iff stress_cell.expectancy_loss_pct ≤ threshold
      issues                list of human-readable warnings / skip reasons
    """
    cfg = {**DEFAULT_SLIPPAGE_STRESS_CONFIG, **(options or {})}

    result: Dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "stress_matrix": [],
        "baseline_expectancy": None,
        "baseline_n_trades": 0,
        "baseline_cell": None,
        "stress_cell": None,
        "max_expectancy_loss_pct": float(cfg.get("max_expectancy_loss_pct", 40.0)),
        "passed": False,
        "issues": [],
    }

    if not result["enabled"]:
        result["issues"].append("slippage_stress disabled in config")
        return result

    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        result["issues"].append("no trades provided")
        return result

    if "profit" not in trades.columns:
        result["issues"].append("'profit' column not found on trades")
        return result

    slip_grid = [float(s) for s in (cfg.get("slippage_ticks") or [1, 2, 3])]
    delay_grid = [int(d) for d in (cfg.get("entry_delays") or [0, 1, 2])]
    if not slip_grid or not delay_grid:
        result["issues"].append("slip_grid or delay_grid is empty")
        return result

    delay_cost_per_bar_ticks = float(cfg.get("delay_cost_per_bar_ticks", 1.0))
    commission_per_side = float(baseline_cost_model.get("commission_per_side", 2.09))
    tick_value = float(baseline_cost_model.get("tick_value", 5.00))

    profits = pd.to_numeric(trades["profit"], errors="coerce").dropna()
    n_trades = int(len(profits))
    if n_trades == 0:
        result["issues"].append("no finite profit values")
        return result

    # Baseline = most-optimistic cell (lowest slip in the grid, zero delay).
    # That is the cell against which every other cell is compared. We do not
    # use (0, 0) because the cost grid is parameterised in terms of *additional*
    # slippage beyond commission, and a 0-tick slip cell is unrealistic.
    baseline_slip = min(slip_grid)
    baseline_cost = 2.0 * commission_per_side + 2.0 * baseline_slip * tick_value
    baseline_net = profits - baseline_cost
    baseline_expectancy = _safe_finite(baseline_net.mean())

    result["baseline_expectancy"] = baseline_expectancy
    result["baseline_n_trades"] = n_trades
    result["baseline_cell"] = {"slip_ticks": baseline_slip, "delay_bars": 0}

    matrix: List[Dict[str, Any]] = []
    for slip in slip_grid:
        for delay in delay_grid:
            extra_ticks = delay * delay_cost_per_bar_ticks
            cell_cost = (
                2.0 * commission_per_side
                + 2.0 * slip * tick_value
                + 2.0 * extra_ticks * tick_value
            )
            cell_net = profits - cell_cost
            cell_expectancy = _safe_finite(cell_net.mean())
            cell_net_profit = _safe_finite(cell_net.sum())
            cell_pf = _profit_factor(cell_net)

            if (
                baseline_expectancy is None
                or baseline_expectancy == 0
                or cell_expectancy is None
            ):
                loss_pct: Optional[float] = None
            else:
                loss_pct = _safe_finite(
                    (baseline_expectancy - cell_expectancy) / abs(baseline_expectancy) * 100.0
                )

            matrix.append({
                "slip_ticks": float(slip),
                "delay_bars": int(delay),
                "n_trades": n_trades,
                "expectancy": cell_expectancy,
                "profit_factor": cell_pf,
                "net_profit": cell_net_profit,
                "expectancy_loss_pct": loss_pct,
            })

    result["stress_matrix"] = matrix

    # Locate the gate cell. If the configured stress_cell is outside the grid,
    # fall back to the worst observed cell so the gate still has something
    # meaningful to score against rather than silently passing.
    stress_cfg = cfg.get("stress_cell") or [2, 1]
    try:
        target_slip = float(stress_cfg[0])
        target_delay = int(stress_cfg[1])
    except Exception:
        target_slip, target_delay = 2.0, 1

    stress_cell = next(
        (cell for cell in matrix
         if cell["slip_ticks"] == target_slip and cell["delay_bars"] == target_delay),
        None,
    )
    if stress_cell is None:
        result["issues"].append(
            f"configured stress_cell ({target_slip}, {target_delay}) not in grid; "
            f"falling back to worst observed cell"
        )
        finite_cells = [c for c in matrix if c.get("expectancy_loss_pct") is not None]
        stress_cell = (
            max(finite_cells, key=lambda c: c["expectancy_loss_pct"])
            if finite_cells else None
        )

    result["stress_cell"] = stress_cell

    if stress_cell is None:
        result["issues"].append("no stress cell available — gate cannot evaluate")
        return result

    loss = stress_cell.get("expectancy_loss_pct")
    if loss is None:
        result["issues"].append(
            "baseline expectancy zero or non-finite — gate cannot evaluate"
        )
        return result

    result["passed"] = bool(loss <= result["max_expectancy_loss_pct"])
    return result
