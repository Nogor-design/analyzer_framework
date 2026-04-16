"""
Group B — DryRun State Machine  (B-01 .. B-05)

Tests that the shell's internal state machine gates entries and management
commands correctly when DryRunMode=true.

DryRun-specific mechanics:
- ENTER_LONG/SHORT: sets shellMode=InPosition but Position is always Flat
- Gate check for ENTER_*: uses shellThinksInTrade (shellMode)
- Gate check for TAKE_PARTIAL/MOVE_STOP: uses Position.MarketPosition (always Flat)
  -> both are always gate-rejected with "no open position" in DryRun
- EXIT_ALL: DryRunExit resets shellMode=Idle regardless of current state
"""
from __future__ import annotations

import time

from bridge_test_harness import message_factory as mf
from bridge_test_harness.assertions import (
    assert_file_in_archive,
    assert_file_in_rejected,
    assert_log_event_for_msg,
    assert_log_fragment,
    assert_outbox_event,
    assert_state_field,
)
from bridge_test_harness.bridge_io import inject_message
from bridge_test_harness.test_cases.base import BaseTestCase, TestContext, TestResult


class TestB01EntryAccepted(BaseTestCase):
    """B-01: Flat -> ENTER_LONG -> DryRun shellMode becomes InPosition."""
    test_id = "B-01"
    category = "DryRun State Machine"
    scenario = "Flat -> ENTER_LONG -> DRYRUN_ENTRY -> shellMode=InPosition"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        # Ensure we start Idle.
        self._send_exit_all(ctx, wait=1.5)
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        entry = mf.base_enter_long(instrument=ctx.instrument)
        mid = entry["message_id"]
        inject_message(ctx.inbox, entry)

        assertions = [
            assert_log_event_for_msg(ctx.log_file, "DRYRUN_ENTRY", mid, since_line=lb, timeout=ctx.assertion_timeout),
            assert_state_field(ctx.state_file, "shell_mode", "InPosition", timeout=ctx.assertion_timeout),
        ]

        # Leave InPosition for B-02
        return self._evaluate(assertions, t0)


class TestB02RepeatedEntryRejected(BaseTestCase):
    """B-02: Second ENTER_LONG while InPosition is gate-rejected 'already in position'."""
    test_id = "B-02"
    category = "DryRun State Machine"
    scenario = "InPosition -> ENTER_LONG -> gate rejected 'already in position'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        # Setup: ensure InPosition.
        setup_id = self._ensure_in_position(ctx, lb)
        if not setup_id:
            return self._skip("Setup failed: could not get shell to InPosition")

        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        # Now send another ENTER_LONG.
        entry2 = mf.base_enter_long(instrument=ctx.instrument)
        mid2 = entry2["message_id"]
        inject_message(ctx.inbox, entry2)

        assertions = [
            # File is archived (passes ValidateInstruction) but gate-rejected in DrainQueue.
            assert_file_in_archive(ctx.archive, mid2, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "already in position", since_line=lb2, timeout=ctx.assertion_timeout),
            assert_outbox_event(ctx.outbox, "REJECTED", since_count=ob2, timeout=ctx.assertion_timeout),
        ]

        # Leave InPosition for B-03
        return self._evaluate(assertions, t0)


class TestB03ExitAllResetsState(BaseTestCase):
    """B-03: EXIT_ALL while InPosition -> DryRun exit -> shellMode=Idle."""
    test_id = "B-03"
    category = "DryRun State Machine"
    scenario = "InPosition -> EXIT_ALL -> DRYRUN_EXIT -> shellMode=Idle"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        # Setup: ensure InPosition.
        setup_id = self._ensure_in_position(ctx, lb)
        if not setup_id:
            return self._skip("Setup failed: could not get shell to InPosition")

        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        exit_msg = mf.exit_all(instrument=ctx.instrument)
        mid = exit_msg["message_id"]
        inject_message(ctx.inbox, exit_msg)

        assertions = [
            assert_log_event_for_msg(ctx.log_file, "DRYRUN_EXIT", mid, since_line=lb2,
                                     timeout=ctx.assertion_timeout),
            assert_state_field(ctx.state_file, "shell_mode", "Idle", timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


class TestB04TakePartialNoPosition(BaseTestCase):
    """B-04: TAKE_PARTIAL with no real position is gate-rejected 'no open position'.

    In DryRun mode Position.MarketPosition is always Flat, so even when
    shellMode=InPosition, TAKE_PARTIAL is gated by the actual-position check.
    """
    test_id = "B-04"
    category = "DryRun State Machine"
    scenario = "DryRun InPosition -> TAKE_PARTIAL -> gate rejected 'no open position'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        # Ensure InPosition (DryRun)
        setup_id = self._ensure_in_position(ctx, lb)
        if not setup_id:
            return self._skip("Setup failed: could not get shell to InPosition")

        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        partial = mf.take_partial(quantity=1, instrument=ctx.instrument)
        mid = partial["message_id"]
        inject_message(ctx.inbox, partial)

        assertions = [
            # TAKE_PARTIAL passes ValidateInstruction so it gets archived.
            assert_file_in_archive(ctx.archive, mid, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "no open position", since_line=lb2, timeout=ctx.assertion_timeout),
            assert_outbox_event(ctx.outbox, "REJECTED", since_count=ob2, timeout=ctx.assertion_timeout),
        ]

        self._send_exit_all(ctx)
        return self._evaluate(assertions, t0)


class TestB05MoveStopNoPosition(BaseTestCase):
    """B-05: MOVE_STOP (valid price) with no real position is gate-rejected.

    The message passes ValidateInstruction (stop_price is present and positive),
    gets archived, then CanExecuteInstructionNow rejects it 'no open position'.
    """
    test_id = "B-05"
    category = "DryRun State Machine"
    scenario = "DryRun InPosition -> MOVE_STOP valid price -> gate rejected 'no open position'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        setup_id = self._ensure_in_position(ctx, lb)
        if not setup_id:
            return self._skip("Setup failed: could not get shell to InPosition")

        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        mv = mf.move_stop(stop_price=19000.0, instrument=ctx.instrument)
        mid = mv["message_id"]
        inject_message(ctx.inbox, mv)

        assertions = [
            assert_file_in_archive(ctx.archive, mid, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "no open position", since_line=lb2, timeout=ctx.assertion_timeout),
            assert_outbox_event(ctx.outbox, "REJECTED", since_count=ob2, timeout=ctx.assertion_timeout),
        ]

        self._send_exit_all(ctx)
        return self._evaluate(assertions, t0)


ALL_TESTS: list[BaseTestCase] = [
    TestB01EntryAccepted(),
    TestB02RepeatedEntryRejected(),
    TestB03ExitAllResetsState(),
    TestB04TakePartialNoPosition(),
    TestB05MoveStopNoPosition(),
]
