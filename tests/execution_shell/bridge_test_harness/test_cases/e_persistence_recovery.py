"""
Group E — Persistence & Recovery  (E-01 .. E-04)

E-01: State file written atomically after DryRun entry (active_template, shell_mode).
E-02: Processed IDs file updated with the message_id of an accepted message.
E-03: Corrupt state file — requires strategy restart, marked as manual/semi-automated.
E-04: Corrupt processed IDs file — requires strategy restart, marked as manual.

E-03 and E-04 produce setup artifacts that must be observed after a manual NT8
strategy restart.  The harness writes the corrupt files and prints instructions.
"""
from __future__ import annotations

import time

from bridge_test_harness import message_factory as mf
from bridge_test_harness.assertions import (
    assert_log_fragment,
    assert_processed_id_present,
    assert_state_field,
)
from bridge_test_harness.bridge_io import inject_message
from bridge_test_harness import log_parser
from bridge_test_harness.test_cases.base import BaseTestCase, TestContext, TestResult


class TestE01StateFileAfterDryRunEntry(BaseTestCase):
    """E-01: State file reflects shell_mode=InPosition and active_template after DryRun entry."""
    test_id = "E-01"
    category = "Persistence / Recovery"
    scenario = "DryRun ENTER_LONG -> state file has shell_mode=InPosition and active_template set"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        self._send_exit_all(ctx, wait=1.5)

        lb = ctx.log_baseline()

        entry = mf.base_enter_long(instrument=ctx.instrument)
        mid = entry["message_id"]
        template = entry.get("template_name", "")
        inject_message(ctx.inbox, entry)

        assertions = [
            assert_state_field(ctx.state_file, "shell_mode", "InPosition", timeout=ctx.assertion_timeout),
            assert_state_field(ctx.state_file, "active_template", template, timeout=ctx.assertion_timeout),
            assert_state_field(ctx.state_file, "last_instruction_id", mid, timeout=ctx.assertion_timeout),
        ]

        self._send_exit_all(ctx)
        return self._evaluate(assertions, t0)


class TestE02ProcessedIdPersistedToFile(BaseTestCase):
    """E-02: Accepted message_id appears in the processed IDs file."""
    test_id = "E-02"
    category = "Persistence / Recovery"
    scenario = "Accepted HEARTBEAT message_id written to processed_ids.log"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()

        hb = mf.heartbeat(instrument=ctx.instrument)
        hb_id = hb["message_id"]
        inject_message(ctx.inbox, hb)

        # Wait for ACCEPT in log first.
        log_parser.find_event_for_msg(
            ctx.log_file, "ACCEPT", hb_id,
            since_line=lb, timeout_seconds=ctx.assertion_timeout
        )

        assertions = [
            assert_processed_id_present(ctx.processed_ids_file, hb_id, timeout=ctx.assertion_timeout),
        ]
        return self._evaluate(assertions, t0)


class TestE03CorruptStateFileSemiManual(BaseTestCase):
    """E-03: Corrupt state file — writes invalid JSON, then requires manual strategy restart.

    This test writes a corrupt state file and provides instructions.
    It cannot verify the shell's log response without a restart, so it
    is marked SKIP-MANUAL with setup output.
    """
    test_id = "E-03"
    category = "Persistence / Recovery"
    scenario = "Corrupt shell_state.json -> strategy restart -> logs WARN 'failed to load persistent state'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()

        # Write deliberately corrupt JSON to state file.
        corrupt_content = '{"ActiveTemplate": "runner", "SignalIntakeEnabled": true CORRUPT_JSON'
        try:
            ctx.state_file.write_text(corrupt_content, encoding="utf-8")
            print(
                f"\n    [E-03] Corrupt state file written to: {ctx.state_file}\n"
                f"    MANUAL STEP: Disable and re-enable the NT8 strategy, then\n"
                f"    verify the log contains: 'failed to load persistent state'\n"
                f"    Expected log fragment: WARN ... failed to load persistent state\n"
            )
        except OSError as e:
            return self._skip(f"Could not write corrupt state file: {e}")

        return self._skip(
            "E-03 is semi-manual: corrupt file written. "
            "Restart strategy and verify log for 'failed to load persistent state'."
        )


class TestE04CorruptProcessedIdsFileSemiManual(BaseTestCase):
    """E-04: Corrupt processed IDs file — writes invalid content, requires restart.

    This test writes corrupt content and prints manual verification instructions.
    """
    test_id = "E-04"
    category = "Persistence / Recovery"
    scenario = "Corrupt processed_ids.log -> restart -> logs WARN 'failed to load processed ids'"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()

        # Write non-parseable content (binary garbage that would fail line parsing
        # in an unusual way — in practice the shell reads line-by-line so this
        # tests robustness to unexpected content).
        corrupt_content = "valid-id-1\n\x00\x01\x02invalid_bytes\nvalid-id-2\n" + "x" * 2000
        try:
            ctx.processed_ids_file.write_text(corrupt_content, encoding="utf-8", errors="replace")
            print(
                f"\n    [E-04] Corrupt processed_ids file written to: {ctx.processed_ids_file}\n"
                f"    MANUAL STEP: Disable and re-enable the NT8 strategy, then\n"
                f"    verify the log contains: 'failed to load processed ids' OR\n"
                f"    'processed_id_count=N' showing graceful recovery.\n"
            )
        except OSError as e:
            return self._skip(f"Could not write corrupt processed_ids file: {e}")

        return self._skip(
            "E-04 is semi-manual: corrupt IDs file written. "
            "Restart strategy and verify log for 'failed to load processed ids'."
        )


ALL_TESTS: list[BaseTestCase] = [
    TestE01StateFileAfterDryRunEntry(),
    TestE02ProcessedIdPersistedToFile(),
    TestE03CorruptStateFileSemiManual(),
    TestE04CorruptProcessedIdsFileSemiManual(),
]
