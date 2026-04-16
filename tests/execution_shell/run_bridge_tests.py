#!/usr/bin/env python3
"""
TaFoundationExecutionShell — Automated Bridge Test Runner
==========================================================

Prerequisites
-------------
1. NT8 is running with TaFoundationExecutionShell loaded on a chart.
2. Strategy parameters match test_config.json:
   - InboxDirectory   = <bridge_root>/inbox
   - ArchiveDirectory = <bridge_root>/archive
   - RejectDirectory  = <bridge_root>/rejected
   - OutboxDirectory  = <bridge_root>/outbox
   - TemplateDirectory= <bridge_root>/templates
   - LogFilePath      = <bridge_root>/logs/<log_filename>
   - StateFilePath    = <bridge_root>/state/<state_filename>
   - ProcessedIdsFilePath = <bridge_root>/state/<processed_ids_filename>
   - DryRunMode       = true
   - EnableOutboxEvents = true
   - RequireInstrumentMatch = true (set instrument in config to match chart)
3. Chart is receiving live ticks (market hours or replay).

Usage
-----
    python run_bridge_tests.py
    python run_bridge_tests.py --config test_config.json
    python run_bridge_tests.py --groups A B D      # run specific groups
    python run_bridge_tests.py --skip-heartbeat    # skip group C (takes 30+ s)
    python run_bridge_tests.py --list              # list all test IDs
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure harness package is importable.
_SUITE_DIR = Path(__file__).parent
if str(_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUITE_DIR))

from bridge_test_harness.test_cases.base import TestContext, TestResult
from bridge_test_harness.test_cases import (
    a_contract_validation,
    b_dryrun_statemachine,
    c_heartbeat,
    d_sequencing,
    e_persistence_recovery,
)
from bridge_test_harness import report_writer


# ---------------------------------------------------------------------------
# Test registry
# ---------------------------------------------------------------------------

GROUP_MAP: dict[str, list] = {
    "A": a_contract_validation.ALL_TESTS,
    "B": b_dryrun_statemachine.ALL_TESTS,
    "C": c_heartbeat.ALL_TESTS,
    "D": d_sequencing.ALL_TESTS,
    "E": e_persistence_recovery.ALL_TESTS,
}

GROUP_LABELS: dict[str, str] = {
    "A": "Contract / Input Validation",
    "B": "DryRun State Machine",
    "C": "Heartbeat / Liveness",
    "D": "Sequencing / Gating",
    "E": "Persistence / Recovery",
}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)
    return json.loads(config_path.read_text(encoding="utf-8"))


def build_context(cfg: dict) -> TestContext:
    root = Path(cfg["bridge_root"])
    logs_dir = root / "logs"
    state_dir = root / "state"

    return TestContext(
        bridge_root=root,
        inbox=root / "inbox",
        archive=root / "archive",
        rejected=root / "rejected",
        outbox=root / "outbox",
        templates=root / "templates",
        logs=logs_dir,
        state=state_dir,
        log_file=logs_dir / cfg.get("log_filename", "execution_shell.log"),
        state_file=state_dir / cfg.get("state_filename", "shell_state.json"),
        processed_ids_file=state_dir / cfg.get("processed_ids_filename", "processed_ids.log"),
        assertion_timeout=float(cfg.get("assertion_timeout_seconds", 10.0)),
        heartbeat_timeout_seconds=int(cfg.get("heartbeat_timeout_seconds", 20)),
        stale_signal_seconds=int(cfg.get("stale_signal_seconds", 8)),
        instrument=cfg.get("instrument", "NQ 06-26"),
    )


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def preflight(ctx: TestContext) -> bool:
    """Check that bridge directories and log file are accessible."""
    ok = True
    checks = [
        (ctx.inbox, "inbox"),
        (ctx.archive, "archive"),
        (ctx.rejected, "rejected"),
        (ctx.outbox, "outbox"),
        (ctx.logs, "logs"),
        (ctx.state, "state"),
    ]
    print("Pre-flight checks:")
    for path, label in checks:
        exists = path.exists()
        status = "ok" if exists else "x MISSING"
        print(f"  {status}  {label}: {path}")
        if not exists:
            ok = False

    # Log file may not exist yet (created by NT8 on first log write) — warn only.
    if not ctx.log_file.exists():
        print(f"  WARN  Log file not yet present: {ctx.log_file}")
        print("     The shell must have written at least one log entry before tests run.")
        ok = False
    else:
        print(f"  ok  log file: {ctx.log_file}")

    print()
    return ok


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_suite(
    ctx: TestContext,
    groups: list[str],
    skip_groups: list[str],
    report_dir: Path,
) -> int:
    """Run selected groups in order. Returns exit code (0=all pass)."""
    report_writer.print_suite_header()

    all_results: list[TestResult] = []
    active_groups = [g for g in groups if g not in skip_groups]

    for group_key in active_groups:
        tests = GROUP_MAP.get(group_key, [])
        if not tests:
            continue

        report_writer.print_category_header(GROUP_LABELS.get(group_key, group_key))

        for test_case in tests:
            try:
                result = test_case.run(ctx)
            except Exception as exc:
                result = TestResult(
                    test_id=test_case.test_id,
                    category=test_case.category,
                    scenario=test_case.scenario,
                    passed=False,
                    failure_diagnostics=f"Unhandled exception: {exc}",
                    elapsed_seconds=0.0,
                )
            all_results.append(result)
            report_writer.print_test_result(result)

            # Brief pause between tests for NT8 to settle.
            time.sleep(0.5)

    report_writer.print_suite_summary(all_results)

    # Write reports.
    now = time.strftime("%Y%m%d_%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"bridge_test_results_{now}.json"
    md_path = report_dir / f"bridge_test_results_{now}.md"
    report_writer.write_json_report(all_results, json_path)
    report_writer.write_markdown_report(all_results, md_path)

    failed = sum(1 for r in all_results if not r.passed and not r.skipped)
    return 1 if failed > 0 else 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="TaFoundationExecutionShell automated bridge test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--config",
        default="test_config.json",
        help="Path to test_config.json (default: %(default)s)",
    )
    p.add_argument(
        "--groups",
        nargs="+",
        choices=list(GROUP_MAP.keys()),
        default=list(GROUP_MAP.keys()),
        help="Groups to run (default: all). Groups run in order A B C D E.",
    )
    p.add_argument(
        "--skip-heartbeat",
        action="store_true",
        help="Skip Group C (heartbeat tests). Heartbeat tests take 30-50s and disable intake.",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List all test IDs and exit.",
    )
    p.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip pre-flight directory checks.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list:
        for group_key, tests in GROUP_MAP.items():
            print(f"\n[{group_key}] {GROUP_LABELS[group_key]}")
            for t in tests:
                print(f"  {t.test_id}: {t.scenario}")
        sys.exit(0)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = _SUITE_DIR / config_path

    cfg = load_config(config_path)

    ctx = build_context(cfg)

    if not args.no_preflight:
        ok = preflight(ctx)
        if not ok:
            print("Pre-flight failed. Ensure NT8 strategy is running and configured correctly.")
            print("Use --no-preflight to skip this check.\n")
            sys.exit(2)

    skip_groups = list(cfg.get("skip_groups", []))
    if args.skip_heartbeat and "C" not in skip_groups:
        skip_groups.append("C")

    report_dir = _SUITE_DIR / cfg.get("report_output_dir", "test_reports")

    # Groups run in fixed order for correct state sequencing.
    ordered_groups = [g for g in ["A", "B", "D", "E", "C"] if g in args.groups]

    exit_code = run_suite(ctx, ordered_groups, skip_groups, report_dir)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
