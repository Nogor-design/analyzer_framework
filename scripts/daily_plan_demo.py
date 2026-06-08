"""End-to-end Phase-1×Phase-2 demo: selector lineup -> per-account daily plan.

Proves the cross-module integration the unit tests don't: load a real session's
candidates, pick a lineup, estimate each pick's per-contract risk from its realised
daily P&L, run the DD engine for an APEX account, and let the allocator size the
lineup differently for an EVALUATION vs a PA account on the SAME budget.

This is a demonstration, not a live plan: contract-risk is estimated from backtest
daily P&L (assumed ~1 contract; real sizing needs the actual backtest qty), and the
account state here is a illustrative snapshot. The real-account replay gate still
governs anything that touches money.

Usage: python scripts/daily_plan_demo.py [session] [for_day YYYY-MM-DD]
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from ta_foundation.analysis.risk import (
    DdEngine,
    allocate_account,
    estimate_per_contract_risk,
    load_firm_profile,
)
from ta_foundation.analysis.selection.loader import load_candidates_from_session
from ta_foundation.analysis.selection.model import SelectionContext, window_pnls
from ta_foundation.analysis.selection.scoring import composite_selector

SESSION = sys.argv[1] if len(sys.argv) > 1 else "opt_a09359e6b60b"
_SROOT = Path(".ta_artifacts/web_optimizer/sessions")


def main() -> int:
    sdir = Path(SESSION) if Path(SESSION).exists() else _SROOT / SESSION
    cands, regime_by_day = load_candidates_from_session(sdir)
    if not cands:
        print("no candidates; check session path")
        return 1
    cal = sorted({d for c in cands for d in c.daily_pnl})
    for_day = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else cal[-1] + timedelta(days=1)
    train = [d for d in cal if d < for_day]

    # 1) selector lineup (one composite pick per slice, leakage-free)
    by_slice: dict[str, list] = {}
    for c in cands:
        by_slice.setdefault(c.slice_key, []).append(c)
    by_id = {c.template_id: c for c in cands}
    picks: list[tuple[str, str]] = []
    for sk, cs in by_slice.items():
        ctx = SelectionContext(train_days=train, test_day=for_day,
                               regime_for_test_day=regime_by_day.get(for_day),
                               regime_by_day=regime_by_day)
        for c in composite_selector(cs, ctx):
            picks.append((sk, c.template_id))

    # 2) per-pick risk + expected return from train-window daily P&L (per backtest qty)
    per_contract_risk, expected_return = {}, {}
    for _sk, tid in picks:
        pnls = window_pnls(by_id[tid], train)
        per_contract_risk[tid] = estimate_per_contract_risk(pnls)
        expected_return[tid] = (sum(pnls) / len(pnls)) if pnls else 0.0

    # 3) DD engine: APEX 50k, account sitting at starting balance (cushion = full max_dd)
    eng = DdEngine(load_firm_profile("apex"))
    print(f"=== Daily plan demo: {sdir.name}  for {for_day}  ({len(picks)} picks) ===")
    for acct_type in ("evaluation", "PA"):
        st = eng.init_account(starting_balance=50000, account_type=acct_type, account_size=50000)
        readout = eng.on_value(st, 50000)            # flat day start: cushion = 2500
        rules = eng.profile.size_rules("50000")
        plan = allocate_account(
            picks, readout=readout, account_type=acct_type, account_size="50000",
            per_contract_risk=per_contract_risk, expected_return=expected_return,
            max_contracts=rules.max_contracts)
        print(f"\n  [{acct_type}]  budget=${readout.daily_risk_budget:,.0f}  "
              f"cap=${plan.daily_loss_cap:,.0f}  contracts={plan.total_contracts}  "
              f"est_risk=${plan.total_est_risk:,.0f}")
        for a in plan.allocations:
            print(f"      [{a.slice_key:<12}] {a.template_id}  x{a.contracts}  "
                  f"(risk/ct ${per_contract_risk[a.template_id]:,.0f})")
        if plan.skipped:
            print(f"      skipped (no room): {', '.join(plan.skipped)}")
    print("\n  eval presses expectancy with more budget; PA fills safest-first with less. "
          "Demo only -- real sizing needs backtest qty + the real-account replay gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
