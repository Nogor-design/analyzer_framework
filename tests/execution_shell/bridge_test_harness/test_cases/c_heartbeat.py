"""
Group C -- Heartbeat / Liveness  (C-01 .. C-02)

IMPORTANT: These tests are time-sensitive.
- C-01 runs for ~(heartbeat_timeout_seconds * 0.6) seconds
- C-02 runs for ~(heartbeat_timeout_seconds + 5) seconds

The HeartbeatTimeoutSeconds configured in NT8 must match
ctx.heartbeat_timeout_seconds in test_config.json.

Default NT8 shell setting: HeartbeatTimeoutSeconds = 20s

Shell heartbeat mechanics:
- lastBridgeMessageUtc is set on ANY accepted message (not just HEARTBEAT action)
- heartbeatFaulted starts False; set True on first timeout
- Once faulted, the check is skipped (no repeated triggers)
- On fault with FlattenOnHeartbeatLoss=false: signalIntakeEnabled=false
- After fault, intake is permanently disabled until strategy restart
"""
from __future__ import annotations

import time

from bridge_test_harness import message_factory as mf
from bridge_test_harness.assertions import (
    assert_log_fragment,
    assert_log_fragment_absent,
    assert_outbox_event,
    assert_state_field,
)
from bridge_test_harness.bridge_io import inject_message
from bridge_test_harness.test_cases.base import BaseTestCase, TestContext, TestResult


class TestC01HeartbeatKeepsIntakeAlive(BaseTestCase):
    """C-01: Regular HEARTBEATs sent within timeout window prevent HEARTBEAT_LOST.

    Sends heartbeats at half the timeout interval for two full cycles, then
    verifies HEARTBEAT_LOST has not appeared and intake is still enabled.

    This test takes approximately (heartbeat_timeout * 1.2) seconds.
    """
    test_id = "C-01"
    category = "Heartbeat / Liveness"
    scenario = "Regular HEARTBEATs prevent timeout -- intake stays enabled"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        hb_interval = ctx.heartbeat_timeout_seconds * 0.4
        cycles = 3
        total_test_duration = hb_interval * cycles

        # Prime the heartbeat clock with a first message.
        first_hb = mf.heartbeat(instrument=ctx.instrument)
        inject_message(ctx.inbox, first_hb)
        time.sleep(2.0)  # Let shell process

        # Verify first heartbeat was accepted.
        from bridge_test_harness import log_parser
        first_ack = log_parser.find_event_for_msg(
            ctx.log_file, "HEARTBEAT", first_hb["message_id"],
            since_line=lb, timeout_seconds=5.0
        )
        if not first_ack:
            return self._skip("First heartbeat not acknowledged -- shell may not be processing. Check NT8.")

        lb2 = ctx.log_baseline()

        # Send heartbeats at intervals shorter than the timeout.
        for _ in range(cycles - 1):
            time.sleep(hb_interval)
            hb = mf.heartbeat(instrument=ctx.instrument)
            inject_message(ctx.inbox, hb)

        # Wait the remaining window.
        time.sleep(hb_interval + 2.0)

        assertions = [
            assert_log_fragment_absent(
                ctx.log_file, "HEARTBEAT_LOST",
                since_line=lb2, wait_seconds=0.0
            ),
            assert_state_field(ctx.state_file, "signal_intake_enabled", True, timeout=3.0),
        ]
        return self._evaluate(assertions, t0)


class TestC02HeartbeatTimeoutDisablesIntake(BaseTestCase):
    """C-02: Silence > HeartbeatTimeoutSeconds triggers HEARTBEAT_LOST and disables intake.

    - FlattenOnHeartbeatLoss = false (default NT8 config)
    - After timeout: heartbeatFaulted=True, signalIntakeEnabled=False
    - Position is untouched (no flatten)

    WARNING: After this test passes, the shell's intake is permanently disabled
    until the NT8 strategy is restarted.  Run this test LAST in the suite.

    This test takes approximately (heartbeat_timeout + 8) seconds.
    """
    test_id = "C-02"
    category = "Heartbeat / Liveness"
    scenario = "Silence > HeartbeatTimeoutSeconds -> HEARTBEAT_LOST + intake disabled"

    def run(self, ctx: TestContext) -> TestResult:
        t0 = time.monotonic()
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        # Prime the heartbeat clock by sending one accepted message.
        prime = mf.heartbeat(instrument=ctx.instrument)
        prime_id = prime["message_id"]
        inject_message(ctx.inbox, prime)

        from bridge_test_harness import log_parser
        ack = log_parser.find_event_for_msg(
            ctx.log_file, "HEARTBEAT", prime_id,
            since_line=lb, timeout_seconds=ctx.assertion_timeout
        )
        if not ack:
            return self._skip("Priming heartbeat not acknowledged -- shell may not be active.")

        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        # Stop sending. Wait for HeartbeatTimeoutSeconds + a buffer.
        wait_duration = ctx.heartbeat_timeout_seconds + 8
        print(
            f"\n    [C-02] Waiting {wait_duration}s for heartbeat timeout "
            f"(HeartbeatTimeoutSeconds={ctx.heartbeat_timeout_seconds})..."
        )
        time.sleep(wait_duration)

        assertions = [
            assert_log_fragment(ctx.log_file, "HEARTBEAT_LOST", since_line=lb2, timeout=5.0),
            assert_outbox_event(ctx.outbox, "HEARTBEAT_LOST", since_count=ob2, timeout=5.0),
            assert_state_field(ctx.state_file, "signal_intake_enabled", False, timeout=5.0),
            assert_state_field(ctx.state_file, "heartbeat_faulted", True, timeout=5.0),
        ]
        return self._evaluate(assertions, t0)


ALL_TESTS: list[BaseTestCase] = [
    TestC01HeartbeatKeepsIntakeAlive(),
    TestC02HeartbeatTimeoutDisablesIntake(),
]
