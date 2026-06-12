"""Headless backtest-with-audit runner — auto-produces the inputs the parity CLI
consumes, closing the Phase-1 loop (no human clicking in NinjaTrader).

Drives **PantheonMaster** through the SAME optimizer batch bridge as
``web/market_data_export.py`` (1-combo FIXED backtest), but pinned for a trajectory
parity run:

  - ``UseLiveStopManagement=false``  -> the MANAGED bar-close trail path (SetStopLoss),
    which both emits the StopAuditCsvPath trajectory AND is what the Python bar replica
    models. (true would be the live/explicit path — that's the Phase-2 leg.)
  - ``UseDiscoveryExitPolicy=true`` + ``DiscoveryExitPolicy=AtrTrail``  -> trail under test.
  - ``EnableDiscoveryFilters=false``  -> plain MA-cross entries so the window actually trades.
  - ``StopAuditCsvPath=<unique file>``  -> the per-event trajectory dump.
  - ``OrderFillResolution=Standard``  -> bar-based fills (no tick series needed; matches the
    replica's fill-at-stop-on-touch assumption).

After the run it locates the exported Trades.csv + the audit dump and feeds both to
``stop_audit_parity.parity_report`` for an automated PASS/FAIL.

Reuse map (nothing rebuilt): seed from .cs + fixed-backtest stamping + backslash-safe
tag patching + RunBatch dispatch/poll all come from ``market_data_export`` /
``grid_workflow`` / ``optimizer_runner``. :func:`build_parity_backtest_template` is pure
and unit-tested; the live dispatch mirrors the proven data-export path.

NOTE: the live dispatch (artifact locations, RunBatch trade export) is validated against
a running NinjaTrader — use ``--no-dispatch`` first to inspect the generated template.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ta_foundation.analysis.exits.nt_atr_trail_parity import NtAtrTrailConfig
from ta_foundation.analysis.exits.stop_audit_parity import parity_report
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
from ta_foundation.web.optimizer_template_writer import (
    _replace_or_insert_strategy_tag,
    _replace_tag_text,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STRATEGY_SOURCE = (
    _REPO_ROOT / "src" / "ta_foundation" / "strategies" / "PantheonMaster" / "PantheonMaster.cs"
)
STRATEGY_ID = "PantheonMaster"
DEFAULT_BARS = r"D:\MarketData\NQ 06-26.Export.txt"


def build_parity_backtest_template(
    *,
    instrument: str,
    from_date: str,
    to_date: str,
    audit_csv_path: str | Path,
    output_path: str | Path,
    cfg: NtAtrTrailConfig,
    strategy_source: str | Path | None = None,
) -> Path:
    """Generate a 1-combo fixed-backtest template for PantheonMaster pinned for a
    bar-close AtrTrail trajectory parity run. Pure (no NinjaTrader IO)."""
    source = Path(strategy_source) if strategy_source else DEFAULT_STRATEGY_SOURCE
    if not source.is_file():
        raise FileNotFoundError(f"Strategy source not found: {source}")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    seed_path = target.with_name(target.stem + "__seed.xml")
    generate_seed_template_from_source(
        source_path=source, output_path=seed_path, strategy_name=STRATEGY_ID,
        instrument=instrument, from_date=f"{from_date}T00:00:00", to_date=f"{to_date}T00:00:00",
    )

    # Numeric/bool params go through grid_workflow (strict: fail if the seed lacks one).
    generate_fixed_backtest_template(
        seed_path, target,
        {
            "UseLiveStopManagement": False,     # managed bar-close path == what the replica models
            "UseDiscoveryExitPolicy": True,
            "EnableDiscoveryFilters": False,    # plain MA-cross entries so the window trades
            "EnableDebugPrint": False,
            "AtrTrailMultiple": float(cfg.atr_multiple),
            "StopTicks": int(cfg.stop_ticks),
            "AtrPeriod": int(cfg.atr_period),
        },
        from_date=from_date, to_date=to_date, strict_params=True,
    )

    # Backslash-/enum-sensitive values: post-patch the <Strategy> section directly.
    text = target.read_text(encoding="utf-8")
    text = _replace_tag_text(text, "StopAuditCsvPath", str(audit_csv_path), count=1)
    text = _replace_or_insert_strategy_tag(text, "DiscoveryExitPolicy", "AtrTrail")
    text = _replace_or_insert_strategy_tag(text, "OrderFillResolution", "Standard")
    text = _replace_or_insert_strategy_tag(text, "InstrumentOrInstrumentList", instrument)
    target.write_text(text, encoding="utf-8")

    try:
        seed_path.unlink()
    except OSError:
        pass
    return target


def _find_trades_csv(dest_folder: Path) -> Path | None:
    hits = sorted(dest_folder.rglob("Trades.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def run_parity_backtest(
    *,
    instrument: str,
    from_date: str,
    to_date: str,
    bars_file: str,
    cfg: NtAtrTrailConfig,
    work_dir: str | Path | None = None,
    audit_csv_path: str | Path | None = None,
    dispatch: bool = True,
    timeout_seconds: int = 3 * 60 * 60,
    poll_interval_seconds: int = 20,
    command_file: str | Path | None = None,
    status_file: str | Path | None = None,
    logger=print,
) -> dict[str, Any]:
    """Build -> dispatch -> poll -> grade. Returns the parity report (plus run meta).
    ``dispatch=False`` builds the template only (dry run)."""
    moment = datetime.now(timezone.utc)
    base = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="parity_bt_"))
    source_folder = base / "generated_templates"
    dest_folder = base / "nt_output"
    source_folder.mkdir(parents=True, exist_ok=True)
    dest_folder.mkdir(parents=True, exist_ok=True)

    run_id = "parity_" + moment.strftime("%Y%m%d_%H%M%S")
    template_path = source_folder / f"{run_id}.xml"
    audit_path = Path(audit_csv_path) if audit_csv_path else (base / f"{run_id}_audit.csv")
    # Audit is APPEND-only — a stale file would corrupt the trajectory.
    try:
        if audit_path.exists():
            audit_path.unlink()
    except OSError:
        pass

    build_parity_backtest_template(
        instrument=instrument, from_date=from_date, to_date=to_date,
        audit_csv_path=audit_path, output_path=template_path, cfg=cfg,
    )
    logger(f"[parity_backtest] template: {template_path}")
    logger(f"[parity_backtest] audit will be written to: {audit_path}")

    if not dispatch:
        return {"state": "prepared", "run_id": run_id, "template_path": str(template_path),
                "audit_path": str(audit_path), "dest_folder": str(dest_folder)}

    cmd_path = Path(command_file) if command_file else DEFAULT_COMMAND_FILE
    status_path = Path(status_file) if status_file else DEFAULT_STATUS_FILE
    ensure_bridge_available(cmd_path, status_path, now=moment)
    try:
        if status_path.exists():
            status_path.unlink()
    except OSError:
        pass

    _write_command_file(cmd_path, build_command_payload(
        run_id=run_id, source_folder=source_folder, dest_folder=dest_folder,
        instrument=instrument, timeout_seconds=timeout_seconds,
    ))
    logger(f"[parity_backtest] dispatched RunBatch {run_id}")
    state = _poll_to_terminal(status_path, run_id=run_id, timeout_seconds=timeout_seconds,
                              poll_interval_seconds=poll_interval_seconds, logger=logger)
    logger(f"[parity_backtest] run state: {state}")

    if not audit_path.exists():
        return {"state": state, "run_id": run_id, "passed": False,
                "error": f"audit file not written: {audit_path}", "audit_path": str(audit_path)}

    trades_csv = _find_trades_csv(dest_folder)
    entries = _load_entries(trades_csv) if trades_csv else None

    bars = _load_bars(bars_file)
    report = parity_report(str(audit_path), bars, cfg, entries=entries)
    report.update({"state": state, "run_id": run_id, "audit_path": str(audit_path),
                   "trades_csv": str(trades_csv) if trades_csv else None})
    return report


# Reuse the CLI's loaders (single source of the NT Trades.csv / bars parsing).
def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sap_cli", _REPO_ROOT / "scripts" / "stop_audit_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_entries(trades_csv: Path) -> pd.DataFrame:
    return _load_cli().load_nt_trades_entries(str(trades_csv))


def _load_bars(bars_file: str) -> pd.DataFrame:
    return _load_cli().load_minute_bars(bars_file)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Headless PantheonMaster backtest + trajectory parity.")
    p.add_argument("--instrument", default="NQ 06-26")
    p.add_argument("--from-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--to-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--bars", default=DEFAULT_BARS)
    p.add_argument("--atr-mode", default="wilder", choices=["wilder", "sma"])
    p.add_argument("--no-dispatch", action="store_true", help="build the template only (dry run)")
    p.add_argument("--work-dir", default=None)
    args = p.parse_args(argv)

    cfg = NtAtrTrailConfig(atr_mode=args.atr_mode)
    out = run_parity_backtest(
        instrument=args.instrument, from_date=args.from_date, to_date=args.to_date,
        bars_file=args.bars, cfg=cfg, work_dir=args.work_dir, dispatch=not args.no_dispatch,
    )
    if out.get("state") == "prepared":
        print(f"[dry-run] template at {out['template_path']}")
        return 0
    if "passed" not in out:
        print(f"run did not produce a verdict: {out}")
        return 1
    print(f"\nVERDICT: {'PASS' if out['passed'] else 'FAIL'}  "
          f"trades={out.get('n_trades')}  match={out.get('overall_stop_match_rate')}")
    return 0 if out.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
