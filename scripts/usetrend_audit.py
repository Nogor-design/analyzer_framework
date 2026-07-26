"""Audit UseTrend / UseTrendReverse / Reverse + MA periods across the 124 final
templates, and cross with each template's holdout window P&L (ungated 1 NQ) so we
can see whether the trend-OFF templates are the bleeders.
"""
from __future__ import annotations

import re
import statistics
from pathlib import Path

from ta_foundation.analysis.risk.account_state import load_firm_profile
from ta_foundation.analysis.risk.survival import simulate_survival
from ta_foundation.analysis.risk.trade_loader import parse_trades_csv

SESSION = "opt_a09359e6b60b"
DP = Path(".ta_artifacts/web_optimizer/sessions") / SESSION / "deployment_package"
TDIR = DP / "final_backtest_handoff" / "named_backtest_templates" / "recipe"
HOLD = DP / "holdout_2026-03-12_2026-04-30_trades"

def xget(text, tag):
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return m.group(1) if m else ""

profile = load_firm_profile("APEX")

rows = []
for p in sorted(TDIR.glob("F_*.xml")):
    rid = re.match(r"(F_\d+)", p.stem).group(1)
    t = p.read_text(encoding="utf-8")
    rec = {
        "rid": rid,
        "UseTrend": xget(t, "UseTrend"),
        "UseTrendReverse": xget(t, "UseTrendReverse"),
        "Reverse": xget(t, "Reverse"),
        "Fast": xget(t, "FastPeriod"),
        "Slow": xget(t, "SlowPeriod"),
        "Trend": xget(t, "TrendPeriod"),
    }
    # holdout ungated window P&L (1 NQ) for this template, if trades exist
    hf = HOLD / f"{p.stem}.csv"
    if hf.exists():
        trades = parse_trades_csv(hf, template_id=rid)
        if trades:
            r = simulate_survival(trades, profile=profile, account_type="evaluation",
                                  account_size="50000", starting_balance=50000.0,
                                  contracts=1, dollar_scale=1.0,
                                  halt_on_violation=False, stop_at_target=False)
            rec["pnl"] = r.final_equity - 50000.0
            rec["n"] = r.n_trades
    rows.append(rec)

# ---- 1. distribution of the flags --------------------------------------------
def tally(key):
    from collections import Counter
    return dict(Counter(r[key] for r in rows))

print(f"=== UseTrend AUDIT — {len(rows)} templates ===")
for k in ("UseTrend", "UseTrendReverse", "Reverse"):
    print(f"  {k:<16}: {tally(k)}")
print(f"  TrendPeriod values: {sorted(set(r['Trend'] for r in rows))}")
print(f"  (Fast,Slow) pairs : {sorted(set((r['Fast'], r['Slow']) for r in rows))}")

# Reverse vs UseTrendReverse interaction (the bug the user flagged)
mismatch = [r for r in rows if r["Reverse"] == "true" and r["UseTrend"] == "true"
            and r["UseTrendReverse"] != "true"]
print(f"\n  !! Reverse=true & UseTrend=true but UseTrendReverse!=true: {len(mismatch)}"
      f"  (trend filter would fight the reversed trade)")

# ---- 2. does trend-ON carry the edge? ----------------------------------------
withp = [r for r in rows if "pnl" in r]
on = [r for r in withp if r["UseTrend"] == "true"]
off = [r for r in withp if r["UseTrend"] != "true"]
def summ(label, g):
    if not g:
        print(f"  {label:<22}: (none)"); return
    pnls = [r["pnl"] for r in g]
    neg = sum(1 for x in pnls if x < 0)
    print(f"  {label:<22}: n={len(g):3d}  median ${statistics.median(pnls):>7,.0f}  "
          f"mean ${statistics.mean(pnls):>7,.0f}  lose {100*neg/len(g):>3.0f}%")
print(f"\n=== EDGE by UseTrend flag (holdout ungated 1 NQ window P&L) ===")
summ("UseTrend=ON", on)
summ("UseTrend=OFF", off)
