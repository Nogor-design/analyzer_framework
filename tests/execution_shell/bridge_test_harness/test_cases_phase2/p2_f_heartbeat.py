"""
Phase 2 Group F: Heartbeat loss with real positions.

P2-F01  FULLY_AUTOMATED  Heartbeat loss while flat: HEARTBEAT_LOST event, intake disabled
P2-F02  SEMI_AUTOMATED   Heartbeat loss while in Long position with FlattenOnHeartbeatLoss=true:
                          position flattened automatically
P2-F03  MANUAL_REQUIRED  Heartbeat loss while in position with FlattenOnHeartbeatLoss=false:
                          position NOT flattened, but intake disabled

NOTE: F01 permanently disables signalIntakeEnabled (same as Phase 1 C-02).
      F02 adds real-money risk (Sim101 flat order).
      Run F01/F02 last in Group F and restart NT8 between test sessions.
"""
from __future__ import annotations

import time

from bridge_test_harness import bridge_io, log_parser, outbox_parser
from bridge_test_harness import message_factory
from bridge_test_harness.health_parser import (
    get_latest_health, wait_for_flat, SIDE_LONG, SIDE_FLAT,
)
from bridge_test_harness.checkpoint import checkpoint, CheckpointOutcome, print_manual_procedure
from bridge_test_harness.test_cases_phase2.base_phase2 import (
    AutomationLevel, Phase2Config, Phase2TestResult,
    BasePhase2TestCase, require_flat_precondition,
)
from bridge_test_harness.test_cases.base import TestContext


class P2F01HeartbeatLossFlat(BasePhase2TestCase):
    test_id = "P2-F01"
    category = "Heartbeat / Liveness"
    scenario = "Heartbeat loss while flat: HEARTBEAT_LOST written, intake permanently disabled"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        hb_timeout = ctx.heartbeat_timeout_seconds
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        # Prime the heartbeat clock with one HB
        prime_msg = message_factory.heartbeat(instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, prime_msg)
        time.sleep(1.0)

        # Now stop sending heartbeats and wait for fault
        wait_seconds = hb_timeout + 8
        print(
            f"  [P2-F01] Waiting {wait_seconds}s for heartbeat fault "
            f"(timeout={hb_timeout}s + 8s buffer)..."
        )
        time.sleep(wait_seconds)

        # Expect HEARTBEAT_LOST in log
        hb_lost = log_parser.find_event_type(ctx.log_file, "HEARTBEAT_LOST", lb)
        if hb_lost is None:
            return self._fail(
                f"No HEARTBEAT_LOST log event after {wait_seconds}s",
                evidence={"hb_timeout": hb_timeout},
            )

        # Expect intake disabled in HEALTH
        health = get_latest_health(ctx.log_file, since_line=lb)
        if health and health.intake:
            return self._fail(
                "signalIntakeEnabled is still True after HEARTBEAT_LOST; expected False",
                evidence={"health": str(health)},
            )

        # WARN: intake is now permanently disabled until NT8 restart
        print(
            "  [P2-F01] WARNING: signalIntakeEnabled is now permanently disabled. "
            "NT8 restart required before running further tests."
        )

        return self._pass(evidence={
            "hb_lost_record": str(hb_lost),
            "health_intake": health.intake if health else "no health record",
            "health_hb_faulted": health.heartbeat_faulted if health else "?",
        })


class P2F02HeartbeatLossWhileLong(BasePhase2TestCase):
    test_id = "P2-F02"
    category = "Heartbeat / Liveness"
    scenario = "Heartbeat loss while in Long position with FlattenOnHBLoss=true: position flattened"
    automation_level = AutomationLevel.SEMI_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        # Enter Long
        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        self._wait_for_stop_attached(ctx, ob, timeout=p2cfg.stop_attach_wait_seconds)

        hb_timeout = ctx.heartbeat_timeout_seconds
        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        # Prime heartbeat clock
        prime_msg = message_factory.heartbeat(instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, prime_msg)
        time.sleep(1.0)

        checkpoints = []
        if p2cfg.show_checkpoints:
            cp = checkpoint(
                "Confirm FlattenOnHeartbeatLoss=true in NT8",
                f"Before proceeding, confirm the NT8 strategy parameter "
                f"'FlattenOnHeartbeatLoss' is set to TRUE.\n"
                f"If it is False, skip this test (P2-F02) and run P2-F03 instead.\n"
                f"Is FlattenOnHeartbeatLoss currently set to TRUE?",
            )
            checkpoints.append(cp)
            if cp.outcome != CheckpointOutcome.PASSED:
                return self._skip(
                    "FlattenOnHeartbeatLoss not confirmed True -- skipping P2-F02"
                )

        # Stop sending heartbeats
        wait_seconds = hb_timeout + 8
        print(
            f"  [P2-F02] Waiting {wait_seconds}s for heartbeat fault while Long..."
        )
        time.sleep(wait_seconds)

        # Expect HEARTBEAT_LOST
        hb_lost = log_parser.find_event_type(ctx.log_file, "HEARTBEAT_LOST", lb2)

        # Expect position to flatten
        flat = wait_for_flat(ctx.log_file, since_line=lb2, timeout_seconds=15.0)

        if p2cfg.show_checkpoints:
            cp2 = checkpoint(
                "NT8 position flattened on heartbeat loss",
                f"Heartbeat timeout has elapsed (~{wait_seconds}s).\n"
                f"  - HEARTBEAT_LOST event in log: {'yes' if hb_lost else 'NO'}\n"
                f"  - Log shows pos=Flat: {'yes' if flat else 'NO'}\n\n"
                f"In NT8 -- is the Sim101 Long position now CLOSED?",
            )
            checkpoints.append(cp2)

        failures = []
        if hb_lost is None:
            failures.append("No HEARTBEAT_LOST log event")
        if not flat:
            failures.append("Position did not appear Flat in log after heartbeat fault")
        if checkpoints[-1:] and checkpoints[-1].outcome == CheckpointOutcome.FAILED:
            failures.append("User confirmed NT8 position was NOT flattened")

        print(
            "  [P2-F02] WARNING: signalIntakeEnabled is now permanently disabled. "
            "NT8 restart required."
        )

        if failures:
            return self._fail(
                "; ".join(failures),
                evidence={
                    "hb_lost": str(hb_lost) if hb_lost else "not found",
                    "flat_confirmed": flat,
                },
                checkpoints=checkpoints,
            )

        return self._pass(
            evidence={
                "hb_lost": str(hb_lost),
                "flat_confirmed": flat,
            },
            checkpoints=checkpoints,
        )


class P2F03HeartbeatLossNoFlatten(BasePhase2TestCase):
    test_id = "P2-F03"
    category = "Heartbeat / Liveness"
    scenario = "Heartbeat loss while in position with FlattenOnHBLoss=false: no flatten (MANUAL)"
    automation_level = AutomationLevel.MANUAL_REQUIRED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        print_manual_procedure(
            test_id=self.test_id,
            scenario=self.scenario,
            steps=[
                "Set NT8 strategy parameter FlattenOnHeartbeatLoss = FALSE.",
                "Enter a Long position via bridge ENTER_LONG. Wait for fill and stop attachment.",
                "Send ONE heartbeat to prime the clock, then stop sending heartbeats.",
                f"Wait {ctx.heartbeat_timeout_seconds + 8}s for heartbeat fault.",
                "Observe log and outbox: HEARTBEAT_LOST should appear and intake disabled.",
                "Observe NT8: the Long position should NOT be closed.",
                "Manually flatten the position via NT8 UI or FLATTEN_AND_DISABLE signal.",
            ],
            expected_outcomes=[
                "HEARTBEAT_LOST log event written.",
                "signalIntakeEnabled set to False (visible in HEALTH snapshot).",
                "Long position remains open in NT8 (not auto-flattened).",
                "FLATTEN_AND_DISABLE via bridge (if tested) closes position even with intake=False.",
            ],
        )
        return self._skip("MANUAL_REQUIRED -- see printed procedure above")


ALL_TESTS: list[BasePhase2TestCase] = [
    P2F01HeartbeatLossFlat(),
    P2F02HeartbeatLossWhileLong(),
    P2F03HeartbeatLossNoFlatten(),
]
