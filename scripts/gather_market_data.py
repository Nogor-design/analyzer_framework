"""Gather NinjaTrader market data (bars + optional ticks) without hand-downloading.

Drives ``TaFoundationDataExportStrategy`` through the optimizer batch bridge to
dump any historical window NinjaTrader has data for. See
``docs/runbooks/market_data_gathering.md``.

NinjaTrader must be logged in, warm, and NOT busy with an optimizer batch (the
shared command bridge is single-writer; the call aborts if another run owns it).

Examples:
    # Gather NQ 06-26 bars + ticks for the May-June deployment window:
    python scripts/gather_market_data.py "NQ 06-26" 2026-05-01 2026-06-01

    # Bars only, into a custom dir:
    python scripts/gather_market_data.py "ES 06-26" 2026-05-01 2026-06-01 --no-ticks --output-dir D:\\MarketData

    # Dry run -- build the template + show what would dispatch, write nothing:
    python scripts/gather_market_data.py "NQ 06-26" 2026-05-01 2026-06-01 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from ta_foundation.web.market_data_export import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SUFFIX,
    DEFAULT_TIMEOUT_SECONDS,
    MarketDataExportError,
    gather_market_data,
)
from ta_foundation.web.optimizer_runner import BridgeBusyError


def _log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gather NT market data (bars + optional ticks).")
    parser.add_argument("instrument", help='NT contract, e.g. "NQ 06-26"')
    parser.add_argument("from_date", help="Window start (YYYY-MM-DD)")
    parser.add_argument("to_date", help="Window end (YYYY-MM-DD)")
    parser.add_argument("--no-ticks", dest="ticks", action="store_false", help="Bars only (skip ticks)")
    parser.add_argument("--suffix", default=DEFAULT_SUFFIX, help=f"Filename suffix (default {DEFAULT_SUFFIX})")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output dir (default D:\\MarketData)")
    parser.add_argument("--append", dest="overwrite", action="store_false", help="Append instead of overwrite")
    parser.add_argument("--command-file", default=None, help="Override IPC command file (default real bridge)")
    parser.add_argument("--status-file", default=None, help="Override status file (default real bridge)")
    parser.add_argument("--work-dir", default=None, help="Where to stage the generated template / nt_output")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Overall timeout (seconds)")
    parser.add_argument("--dry-run", action="store_true", help="Build template only; do not dispatch")
    args = parser.parse_args(argv)

    try:
        result = gather_market_data(
            instrument=args.instrument,
            from_date=args.from_date,
            to_date=args.to_date,
            export_ticks=args.ticks,
            suffix=args.suffix,
            output_dir=args.output_dir,
            overwrite_if_exists=args.overwrite,
            command_file=args.command_file,
            status_file=args.status_file,
            work_dir=args.work_dir,
            timeout_seconds=args.timeout,
            dispatch=not args.dry_run,
            logger=_log,
        )
    except BridgeBusyError as exc:
        _log(f"ABORT: {exc}")
        return 2
    except MarketDataExportError as exc:
        _log(f"ERROR: {exc}")
        return 1

    _log(f"state={result.state}")
    for f in result.files:
        _log(f"  {f.kind}: exists={f.exists} lines={f.line_count} bytes={f.size_bytes} {f.path}")
    if result.error:
        _log(f"  issue: {result.error}")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    if args.dry_run:
        return 0
    ok = result.state in {"finished", "completed", "complete", "success", "done"} and not result.error
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
