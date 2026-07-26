"""Headless SDF exit-verification backtest (tasks #1/#2 of the SDF fix).

Stamps two fixed Backtest-mode templates for StrategyDiscoveryFilter from a seed
generated off the live source — one FixedRR (managed stop+target), one AtrTrail
(explicit working-stop trail) — with filters loosened so the run produces trades,
dispatches ONE RunBatch through the proven optimizer bridge, then parses each
template's Trades.csv and reports the EXIT-NAME breakdown + stop/target bounds.

Proof criteria:
  * FixedRR  -> exits are 'Stop loss' / 'Profit target'; |loss| ~ StopTicks, win ~ TargetTicks
  * AtrTrail -> a 'SdfLongStop'/'SdfShortStop' exit appears (explicit working stop fired)

Canonical pipeline (CLAUDE.md): seed via generate_seed_template_from_source,
pin via generate_fixed_backtest_template. No hand-rolled XML.

Usage: python scripts/verify_sdf_backtest.py [--instrument "NQ 06-26"] [--timeout 1800]
"""
from __future__ import annotations

import argparse
import csv
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.nt_strategy_loop.seed_template import generate_seed_template_from_source
from ta_foundation.optimization.grid_workflow import generate_fixed_backtest_template
from ta_foundation.web.market_data_export import (
    _poll_to_terminal,
    _write_command_file,
    build_command_payload,
)
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE,
    DEFAULT_STATUS_FILE,
    ensure_bridge_available,
)

SDF_SRC = Path("src/ta_foundation/strategies/StrategyDiscoveryFilter/StrategyDiscoveryFilter.cs")

# Loosened filters so the Days-mode default window actually produces trades.
COMMON = {
    "RegimeMode": "Any",
    "UseTrendAlignment": False,
    "RequireEmaConfirmation": False,
    "RequireMinAtrMultiple": 0.0,
    "AllowRTH": True, "AllowONH": True, "AllowETH": True,
    "AllowLong": True, "AllowShort": True,
    "EntrySignal": "EmaCross", "TimingMode": "NextOpen",
    "EntryEmaPeriod": 9, "SlowEmaPeriod": 21,
    "Contracts": 1,
    # clamp to each property's [Range] cap (validated by NT) so the load doesn't
    # error; over a short verification window these are effectively non-binding.
    "MaxDailyLossUsd": 10000.0, "MaxDailyProfitUsd": 50000.0, "MaxDailyTrades": 50,
    "EnableDebugPrint": True,
}
VARIANTS = {
    "SDF_FixedRR":  {**COMMON, "ExitPolicy": "FixedRR",  "StopTicks": 40, "TargetTicks": 60},
    "SDF_AtrTrail": {**COMMON, "ExitPolicy": "AtrTrail", "StopTicks": 40, "AtrTrailMultiple": 2.0},
}


def _money(s: str) -> float:
    s = (s or "").strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NQ 06-26")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="sdf_verify_"))
    seed = work / "SDF_seed.xml"
    src = work / "generated_templates"
    dest = work / "nt_output"
    src.mkdir(parents=True); dest.mkdir(parents=True)

    generate_seed_template_from_source(
        SDF_SRC, seed, strategy_name="StrategyDiscoveryFilter", instrument=args.instrument)
    for name, vals in VARIANTS.items():
        generate_fixed_backtest_template(seed, src / f"{name}.xml", vals, strict_params=True)
    print(f"stamped {len(VARIANTS)} templates -> {src}")

    run_id = "sdfverify_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cmd, status = DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE
    ensure_bridge_available(cmd, status, now=datetime.now(timezone.utc))
    try:
        Path(status).unlink()
    except OSError:
        pass
    _write_command_file(cmd, build_command_payload(
        run_id=run_id, source_folder=src, dest_folder=dest,
        instrument=args.instrument, timeout_seconds=args.timeout))
    print(f"dispatched RunBatch {run_id}; polling (instrument {args.instrument})...")
    state = _poll_to_terminal(status, run_id=run_id, timeout_seconds=args.timeout,
                              poll_interval_seconds=15, logger=print)
    print(f"run state: {state}")

    found = {p.parent.name: p for p in dest.rglob("Trades.csv")}
    print(f"output dirs: {sorted(found)}")

    out = Path(".ta_artifacts/sdf_verify"); out.mkdir(parents=True, exist_ok=True)
    overall_ok = True
    for name, vals in VARIANTS.items():
        tp = found.get(name)
        print(f"\n===== {name}  (ExitPolicy={vals['ExitPolicy']}, StopTicks={vals['StopTicks']}) =====")
        if tp is None:
            print("  NO Trades.csv produced"); overall_ok = False; continue
        shutil.copy2(tp, out / f"{name}_Trades.csv")
        rows = list(csv.DictReader(tp.open(encoding="utf-8-sig")))
        if not rows:
            print("  0 trades"); overall_ok = False; continue
        exits = Counter((r.get("Exit name") or "").strip() for r in rows)
        profits = [_money(r.get("Profit", "")) for r in rows]
        losses = [p for p in profits if p < 0]
        wins = [p for p in profits if p > 0]
        print(f"  trades       : {len(rows)}")
        print(f"  exit names   : {dict(exits)}")
        print(f"  worst loss   : ${min(profits):.0f}   best win: ${max(profits):.0f}")
        if losses:
            print(f"  avg loss     : ${sum(losses)/len(losses):.0f}  (StopTicks={vals['StopTicks']} ~ ${vals['StopTicks']*5:.0f} on NQ)")
        if wins:
            print(f"  avg win      : ${sum(wins)/len(wins):.0f}")
        # proof check
        names = set(exits)
        if vals["ExitPolicy"] == "FixedRR":
            ok = ("Stop loss" in names) or ("Profit target" in names)
            print(f"  PROOF        : managed stop/target exits present -> {'PASS' if ok else 'FAIL'}")
        else:
            ok = any(n in names for n in ("SdfLongStop", "SdfShortStop"))
            print(f"  PROOF        : explicit trail stop exits present -> {'PASS' if ok else 'FAIL'}")
        overall_ok = overall_ok and ok
    print(f"\nsaved Trades.csv copies -> {out}")
    print(f"\nOVERALL: {'PASS -- SDF exits fire correctly' if overall_ok else 'NEEDS REVIEW'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
