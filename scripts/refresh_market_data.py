"""Refresh stale / incomplete NinjaTrader market data in ``D:\\MarketData``.

This is the *glue* that was missing between two pieces that already existed:

  1. a freshness view of ``D:\\MarketData`` (which contracts are stale or are
     missing a bars<->ticks partner), and
  2. :func:`ta_foundation.web.market_data_export.gather_market_data`, which
     drives ``TaFoundationDataExportStrategy`` through the NT batch bridge to
     re-dump any window NinjaTrader has data for.

It scans the folder, decides which contracts need a pull (see
:func:`plan_refresh` -- the one policy knob worth tuning), then dispatches a
gather per gap, serially, respecting the single-writer NT command bridge.

Freshness parsing here mirrors ``market_data_dashboard.py`` but is inlined and
tail-seeking so multi-GB tick files are read in O(64 KB), not O(file). The
actual data-pull capability is NOT reimplemented -- it is the shared
``gather_market_data``.

Prerequisites (same as ``scripts/gather_market_data.py``): NinjaTrader must be
logged in, warm, and NOT running an optimizer batch (the bridge is
single-writer; this aborts cleanly if a batch owns it).

Examples
--------
    # Preview only -- show what is stale/missing and what windows would pull.
    python scripts/refresh_market_data.py --dry-run

    # Refresh everything stale (>7d) or missing a partner file, bars + ticks:
    python scripts/refresh_market_data.py

    # Just NQ, bars only, treat >2 days as stale:
    python scripts/refresh_market_data.py --instrument NQ --no-ticks --stale-days 2
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ta_foundation.web.market_data_export import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SUFFIX,
    DEFAULT_TIMEOUT_SECONDS,
    MarketDataExportError,
    gather_market_data,
)
from ta_foundation.web.optimizer_runner import BridgeBusyError

# ``<INST> <CONTRACT>[ Tick].<Last|Full|Export>.txt`` -- the three suffixes the
# ta_foundation parser accepts interchangeably. ``.tmp`` / ``.parquet`` etc. do
# not match and are ignored.
RE_FILE = re.compile(
    r"^(?P<inst>[A-Z0-9]+)\s+(?P<contract>\d{2}-\d{2,4})"
    r"(?P<tick>\s+Tick)?\.(?:Last|Full|Export)\.txt$",
    re.IGNORECASE,
)
RE_DT_SEMI = re.compile(r"^(\d{8})[; ](\d{6})")
RE_DT_DATE = re.compile(r"^(\d{8})")


def _log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


# ---------------------------------------------------------------------------
# Lean, tail-seeking freshness scan (safe on multi-GB tick files)
# ---------------------------------------------------------------------------

def _parse_dt(line: str) -> datetime | None:
    m = RE_DT_SEMI.match(line)
    if m:
        stamp = m.group(1) + m.group(2)
    else:
        m2 = RE_DT_DATE.match(line)
        if not m2:
            return None
        stamp = m2.group(1) + "000000"
    try:
        return datetime.strptime(stamp, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _first_data_dt(path: Path) -> datetime | None:
    """First parseable timestamp, reading only from the head of the file."""
    try:
        with path.open("rb") as fh:
            for _ in range(64):  # skip at most a few header lines
                raw = fh.readline()
                if not raw:
                    break
                dt = _parse_dt(raw.decode("utf-8", "replace").strip())
                if dt is not None:
                    return dt
    except OSError:
        return None
    return None


def _last_data_dt(path: Path, tail_bytes: int = 65536) -> datetime | None:
    """Last parseable timestamp, reading only the final ``tail_bytes`` bytes."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - tail_bytes))
            chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(chunk.splitlines()):
        dt = _parse_dt(line.strip())
        if dt is not None:
            return dt
    return None


@dataclass
class Feed:
    """Newest file (across Last/Full/Export) for one bars-or-ticks feed."""
    exists: bool = False
    path: Path | None = None
    first_dt: datetime | None = None
    last_dt: datetime | None = None


@dataclass
class Contract:
    instrument: str
    contract: str
    bars: Feed
    ticks: Feed

    @property
    def name(self) -> str:
        # NT wants "<INST> <CONTRACT>", e.g. "NQ 06-26".
        return f"{self.instrument} {self.contract}"


def scan_contracts(folder: Path) -> list[Contract]:
    """Group ``folder`` into per-contract bars/ticks feeds, keeping the newest
    file per feed when Last/Full/Export coexist."""
    # key -> {"bars": Feed, "ticks": Feed}
    acc: dict[tuple[str, str], dict[str, Feed]] = {}
    for f in folder.iterdir():
        if not f.is_file():
            continue
        m = RE_FILE.match(f.name)
        if not m:
            continue
        key = (m.group("inst").upper(), m.group("contract"))
        kind = "ticks" if m.group("tick") else "bars"
        last_dt = _last_data_dt(f)
        slot = acc.setdefault(key, {"bars": Feed(), "ticks": Feed()})
        cur = slot[kind]
        # Keep whichever file carries the newest last bar/tick.
        if not cur.exists or (last_dt and (cur.last_dt is None or last_dt > cur.last_dt)):
            slot[kind] = Feed(
                exists=True,
                path=f,
                first_dt=_first_data_dt(f),
                last_dt=last_dt,
            )
    contracts = [
        Contract(instrument=inst, contract=con, bars=slot["bars"], ticks=slot["ticks"])
        for (inst, con), slot in acc.items()
    ]
    contracts.sort(key=lambda c: (c.instrument, c.contract))
    return contracts


# ---------------------------------------------------------------------------
# THE policy knob: decide whether (and over what window) to refresh a contract.
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    refresh: bool
    reason: str
    from_date: str = ""
    to_date: str = ""


def contract_expiry(contract: str) -> date | None:
    """Approximate expiry for a ``MM-YY`` / ``MM-YYYY`` contract code: the **3rd
    Friday** of the contract month.

    That is exact for the equity-index roots this store cares about (NQ/ES/YM/RTY
    all expire the 3rd Friday). For GC/NG the true last-trade date differs by a
    few days, but as an "is this contract over yet?" gate a few days' slop is
    harmless. Returns ``None`` if the code can't be parsed.
    """
    m = re.match(r"^(\d{2})-(\d{2,4})$", contract.strip())
    if not m:
        return None
    month = int(m.group(1))
    year = int(m.group(2))
    if year < 100:
        year += 2000
    if not 1 <= month <= 12:
        return None
    first = date(year, month, 1)
    # weekday(): Mon=0 .. Fri=4. First Friday, then +14 for the third.
    first_friday = 1 + ((4 - first.weekday()) % 7)
    return date(year, month, first_friday + 14)


def plan_refresh(
    c: Contract,
    *,
    today: date,
    stale_days: int,
    lookback_days: int,
    only_missing: bool,
    include_expired: bool,
) -> Plan:
    """Return the refresh decision + window for one contract.

    A contract is refreshed when either feed is **stale** (its last bar/tick is
    older than ``stale_days``) or a **partner is missing** (bars exist but ticks
    do not, or vice versa). When ``only_missing`` is set, staleness is ignored
    and only partner/fully-missing gaps are filled.

    **Expiry does double duty:**

    * A contract already **past its expiry** (see :func:`contract_expiry`) is
      *historical* — nothing new can ever arrive — so it is skipped by default.
      ``--include-expired`` overrides this to allow a one-time backfill.
    * Expiry also **caps the window end** (``end = min(today, expiry)``): staleness
      is measured against ``end``, not ``today``, and ``to_date`` never runs past
      expiry. So a contract whose data already reaches its expiry reads as *ok*
      instead of looking eternally stale-vs-today and re-pulling forever.

    The pull re-dumps the contract: ``from_date`` = the earliest first-bar we
    already have (so coverage never shrinks), falling back to
    ``end - lookback_days`` when no file exists yet.

    This is the deliberate business-logic seam. Reasonable alternatives you may
    prefer: incremental (``from_date = last_dt``) instead of full-overwrite, or a
    per-instrument stale threshold.
    """
    expiry = contract_expiry(c.contract)
    is_expired = expiry is not None and today > expiry

    # Historical contract: past expiry, no new data can arrive. Leave it alone
    # unless the operator explicitly wants to backfill old contracts.
    if is_expired and not include_expired:
        return Plan(refresh=False, reason=f"expired {expiry.isoformat()}")

    # Never request data past a contract's expiry.
    end = min(today, expiry) if expiry is not None else today
    to_date = end.isoformat()

    partner_missing = c.bars.exists != c.ticks.exists
    reasons: list[str] = []
    if partner_missing:
        reasons.append("ticks missing" if c.bars.exists else "bars missing")

    if not only_missing:
        for label, feed in (("bars", c.bars), ("ticks", c.ticks)):
            if feed.exists and feed.last_dt is not None:
                # Age vs the expiry-capped end, so a complete expired contract
                # is not perpetually "stale".
                age = (end - feed.last_dt.date()).days
                if age > stale_days:
                    reasons.append(f"{label} stale {age}d")

    if not reasons:
        return Plan(refresh=False, reason="ok")

    firsts = [f.first_dt for f in (c.bars, c.ticks) if f.first_dt is not None]
    from_dt = min(firsts).date() if firsts else (end - timedelta(days=lookback_days))
    return Plan(
        refresh=True,
        reason="; ".join(reasons),
        from_date=from_dt.isoformat(),
        to_date=to_date,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt_feed(feed: Feed, today: date) -> str:
    if not feed.exists or feed.last_dt is None:
        return "-"
    age = (today - feed.last_dt.date()).days
    return f"{feed.last_dt.date()} ({age}d)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--folder", default=str(DEFAULT_OUTPUT_DIR), help="Market data folder (default D:\\MarketData)")
    p.add_argument("--stale-days", type=int, default=7, help="Flag a feed stale if its last bar/tick is older than this (default 7)")
    p.add_argument("--lookback-days", type=int, default=200, help="from_date fallback window when a feed has no existing file (default 200)")
    p.add_argument("--instrument", default=None, help="Only this instrument root, e.g. NQ")
    p.add_argument("--contract", default=None, help="Only this contract, e.g. 06-26")
    p.add_argument("--only-missing", action="store_true", help="Ignore staleness; fill only missing bars/ticks partners")
    p.add_argument("--include-expired", action="store_true", help="Also (re)pull contracts already past expiry -- for one-off historical backfills")
    p.add_argument("--no-ticks", dest="ticks", action="store_false", help="Pull bars only (ticks are GB-scale)")
    p.add_argument("--suffix", default=DEFAULT_SUFFIX, help=f"Output filename suffix (default {DEFAULT_SUFFIX})")
    p.add_argument("--to-date", default=None, help="Override window end (YYYY-MM-DD; default today)")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Per-contract gather timeout (seconds)")
    p.add_argument("--dry-run", action="store_true", help="Show the plan; do not dispatch anything")
    args = p.parse_args(argv)

    folder = Path(args.folder)
    if not folder.is_dir():
        _log(f"ERROR: not a directory: {folder}")
        return 1

    today = date.fromisoformat(args.to_date) if args.to_date else date.today()

    contracts = scan_contracts(folder)
    if args.instrument:
        want = args.instrument.upper()
        contracts = [c for c in contracts if c.instrument == want]
    if args.contract:
        contracts = [c for c in contracts if c.contract == args.contract]

    if not contracts:
        _log(f"No market-data contracts matched in {folder}")
        return 0

    # Build the plan for every contract, then act on the ones needing a pull.
    planned: list[tuple[Contract, Plan]] = []
    _log(f"Scanned {len(contracts)} contract(s) in {folder} (stale threshold: {args.stale_days}d)")
    print(f"\n  {'CONTRACT':<12} {'BARS last':<20} {'TICKS last':<20} {'DECISION'}")
    print("  " + "-" * 78)
    n_expired = 0
    for c in contracts:
        plan = plan_refresh(
            c,
            today=today,
            stale_days=args.stale_days,
            lookback_days=args.lookback_days,
            only_missing=args.only_missing,
            include_expired=args.include_expired,
        )
        if plan.refresh:
            decision = f"REFRESH {plan.from_date}->{plan.to_date} [{plan.reason}]"
        else:
            decision = plan.reason  # "ok" or "expired YYYY-MM-DD"
            if plan.reason.startswith("expired"):
                n_expired += 1
        print(f"  {c.name:<12} {_fmt_feed(c.bars, today):<20} {_fmt_feed(c.ticks, today):<20} {decision}")
        if plan.refresh:
            planned.append((c, plan))
    print()

    if not planned:
        _log("Everything current -- nothing to pull.")
        if n_expired:
            _log(f"({n_expired} contract(s) skipped as expired; use --include-expired to backfill them.)")
        return 0

    _log(f"{len(planned)} contract(s) need a pull"
         + (" -- DRY RUN, dispatching nothing." if args.dry_run else f" (ticks={'yes' if args.ticks else 'no'})."))
    if args.dry_run:
        return 0

    failures = 0
    for c, plan in planned:
        _log(f"--- gather {c.name}  {plan.from_date} -> {plan.to_date}  ({plan.reason})")
        try:
            result = gather_market_data(
                instrument=c.name,
                from_date=plan.from_date,
                to_date=plan.to_date,
                export_ticks=args.ticks,
                suffix=args.suffix,
                output_dir=folder,
                overwrite_if_exists=True,
                timeout_seconds=args.timeout,
                logger=_log,
            )
        except BridgeBusyError as exc:
            # Bridge is single-writer; an optimizer batch owns it. Stop cleanly
            # rather than fight for it — rerun this script when the batch frees.
            _log(f"ABORT: NT bridge busy ({exc}). Stopping; rerun when free.")
            return 2
        except MarketDataExportError as exc:
            _log(f"  ERROR building/dispatching {c.name}: {exc}")
            failures += 1
            continue

        ok = result.state in {"finished", "completed", "complete", "success", "done"} and not result.error
        for f in result.files:
            _log(f"    {f.kind}: exists={f.exists} lines={f.line_count} bytes={f.size_bytes}")
        if not ok:
            _log(f"  INCOMPLETE {c.name}: state={result.state} {result.error or ''}")
            failures += 1

    _log(f"Done. {len(planned) - failures}/{len(planned)} contract(s) refreshed OK.")
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
