"""
Group A — Contract / Input Validation  (A-01 .. A-07)

Tests that every invalid payload is caught by ValidateInstruction() before
the file is ever queued.  All invalid files should move to the rejected
directory; accepted files should move to archive.
"""
from __future__ import annotations

import time
import uuid

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


class TestA01ValidEntry(BaseTestCase):
    """A-01: Valid ENTER_LONG is accepted, archived, DryRun entry logged."""
    test_id = "A-01"
    category = "Contract / Input Validation"
    scenario = "Valid ENTER_LONG accepted -> archive + DRYRUN_ENTRY + outbox ACCEPTED + state InPosition"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        msg = mf.base_enter_long(instrument=ctx.instrument)
        mid = msg["message_id"]
        inject_message(ctx.inbox, msg)

        assertions = [
            assert_file_in_archive(ctx.archive, mid, timeout=ctx.assertion_timeout),
            assert_log_event_for_msg(ctx.log_file, "ACCEPT", mid, since_line=lb, timeout=ctx.assertion_timeout),
            assert_log_event_for_msg(ctx.log_file, "DRYRUN_ENTRY", mid, since_line=lb, timeout=ctx.assertion_timeout),
            assert_outbox_event(ctx.outbox, "ACCEPTED", msg_id=mid, since_count=ob, timeout=ctx.assertion_timeout),
            assert_state_field(ctx.state_file, "shell_mode", "InPosition", timeout=ctx.assertion_timeout),
        ]

        # Reset for subsequent tests
        self._send_exit_all(ctx)

        return self._evaluate(assertions, t0)


class TestA02MissingMessageId(BaseTestCase):
    """A-02: JSON with no message_id field is rejected."""
    test_id = "A-02"
    category = "Contract / Input Validation"
    scenario = "Missing message_id -> rejected directory + log 'missing message_id'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        payload = mf.missing_message_id(instrument=ctx.instrument)
        # We control the filename since the payload has no message_id.
        file_id = f"a02_{uuid.uuid4().hex[:8]}"
        inject_message(ctx.inbox, payload, msg_id=file_id)

        assertions = [
            assert_file_in_rejected(ctx.rejected, file_id, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "missing message_id", since_line=lb, timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


class TestA03InvalidTimestamp(BaseTestCase):
    """A-03: Non-ISO timestamp is rejected."""
    test_id = "A-03"
    category = "Contract / Input Validation"
    scenario = "Invalid timestamp -> rejected + log 'invalid timestamp'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        msg = mf.invalid_timestamp(instrument=ctx.instrument)
        mid = msg["message_id"]
        inject_message(ctx.inbox, msg)

        assertions = [
            assert_file_in_rejected(ctx.rejected, mid, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "invalid timestamp", since_line=lb, timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


class TestA04FutureTimestamp(BaseTestCase):
    """A-04: Timestamp > now + 30s is rejected as future timestamp."""
    test_id = "A-04"
    category = "Contract / Input Validation"
    scenario = "Future timestamp (> now+30s) -> rejected + log 'future timestamp'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        msg = mf.future_timestamp(seconds_ahead=90, instrument=ctx.instrument)
        mid = msg["message_id"]
        inject_message(ctx.inbox, msg)

        assertions = [
            assert_file_in_rejected(ctx.rejected, mid, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "future timestamp", since_line=lb, timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


class TestA05DuplicateMessageId(BaseTestCase):
    """A-05: Second delivery of the same message_id is rejected as duplicate."""
    test_id = "A-05"
    category = "Contract / Input Validation"
    scenario = "Duplicate message_id replay -> rejected + log 'duplicate message_id'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        # Use a HEARTBEAT as the first (accepted) message to avoid state changes.
        hb = mf.heartbeat(instrument=ctx.instrument)
        hb_id = hb["message_id"]
        inject_message(ctx.inbox, hb)

        # Wait for it to be accepted and archived.
        from bridge_test_harness import log_parser
        log_parser.find_event_for_msg(
            ctx.log_file, "ACCEPT", hb_id,
            since_line=lb, timeout_seconds=ctx.assertion_timeout
        )
        time.sleep(0.5)  # small buffer before replay

        # Replay the exact same payload under a different filename.
        replay_file_id = f"a05_replay_{uuid.uuid4().hex[:8]}"
        inject_message(ctx.inbox, dict(hb), msg_id=replay_file_id)

        lb2 = log_parser.line_count(ctx.log_file)
        assertions = [
            assert_file_in_archive(ctx.archive, hb_id, timeout=ctx.assertion_timeout),
            assert_file_in_rejected(ctx.rejected, replay_file_id, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "duplicate message_id", since_line=lb, timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


class TestA06MissingTemplateName(BaseTestCase):
    """A-06: ENTER_SHORT without template_name is rejected."""
    test_id = "A-06"
    category = "Contract / Input Validation"
    scenario = "ENTER_SHORT with null template_name -> rejected + log 'missing template_name'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        msg = mf.missing_template_name(instrument=ctx.instrument)
        mid = msg["message_id"]
        inject_message(ctx.inbox, msg)

        assertions = [
            assert_file_in_rejected(ctx.rejected, mid, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "missing template_name", since_line=lb, timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


class TestA07MoveStopMissingPrice(BaseTestCase):
    """A-07: MOVE_STOP with null stop_price is rejected at ValidateInstruction."""
    test_id = "A-07"
    category = "Contract / Input Validation"
    scenario = "MOVE_STOP null stop_price -> rejected + log 'invalid stop price'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        msg = mf.move_stop_missing_price(instrument=ctx.instrument)
        mid = msg["message_id"]
        inject_message(ctx.inbox, msg)

        assertions = [
            assert_file_in_rejected(ctx.rejected, mid, timeout=ctx.assertion_timeout),
            assert_log_fragment(ctx.log_file, "invalid stop price", since_line=lb, timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


ALL_TESTS: list[BaseTestCase] = [
    TestA01ValidEntry(),
    TestA02MissingMessageId(),
    TestA03InvalidTimestamp(),
    TestA04FutureTimestamp(),
    TestA05DuplicateMessageId(),
    TestA06MissingTemplateName(),
    TestA07MoveStopMissingPrice(),
]
