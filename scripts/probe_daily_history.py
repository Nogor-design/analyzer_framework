"""One-off probe: does NT serve YEARS of DAILY bars for the front futures
contract (continuous/merged), even though per-contract intraday rolls off at
~6 months? Builds the export template, patches it from Minute(4) to Day(5)
bars, requests a multi-year window, dispatches via the bridge, reports the
daily bar count + date span actually returned.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.web.market_data_export import (
    build_export_template, build_command_payload, _write_command_file,
    _poll_to_terminal, DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE,
)
from ta_foundation.web.optimizer_runner import ensure_bridge_available

INSTR = sys.argv[1] if len(sys.argv) > 1 else "NQ 06-26"
FROM = sys.argv[2] if len(sys.argv) > 2 else "2021-01-01"
TO = sys.argv[3] if len(sys.argv) > 3 else "2026-06-15"
SUFFIX = "DailyProbe"
OUT = Path(r"D:\MarketData")


def main() -> int:
    moment = datetime.now(timezone.utc)
    base = Path(tempfile.mkdtemp(prefix="daily_probe_"))
    src = base / "generated_templates"; src.mkdir(parents=True)
    dst = base / "nt_output"; dst.mkdir(parents=True)
    run_id = "dailyprobe_" + moment.strftime("%Y%m%d_%H%M%S")
    tmpl = src / f"{run_id}.xml"

    build_export_template(
        instrument=INSTR, from_date=FROM, to_date=TO, output_dir=OUT,
        suffix=SUFFIX, export_ticks=False, output_path=tmpl, overwrite_if_exists=True,
    )
    # Patch Minute(4) -> Day(5)
    txt = tmpl.read_text(encoding="utf-8")
    txt = txt.replace("<BarsPeriodTypeSerialize>4</BarsPeriodTypeSerialize>",
                      "<BarsPeriodTypeSerialize>5</BarsPeriodTypeSerialize>")
    txt = txt.replace("<BaseBarsPeriodType>Minute</BaseBarsPeriodType>",
                      "<BaseBarsPeriodType>Day</BaseBarsPeriodType>")
    tmpl.write_text(txt, encoding="utf-8")
    print(f"template patched to Day bars: {tmpl}", flush=True)

    ensure_bridge_available(DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE, now=moment)
    if DEFAULT_STATUS_FILE.exists():
        DEFAULT_STATUS_FILE.unlink()
    payload = build_command_payload(run_id=run_id, source_folder=src, dest_folder=dst,
                                    instrument=INSTR, timeout_seconds=600)
    _write_command_file(DEFAULT_COMMAND_FILE, payload)
    print(f"dispatched {run_id} for {INSTR} {FROM}..{TO} (DAILY)", flush=True)
    state = _poll_to_terminal(DEFAULT_STATUS_FILE, run_id=run_id, timeout_seconds=600,
                              poll_interval_seconds=15, logger=print)
    print(f"state={state}", flush=True)

    out_file = OUT / f"{INSTR}.{SUFFIX}.txt"
    if out_file.exists():
        lines = out_file.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"DAILY bars file: {out_file}  lines={len(lines)}")
        if lines:
            print(f"  first: {lines[0]}")
            print(f"  last : {lines[-1]}")
    else:
        print(f"NO output file at {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
