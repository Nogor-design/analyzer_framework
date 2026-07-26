"""Clean OOS re-run of a session's 124 final templates over an independent window.

Unlike holdout_rebacktest.py (raw text-restamp of the optimize templates, which did
NOT honor the window), this stamps each final template via
generate_fixed_backtest_template(from_date, to_date) -- the path PROVEN 2026-06-15 to
honor From/To in the headless RunBatch (scripts/probe_window_injection.py). Dispatches
ONE RunBatch, collects each fresh Trades.csv, and saves them to
deployment_package/holdout_<from>_<to>_trades/F_xxx.csv so holdout_oos_shortlist.py can
certify survival + characterize the survivors.

Usage:
  python scripts/holdout_oos_certify.py [SESSION] --from-date 2026-02-01 --to-date 2026-03-31 \
      [--instrument "NQ 06-26"] [--limit N] [--per-template-timeout 900] [--timeout 7200]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.optimization.grid_workflow import generate_fixed_backtest_template
from ta_foundation.web.market_data_export import (
    _poll_to_terminal, _write_command_file, build_command_payload,
)
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE, ensure_bridge_available,
)

_SROOT = Path(".ta_artifacts/web_optimizer/sessions")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", default="opt_a09359e6b60b")
    ap.add_argument("--from-date", required=True)
    ap.add_argument("--to-date", required=True)
    ap.add_argument("--instrument", default="NQ 06-26")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--per-template-timeout", type=int, default=900)
    ap.add_argument("--timeout", type=int, default=7200)
    args = ap.parse_args()

    sdir = Path(args.session) if Path(args.session).exists() else _SROOT / args.session
    tdir = sdir / "deployment_package" / "final_backtest_handoff" / "named_backtest_templates" / "recipe"
    templates = sorted(tdir.glob("*.xml"))
    if not templates:
        print(f"no templates under {tdir}", file=sys.stderr)
        return 2
    if args.limit:
        templates = templates[: args.limit]

    work = Path(tempfile.mkdtemp(prefix="oos_certify_"))
    src = work / "generated_templates"; dest = work / "nt_output"
    src.mkdir(parents=True); dest.mkdir(parents=True)
    for p in templates:
        # keep the full stem (F_001_StartTimeH_08) so the RunBatch output dir name maps back
        generate_fixed_backtest_template(
            p, src / p.name, {}, from_date=args.from_date, to_date=args.to_date, strict_params=False)
    print(f"session   : {sdir.name}")
    print(f"stamped   : {len(templates)} fixed-backtest templates @ {args.from_date}..{args.to_date}")

    run_id = "ooscert_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cmd, status = DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE
    ensure_bridge_available(cmd, status, now=datetime.now(timezone.utc))
    try:
        Path(status).unlink()
    except OSError:
        pass
    _write_command_file(cmd, build_command_payload(
        run_id=run_id, source_folder=src, dest_folder=dest,
        instrument=args.instrument, timeout_seconds=args.per_template_timeout))
    print(f"dispatched RunBatch {run_id}; polling (instrument {args.instrument})...")
    state = _poll_to_terminal(status, run_id=run_id, timeout_seconds=args.timeout,
                              poll_interval_seconds=20, logger=print)
    print(f"run state : {state}")

    found = {p.parent.name: p for p in dest.rglob("Trades.csv")}
    rid_of = lambda p: (re.match(r"(F_\d+)", p.stem).group(1)
                        if re.match(r"(F_\d+)", p.stem) else p.stem)
    out = sdir / "deployment_package" / f"holdout_{args.from_date}_{args.to_date}_trades"
    out.mkdir(parents=True, exist_ok=True)
    mapped = 0
    for p in templates:
        tp = found.get(p.stem) or found.get(rid_of(p))
        if tp is None:
            continue
        shutil.copy2(tp, out / f"{rid_of(p)}.csv")
        mapped += 1
    print(f"mapped    : {mapped}/{len(templates)} templates produced Trades.csv")
    print(f"saved     : {out}")
    print(f"\nNEXT: python scripts/holdout_oos_shortlist.py {sdir.name} "
          f"--from-date {args.from_date} --to-date {args.to_date}")
    return 0 if mapped else 1


if __name__ == "__main__":
    raise SystemExit(main())
