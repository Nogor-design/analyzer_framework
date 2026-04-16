"""
Report writer for NT8 bridge test suite.

Produces:
1. Console output — colour-coded pass/fail per test.
2. JSON report    — structured results for machine consumption.
3. Markdown report — human-readable summary with evidence snippets.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from bridge_test_harness.test_cases.base import TestResult


# ---------------------------------------------------------------------------
# ANSI colour helpers (stripped on non-TTY)
# ---------------------------------------------------------------------------

def _supports_colour() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_GREEN = "\033[92m" if _supports_colour() else ""
_RED = "\033[91m" if _supports_colour() else ""
_YELLOW = "\033[93m" if _supports_colour() else ""
_CYAN = "\033[96m" if _supports_colour() else ""
_RESET = "\033[0m" if _supports_colour() else ""
_BOLD = "\033[1m" if _supports_colour() else ""


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def print_suite_header() -> None:
    print(f"\n{_BOLD}{_CYAN}{'='*70}{_RESET}")
    print(f"{_BOLD}{_CYAN}  TaFoundationExecutionShell — Automated Bridge Test Suite{_RESET}")
    print(f"{_CYAN}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{_RESET}")
    print(f"{_BOLD}{_CYAN}{'='*70}{_RESET}\n")


def print_phase2_suite_header() -> None:
    print(f"\n{_BOLD}{_CYAN}{'='*70}{_RESET}")
    print(f"{_BOLD}{_CYAN}  TaFoundationExecutionShell — Phase 2 SIM Account Tests{_RESET}")
    print(f"{_CYAN}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{_RESET}")
    print(f"{_CYAN}  Requires: DryRunMode=false, Sim101 account, live/replay ticks{_RESET}")
    print(f"{_BOLD}{_CYAN}{'='*70}{_RESET}\n")


def print_test_result(result: TestResult) -> None:
    if result.skipped:
        colour = _YELLOW
        status = "SKIP"
        suffix = f"  ({result.skip_reason})"
    elif result.passed:
        colour = _GREEN
        status = "PASS"
        suffix = f"  ({result.elapsed_seconds:.1f}s)"
    else:
        colour = _RED
        status = "FAIL"
        suffix = f"  ({result.elapsed_seconds:.1f}s)"

    print(f"  {colour}{status}{_RESET}  [{result.test_id}] {result.scenario[:70]}{suffix}")

    if not result.passed and not result.skipped:
        for a in result.failed_assertions:
            print(f"         {_RED}x{_RESET} {a.label}: {a.detail}")
            if a.evidence:
                # Indent evidence for readability.
                for line in a.evidence.splitlines()[:8]:
                    print(f"             {line}")


def print_category_header(category: str) -> None:
    print(f"\n{_BOLD}── {category} ──{_RESET}")


def print_suite_summary(results: Sequence[TestResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    elapsed = sum(r.elapsed_seconds for r in results)

    print(f"\n{_BOLD}{'='*70}{_RESET}")
    print(f"{_BOLD}  SUMMARY{_RESET}")
    print(f"  Total  : {total}")
    print(f"  {_GREEN}Passed : {passed}{_RESET}")
    if failed:
        print(f"  {_RED}Failed : {failed}{_RESET}")
    else:
        print(f"  Failed : {failed}")
    if skipped:
        print(f"  {_YELLOW}Skipped: {skipped}{_RESET}")
    print(f"  Time   : {elapsed:.1f}s")
    print(f"{_BOLD}{'='*70}{_RESET}\n")

    if failed:
        print(f"{_RED}  FAILED TESTS:{_RESET}")
        for r in results:
            if not r.passed and not r.skipped:
                print(f"    [{r.test_id}] {r.scenario}")
        print()


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------

def write_json_report(results: Sequence[TestResult], output_path: Path) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "TaFoundationExecutionShell Bridge Tests",
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed and not r.skipped),
            "failed": sum(1 for r in results if not r.passed and not r.skipped),
            "skipped": sum(1 for r in results if r.skipped),
            "elapsed_seconds": round(sum(r.elapsed_seconds for r in results), 2),
        },
        "tests": [_result_to_dict(r) for r in results],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  JSON report : {output_path}")


def _result_to_dict(r: TestResult) -> dict:
    return {
        "test_id": r.test_id,
        "category": r.category,
        "scenario": r.scenario,
        "status": r.status_str,
        "elapsed_seconds": round(r.elapsed_seconds, 2),
        "skip_reason": r.skip_reason if r.skipped else None,
        "failure_diagnostics": r.failure_diagnostics if not r.passed else None,
        "assertions": [
            {
                "label": a.label,
                "passed": a.passed,
                "detail": a.detail if not a.passed else None,
                "evidence": a.evidence if not a.passed else None,
            }
            for a in r.assertions
        ],
    }


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def write_markdown_report(results: Sequence[TestResult], output_path: Path) -> None:
    lines: list[str] = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(results)
    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)

    lines += [
        "# TaFoundationExecutionShell — Bridge Test Report",
        "",
        f"**Generated:** {ts}  ",
        f"**Total:** {total}  **Passed:** {passed}  **Failed:** {failed}  **Skipped:** {skipped}",
        "",
    ]

    # Overall status banner.
    if failed == 0:
        lines.append("## [PASS] All automated tests passed")
    else:
        lines.append(f"## [FAIL] {failed} test(s) failed — see details below")
    lines.append("")

    # Per-category sections.
    categories: dict[str, list[TestResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    for cat, cat_results in categories.items():
        lines.append(f"## {cat}")
        lines.append("")
        lines.append("| ID | Scenario | Status | Time |")
        lines.append("|---|---|---|---|")
        for r in cat_results:
            status = r.status_str
            icon = "[PASS]" if r.passed else (">>️" if r.skipped else "[FAIL]")
            lines.append(
                f"| {r.test_id} | {r.scenario[:80]} | {icon} {status} | {r.elapsed_seconds:.1f}s |"
            )
        lines.append("")

        # Detail for failed tests.
        for r in cat_results:
            if not r.passed and not r.skipped:
                lines.append(f"### [FAIL] {r.test_id} — Failure Detail")
                lines.append("")
                for a in r.failed_assertions:
                    lines.append(f"**Assertion:** `{a.label}`  ")
                    lines.append(f"**Reason:** {a.detail}  ")
                    if a.evidence:
                        lines.append("**Evidence:**")
                        lines.append("```")
                        lines.extend(a.evidence.splitlines()[:20])
                        lines.append("```")
                    lines.append("")

    # Appendix: skipped tests.
    skipped_tests = [r for r in results if r.skipped]
    if skipped_tests:
        lines.append("## Skipped / Manual Tests")
        lines.append("")
        for r in skipped_tests:
            lines.append(f"- **{r.test_id}**: {r.skip_reason}")
        lines.append("")

    lines += [
        "---",
        "*Generated by `run_bridge_tests.py`*",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Markdown report: {output_path}")
