#!/usr/bin/env python3
"""
TaFoundationExecutionShell — Phase 2 SIM Account Test Runner
=============================================================

Phase 2 tests validate real managed-order behaviour on a Sim101 account
with DryRunMode=false.  They require live or replay market ticks.

Prerequisites
-------------
1. NinjaTrader 8 running with TaFoundationExecutionShell on a chart.
2. Strategy parameters:
   - DryRunMode          = false
   - Account             = Sim101   (or as set in test_config.json phase2.sim_account)
   - EnableOutboxEvents  = true
   - InboxDirectory / ArchiveDirectory / RejectDirectory / OutboxDirectory / LogFilePath
     all pointing to the bridge_root in test_config.json
3. Phase 2 shell changes deployed:
   - SHELL_READY outbox event on DataLoaded
   - Enriched FILLED event (remaining_qty, pos_side)
   - Enriched STOP_ATTACHED event (pending_stop_price)
   - STOP_WORKING outbox event in OnOrderUpdate
4. Chart is receiving live ticks or running market replay.
5. Sim101 starts FLAT (no open positions).

Automation tiers
----------------
FULLY_AUTOMATED   No human involvement.
SEMI_AUTOMATED    Pauses at checkpoints — user presses Enter to confirm.
MANUAL_REQUIRED   Procedure printed; always SKIPPED by runner.

Usage
-----
    python run_phase2_tests.py
    python run_phase2_tests.py --config test_config.json
    python run_phase2_tests.py --groups A B C       # subset of groups
    python run_phase2_tests.py --no-checkpoints     # skip human checkpoint prompts
    python run_phase2_tests.py --list               # list all Phase 2 test IDs
    python run_phase2_tests.py --no-preflight       # skip directory checks
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_SUITE_DIR = Path(__file__).parent
if str(_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUITE_DIR))

from bridge_test_harness.test_cases.base import TestContext, TestResult
from bridge_test_harness.test_cases_phase2.base_phase2 import Phase2Config
from bridge_test_harness.test_cases_phase2 import (
    p2_a_entry_fill,
    p2_b_stop_mgmt,
    p2_c_exit_mgmt,
    p2_d_conflict,
    p2_e_recovery,
    p2_f_heartbeat,
    p2_g_cleanup,
)
from bridge_test_harness import report_writer


# ---------------------------------------------------------------------------
# Test registry
# ---------------------------------------------------------------------------

GROUP_MAP: dict[str, list] = {
    "A": p2_a_entry_fill.ALL_TESTS,
    "B": p2_b_stop_mgmt.ALL_TESTS,
    "C": p2_c_exit_mgmt.ALL_TESTS,
    "D": p2_d_conflict.ALL_TESTS,
    "E": p2_e_recovery.ALL_TESTS,
    "F": p2_f_heartbeat.ALL_TESTS,
    "G": p2_g_cleanup.ALL_TESTS,
}

GROUP_LABELS: dict[str, str] = {
    "A": "Entry / Fill Detection",
    "B": "Stop Attachment & Movement",
    "C": "Exit Management",
    "D": "Conflict / Sequencing",
    "E": "Restart & Recovery",
    "F": "Heartbeat / Liveness (run LAST)",
    "G": "Cleanup / Teardown",
}

# Fixed execution order: G (cleanup) always last; F (heartbeat) second-to-last
_ORDERED = ["E", "A", "B", "C", "D", "F", "G"]


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


def build_phase2_config(cfg: dict, no_checkpoints: bool) -> Phase2Config:
    p2_raw = cfg.get("phase2", {})
    p2cfg = Phase2Config.from_dict(p2_raw)
    if no_checkpoints:
        p2cfg.show_checkpoints = False
    return p2cfg


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def preflight(ctx: TestContext) -> bool:
    ok = True
    print("Pre-flight checks:")
    checks = [
        (ctx.inbox, "inbox"),
        (ctx.archive, "archive"),
        (ctx.rejected, "rejected"),
        (ctx.outbox, "outbox"),
        (ctx.logs, "logs"),
        (ctx.state, "state"),
    ]
    for path, label in checks:
        exists = path.exists()
        status = "ok" if exists else "x MISSING"
        print(f"  {status}  {label}: {path}")
        if not exists:
            ok = False

    if not ctx.log_file.exists():
        print(f"  WARN  Log file not yet present: {ctx.log_file}")
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
    p2cfg: Phase2Config,
    groups: list[str],
    skip_groups: list[str],
    report_dir: Path,
) -> int:
    report_writer.print_phase2_suite_header()

    all_results: list[TestResult] = []
    active_groups = [g for g in groups if g not in skip_groups]

    for group_key in active_groups:
        tests = GROUP_MAP.get(group_key, [])
        if not tests:
            continue

        report_writer.print_category_header(GROUP_LABELS.get(group_key, group_key))

        for test_case in tests:
            try:
                p2_result = test_case.run(ctx, p2cfg)
                result = p2_result.to_test_result()
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
            time.sleep(0.5)

    report_writer.print_suite_summary(all_results)

    now = time.strftime("%Y%m%d_%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"phase2_results_{now}.json"
    md_path = report_dir / f"phase2_results_{now}.md"
    report_writer.write_json_report(all_results, json_path)
    report_writer.write_markdown_report(all_results, md_path)

    failed = sum(1 for r in all_results if not r.passed and not r.skipped)
    return 1 if failed > 0 else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="TaFoundationExecutionShell Phase 2 SIM account test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config", default="test_config.json")
    p.add_argument(
        "--groups", nargs="+", choices=list(GROUP_MAP.keys()),
        default=list(GROUP_MAP.keys()),
        help="Groups to run (default: all). Fixed order: E A B C D F G.",
    )
    p.add_argument(
        "--no-checkpoints", action="store_true",
        help="Disable interactive user checkpoints (all SEMI_AUTOMATED steps auto-pass).",
    )
    p.add_argument("--no-preflight", action="store_true")
    p.add_argument("--list", action="store_true", help="List all Phase 2 test IDs and exit.")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list:
        for group_key in _ORDERED:
            tests = GROUP_MAP.get(group_key, [])
            if not tests:
                continue
            print(f"\n[{group_key}] {GROUP_LABELS[group_key]}")
            for t in tests:
                level = t.automation_level.value
                print(f"  {t.test_id}: [{level}] {t.scenario}")
        sys.exit(0)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = _SUITE_DIR / config_path

    cfg = load_config(config_path)
    ctx = build_context(cfg)
    p2cfg = build_phase2_config(cfg, args.no_checkpoints)

    if not args.no_preflight:
        ok = preflight(ctx)
        if not ok:
            print("Pre-flight failed. Ensure NT8 strategy is running correctly.")
            print("Use --no-preflight to skip.\n")
            sys.exit(2)

    skip_groups = list(cfg.get("skip_groups", []))
    report_dir = _SUITE_DIR / cfg.get("report_output_dir", "test_reports")

    ordered_groups = [g for g in _ORDERED if g in args.groups]
    exit_code = run_suite(ctx, p2cfg, ordered_groups, skip_groups, report_dir)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
