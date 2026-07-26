"""Offline certification + characterization of the OOS holdout survivors.

The holdout re-backtest (holdout_rebacktest.py) saved 124 fresh Trades.csv over an
INDEPENDENT window (2026-03-12..04-30) but only PRINTED the survive/pass counts.
This script reproduces the per-template APEX survival from the SAVED trades — no NT,
no re-run — and answers the questions the print did not:

  1. Window guard: did the realized trades actually fall in the requested window?
     (bare RunBatch can ignore <From>/<To>; <80% in-window => not a clean OOS test)
  2. Which templates PASS out-of-sample, and what config DNA do they share?
  3. Overfit turnover: of the in-window passers, how many still pass OOS?

Outputs a per-template OOS CSV + a markdown findings file next to the trades.

Usage:
  python scripts/holdout_oos_shortlist.py [SESSION] \
     [--from-date 2026-03-12 --to-date 2026-04-30] [--firm APEX --size 50000]
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import re
from collections import Counter
from datetime import date
from pathlib import Path

from ta_foundation.analysis.risk.account_state import load_firm_profile
from ta_foundation.analysis.risk.survival import per_template_survival
from ta_foundation.analysis.risk.trade_loader import parse_trades_csv

_SROOT = Path(".ta_artifacts/web_optimizer/sessions")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", default="opt_a09359e6b60b")
    ap.add_argument("--from-date", default="2026-03-12")
    ap.add_argument("--to-date", default="2026-04-30")
    ap.add_argument("--firm", default="APEX")
    ap.add_argument("--size", default="50000")
    args = ap.parse_args()

    sdir = Path(args.session) if Path(args.session).exists() else _SROOT / args.session
    dp = sdir / "deployment_package"
    tdir = dp / f"holdout_{args.from_date}_{args.to_date}_trades"
    if not tdir.is_dir():
        print(f"no holdout trades dir: {tdir}")
        return 2

    # rid -> StartTimeH (from recipe template filename F_001_StartTimeH_08.xml)
    rdir = dp / "final_backtest_handoff" / "named_backtest_templates" / "recipe"
    start_h = {}
    use_trend = {}
    for p in rdir.glob("*.xml"):
        m = re.match(r"(F_\d+)", p.stem)
        if not m:
            continue
        rid = m.group(1)
        mh = re.search(r"StartTimeH_(\d+)", p.stem)
        if mh:
            start_h[rid] = int(mh.group(1))
        mt = re.search(r"<UseTrend>(true|false)</", p.read_text(encoding="utf-8", errors="ignore"))
        if mt:
            use_trend[rid] = mt.group(1) == "true"

    # rid -> session bucket
    slice_of = {}
    cand = dp / "final_backtest_handoff" / "final_backtest_review" / "evaluated_candidates.csv"
    if cand.exists():
        for r in csv.DictReader(cand.open(encoding="utf-8-sig")):
            slice_of[(r.get("run_id") or "").strip()] = (r.get("session_bucket") or "all").strip() or "all"

    # in-window baseline result per template
    inwin = {}
    iw_csv = dp / f"apex_{args.size}_survival_shortlist.csv"
    if iw_csv.exists():
        for r in csv.DictReader(iw_csv.open(encoding="utf-8-sig")):
            inwin[(r.get("template_id") or "").strip()] = r

    # Load saved OOS trades.
    template_trades = {}
    for p in sorted(tdir.glob("F_*.csv")):
        rid = p.stem
        trades = parse_trades_csv(p, template_id=rid, slice_key=slice_of.get(rid, "all"))
        if trades:
            template_trades[rid] = trades
    if not template_trades:
        print("no trades parsed")
        return 1

    # --- Window guard --------------------------------------------------------
    fy, fm, fd = map(int, args.from_date.split("-"))
    ty, tm, td = map(int, args.to_date.split("-"))
    lo, hi = date(fy, fm, fd), date(ty, tm, td)
    days = [t.entry_dt.date() for ts in template_trades.values() for t in ts]
    in_win = sum(1 for d in days if lo <= d <= hi)
    frac = in_win / len(days) if days else 0.0
    print(f"session     : {sdir.name}")
    print(f"templates   : {len(template_trades)}  trades: {len(days)}")
    print(f"window guard: {frac*100:.0f}% of trades in {args.from_date}..{args.to_date} "
          f"(realized {min(days)}..{max(days)})")
    certified = frac >= 0.80
    print(f"OOS certified: {'YES' if certified else 'NO -- NT used a different (sticky) window'}")

    # --- Survival ------------------------------------------------------------
    profile = load_firm_profile(args.firm)
    sweep = per_template_survival(
        template_trades, profile=profile, account_type="evaluation",
        account_size=args.size, starting_balance=float(args.size),
        contracts=1, dollar_scale=1.0,
    )
    print(f"\n=== OOS survival ({args.firm} {args.size}, 1-contract) ===")
    print(f"survived {sweep.n_survived}/{sweep.n_templates}   passed {sweep.n_passed}/{sweep.n_templates}")

    res_fields = [f.name for f in dataclasses.fields(next(iter(sweep.results.values())))]

    rows = []
    for rid, r in sweep.results.items():
        net = sum(t.profit for t in template_trades[rid])
        iw = inwin.get(rid, {})
        rows.append({
            "template_id": rid,
            "session_bucket": slice_of.get(rid, "all"),
            "start_h": start_h.get(rid, ""),
            "use_trend": use_trend.get(rid, ""),
            "oos_survived": r.survived,
            "oos_passed": r.passed,
            "oos_net_profit": round(net, 2),
            "oos_n_trades": len(template_trades[rid]),
            "oos_min_cushion": round(getattr(r, "min_cushion", float("nan")), 2),
            "inwin_passed": (iw.get("passed", "") == "True"),
            "inwin_final_equity": iw.get("final_equity", ""),
            "inwin_n_trades": iw.get("n_trades", ""),
        })
    rows.sort(key=lambda x: (not x["oos_passed"], -x["oos_net_profit"]))

    out_csv = dp / f"holdout_{args.from_date}_{args.to_date}_oos_survival.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"per-template OOS table -> {out_csv}")

    # --- Characterize passers + overfit turnover -----------------------------
    passers = [r for r in rows if r["oos_passed"]]
    iw_pass_ids = {k for k, v in inwin.items() if v.get("passed") == "True"}
    oos_pass_ids = {r["template_id"] for r in passers}
    held = oos_pass_ids & iw_pass_ids

    print(f"\n=== {len(passers)} OOS PASSERS ===")
    for r in passers:
        tag = "kept-edge" if r["template_id"] in iw_pass_ids else "NEW (failed in-window)"
        print(f"  {r['template_id']:7} {str(r['session_bucket']):14} startH={r['start_h']:<3} "
              f"trend={r['use_trend']!s:5} net=${r['oos_net_profit']:>8.0f} "
              f"trades={r['oos_n_trades']:>3}  [{tag}]")

    print("\n=== overfit turnover ===")
    print(f"in-window passers : {len(iw_pass_ids)}")
    print(f"OOS passers       : {len(oos_pass_ids)}")
    print(f"held edge (both)  : {len(held)}  "
          f"({100*len(held)/max(1,len(iw_pass_ids)):.0f}% of in-window survived OOS)")
    if passers:
        by_bucket = Counter(r["session_bucket"] for r in passers)
        by_trend = Counter(str(r["use_trend"]) for r in passers)
        by_starth = Counter(str(r["start_h"]) for r in passers)
        print(f"passers by session: {dict(by_bucket)}")
        print(f"passers by UseTrend: {dict(by_trend)}")
        print(f"passers by StartH : {dict(by_starth)}")

    # --- Markdown findings ---------------------------------------------------
    md = dp / f"holdout_{args.from_date}_{args.to_date}_FINDINGS.md"
    lines = [
        f"# OOS holdout findings — {sdir.name}",
        "",
        f"Window {args.from_date}..{args.to_date} · {args.firm} {args.size} · 1 NQ contract · 124 templates",
        "",
        f"- **Window guard:** {frac*100:.0f}% of trades in-window "
        f"(realized {min(days)}..{max(days)}) — **OOS {'CERTIFIED' if certified else 'NOT certified'}**",
        f"- **OOS survival:** {sweep.n_survived}/{sweep.n_templates} survived, "
        f"**{sweep.n_passed}/{sweep.n_templates} passed**",
        f"- **In-window baseline:** {len(iw_pass_ids)} passed",
        f"- **Edge retention:** {len(held)}/{len(iw_pass_ids)} in-window passers also pass OOS "
        f"({100*len(held)/max(1,len(iw_pass_ids)):.0f}%)",
        "",
        "## OOS passers (deployable shortlist)",
        "",
        "| template | session | startH | UseTrend | OOS net $ | trades | in-window? |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in passers:
        lines.append(
            f"| {r['template_id']} | {r['session_bucket']} | {r['start_h']} | {r['use_trend']} | "
            f"{r['oos_net_profit']:.0f} | {r['oos_n_trades']} | "
            f"{'yes' if r['template_id'] in iw_pass_ids else 'NO (new)'} |"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nfindings -> {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
