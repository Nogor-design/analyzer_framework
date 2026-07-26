"""
Phase 2 Group D: Conflict and sequencing with live fills.

P2-D01  FULLY_AUTOMATED  ENTER_SHORT while Long is gated (shell blocks conflicting direction)
P2-D02  FULLY_AUTOMATED  EXIT_ALL then immediate ENTER_LONG: second entry accepted after flat
P2-D03  SEMI_AUTOMATED   Rapid burst: ENTER_LONG + MOVE_STOP before fill -- MOVE_STOP is queued
                          or rejected until position is live
"""
from __future__ import annotations

import time

from bridge_test_harness import bridge_io, log_parser, outbox_parser
from bridge_test_harness import message_factory
from bridge_test_harness.health_parser import (
    wait_for_flat, wait_for_long,
    SIDE_LONG, SIDE_SHORT, SIDE_FLAT,
)
from bridge_test_harness.checkpoint import checkpoint, CheckpointOutcome
from bridge_test_harness.test_cases_phase2.base_phase2 import (
    AutomationLevel, Phase2Config, Phase2TestResult,
    BasePhase2TestCase, require_flat_precondition,
)
from bridge_test_harness.test_cases.base import TestContext


class P2D01EnterShortWhileLong(BasePhase2TestCase):
    test_id = "P2-D01"
    category = "Conflict / Sequencing"
    scenario = "ENTER_SHORT while Long position live is gate-rejected by shell"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        # Wait for stop attached so shell is fully InPosition
        self._wait_for_stop_attached(ctx, ob, timeout=p2cfg.stop_attach_wait_seconds)

        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()

        # Send conflicting ENTER_SHORT
        short_msg = message_factory.base_enter_short(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=p2cfg.default_stop_ticks,
        )
        bridge_io.inject_message(ctx.inbox, short_msg)

        # Expect rejection
        rejected_evt = outbox_parser.find_event(
            ctx.outbox, "REJECTED",
            msg_id=short_msg["message_id"],
            since_count=ob2,
            timeout_seconds=ctx.assertion_timeout,
        )
        gate_log = log_parser.find_event_for_msg(
            ctx.log_file, "GATE", short_msg["message_id"], lb2
        )

        self._ensure_flat(ctx, p2cfg)

        if rejected_evt is None and gate_log is None:
            return self._fail(
                "ENTER_SHORT while Long was NOT rejected (expected GATE or REJECTED)",
                evidence={"short_msg_id": short_msg["message_id"]},
            )

        return self._pass(evidence={
            "rejected_event": str(rejected_evt) if rejected_evt else "n/a",
            "gate_log": str(gate_log) if gate_log else "n/a",
        })


class P2D02ExitThenReenter(BasePhase2TestCase):
    test_id = "P2-D02"
    category = "Conflict / Sequencing"
    scenario = "EXIT_ALL followed by new ENTER_LONG: second entry is accepted after flat"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        # Enter and fill
        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        self._wait_for_stop_attached(ctx, ob, timeout=p2cfg.stop_attach_wait_seconds)

        # Exit
        lb2 = ctx.log_baseline()
        ob2 = ctx.outbox_baseline()
        exit_msg = message_factory.exit_all(instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, exit_msg)

        flat = wait_for_flat(ctx.log_file, since_line=lb2, timeout_seconds=30.0)
        if not flat:
            return self._fail("Position did not flatten within 30s after EXIT_ALL")

        # Brief pause to let shell settle to Idle
        time.sleep(1.5)

        # Re-enter
        lb3 = ctx.log_baseline()
        ob3 = ctx.outbox_baseline()

        enter2_msg = message_factory.base_enter_long(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=p2cfg.default_stop_ticks,
        )
        bridge_io.inject_message(ctx.inbox, enter2_msg)

        accept2 = outbox_parser.find_event(
            ctx.outbox, "ACCEPTED",
            msg_id=enter2_msg["message_id"],
            since_count=ob3,
            timeout_seconds=ctx.assertion_timeout,
        )

        # Clean up
        time.sleep(1.0)
        self._ensure_flat(ctx, p2cfg)

        if accept2 is None:
            return self._fail(
                "Second ENTER_LONG after EXIT_ALL was not accepted",
                evidence={"enter2_msg_id": enter2_msg["message_id"]},
            )

        return self._pass(evidence={
            "first_filled": filled.detail,
            "second_accept": str(accept2),
        })


class P2D03RapidEntryThenMoveStop(BasePhase2TestCase):
    test_id = "P2-D03"
    category = "Conflict / Sequencing"
    scenario = "MOVE_STOP injected before fill is received: gated until position is live"
    automation_level = AutomationLevel.SEMI_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        # Inject ENTER_LONG
        enter_msg = message_factory.base_enter_long(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=p2cfg.default_stop_ticks,
        )
        bridge_io.inject_message(ctx.inbox, enter_msg)

        # Immediately (within ~200ms) inject MOVE_STOP before fill can arrive
        time.sleep(0.2)
        ob1 = ctx.outbox_baseline()
        lb1 = ctx.log_baseline()
        move_msg = message_factory.move_stop(stop_price=19000.0, instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, move_msg)

        # Allow time for both to be processed
        time.sleep(max(p2cfg.fill_wait_seconds * 0.5, 5.0))

        # MOVE_STOP should be rejected (shell still in EntryPending, pos=Flat)
        # OR it may be queued and applied after fill -- check what actually happened
        move_rejected = outbox_parser.find_event(
            ctx.outbox, "REJECTED",
            msg_id=move_msg["message_id"],
            since_count=ob1,
            timeout_seconds=2.0,
        )
        move_gate = log_parser.find_event_for_msg(
            ctx.log_file, "GATE", move_msg["message_id"], lb1
        )
        move_accepted = outbox_parser.find_event(
            ctx.outbox, "ACCEPTED",
            msg_id=move_msg["message_id"],
            since_count=ob1,
            timeout_seconds=2.0,
        )

        checkpoints = []
        if p2cfg.show_checkpoints:
            cp = checkpoint(
                "MOVE_STOP before fill outcome",
                f"MOVE_STOP injected ~200ms after ENTER_LONG (before expected fill).\n"
                f"  - move_rejected: {'yes' if move_rejected else 'no'}\n"
                f"  - gate_log: {'yes' if move_gate else 'no'}\n"
                f"  - move_accepted: {'yes' if move_accepted else 'no'}\n\n"
                f"Expected: MOVE_STOP is rejected or gate-blocked because position is not yet live.\n"
                f"Check NT8 -- was any unexpected stop order placed before the fill?",
            )
            checkpoints.append(cp)

        # Clean up
        self._ensure_flat(ctx, p2cfg)

        evidence = {
            "move_rejected": str(move_rejected) if move_rejected else "no",
            "move_gate": str(move_gate) if move_gate else "no",
            "move_accepted": str(move_accepted) if move_accepted else "no",
        }

        if move_accepted and not move_rejected and not move_gate:
            # MOVE_STOP was accepted before fill -- this may be acceptable if
            # the fill arrived first; leave the human checkpoint as arbiter
            if checkpoints and checkpoints[0].outcome == CheckpointOutcome.FAILED:
                return self._fail(
                    "User confirmed MOVE_STOP was applied unexpectedly before position was live",
                    evidence=evidence,
                    checkpoints=checkpoints,
                )

        return self._pass(evidence=evidence, checkpoints=checkpoints)


ALL_TESTS: list[BasePhase2TestCase] = [
    P2D01EnterShortWhileLong(),
    P2D02ExitThenReenter(),
    P2D03RapidEntryThenMoveStop(),
]
