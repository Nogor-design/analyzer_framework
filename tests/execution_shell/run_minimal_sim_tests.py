#!/usr/bin/env python3
"""
Minimal SIM Readiness Validation
=================================
Runs the 6 highest-value Phase 2 SIM tests only.

Answers:
  1. Does ENTER_LONG submit and fill?
  2. Does the protective stop attach after fill?
  3. Does a valid MOVE_STOP update the stop?
  4. Is an unsafe MOVE_STOP rejected?
  5. Does EXIT_ALL flatten the position?
  6. Is restart while flat safe (SHELL_READY mode=Idle, ENTER accepted)?

Usage
-----
    cd tests/execution_shell
    python run_minimal_sim_tests.py
    python run_minimal_sim_tests.py --config test_config.json
    python run_minimal_sim_tests.py --no-preflight

Prerequisites
-------------
  - NT8 running with TaFoundationExecutionShell, DryRunMode=false, Sim101 account
  - EnableOutboxEvents=true, bridge dirs pointing at bridge_root in test_config.json
  - Chart receiving live or replay ticks
  - Sim101 starts FLAT
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

from bridge_test_harness.test_cases.base import TestContext
from bridge_test_harness.test_cases_phase2.base_phase2 import Phase2Config, AutomationLevel
from bridge_test_harness.test_cases_phase2 import (
    p2_a_entry_fill,
    p2_b_stop_mgmt,
    p2_c_exit_mgmt,
    p2_e_recovery,
)

# ── The 6 tests that must pass before SIM-readiness is declared ──────────────
#
#   P2-A02  ENTER_LONG fills
#   P2-B01  protective stop attaches after fill
#   P2-B02  valid MOVE_STOP accepted
#   P2-B03  unsafe MOVE_STOP rejected
#   P2-C01  EXIT_ALL flattens position
#   P2-E05  restart while flat: new ENTER_LONG accepted (no phantom orders)
#
_CRITICAL_IDS = ["P2-A02", "P2-B01", "P2-B02", "P2-B03", "P2-C01", "P2-E05"]

# Build lookup from ALL phase 2 tests
_ALL_PHASE2 = (
    p2_a_entry_fill.ALL_TESTS
    + p2_b_stop_mgmt.ALL_TESTS
    + p2_c_exit_mgmt.ALL_TESTS
    + p2_e_recovery.ALL_TESTS
)
CRITICAL_TESTS = [t for t in _ALL_PHASE2 if t.test_id in _CRITICAL_IDS]
# Preserve the explicit run order above
CRITICAL_TESTS.sort(key=lambda t: _CRITICAL_IDS.index(t.test_id))


# ── Config / context helpers (mirrors run_phase2_tests.py) ───────────────────

def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}")
        sys.exit(1)
    return json.loads(config_path.read_text(encoding="utf-8"))


def _build_context(cfg: dict) -> TestContext:
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


def _build_p2cfg(cfg: dict) -> Phase2Config:
    return Phase2Config.from_dict(cfg.get("phase2", {}))


# ── Preflight ─────────────────────────────────────────────────────────────────

def _preflight(ctx: TestContext) -> bool:
    ok = True
    print("Pre-flight checks:")
    for path, label in [
        (ctx.inbox,   "inbox"),
        (ctx.outbox,  "outbox"),
        (ctx.archive, "archive"),
        (ctx.logs,    "logs"),
    ]:
        status = "  ok " if path.exists() else "  MISSING"
        print(f"{status}  {label}: {path}")
        if not path.exists():
            ok = False
    if not ctx.log_file.exists():
        print(f"  WARN  log file not yet present: {ctx.log_file}")
        ok = False
    else:
        print(f"  ok   log file: {ctx.log_file}")
    print()
    return ok


# ── Pretty output ─────────────────────────────────────────────────────────────

_W = 70

def _header():
    print("=" * _W)
    print("  MINIMAL SIM READINESS — 6-test critical path")
    print("=" * _W)
    print()

def _print_result(seq: int, total: int, test_id: str, scenario: str, passed: bool,
                  skipped: bool, elapsed: float, diag: str, skip_reason: str = ""):
    if skipped:
        icon = "SKIP"
    elif passed:
        icon = "PASS"
    else:
        icon = "FAIL"
    label = f"[{seq}/{total}] {test_id}"
    print(f"  {icon}  {label}  ({elapsed:.1f}s)")
    if skipped and skip_reason:
        print(f"         skip: {skip_reason}")
    if not passed and not skipped and diag:
        # Indent diagnostic lines
        for line in diag.splitlines():
            print(f"         {line}")

def _summary(results: list[dict]) -> int:
    passed  = sum(1 for r in results if r["passed"] and not r["skipped"])
    skipped = sum(1 for r in results if r["skipped"])
    failed  = sum(1 for r in results if not r["passed"] and not r["skipped"])

    print()
    print("─" * _W)
    print(f"  Results:  {passed} PASS  |  {failed} FAIL  |  {skipped} SKIP  "
          f"(of {len(results)} critical tests)")
    print("─" * _W)

    if failed == 0 and skipped == 0:
        print("  ✓  ALL CRITICAL TESTS PASSED — SIM readiness confirmed")
    elif failed == 0:
        print(f"  ~  {skipped} test(s) SKIPPED — re-check preconditions then re-run")
        for r in results:
            if r["skipped"] and r.get("skip_reason"):
                print(f"     • {r['test_id']}: {r['skip_reason']}")
    else:
        print(f"  ✗  {failed} CRITICAL TEST(S) FAILED — NOT SIM-ready")
        for r in results:
            if not r["passed"] and not r["skipped"]:
                print(f"     • {r['test_id']}: {r['diag']}")
    print()
    return 1 if failed > 0 else 0


# ── Runner ────────────────────────────────────────────────────────────────────

def run(ctx: TestContext, p2cfg: Phase2Config) -> int:
    _header()
    results = []
    total = len(CRITICAL_TESTS)

    for seq, test_case in enumerate(CRITICAL_TESTS, start=1):
        t0 = time.monotonic()
        try:
            p2_result = test_case.run(ctx, p2cfg)
            tr = p2_result.to_test_result()
        except Exception as exc:
            from bridge_test_harness.test_cases.base import TestResult
            tr = TestResult(
                test_id=test_case.test_id,
                category=test_case.category,
                scenario=test_case.scenario,
                passed=False,
                failure_diagnostics=f"Unhandled exception: {exc}",
                elapsed_seconds=0.0,
            )
        elapsed = time.monotonic() - t0

        _print_result(
            seq, total,
            tr.test_id,
            tr.scenario,
            passed=tr.passed,
            skipped=getattr(tr, "skipped", False),
            elapsed=elapsed,
            diag=getattr(tr, "failure_diagnostics", "") or "",
            skip_reason=getattr(tr, "skip_reason", "") or "",
        )
        results.append({
            "test_id": tr.test_id,
            "passed": tr.passed,
            "skipped": getattr(tr, "skipped", False),
            "diag": getattr(tr, "failure_diagnostics", "") or "",
            "skip_reason": getattr(tr, "skip_reason", "") or "",
        })
        time.sleep(0.3)

    return _summary(results)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Minimal SIM readiness validation (6 critical tests)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config", default="test_config.json",
                   help="Path to test_config.json (default: test_config.json)")
    p.add_argument("--no-preflight", action="store_true",
                   help="Skip directory pre-flight checks")
    return p.parse_args()


def main():
    args = _parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = _SUITE_DIR / config_path

    cfg = _load_config(config_path)
    ctx = _build_context(cfg)
    p2cfg = _build_p2cfg(cfg)

    if not args.no_preflight:
        ok = _preflight(ctx)
        if not ok:
            print("Pre-flight failed. Check that NT8 strategy is running and bridge dirs exist.")
            print("Use --no-preflight to skip.\n")
            sys.exit(2)

    sys.exit(run(ctx, p2cfg))


if __name__ == "__main__":
    main()
