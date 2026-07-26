"""Diagnostic: WHY does the OOS pass rate crater, and what happens at MNQ sizes?

Loads the saved holdout (fresh-OOS) trades for a session and answers two questions:

1. CRATER DECOMPOSITION — is the 12/124 OOS pass rate a *no-edge* problem
   (templates lose money OOS) or a *too-slow / too-small* problem (templates make
   money but can't reach the fixed $3,000 target in the short window)? We measure
   each template's raw window P&L with DD-halt and target-stop BOTH OFF, so the
   pure edge is visible, then bucket: negative / positive-but-sub-target / >=target.

2. SIZE FRONTIER — the group trades 1-5 MNQ, not 1 NQ. MNQ is 1/10 the $ value, so
   dollar_scale=0.1 per MNQ contract. The DD ($2,500) and target ($3,000) are FIXED
   dollars, so size scales the equity path but not the rails. Sweep size and show the
   survival/pass tension.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

from ta_foundation.analysis.risk.account_state import load_firm_profile
from ta_foundation.analysis.risk.survival import simulate_survival
from ta_foundation.analysis.risk.trade_loader import parse_trades_csv

SESSION = sys.argv[1] if len(sys.argv) > 1 else "opt_a09359e6b60b"
WINDOW = sys.argv[2] if len(sys.argv) > 2 else "holdout_2026-03-12_2026-04-30_trades"

root = Path(".ta_artifacts/web_optimizer/sessions") / SESSION / "deployment_package" / WINDOW
files = sorted(root.glob("F_*.csv"))
if not files:
    print(f"no trade files under {root}", file=sys.stderr)
    raise SystemExit(2)

template_trades = {p.stem: parse_trades_csv(p, template_id=p.stem) for p in files}
template_trades = {k: v for k, v in template_trades.items() if v}

profile = load_firm_profile("APEX")
SIZE = "50000"
START = 50000.0
TARGET = 3000.0

# ---- 1. crater decomposition: raw window P&L per template (no gating) -----------
print(f"=== CRATER DECOMPOSITION — {len(template_trades)} templates, window {WINDOW} ===")
raw_totals = []      # ungated total $ over window at 1 NQ
n_trades = []
for tid, trades in template_trades.items():
    r = simulate_survival(
        trades, profile=profile, account_type="evaluation", account_size=SIZE,
        starting_balance=START, contracts=1, dollar_scale=1.0,
        halt_on_violation=False, stop_at_target=False,
    )
    raw_totals.append((tid, r.final_equity - START, r.n_trades, r.max_equity_drawdown))
    n_trades.append(r.n_trades)

neg = [x for x in raw_totals if x[1] < 0]
sub = [x for x in raw_totals if 0 <= x[1] < TARGET]
hit = [x for x in raw_totals if x[1] >= TARGET]
tot = len(raw_totals)
pnls = [x[1] for x in raw_totals]
print(f"ungated window P&L per template (1 NQ, no DD-halt, no target-stop):")
print(f"  median ${statistics.median(pnls):,.0f}   mean ${statistics.mean(pnls):,.0f}   "
      f"min ${min(pnls):,.0f}   max ${max(pnls):,.0f}")
print(f"  LOSE money (<$0)          : {len(neg):3d}/{tot}  ({100*len(neg)/tot:.0f}%)")
print(f"  make $ but < $3k target   : {len(sub):3d}/{tot}  ({100*len(sub)/tot:.0f}%)  <-- 'too slow/small'")
print(f"  reach >= $3k target       : {len(hit):3d}/{tot}  ({100*len(hit)/tot:.0f}%)")
print(f"  median trades/template over window: {statistics.median(n_trades):.0f}  "
      f"(range {min(n_trades)}..{max(n_trades)})")

# ---- 2. size frontier: NQ vs MNQ 1/2/3/5/10 ------------------------------------
# (dollar_scale, contracts, label).  MNQ = 0.1x; 10 MNQ == 1 NQ in $ exposure.
GRID = [
    (1.0, 1, "1 NQ        (= 10 MNQ)"),
    (0.1, 1, "1 MNQ       (0.1 NQ)"),
    (0.1, 2, "2 MNQ       (0.2 NQ)"),
    (0.1, 3, "3 MNQ       (0.3 NQ)"),
    (0.1, 5, "5 MNQ       (0.5 NQ)"),
    (0.1, 10, "10 MNQ      (= 1 NQ)"),
]
print(f"\n=== SIZE FRONTIER — APEX 50k eval, fixed $2,500 DD / $3,000 target ===")
print(f"{'size':<24} {'survive':>9} {'pass':>8} {'median final$':>14} {'median maxDD$':>14}")
for scale, qty, label in GRID:
    results = [
        simulate_survival(
            trades, profile=profile, account_type="evaluation", account_size=SIZE,
            starting_balance=START, contracts=qty, dollar_scale=scale,
        )
        for trades in template_trades.values()
    ]
    surv = sum(1 for r in results if r.survived)
    pas = sum(1 for r in results if r.passed and r.survived)
    medfin = statistics.median([r.final_equity - START for r in results])
    meddd = statistics.median([r.max_equity_drawdown for r in results])
    print(f"{label:<24} {surv:>4}/{tot:<4} {pas:>4}/{tot:<3} "
          f"{medfin:>13,.0f} {meddd:>13,.0f}")

print("\nNote: pass needs +$3,000 REALIZED; survive = never touched the $2,500 trail.")
print("Smaller size -> higher survival, lower pass (target is fixed $). The two are in")
print("tension: challenge accounts need size to pass; funded accounts need small to protect.")
