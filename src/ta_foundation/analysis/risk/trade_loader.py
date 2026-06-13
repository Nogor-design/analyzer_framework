"""Per-trade loader for the account-survival harness (Phase 2 validation).

The selector spine's ``selection/loader.py`` reduces each template to per-*day*
net P&L — enough to rank lineups, but NOT enough to test trailing-drawdown
*survival*: APEX-style intraday trailing trails off account **equity incl. open
PnL**, so a violation can fire mid-trade at the worst unrealized point, not just
on the day's realized close. That needs per-*trade* granularity WITH each trade's
adverse/favorable excursion.

NinjaTrader's Trades.csv carries exactly that: ``MAE`` (max adverse excursion, the
worst unrealized loss the trade saw, $ magnitude) and ``MFE`` (max favorable
excursion). This module reads those per template so the survival simulator can
reconstruct a conservative intra-trade equity path. All dollar figures are for the
backtest qty (1 contract) — the simulator scales by contracts / instrument
multiplier.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

_MONEY_RE = re.compile(r"[(),$\s]")


def _money(x: str) -> float:
    """Parse an NT money cell (``$1,910.00`` / ``($25.00)`` parenthesised-negative)."""
    s = (x or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = _MONEY_RE.sub("", s)
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def _dt(x: str) -> Optional[datetime]:
    s = (x or "").strip()
    if not s:
        return None
    d = pd.to_datetime(s, format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
    if pd.isna(d):
        d = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(d) else d.to_pydatetime()


@dataclass(frozen=True)
class Trade:
    """One realised backtest trade for ONE contract (the backtest qty).

    ``mae``/``mfe`` are POSITIVE $ magnitudes (NT convention): during the trade the
    unrealized P&L ranged from ``-mae`` to ``+mfe`` before settling at ``profit``.
    """

    template_id: str
    slice_key: str
    entry_dt: datetime
    exit_dt: datetime
    profit: float
    mae: float
    mfe: float

    @property
    def day(self):
        return self.entry_dt.date()


def parse_trades_csv(trades_csv: Path, *, template_id: str, slice_key: str = "all") -> list[Trade]:
    """Read one NT Trades.csv into ``Trade`` rows (per 1 contract). Skips rows
    without a parseable entry time. ``MAE``/``MFE`` default to 0 if absent so the
    loader degrades gracefully on older exports (survival just loses its intra-trade
    excursion guard for those, becoming optimistic — flagged by the caller)."""
    if not trades_csv.exists():
        return []
    with trades_csv.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out: list[Trade] = []
    for r in rows:
        edt = _dt(r.get("Entry time"))
        xdt = _dt(r.get("Exit time"))
        if edt is None:
            continue
        out.append(Trade(
            template_id=template_id,
            slice_key=slice_key,
            entry_dt=edt,
            exit_dt=xdt or edt,
            profit=_money(r.get("Profit", "")),
            mae=abs(_money(r.get("MAE", ""))),
            mfe=abs(_money(r.get("MFE", ""))),
        ))
    return out


def load_template_trades(
    session_dir: str | Path, *, passed_only: bool = False
) -> dict[str, list[Trade]]:
    """``{template_id: [Trade, ...]}`` for every final-backtest template in a
    finished deployment session (mirrors ``selection/loader.load_candidates_from_session``
    but keeps per-trade rows). ``passed_only`` keeps only validation-passers."""
    sdir = Path(session_dir)
    review = sdir / "deployment_package" / "final_backtest_handoff" / "final_backtest_review"
    results_root = sdir / "deployment_package" / "final_backtest_handoff" / "nt8_backtest_results"
    cand_csv = review / "evaluated_candidates.csv"
    if not cand_csv.exists():
        raise FileNotFoundError(f"no evaluated_candidates.csv at {cand_csv}")

    out: dict[str, list[Trade]] = {}
    with cand_csv.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if passed_only and "pass" not in (r.get("status") or "").lower():
                continue
            rid = (r.get("run_id") or "").strip()
            if not rid:
                continue
            slice_key = (r.get("session_bucket") or r.get("start_hour") or "all").strip() or "all"
            trades = parse_trades_csv(
                results_root / rid / "Trades.csv", template_id=rid, slice_key=slice_key
            )
            if trades:
                out[rid] = trades
    return out
