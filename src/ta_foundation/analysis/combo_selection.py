from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, List, Tuple

import pandas as pd


@dataclass(frozen=True)
class ComboScore:
    run_ids: Tuple[str, ...]
    k: int

    # Primary objectives: avoid shared losing days
    any_coloss_rate: float   # fraction of traded-days with >=2 losers inside the combo
    all_loss_rate: float     # fraction of traded-days where all bots lose

    # Tie-breakers / diagnostics
    traded_days: int         # days where at least one bot in combo traded
    combo_cum_end: float     # final cumulative PnL of the combo (daily pnl summed across bots)


def score_combo(pnl: pd.DataFrame, traded: pd.DataFrame, run_ids: Tuple[str, ...]) -> ComboScore:
    """
    pnl/traded: aligned daily frames (index=dates, columns=run_id)
    "lose" = (traded) & (pnl < 0)

    any_coloss_rate is computed on combo-traded days only (>=1 bot traded).
    """
    k = len(run_ids)
    cols = list(run_ids)

    pnl_c = pnl[cols].copy().fillna(0.0)
    trd_c = traded[cols].copy().fillna(False)

    combo_traded_mask = trd_c.any(axis=1)
    traded_days = int(combo_traded_mask.sum())

    # If never traded, treat as worst (so it doesn't rank high)
    if traded_days == 0:
        return ComboScore(run_ids=run_ids, k=k, any_coloss_rate=1.0, all_loss_rate=1.0, traded_days=0, combo_cum_end=0.0)

    loss = (pnl_c < 0) & trd_c
    loss_count = loss.sum(axis=1)

    loss_count_t = loss_count[combo_traded_mask]
    any_coloss_rate = float((loss_count_t >= 2).mean())
    all_loss_rate = float((loss_count_t == k).mean())

    combo_daily = pnl_c.sum(axis=1)
    combo_cum_end = float(combo_daily.cumsum().iloc[-1]) if len(combo_daily) else 0.0

    return ComboScore(
        run_ids=run_ids,
        k=k,
        any_coloss_rate=any_coloss_rate,
        all_loss_rate=all_loss_rate,
        traded_days=traded_days,
        combo_cum_end=combo_cum_end,
    )


def _sort_key(cs: ComboScore):
    # lower co-loss is best; then lower all-loss; then higher pnl; then more traded days
    return (cs.any_coloss_rate, cs.all_loss_rate, -cs.combo_cum_end, -cs.traded_days)


def top_k2_exact(pnl: pd.DataFrame, traded: pd.DataFrame, run_ids: List[str], top_n: int) -> List[ComboScore]:
    out: List[ComboScore] = []
    for a, b in combinations(run_ids, 2):
        out.append(score_combo(pnl, traded, (a, b)))
    out.sort(key=_sort_key)
    return out[:top_n]


def top_k_beam(pnl: pd.DataFrame, traded: pd.DataFrame, run_ids: List[str], k: int, top_n: int, beam_width: int) -> List[ComboScore]:
    """
    Beam search for k>=3 to keep runtime sane for many bots.

    Seed: best pairs (expanded)
    Expand: add one bot at a time; keep best beam_width at each depth.
    """
    assert k >= 3

    seeds = top_k2_exact(pnl, traded, run_ids, top_n=min(beam_width, max(top_n * 25, beam_width // 2)))
    beam = seeds
    cur_k = 2

    while cur_k < k:
        candidates: Dict[Tuple[str, ...], ComboScore] = {}
        for cs in beam:
            used = set(cs.run_ids)
            for rid in run_ids:
                if rid in used:
                    continue
                new_ids = tuple(sorted((*cs.run_ids, rid)))
                sc = score_combo(pnl, traded, new_ids)
                prev = candidates.get(new_ids)
                if prev is None or _sort_key(sc) < _sort_key(prev):
                    candidates[new_ids] = sc

        nxt = list(candidates.values())
        nxt.sort(key=_sort_key)
        beam = nxt[:beam_width]
        cur_k += 1

    beam.sort(key=_sort_key)
    return beam[:top_n]


def top_combos(pnl: pd.DataFrame, traded: pd.DataFrame, run_ids: List[str], k: int, top_n: int = 5, beam_width: int = 200) -> List[ComboScore]:
    if k == 2:
        return top_k2_exact(pnl, traded, run_ids, top_n)
    return top_k_beam(pnl, traded, run_ids, k, top_n, beam_width)
