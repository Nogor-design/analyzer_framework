"""
Base test case scaffold.

Every test case:
1. Captures a log baseline (line count before the test starts).
2. Captures an outbox baseline (event count before the test starts).
3. Injects messages and waits for expected outcomes.
4. Returns a TestResult with per-assertion detail.

TestContext is built once by the runner and passed to every test.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bridge_test_harness import bridge_io, log_parser, outbox_parser, message_factory as mf
from bridge_test_harness.assertions import AssertionResult


@dataclass
class TestContext:
    # Bridge directory paths
    bridge_root: Path
    inbox: Path
    archive: Path
    rejected: Path
    outbox: Path
    templates: Path
    logs: Path
    state: Path

    # Derived file paths
    log_file: Path
    state_file: Path
    processed_ids_file: Path

    # Timing / behaviour knobs (sourced from test_config.json)
    assertion_timeout: float = 10.0
    heartbeat_timeout_seconds: int = 20
    stale_signal_seconds: int = 8
    instrument: str = "NQ 06-26"

    def log_baseline(self) -> int:
        """Current line count of the log file — capture before each test."""
        return log_parser.line_count(self.log_file)

    def outbox_baseline(self) -> int:
        """Current count of outbox .evt.json files — capture before each test."""
        return outbox_parser.count_events(self.outbox)


@dataclass
class TestResult:
    test_id: str
    category: str
    scenario: str
    passed: bool
    assertions: list[AssertionResult] = field(default_factory=list)
    failure_diagnostics: str = ""
    elapsed_seconds: float = 0.0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def failed_assertions(self) -> list[AssertionResult]:
        return [a for a in self.assertions if not a.passed]

    @property
    def status_str(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"


class BaseTestCase:
    """Abstract base for all test cases."""

    test_id: str = "X-00"
    category: str = "uncategorised"
    scenario: str = "base test"

    def run(self, ctx: TestContext) -> TestResult:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Convenience result constructors
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        assertions: list[AssertionResult],
        start_time: float,
    ) -> TestResult:
        """Build a TestResult from a list of assertion results."""
        failed = [a for a in assertions if not a.passed]
        diagnostics = "\n".join(
            f"  [{a.label}] {a.detail}\n    Evidence: {a.evidence}"
            for a in failed
        )
        return TestResult(
            test_id=self.test_id,
            category=self.category,
            scenario=self.scenario,
            passed=len(failed) == 0,
            assertions=assertions,
            failure_diagnostics=diagnostics,
            elapsed_seconds=time.monotonic() - start_time,
        )

    def _skip(self, reason: str) -> TestResult:
        return TestResult(
            test_id=self.test_id,
            category=self.category,
            scenario=self.scenario,
            passed=True,
            skipped=True,
            skip_reason=reason,
        )

    # ------------------------------------------------------------------
    # Reset helpers
    # ------------------------------------------------------------------

    def _send_exit_all(self, ctx: TestContext, wait: float = 2.5) -> str:
        """
        Inject EXIT_ALL to attempt state reset to Idle.
        Returns the message_id of the injected EXIT_ALL.
        In DryRun mode this always logs DRYRUN_EXIT and sets shellMode=Idle.
        """
        reset_msg = mf.exit_all(instrument=ctx.instrument)
        msg_id = reset_msg["message_id"]
        bridge_io.inject_message(ctx.inbox, reset_msg)
        time.sleep(wait)
        return msg_id

    def _ensure_idle(self, ctx: TestContext, log_base: int) -> bool:
        """
        Send EXIT_ALL and wait for DRYRUN_EXIT in log.
        Returns True if shell acknowledged, False if timed out.
        """
        reset_msg = mf.exit_all(instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, reset_msg)
        rec = log_parser.find_event_type(
            ctx.log_file, "DRYRUN_EXIT", since_line=log_base, timeout_seconds=5.0
        )
        return rec is not None

    def _ensure_in_position(self, ctx: TestContext, log_base: int) -> Optional[str]:
        """
        Send ENTER_LONG and wait for DRYRUN_ENTRY.
        Returns msg_id if successful, None on failure.
        """
        entry = mf.base_enter_long(instrument=ctx.instrument)
        msg_id: str = entry["message_id"]
        bridge_io.inject_message(ctx.inbox, entry)
        rec = log_parser.find_event_for_msg(
            ctx.log_file, "DRYRUN_ENTRY", msg_id,
            since_line=log_base, timeout_seconds=ctx.assertion_timeout
        )
        return msg_id if rec else None
