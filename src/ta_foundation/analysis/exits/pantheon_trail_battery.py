"""Fast logic-validation battery for PantheonMaster's stop/trail policies.

This is the tool that kills the "one trade at a time in Playback" loop. It drives
the shared :mod:`pantheon_stop_engine` (the Python mirror of
``PantheonStopEngine.cs``) over many synthetic *adversarial* price paths — run-up
then retrace, gap-through-the-stop, chop, steady trend — for all five exit
policies, and reports the stop trajectory, exit, and a set of invariant checks.

Because it touches no NinjaTrader, a full battery runs in well under a second, so
you validate the *math* across hundreds of scenarios before ever compiling the
strategy or the AddOn. Final live-plumbing confirmation (that NT actually submits
the ``Change`` orders) is a separate, small step against the same engine.

Driver semantics mirror the live executor (PantheonMaster.ManageLiveDynamicStop +
OnExecutionUpdate seeding + MoveStopIfImproved):
  - seed the resting stop at ``initial_stop`` on entry;
  - per tick: (1) fill-check against the *resting* stop set by prior ticks
    (a long never trips its own freshly-raised stop), (2) update favorable extreme
    and peak open profit with this tick, (3) compute the policy's proposed stop,
    ratchet it (move only if it improves), and that becomes next tick's resting stop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ta_foundation.analysis.exits.pantheon_stop_engine import (
    LONG,
    SHORT,
    PantheonStopConfig,
    PantheonStopPolicy,
    compute_trail_stop,
    initial_stop,
    open_profit_currency,
)


@dataclass
class TrailReplay:
    """Outcome of replaying one price path under one policy."""
    exit_index: Optional[int]
    exit_price: Optional[float]
    exit_reason: str                       # "trail_stop" | "no_exit_in_window"
    n_stop_moves: int
    final_stop: float
    stop_trajectory: list = field(default_factory=list)   # (tick_index, stop_price) on each move
    pnl_ticks: Optional[float] = None      # signed, only when exited


def replay_trail(
    direction: int,
    entry_price: float,
    prices: np.ndarray,
    cfg: PantheonStopConfig,
    *,
    atr: float | np.ndarray = 1.0,
    quantity: int = 1,
    chandelier_ref: Optional[np.ndarray] = None,
    apply_market_guard: bool = True,
) -> TrailReplay:
    """Replay a single tick path. ``atr`` may be scalar (constant) or per-tick.
    ``chandelier_ref`` defaults to the running favorable extreme (so Chandelier
    behaves as AtrTrail when no separate bar reference is supplied)."""
    p = np.asarray(prices, dtype=float)
    n = p.size
    if n == 0:
        return TrailReplay(None, None, "no_exit_in_window", 0, initial_stop(direction, entry_price, cfg))

    atr_arr = np.full(n, float(atr)) if np.isscalar(atr) else np.asarray(atr, dtype=float)
    tick = cfg.tick_size

    resting_stop = initial_stop(direction, entry_price, cfg)
    last_stop = resting_stop
    favorable = entry_price
    peak_profit = 0.0
    breakeven_active = False
    moves: list = []

    for i in range(n):
        price = float(p[i])

        # (1) fill-check against the resting stop set by prior ticks
        if direction == LONG and price <= resting_stop:
            return _exit(direction, entry_price, i, resting_stop, last_stop, moves, tick, quantity, cfg)
        if direction == SHORT and price >= resting_stop:
            return _exit(direction, entry_price, i, resting_stop, last_stop, moves, tick, quantity, cfg)

        # (2) update running state with this tick
        favorable = max(favorable, price) if direction == LONG else min(favorable, price)
        peak_profit = max(peak_profit, open_profit_currency(direction, price, entry_price, cfg.point_value, quantity))
        cref = float(chandelier_ref[i]) if chandelier_ref is not None else favorable

        # (3) compute + ratchet the policy's proposed stop -> next tick's resting stop
        res = compute_trail_stop(
            direction, entry_price, price, favorable, float(atr_arr[i]),
            peak_profit, quantity, cref, breakeven_active, cfg,
        )
        if res.breakeven_activated:
            breakeven_active = True
        if res.has_proposal and _improves_guarded(direction, res.proposed_stop, last_stop, price, tick, apply_market_guard):
            last_stop = res.proposed_stop
            moves.append((i, last_stop))
        resting_stop = last_stop

    return TrailReplay(None, None, "no_exit_in_window", len(moves), last_stop, moves)


def _improves_guarded(direction, proposed, last_stop, market_price, tick, apply_market_guard) -> bool:
    from ta_foundation.analysis.exits.pantheon_stop_engine import improves
    if not improves(direction, proposed, last_stop, tick):
        return False
    if not apply_market_guard:
        return True
    # Side-of-market guard (mirror of MoveStopIfImproved): a long stop must stay
    # below market, a short stop above — otherwise the broker rejects it.
    if direction == LONG and proposed >= market_price - tick * 0.5:
        return False
    if direction == SHORT and proposed <= market_price + tick * 0.5:
        return False
    return True


def _exit(direction, entry_price, i, fill_stop, last_stop, moves, tick, quantity, cfg) -> TrailReplay:
    pnl_ticks = (fill_stop - entry_price) / tick if direction == LONG else (entry_price - fill_stop) / tick
    return TrailReplay(
        exit_index=i, exit_price=fill_stop, exit_reason="trail_stop",
        n_stop_moves=len(moves), final_stop=last_stop, stop_trajectory=list(moves),
        pnl_ticks=pnl_ticks,
    )


# ── Synthetic adversarial paths ────────────────────────────────────────────────
# Each returns a price array (ticks) for a LONG by default; pass direction=SHORT to
# mirror it around the entry so the same scenario tests the short side.

def _mirror(entry: float, path: np.ndarray, direction: int) -> np.ndarray:
    return path if direction == LONG else entry - (path - entry)


def path_run_up_then_retrace(entry=20000.0, tick=0.25, peak_ticks=120, retrace_ticks=80,
                             step_ticks=2, direction=LONG) -> np.ndarray:
    up = np.arange(0, peak_ticks + 1, step_ticks) * tick + entry
    down = entry + (peak_ticks - np.arange(step_ticks, retrace_ticks + 1, step_ticks)) * tick
    return _mirror(entry, np.concatenate([up, down]), direction)


def path_gap_through(entry=20000.0, tick=0.25, up_ticks=20, gap_ticks=200, direction=LONG) -> np.ndarray:
    up = np.arange(0, up_ticks + 1, 2) * tick + entry
    gap = np.array([entry - gap_ticks * tick])   # jumps straight past the initial stop
    return _mirror(entry, np.concatenate([up, gap]), direction)


def path_chop(entry=20000.0, tick=0.25, amplitude_ticks=15, cycles=10, direction=LONG) -> np.ndarray:
    t = np.linspace(0, cycles * 2 * np.pi, cycles * 24)
    return _mirror(entry, entry + np.sin(t) * amplitude_ticks * tick, direction)


def path_steady_trend(entry=20000.0, tick=0.25, run_ticks=300, step_ticks=2, direction=LONG) -> np.ndarray:
    return _mirror(entry, np.arange(0, run_ticks + 1, step_ticks) * tick + entry, direction)


def path_trend_then_reverse(entry=20000.0, tick=0.25, run_ticks=200, step_ticks=2, direction=LONG) -> np.ndarray:
    """Run a long way in favour, then reverse all the way back through entry. The
    headline trail test: a trailing policy should ratchet the stop up the run and
    get filled on the reversal AT A PROFIT (not back at the initial stop)."""
    up = np.arange(0, run_ticks + 1, step_ticks) * tick + entry
    down = entry + (run_ticks - np.arange(step_ticks, run_ticks + 40 + 1, step_ticks)) * tick
    return _mirror(entry, np.concatenate([up, down]), direction)


SYNTHETIC_PATHS: dict[str, Callable[..., np.ndarray]] = {
    "run_up_then_retrace": path_run_up_then_retrace,
    "trend_then_reverse": path_trend_then_reverse,
    "gap_through": path_gap_through,
    "chop": path_chop,
    "steady_trend": path_steady_trend,
}

ALL_POLICIES = [
    PantheonStopPolicy.FIXED_RR,
    PantheonStopPolicy.ATR_TRAIL,
    PantheonStopPolicy.CHANDELIER,
    PantheonStopPolicy.GIVEBACK,
    PantheonStopPolicy.FIXED_STOP,
    PantheonStopPolicy.BREAK_EVEN_ONLY,
]


def run_battery(
    *,
    entry: float = 20000.0,
    atr: float = 15.0,
    directions=(LONG, SHORT),
    policies=ALL_POLICIES,
    paths: Optional[dict[str, Callable[..., np.ndarray]]] = None,
    base_cfg_kwargs: Optional[dict] = None,
) -> pd.DataFrame:
    """Run every (policy × path × direction) and return a tidy report DataFrame.
    One row per scenario with the exit, stop-move count, and invariant flags."""
    paths = paths or SYNTHETIC_PATHS
    base_cfg_kwargs = base_cfg_kwargs or {}
    rows: list[dict] = []

    for policy in policies:
        cfg = PantheonStopConfig(policy=policy, **base_cfg_kwargs)
        for path_name, maker in paths.items():
            for direction in directions:
                prices = maker(entry=entry, tick=cfg.tick_size, direction=direction)
                rep = replay_trail(direction, entry, prices, cfg, atr=atr)
                inv = check_invariants(direction, entry, prices, cfg, rep)
                rows.append({
                    "policy": policy.value,
                    "path": path_name,
                    "direction": "long" if direction == LONG else "short",
                    "n_ticks": len(prices),
                    "exit_reason": rep.exit_reason,
                    "exit_price": rep.exit_price,
                    "pnl_ticks": rep.pnl_ticks,
                    "n_stop_moves": rep.n_stop_moves,
                    "final_stop": rep.final_stop,
                    "stop_monotonic_ok": inv["stop_monotonic_ok"],
                    "stop_side_ok": inv["stop_side_ok"],
                })
    return pd.DataFrame(rows)


def check_invariants(direction, entry_price, prices, cfg: PantheonStopConfig, rep: TrailReplay) -> dict:
    """Two invariants every policy must satisfy:
    - stop_monotonic_ok: the stop only ever ratchets favourably (never loosens);
    - stop_side_ok: at the moment each move was made, the stop sat on the valid
      side of that tick's price (the live broker would not have rejected it).
    """
    traj = rep.stop_trajectory
    stop_monotonic_ok = True
    for a, b in zip(traj, traj[1:]):
        if direction == LONG and b[1] < a[1] - 1e-9:
            stop_monotonic_ok = False
        if direction == SHORT and b[1] > a[1] + 1e-9:
            stop_monotonic_ok = False

    p = np.asarray(prices, dtype=float)
    stop_side_ok = True
    for idx, stop in traj:
        mkt = float(p[idx])
        if direction == LONG and stop >= mkt:
            stop_side_ok = False
        if direction == SHORT and stop <= mkt:
            stop_side_ok = False

    return {"stop_monotonic_ok": stop_monotonic_ok, "stop_side_ok": stop_side_ok}


if __name__ == "__main__":  # pragma: no cover - manual smoke
    df = run_battery()
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(df.to_string(index=False))
    bad = df[~(df["stop_monotonic_ok"] & df["stop_side_ok"])]
    print(f"\n{len(df)} scenarios, {len(bad)} invariant violations")
