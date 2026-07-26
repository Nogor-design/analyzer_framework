"""Does a TREND filter rescue the OOS crater? Split each template's holdout trades
into with-trend vs counter-trend (by NQ minute-bar trend at entry) and re-run the
ungated window-P&L decomposition + survival/pass on the with-trend subset only.

Trend = sign(close - SMA(N)) on the NQ 06-26 minute series. A trade is 'with-trend'
if a Long fires in an up-trend or a Short in a down-trend. Bars are UTC in the file;
trades are NT-local (America/Denver). The holdout window (2026-03-12..04-30) is
entirely in MDT, so Denver = UTC - 6h.
"""
from __future__ import annotations

import csv
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ta_foundation.analysis.risk.account_state import load_firm_profile
from ta_foundation.analysis.risk.survival import simulate_survival
from ta_foundation.analysis.risk.trade_loader import Trade

SESSION = "opt_a09359e6b60b"
WINDOW = "holdout_2026-03-12_2026-04-30_trades"
BARS = Path("D:/MarketData/NQ 06-26.Last.txt")

# ---- minute closes from NQ bars (UTC -> Denver naive) ---------------------------
rows = []
with BARS.open() as fh:
    for line in fh:
        parts = line.strip().split(";")
        if len(parts) < 6:
            continue
        ts = datetime.strptime(parts[0], "%Y%m%d %H%M%S") - timedelta(hours=6)  # UTC->MDT
        rows.append((ts, float(parts[4])))  # close
bars = pd.DataFrame(rows, columns=["ts", "close"]).set_index("ts").sort_index()

_trend_cache = {}
def trend_series(n: int):
    if n not in _trend_cache:
        sma = bars["close"].rolling(n).mean()
        s = ((bars["close"] > sma).astype(int) - (bars["close"] < sma).astype(int)).dropna()
        _trend_cache[n] = s
    return _trend_cache[n]

def make_trend_at(n: int):
    s = trend_series(n)
    def trend_at(dt: datetime) -> int:
        idx = s.index.searchsorted(pd.Timestamp(dt), side="right") - 1
        return int(s.iloc[idx]) if idx >= 0 else 0
    return trend_at

# ---- load holdout trades WITH direction ----------------------------------------
def _money(x: str) -> float:
    s = (x or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v

def _dt(x: str):
    d = pd.to_datetime((x or "").strip(), format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
    return None if pd.isna(d) else d.to_pydatetime()

root = Path(".ta_artifacts/web_optimizer/sessions") / SESSION / "deployment_package" / WINDOW
all_trades: dict[str, list[tuple[Trade, str]]] = {}  # tid -> [(Trade, side)]
for p in sorted(root.glob("F_*.csv")):
    out = []
    for r in csv.DictReader(p.open(encoding="utf-8-sig")):
        edt = _dt(r.get("Entry time"))
        if edt is None:
            continue
        out.append((
            Trade(template_id=p.stem, slice_key="all", entry_dt=edt,
                  exit_dt=_dt(r.get("Exit time")) or edt, profit=_money(r.get("Profit", "")),
                  mae=abs(_money(r.get("MAE", ""))), mfe=abs(_money(r.get("MFE", "")))),
            (r.get("Market pos.") or "").strip().lower(),
        ))
    if out:
        all_trades[p.stem] = out

profile = load_firm_profile("APEX")
SIZE, START, TARGET = "50000", 50000.0, 3000.0

def decompose(label: str, picker):
    pnls, kept = [], []
    for tid, rows in all_trades.items():
        trades = [t for (t, side) in rows if picker(t, side)]
        if not trades:
            continue
        r = simulate_survival(trades, profile=profile, account_type="evaluation",
                              account_size=SIZE, starting_balance=START, contracts=1,
                              dollar_scale=1.0, halt_on_violation=False, stop_at_target=False)
        pnls.append(r.final_equity - START)
        kept.append((tid, trades))
    if not pnls:
        print(f"{label}: no trades"); return
    neg = sum(1 for x in pnls if x < 0)
    hit = sum(1 for x in pnls if x >= TARGET)
    n = len(pnls)
    # gated survival/pass at 1 NQ on the filtered subset
    surv = pas = 0
    for tid, trades in kept:
        g = simulate_survival(trades, profile=profile, account_type="evaluation",
                              account_size=SIZE, starting_balance=START, contracts=1, dollar_scale=1.0)
        surv += g.survived
        pas += (g.passed and g.survived)
    print(f"{label}  ({n} templates with >=1 such trade)")
    print(f"  ungated median ${statistics.median(pnls):>7,.0f}   mean ${statistics.mean(pnls):>7,.0f}   "
          f"lose {100*neg/n:>3.0f}%   >=target {100*hit/n:>3.0f}%")
    print(f"  gated @1NQ: survive {surv}/{n}   pass {pas}/{n}")

# Faithful preview of UseTrend=ON: filter actual-direction trades by Close-vs-trendSMA.
# Sweep the trend SMA period (TrendPeriod is pinned 300 in all 124 — never optimized).
print(f"=== UseTrend=ON PREVIEW (actual trade direction vs Close-over-trendSMA) — {WINDOW} ===")
decompose("ALL (UseTrend OFF, as deployed)", lambda t, s: True)
for n in (100, 200, 300, 400):
    ta = make_trend_at(n)
    def wt(t, s, ta=ta):
        sg = ta(t.entry_dt)
        return (s.startswith("long") and sg > 0) or (s.startswith("short") and sg < 0)
    print(f"-- trend SMA period = {n} minutes --")
    decompose(f"  WITH-TREND (TrendPer~{n})", wt)
    decompose(f"  AGAINST-TREND (TrendPer~{n})", lambda t, s, wt=wt: not wt(t, s))
