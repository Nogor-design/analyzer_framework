"""Account-survival report — does an APEX account survive trading our backtest
lineups under trailing drawdown? (Phase 2 validation, the ``❌`` profit-chain link.)

Runs the survival simulator over a finished deployment session:
  1. Per-template survival (each template alone, fresh account) at several
     instrument scales (NQ vs MNQ) and contract sizes — which templates are even
     APEX-survivable, and at what scale.
  2. The production daily lineup (equal_weight, the current production selector)
     merged chronologically through ONE account.

Usage:
  python scripts/account_survival_report.py [SESSION_ID] [--firm APEX] \
      [--size 50000] [--start 50000] [--passed-only]
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from ta_foundation.analysis.risk.account_state import load_firm_profile
from ta_foundation.analysis.risk.survival import (
    lineup_survival,
    per_template_survival,
)
from ta_foundation.analysis.risk.trade_loader import load_template_trades

_SESS_ROOT = Path(".ta_artifacts/web_optimizer/sessions")
# (label, dollar_scale): NQ = full ($20/pt, as backtested); MNQ = micro ($2/pt).
_SCALES = [("NQ", 1.0), ("MNQ", 0.1)]


def _resolve_session(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        return p if p.exists() else _SESS_ROOT / arg
    # default: the decisive PantheonMaster AtrTrail session
    return _SESS_ROOT / "opt_a09359e6b60b"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", default=None)
    ap.add_argument("--firm", default="APEX")
    ap.add_argument("--size", default="50000")
    ap.add_argument("--start", type=float, default=None,
                    help="starting balance (default = account size)")
    ap.add_argument("--passed-only", action="store_true",
                    help="only validation-passing templates")
    args = ap.parse_args()

    sdir = _resolve_session(args.session)
    if not sdir.exists():
        print(f"no session at {sdir}", file=sys.stderr)
        return 2
    profile = load_firm_profile(args.firm)
    rules = profile.size_rules(args.size)
    start = args.start if args.start is not None else float(args.size)

    print(f"=== Account-survival report ===")
    print(f"session   : {sdir.name}")
    print(f"firm/size : {profile.firm} {profile.version}  {args.size}  "
          f"(trail ${rules.max_drawdown:.0f}, target ${rules.profit_target:.0f}, "
          f"max {rules.max_contracts} contracts)")
    print(f"start bal : {start:.0f}   drawdown_type={profile.drawdown_type}\n")

    template_trades = load_template_trades(sdir, passed_only=args.passed_only)
    if not template_trades:
        print("no template trades found", file=sys.stderr)
        return 2
    n_templ = len(template_trades)
    total_trades = sum(len(v) for v in template_trades.values())
    print(f"templates : {n_templ}   total trades: {total_trades}"
          f"{'  (passed-only)' if args.passed_only else ''}\n")

    # --- 1. per-template survival across scales / contract sizes -------------
    print("--- Per-template survival (each template alone, fresh account) ---")
    print("  survived = never hit the trail (incl. evals that passed & stopped); "
          "passed = cleared $%.0f target alive" % rules.profit_target)
    print(f"{'scale':<6}{'contracts':>10}{'survived':>12}{'passed':>12}"
          f"{'median maxDD':>14}{'worst maxDD':>13}")
    for label, scale in _SCALES:
        for contracts in (1, 2):
            sweep = per_template_survival(
                template_trades, profile=profile, account_type="evaluation",
                account_size=args.size, starting_balance=start,
                contracts=contracts, dollar_scale=scale,
            )
            dds = [r.max_equity_drawdown for r in sweep.results.values()]
            med_dd = statistics.median(dds) if dds else 0.0
            worst_dd = min(dds) if dds else 0.0
            print(f"{label:<6}{contracts:>10}"
                  f"{sweep.n_survived:>6}/{sweep.n_templates:<5}"
                  f"{sweep.n_passed:>8}/{sweep.n_templates:<3}"
                  f"{med_dd:>14.0f}{worst_dd:>13.0f}")
    print()

    # --- 2. production lineup (equal_weight) through ONE account -------------
    # equal_weight = trade the whole pool; merge every template's trades.
    all_trades = [t for trades in template_trades.values() for t in trades]
    print("--- Production lineup (equal_weight = whole pool) through ONE account ---")
    print(f"{'scale':<6}{'contracts':>10}{'survived':>10}{'first breach':>22}"
          f"{'peak equity':>13}{'final':>11}")
    for label, scale in _SCALES:
        for contracts in (1, 2):
            r = lineup_survival(
                all_trades, profile=profile, account_type="evaluation",
                account_size=args.size, starting_balance=start,
                contracts=contracts, dollar_scale=scale,
            )
            breach = r.violated_dt.strftime("%Y-%m-%d %H:%M") if r.violated_dt else "-"
            print(f"{label:<6}{contracts:>10}{('YES' if r.survived else 'NO'):>10}"
                  f"{breach:>22}{r.peak_equity:>13.0f}{r.final_equity:>11.0f}")
    print()

    # --- 3. APEX-safe shortlist artifact (NQ 1-contract = the decision scale) ---
    decision = per_template_survival(
        template_trades, profile=profile, account_type="evaluation",
        account_size=args.size, starting_balance=start, contracts=1, dollar_scale=1.0,
    )
    out_csv = sdir / "deployment_package" / f"apex_{args.size}_survival_shortlist.csv"
    import csv as _csv
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["template_id", "slice_key", "survived", "passed",
                    "max_equity_drawdown", "peak_equity", "final_equity",
                    "n_trades", "violated_dt"])
        for tid, r in sorted(decision.results.items(),
                             key=lambda kv: (not (kv[1].survived and kv[1].passed),
                                             kv[1].max_equity_drawdown)):
            sk = template_trades[tid][0].slice_key if template_trades.get(tid) else ""
            w.writerow([tid, sk, r.survived, r.passed,
                        f"{r.max_equity_drawdown:.0f}", f"{r.peak_equity:.0f}",
                        f"{r.final_equity:.0f}", r.n_trades,
                        r.violated_dt.isoformat() if r.violated_dt else ""])
    n_safe = decision.n_passed
    print(f"APEX-safe shortlist (NQ 1-contract, survive AND clear target alive): "
          f"{n_safe}/{decision.n_templates}  ->  {out_csv}\n")

    print("Note: equal_weight trades the ENTIRE pool simultaneously — far more size than "
          "an APEX 50k can fund; this row shows why the allocator (budget-bounded contract "
          "sizing) is mandatory, not a 'survival recipe'. Per-template rows above are the "
          "honest single-edge survivability read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
