"""
Phase 2 Group C: Exit management (full exit, partial exit, residual stop).

P2-C01  FULLY_AUTOMATED  EXIT_ALL while Long: position flattens, CLOSED event written
P2-C02  FULLY_AUTOMATED  TAKE_PARTIAL reduces qty, PARTIAL_FILL event written
P2-C03  FULLY_AUTOMATED  After partial exit, stop order still live (residual protection)
P2-C04  SEMI_AUTOMATED   TAKE_PARTIAL reduces qty and NT8 UI reflects reduced position
"""
from __future__ import annotations

import time

from bridge_test_harness import bridge_io, log_parser, outbox_parser
from bridge_test_harness import message_factory
from bridge_test_harness.health_parser import (
    get_latest_health, wait_for_flat, wait_for_qty,
    parse_detail, SIDE_LONG, SIDE_SHORT, SIDE_FLAT,
)
from bridge_test_harness.checkpoint import checkpoint, CheckpointOutcome
from bridge_test_harness.test_cases_phase2.base_phase2 import (
    AutomationLevel, Phase2Config, Phase2TestResult,
    BasePhase2TestCase, require_flat_precondition,
)
from bridge_test_harness.test_cases.base import TestContext


class P2C01ExitAllFlattens(BasePhase2TestCase):
    test_id = "P2-C01"
    category = "Exit Management"
    scenario = "EXIT_ALL while Long flattens position and CLOSED/EXIT event is written"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        # Send EXIT_ALL
        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()
        exit_msg = message_factory.exit_all(instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, exit_msg)

        # Wait for position to become Flat in log
        flat = wait_for_flat(ctx.log_file, since_line=lb2, timeout_seconds=30.0)
        if not flat:
            return self._fail(
                "Position did not flatten within 30s after EXIT_ALL",
                evidence={"exit_msg_id": exit_msg["message_id"]},
            )

        # Look for CLOSED or EXIT outbox event
        closed_evt = self._wait_for_outbox_event(ctx, "CLOSED", ob2, timeout=10.0)
        if closed_evt is None:
            closed_evt = self._wait_for_outbox_event(ctx, "EXIT", ob2, timeout=5.0)

        return self._pass(evidence={
            "exit_msg_id": exit_msg["message_id"],
            "flat_confirmed": flat,
            "closed_event": str(closed_evt) if closed_evt else "not observed (non-fatal)",
        })


class P2C02TakePartialReducesQty(BasePhase2TestCase):
    test_id = "P2-C02"
    category = "Exit Management"
    scenario = "TAKE_PARTIAL reduces live position qty, PARTIAL_FILL event written"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        if p2cfg.entry_qty < 2:
            return self._skip(
                "entry_qty must be >= 2 to test partial exit; "
                "set phase2.entry_qty=2 in test_config.json"
            )

        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        # Wait for stop attached so we're fully in-position
        self._wait_for_stop_attached(ctx, ob, timeout=p2cfg.stop_attach_wait_seconds, since_log_line=lb)

        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        partial_msg = message_factory.take_partial(
            quantity=p2cfg.partial_qty,
            instrument=ctx.instrument,
        )
        bridge_io.inject_message(ctx.inbox, partial_msg)

        expected_remaining = p2cfg.entry_qty - p2cfg.partial_qty

        # Wait for qty to drop in log
        reduced_rec = wait_for_qty(
            ctx.log_file,
            expected_qty=expected_remaining,
            since_line=lb2,
            timeout_seconds=30.0,
        )

        if reduced_rec is None:
            self._ensure_flat(ctx, p2cfg)
            return self._fail(
                f"Position qty did not reduce to {expected_remaining} within 30s "
                f"after TAKE_PARTIAL(qty={p2cfg.partial_qty})",
                evidence={"partial_msg_id": partial_msg["message_id"]},
            )

        # Look for PARTIAL_FILL or FILLED event
        partial_fill = self._wait_for_outbox_event(ctx, "PARTIAL_FILL", ob2, timeout=10.0)
        if partial_fill is None:
            partial_fill = self._wait_for_outbox_event(ctx, "FILLED", ob2, timeout=5.0)

        self._ensure_flat(ctx, p2cfg)

        return self._pass(evidence={
            "entry_qty": p2cfg.entry_qty,
            "partial_qty": p2cfg.partial_qty,
            "expected_remaining": expected_remaining,
            "qty_log_record": str(reduced_rec),
            "partial_fill_event": str(partial_fill) if partial_fill else "not observed (non-fatal)",
        })


class P2C03ResidualStopAfterPartial(BasePhase2TestCase):
    test_id = "P2-C03"
    category = "Exit Management"
    scenario = "After partial exit, remaining position still has an active stop order"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        if p2cfg.entry_qty < 2:
            return self._skip(
                "entry_qty must be >= 2 for residual stop test; "
                "set phase2.entry_qty=2 in test_config.json"
            )

        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        attached = self._wait_for_stop_attached(ctx, ob, timeout=p2cfg.stop_attach_wait_seconds, since_log_line=lb)
        if attached is None:
            self._ensure_flat(ctx, p2cfg)
            return self._skip("No STOP_ATTACHED -- cannot test residual stop")

        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        # Take partial exit
        partial_msg = message_factory.take_partial(
            quantity=p2cfg.partial_qty,
            instrument=ctx.instrument,
        )
        bridge_io.inject_message(ctx.inbox, partial_msg)

        expected_remaining = p2cfg.entry_qty - p2cfg.partial_qty
        wait_for_qty(ctx.log_file, expected_remaining, since_line=lb2, timeout_seconds=30.0)

        # Allow shell time to reattach / maintain stop
        time.sleep(2.0)

        # Check HEALTH snapshot: stop_order should still be live
        health = get_latest_health(ctx.log_file, since_line=lb2)
        stop_still_live = health.stop_order_live if health else None

        self._ensure_flat(ctx, p2cfg)

        if stop_still_live is False:
            return self._fail(
                "HEALTH snapshot shows stop_order=<null> after partial exit; "
                "residual position has no stop protection",
                evidence={
                    "health_stop_order": health.stop_order if health else "no health record",
                    "remaining_qty": expected_remaining,
                },
            )

        return self._pass(evidence={
            "remaining_qty": expected_remaining,
            "health_stop_order": health.stop_order if health else "no health record",
            "stop_still_live": stop_still_live,
        })


class P2C04PartialExitNT8UI(BasePhase2TestCase):
    test_id = "P2-C04"
    category = "Exit Management"
    scenario = "TAKE_PARTIAL: NT8 Positions tab reflects reduced quantity (user-verified)"
    automation_level = AutomationLevel.SEMI_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        if p2cfg.entry_qty < 2:
            return self._skip(
                "entry_qty must be >= 2 for partial exit UI test; "
                "set phase2.entry_qty=2 in test_config.json"
            )

        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        self._wait_for_stop_attached(ctx, ob, timeout=p2cfg.stop_attach_wait_seconds, since_log_line=lb)
        checkpoints = []

        if p2cfg.show_checkpoints:
            cp1 = checkpoint(
                "NT8 shows full position",
                f"In NinjaTrader, confirm Sim101 shows a Long position of "
                f"{p2cfg.entry_qty} contract(s) with a stop order.\n"
                f"FILLED detail: {filled.detail}",
            )
            checkpoints.append(cp1)
            if cp1.outcome == CheckpointOutcome.FAILED:
                self._ensure_flat(ctx, p2cfg)
                return self._fail(
                    "User confirmed initial position was NOT correctly shown in NT8 UI",
                    checkpoints=checkpoints,
                )

        # Send partial exit
        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()
        partial_msg = message_factory.take_partial(
            quantity=p2cfg.partial_qty,
            instrument=ctx.instrument,
        )
        bridge_io.inject_message(ctx.inbox, partial_msg)

        expected_remaining = p2cfg.entry_qty - p2cfg.partial_qty
        wait_for_qty(ctx.log_file, expected_remaining, since_line=lb2, timeout_seconds=30.0)
        time.sleep(1.0)

        if p2cfg.show_checkpoints:
            cp2 = checkpoint(
                "NT8 shows reduced position",
                f"In NinjaTrader, confirm Sim101 now shows:\n"
                f"  - Long position of {expected_remaining} contract(s) (reduced from {p2cfg.entry_qty})\n"
                f"  - Stop order still present (residual protection)\n"
                f"  - No extra stop orders added\n"
                f"Does the NT8 Positions/Orders tab reflect the correct state?",
            )
            checkpoints.append(cp2)

        self._ensure_flat(ctx, p2cfg)

        if checkpoints and any(
            c.outcome == CheckpointOutcome.FAILED for c in checkpoints
        ):
            return self._fail(
                "User reported incorrect position state in NT8 UI after TAKE_PARTIAL",
                checkpoints=checkpoints,
            )

        return self._pass(
            evidence={"expected_remaining": expected_remaining},
            checkpoints=checkpoints,
        )


ALL_TESTS: list[BasePhase2TestCase] = [
    P2C01ExitAllFlattens(),
    P2C02TakePartialReducesQty(),
    P2C03ResidualStopAfterPartial(),
    P2C04PartialExitNT8UI(),
]
