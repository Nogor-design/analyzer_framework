"""Bulk multi-year minute-bar history pull (Lever 1).

Rolls back through quarterly futures contracts for an instrument panel and dumps
each contract's front-quarter minute bars via the proven single-contract
`gather_market_data` bridge. BARS ONLY by default (ticks are GB-scale and not
needed for lower-frequency discovery).

Each contract gets a clean, NON-overlapping ~3-month front-quarter window so the
per-contract files stitch without duplicate-timestamp price jumps:
    MM=03 -> (YY-1)-12-15 .. YY-03-15
    MM=06 ->     YY-03-15 .. YY-06-15
    MM=09 ->     YY-06-15 .. YY-09-15
    MM=12 ->     YY-09-15 .. YY-12-15

Files land in D:\MarketData as "<INST> <MM>-<YY>.Export.txt" (the Python parser
reads .Export transparently). Skips contracts whose file already exists & is
non-trivial, so the run is resumable.

Usage:
    python scripts/gather_history_bulk.py --from-year 2024 --instruments NQ ES YM RTY
    python scripts/gather_history_bulk.py --from-year 2023 --instruments NQ --dry-run
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from ta_foundation.web.market_data_export import gather_market_data, DEFAULT_OUTPUT_DIR

QUARTER_WINDOWS = {
    "03": lambda y: (f"{y-1}-12-15", f"{y}-03-15"),
    "06": lambda y: (f"{y}-03-15", f"{y}-06-15"),
    "09": lambda y: (f"{y}-06-15", f"{y}-09-15"),
    "12": lambda y: (f"{y}-09-15", f"{y}-12-15"),
}


def _log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def contracts(from_year: int, to_year: int, to_month: int):
    """Yield (instr-less) '<MM>-<YY>' contract codes + windows, newest first."""
    out = []
    for year in range(to_year, from_year - 1, -1):
        for mm in ("12", "09", "06", "03"):
            if year == to_year and int(mm) > to_month:
                continue
            yy = f"{year % 100:02d}"
            frm, to = QUARTER_WINDOWS[mm](year)
            out.append((f"{mm}-{yy}", frm, to))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instruments", nargs="+", default=["NQ", "ES", "YM", "RTY"])
    ap.add_argument("--from-year", type=int, required=True, help="oldest year to attempt")
    ap.add_argument("--to-year", type=int, default=2026)
    ap.add_argument("--to-month", type=int, default=6, help="newest quarter month in to-year")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--per-timeout", type=int, default=420)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.output_dir)
    plan = contracts(args.from_year, args.to_year, args.to_month)
    _log(f"Plan: {len(args.instruments)} instruments x {len(plan)} contracts = "
         f"{len(args.instruments) * len(plan)} pulls (bars only)")

    n_ok = n_skip = n_empty = n_fail = 0
    for inst in args.instruments:
        for code, frm, to in plan:
            contract = f"{inst} {code}"
            bars_file = out_dir / f"{contract}.Export.txt"
            last_file = out_dir / f"{contract}.Last.txt"
            if (bars_file.exists() and bars_file.stat().st_size > 10_000) or \
               (last_file.exists() and last_file.stat().st_size > 10_000):
                _log(f"SKIP {contract} (already present)")
                n_skip += 1
                continue
            if args.dry_run:
                _log(f"PLAN {contract}  {frm}..{to}")
                continue
            try:
                r = gather_market_data(
                    instrument=contract, from_date=frm, to_date=to,
                    export_ticks=False, output_dir=args.output_dir,
                    timeout_seconds=args.per_timeout, logger=lambda m: None,
                )
            except Exception as exc:  # bridge busy / build error -> log, continue
                _log(f"FAIL {contract}: {type(exc).__name__}: {exc}")
                n_fail += 1
                continue
            lines = r.files[0].line_count if r.files else 0
            if r.error or lines == 0:
                _log(f"EMPTY {contract}  {frm}..{to}  (NT has no data this far back?)")
                n_empty += 1
            else:
                _log(f"OK   {contract}  {frm}..{to}  bars={lines:,}")
                n_ok += 1
    _log(f"DONE  ok={n_ok} skip={n_skip} empty={n_empty} fail={n_fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
