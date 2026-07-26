"""
Group D — Sequencing / Gating  (D-01 .. D-03)

Tests that the shell processes burst message sequences deterministically.

Key gating rules in DryRun mode:
- ENTER while shellThinksInTrade -> REJECT_GATE "already in position"
- TAKE_PARTIAL / MOVE_STOP while Position.Flat -> REJECT_GATE "no open position"
- EXIT_ALL is always executable (resets state)
- HEARTBEAT / FLATTEN_AND_DISABLE bypass all gates
"""
from __future__ import annotations

import time

from bridge_test_harness import message_factory as mf
from bridge_test_harness.assertions import (
    assert_file_in_archive,
    assert_log_event_for_msg,
    assert_log_fragment,
    assert_outbox_event,
    assert_state_field,
)
from bridge_test_harness.bridge_io import inject_message
from bridge_test_harness.test_cases.base import BaseTestCase, TestContext, TestResult


class TestD01BurstEntryPartialMoveStop(BaseTestCase):
    """D-01: ENTER_LONG -> TAKE_PARTIAL -> MOVE_STOP burst.

    Expected in DryRun mode:
    - ENTER_LONG: accepted -> DRYRUN_ENTRY
    - TAKE_PARTIAL: archived but gate-rejected 'no open position'
    - MOVE_STOP: archived but gate-rejected 'no open position'

    (In DryRun, Position is always Flat so management commands are always gated.)
    """
    test_id = "D-01"
    category = "Sequencing / Gating"
    scenario = "Burst ENTER_LONG -> TAKE_PARTIAL -> MOVE_STOP: partials/move-stop gate-rejected"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        # Reset to Idle first.
        self._send_exit_all(ctx, wait=1.5)

        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        entry = mf.base_enter_long(instrument=ctx.instrument)
        partial = mf.take_partial(quantity=1, instrument=ctx.instrument)
        movstop = mf.move_stop(stop_price=19000.0, instrument=ctx.instrument)

        inject_message(ctx.inbox, entry)
        inject_message(ctx.inbox, partial)
        inject_message(ctx.inbox, movstop)

        entry_id = entry["message_id"]
        partial_id = partial["message_id"]
        movstop_id = movstop["message_id"]

        assertions = [
            # Entry accepted and executed.
            assert_log_event_for_msg(ctx.log_file, "DRYRUN_ENTRY", entry_id, since_line=lb, timeout=ctx.assertion_timeout),
            # Partial archived but gate-rejected.
            assert_file_in_archive(ctx.archive, partial_id, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "no open position", since_line=lb, timeout=ctx.assertion_timeout),
            # MoveStop archived but gate-rejected.
            assert_file_in_archive(ctx.archive, movstop_id, timeout=ctx.assertion_timeout),
        ]

        self._send_exit_all(ctx)
        return self._evaluate(assertions, t0)


class TestD02BurstEntryExitMoveStop(BaseTestCase):
    """D-02: ENTER_LONG -> EXIT_ALL -> MOVE_STOP.

    Expected:
    - ENTER_LONG: DRYRUN_ENTRY -> InPosition
    - EXIT_ALL: DRYRUN_EXIT -> Idle
    - MOVE_STOP: archived but gate-rejected 'no open position'
    """
    test_id = "D-02"
    category = "Sequencing / Gating"
    scenario = "ENTER_LONG -> EXIT_ALL -> MOVE_STOP: MOVE_STOP rejected after exit"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        self._send_exit_all(ctx, wait=1.5)

        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        entry = mf.base_enter_long(instrument=ctx.instrument)
        ex = mf.exit_all(instrument=ctx.instrument)
        movstop = mf.move_stop(stop_price=19000.0, instrument=ctx.instrument)

        inject_message(ctx.inbox, entry)
        inject_message(ctx.inbox, ex)
        inject_message(ctx.inbox, movstop)

        entry_id = entry["message_id"]
        ex_id = ex["message_id"]
        movstop_id = movstop["message_id"]

        assertions = [
            assert_log_event_for_msg(ctx.log_file, "DRYRUN_ENTRY", entry_id, since_line=lb, timeout=ctx.assertion_timeout),
            assert_log_event_for_msg(ctx.log_file, "DRYRUN_EXIT", ex_id, since_line=lb, timeout=ctx.assertion_timeout),
            # MOVE_STOP should be archived then gate-rejected.
            assert_file_in_archive(ctx.archive, movstop_id, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "no open position", since_line=lb, timeout=ctx.assertion_timeout),
            # Final state: Idle
            assert_state_field(ctx.state_file, "shell_mode", "Idle", timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


class TestD03OppositeEntryWhileInPosition(BaseTestCase):
    """D-03: ENTER_LONG then ENTER_SHORT while DryRun InPosition.

    Expected:
    - ENTER_LONG: accepted -> InPosition
    - ENTER_SHORT: archived but gate-rejected 'already in position'
    - No reversal, state stays InPosition.
    """
    test_id = "D-03"
    category = "Sequencing / Gating"
    scenario = "ENTER_LONG -> ENTER_SHORT: short entry gate-rejected 'already in position'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        self._send_exit_all(ctx, wait=1.5)

        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        long_entry = mf.base_enter_long(instrument=ctx.instrument)
        short_entry = mf.base_enter_short(instrument=ctx.instrument)

        inject_message(ctx.inbox, long_entry)
        inject_message(ctx.inbox, short_entry)

        long_id = long_entry["message_id"]
        short_id = short_entry["message_id"]

        assertions = [
            assert_log_event_for_msg(ctx.log_file, "DRYRUN_ENTRY", long_id, since_line=lb, timeout=ctx.assertion_timeout),
            assert_file_in_archive(ctx.archive, short_id, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "already in position", since_line=lb, timeout=ctx.assertion_timeout),
            assert_outbox_event(ctx.outbox, "REJECTED", since_count=ob, timeout=ctx.assertion_timeout),
            assert_state_field(ctx.state_file, "shell_mode", "InPosition", timeout=ctx.assertion_timeout),
        ]

        self._send_exit_all(ctx)
        return self._evaluate(assertions, t0)


ALL_TESTS: list[BaseTestCase] = [
    TestD01BurstEntryPartialMoveStop(),
    TestD02BurstEntryExitMoveStop(),
    TestD03OppositeEntryWhileInPosition(),
]
