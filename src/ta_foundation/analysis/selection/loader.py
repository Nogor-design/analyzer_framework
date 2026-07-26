"""Build ``Candidate`` objects from a completed deployment-matrix session.

Bootstraps the replay harness on data we ALREADY produce: each final-backtest
template (F_xxx) carries realised per-trade P&L (Trades.csv) over the OOS window
+ its cell descriptors (evaluated_candidates.csv). No live outcome ledger is
needed to start scoring selectors; the live ledger comes once the strategy runs
for real. This is the only selector-spine module that touches NT files.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from .model import Candidate

_MONEY_RE = re.compile(r"[(),$\s]")


def _money(x: str) -> float:
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


def _daily_pnl_from_trades(trades_csv: Path) -> dict[date, float]:
    if not trades_csv.exists():
        return {}
    with trades_csv.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_day: dict[date, float] = defaultdict(float)
    for r in rows:
        et = (r.get("Entry time") or "").strip()
        if not et:
            continue
        dt = pd.to_datetime(et, format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
        if pd.isna(dt):
            continue
        by_day[dt.date()] += _money(r.get("Profit", ""))
    return dict(by_day)


def _regime_by_day(bars_file: Path) -> dict[date, str]:
    """Modal bar-regime per calendar day, via the shared regime labeler."""
    from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
    from ta_foundation.analysis.strategy_discovery.regime import compute_bar_regime

    art = MinuteBarsLastTxtParser().parse(bars_file, run_id=None)
    bars = art.df.copy()
    bars["dt"] = pd.to_datetime(bars["dt"]).dt.tz_localize(None)
    bars = bars.sort_values("dt").reset_index(drop=True)
    labelled = compute_bar_regime(bars)
    out: dict[date, str] = {}
    for d, grp in labelled.groupby(pd.to_datetime(labelled["dt"]).dt.date):
        out[d] = Counter(grp["regime"]).most_common(1)[0][0]
    return out


def load_candidates_from_session(
    session_dir: str | Path,
    *,
    bars_file: Optional[str | Path] = None,
    passed_only: bool = False,
) -> tuple[list[Candidate], dict[date, str]]:
    """Return (candidates, regime_by_day) for a finished deployment session.

    ``slice_key`` is the session bucket (the lineup time-slice the template
    competes in). ``passed_only`` keeps only templates that cleared validation.
    ``bars_file`` (e.g. the fresh ``NQ 06-26.Export.txt``) enables the
    regime-matched baseline; omit it and that baseline falls back to top_pf.
    """
    sdir = Path(session_dir)
    review = sdir / "deployment_package" / "final_backtest_handoff" / "final_backtest_review"
    results_root = sdir / "deployment_package" / "final_backtest_handoff" / "nt8_backtest_results"
    rows = list(csv.DictReader((review / "evaluated_candidates.csv").open(encoding="utf-8-sig")))

    candidates: list[Candidate] = []
    for r in rows:
        if passed_only and "pass" not in (r.get("status") or "").lower():
            continue
        rid = (r.get("run_id") or "").strip()
        if not rid:
            continue
        daily = _daily_pnl_from_trades(results_root / rid / "Trades.csv")
        if not daily:
            continue
        slice_key = (r.get("session_bucket") or r.get("start_hour") or "all").strip() or "all"
        candidates.append(Candidate(
            template_id=rid,
            slice_key=slice_key,
            daily_pnl=daily,
            metrics={
                "profit_factor": r.get("profit_factor"),
                "total_net_profit": r.get("total_net_profit"),
                "trades": r.get("trades"),
                "status": r.get("status"),
            },
        ))

    regime_by_day = _regime_by_day(Path(bars_file)) if bars_file else {}
    return candidates, regime_by_day


def survivable_template_ids(
    session_dir: str | Path,
    *,
    firm: str = "APEX",
    account_size: str = "50000",
    starting_balance: Optional[float] = None,
    contracts: int = 1,
    dollar_scale: float = 1.0,
    require_pass: bool = False,
) -> set[str]:
    """Template ids whose own trades keep a fresh prop account ALIVE under trailing
    drawdown (the survival gate the candidate universe should clear before the
    selector ranks it — see ``analysis/risk/survival.py``). ``require_pass`` keeps
    only templates that also clear the profit target alive. Local import keeps the
    selection spine free of a load-time dependency on the risk layer."""
    from ta_foundation.analysis.risk.account_state import load_firm_profile
    from ta_foundation.analysis.risk.survival import per_template_survival
    from ta_foundation.analysis.risk.trade_loader import load_template_trades

    profile = load_firm_profile(firm)
    start = starting_balance if starting_balance is not None else float(account_size)
    sweep = per_template_survival(
        load_template_trades(session_dir), profile=profile, account_type="evaluation",
        account_size=account_size, starting_balance=start,
        contracts=contracts, dollar_scale=dollar_scale,
    )
    return {
        tid for tid, r in sweep.results.items()
        if r.survived and (r.passed or not require_pass)
    }
