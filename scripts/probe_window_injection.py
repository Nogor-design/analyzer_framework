"""Decisive probe: does the headless RunBatch path honor a template's From/To window?

Runs TWO backtests over 2026-02-02..02-06 in one batch:
  A = the operator's UI-saved Custom-Range template (converted to a runnable backtest)
  B = our generated seed template with From/To stamped to the same window
Then reads each Trades.csv and reports the realized trade-date span.

Interpretation:
  * both land in Feb 02-06            -> headless HONORS the window (no AddOn bug)
  * both land elsewhere (recent dates)-> headless IGNORES it (tab-mode bug confirmed)
  * A honors but B doesn't            -> our generation is missing what A carries
"""
from __future__ import annotations

import csv
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.nt_strategy_loop.seed_template import generate_seed_template_from_source
from ta_foundation.optimization.grid_workflow import generate_fixed_backtest_template
from ta_foundation.web.market_data_export import (
    _poll_to_terminal, _write_command_file, build_command_payload,
)
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE, ensure_bridge_available,
)

USER_TPL = Path(r"C:\Users\Owner\Documents\NinjaTrader 8\templates\Strategy\StrategyDiscoveryFilter\2026-02-02-02-06.xml")
SDF_SRC = Path("src/ta_foundation/strategies/StrategyDiscoveryFilter/StrategyDiscoveryFilter.cs")
INSTR = "NQ 06-26"
FROM, TO = "2026-02-02", "2026-02-06"

# loose, trade-producing, all within [Range] caps
PARAMS = {
    "RegimeMode": "Any", "UseTrendAlignment": False, "RequireEmaConfirmation": False,
    "RequireMinAtrMultiple": 0.0, "AllowRTH": True, "AllowONH": True, "AllowETH": True,
    "AllowLong": True, "AllowShort": True, "EntrySignal": "EmaCross", "TimingMode": "NextOpen",
    "EntryEmaPeriod": 9, "SlowEmaPeriod": 21, "Contracts": 1, "ExitPolicy": "FixedRR",
    "StopTicks": 40, "TargetTicks": 60,
    "MaxDailyLossUsd": 10000.0, "MaxDailyProfitUsd": 50000.0, "MaxDailyTrades": 50,
}


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="sdf_probe_"))
    src = work / "generated_templates"; dest = work / "nt_output"
    src.mkdir(parents=True); dest.mkdir(parents=True)

    # A: operator's template -> runnable backtest, KEEP its embedded From/To (no override)
    generate_fixed_backtest_template(USER_TPL, src / "A_user_customrange.xml", PARAMS, strict_params=False)
    # B: our seed -> stamp the same window explicitly
    seed = work / "seed.xml"
    generate_seed_template_from_source(SDF_SRC, seed, strategy_name="StrategyDiscoveryFilter", instrument=INSTR)
    generate_fixed_backtest_template(seed, src / "B_ours_stamped.xml", PARAMS,
                                     from_date=FROM, to_date=TO, strict_params=True)
    print(f"stamped A (user) + B (ours); window {FROM}..{TO}; instrument {INSTR}")

    run_id = "sdfprobe_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cmd, status = DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE
    ensure_bridge_available(cmd, status, now=datetime.now(timezone.utc))
    try:
        Path(status).unlink()
    except OSError:
        pass
    _write_command_file(cmd, build_command_payload(
        run_id=run_id, source_folder=src, dest_folder=dest, instrument=INSTR, timeout_seconds=1800))
    print(f"dispatched {run_id}; polling...")
    state = _poll_to_terminal(status, run_id=run_id, timeout_seconds=1800,
                              poll_interval_seconds=15, logger=print)
    print(f"run state: {state}")

    found = {p.parent.name: p for p in dest.rglob("Trades.csv")}
    print(f"output dirs: {sorted(found)}\n")
    out = Path(".ta_artifacts/sdf_verify"); out.mkdir(parents=True, exist_ok=True)
    for label in ("A_user_customrange", "B_ours_stamped"):
        tp = found.get(label)
        print(f"===== {label} (requested {FROM}..{TO}) =====")
        if tp is None:
            print("  NO Trades.csv\n"); continue
        shutil.copy2(tp, out / f"probe_{label}_Trades.csv")
        rows = list(csv.DictReader(tp.open(encoding="utf-8-sig")))
        dates = []
        for r in rows:
            et = (r.get("Entry time") or "").split(" ")[0]
            try:
                dates.append(datetime.strptime(et, "%m/%d/%Y").date())
            except ValueError:
                pass
        if not dates:
            print(f"  {len(rows)} rows but no parseable Entry time\n"); continue
        lo, hi = min(dates), max(dates)
        from datetime import date
        fy, fm, fd = map(int, FROM.split("-")); ty, tm, td = map(int, TO.split("-"))
        win_lo, win_hi = date(fy, fm, fd), date(ty, tm, td)
        in_win = sum(1 for d in dates if win_lo <= d <= win_hi)
        print(f"  trades: {len(rows)}   realized {lo}..{hi}   in-window: {in_win}/{len(dates)} ({100*in_win/len(dates):.0f}%)")
        print(f"  verdict: {'HONORED' if in_win/len(dates) >= 0.8 else 'IGNORED (ran outside requested window)'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
