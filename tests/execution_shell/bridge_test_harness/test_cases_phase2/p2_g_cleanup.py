"""
Phase 2 Group G: Post-suite cleanup and session teardown.

G tests run last. They ensure the account is flat and the shell is in a known
clean state so subsequent manual or automated testing can start from scratch.

P2-G01  FULLY_AUTOMATED  Send EXIT_ALL and verify position is Flat
P2-G02  FULLY_AUTOMATED  Send FLATTEN_AND_DISABLE and verify intake=false, position flat
P2-G03  SEMI_AUTOMATED   Confirm NT8 Accounts tab shows zero open positions
"""
from __future__ import annotations

import time

from bridge_test_harness import bridge_io, log_parser, outbox_parser
from bridge_test_harness import message_factory
from bridge_test_harness.health_parser import (
    get_latest_health, wait_for_flat,
    get_current_position_from_log, SIDE_FLAT,
)
from bridge_test_harness.checkpoint import checkpoint, CheckpointOutcome
from bridge_test_harness.test_cases_phase2.base_phase2 import (
    AutomationLevel, Phase2Config, Phase2TestResult,
    BasePhase2TestCase,
)
from bridge_test_harness.test_cases.base import TestContext


class P2G01ExitAllCleanup(BasePhase2TestCase):
    test_id = "P2-G01"
    category = "Cleanup"
    scenario = "EXIT_ALL flattens any remaining position (end-of-suite cleanup)"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        side, qty = get_current_position_from_log(ctx.log_file)

        if side == SIDE_FLAT and qty == 0:
            return self._pass(evidence={"position": "already flat"})

        lb = ctx.log_baseline()
        exit_msg = message_factory.exit_all(instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, exit_msg)

        flat = wait_for_flat(ctx.log_file, since_line=lb, timeout_seconds=30.0)
        if not flat:
            side2, qty2 = get_current_position_from_log(ctx.log_file)
            return self._fail(
                f"Position not flat after EXIT_ALL: side={side2} qty={qty2}",
                evidence={"exit_msg_id": exit_msg["message_id"]},
            )

        return self._pass(evidence={
            "exit_msg_id": exit_msg["message_id"],
            "was_side": side,
            "was_qty": qty,
        })


class P2G02FlattenAndDisable(BasePhase2TestCase):
    test_id = "P2-G02"
    category = "Cleanup"
    scenario = "FLATTEN_AND_DISABLE leaves position flat and intake permanently disabled"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        fad_msg = message_factory.flatten_and_disable(instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, fad_msg)

        # Wait for flat
        flat = wait_for_flat(ctx.log_file, since_line=lb, timeout_seconds=30.0)

        # Allow HEALTH snapshot to propagate
        time.sleep(2.0)
        health = get_latest_health(ctx.log_file, since_line=lb)

        failures = []
        evidence: dict = {"fad_msg_id": fad_msg["message_id"]}

        if not flat:
            side, qty = get_current_position_from_log(ctx.log_file)
            failures.append(f"Position not flat after FLATTEN_AND_DISABLE: side={side} qty={qty}")

        if health:
            evidence["health_intake"] = health.intake
            evidence["health_mode"] = health.shell_mode
            if health.intake:
                failures.append(
                    "signalIntakeEnabled is still True after FLATTEN_AND_DISABLE; expected False"
                )
        else:
            evidence["health"] = "no HEALTH record found in window"

        if failures:
            return self._fail("; ".join(failures), evidence=evidence)

        return self._pass(evidence=evidence)


class P2G03ConfirmCleanState(BasePhase2TestCase):
    test_id = "P2-G03"
    category = "Cleanup"
    scenario = "NT8 Accounts tab shows zero open positions after suite teardown (user-verified)"
    automation_level = AutomationLevel.SEMI_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        side, qty = get_current_position_from_log(ctx.log_file)

        checkpoints = []
        if p2cfg.show_checkpoints:
            cp = checkpoint(
                "NT8 zero open positions",
                f"Final cleanup checkpoint.\n"
                f"  - Log reports: side={side} qty={qty}\n\n"
                f"In NinjaTrader, check the Accounts tab / Positions tab:\n"
                f"  - Sim101 should show NO open positions\n"
                f"  - All stop/target orders should be cancelled\n"
                f"Does NT8 confirm a clean, flat state?",
            )
            checkpoints.append(cp)
            if cp.outcome == CheckpointOutcome.FAILED:
                return self._fail(
                    "User confirmed NT8 still has open positions after suite teardown",
                    evidence={"log_side": side, "log_qty": qty},
                    checkpoints=checkpoints,
                )

        return self._pass(
            evidence={"log_side": side, "log_qty": qty},
            checkpoints=checkpoints,
        )


ALL_TESTS: list[BasePhase2TestCase] = [
    P2G01ExitAllCleanup(),
    P2G02FlattenAndDisable(),
    P2G03ConfirmCleanState(),
]
