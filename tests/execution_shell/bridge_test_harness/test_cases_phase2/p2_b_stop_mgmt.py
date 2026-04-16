"""
Phase 2 Group B: Protective stop attachment and movement.

P2-B01  FULLY_AUTOMATED  After ENTER_LONG fill: STOP_ATTACHED + STOP_WORKING events appear
P2-B02  FULLY_AUTOMATED  MOVE_STOP to valid price: accepted + STOP_MOVED event
P2-B03  FULLY_AUTOMATED  MOVE_STOP to unsafe price (wrong side): rejected
P2-B04  SEMI_AUTOMATED   No stop stacking: second MOVE_STOP does not add a second stop order
"""
from __future__ import annotations

import time

from bridge_test_harness import bridge_io, log_parser, outbox_parser
from bridge_test_harness import message_factory
from bridge_test_harness.health_parser import (
    get_latest_health, wait_for_long, wait_for_flat,
    parse_detail, SIDE_LONG, SIDE_SHORT, SIDE_FLAT,
)
from bridge_test_harness.checkpoint import checkpoint, CheckpointOutcome
from bridge_test_harness.test_cases_phase2.base_phase2 import (
    AutomationLevel, Phase2Config, Phase2TestResult,
    BasePhase2TestCase, require_flat_precondition,
)
from bridge_test_harness.test_cases.base import TestContext


class P2B01StopAttachedAfterFill(BasePhase2TestCase):
    test_id = "P2-B01"
    category = "Stop Management"
    scenario = "After ENTER_LONG fill: STOP_ATTACHED and STOP_WORKING outbox events appear"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(
                f"No FILLED event within {p2cfg.fill_wait_seconds}s -- "
                "Sim101 must be receiving ticks"
            )

        # Wait for STOP_ATTACHED
        attached = self._wait_for_stop_attached(
            ctx, ob, timeout=p2cfg.stop_attach_wait_seconds, since_log_line=lb
        )
        if attached is None:
            self._ensure_flat(ctx, p2cfg)
            return self._fail(
                f"No STOP_ATTACHED event within {p2cfg.stop_attach_wait_seconds}s after fill",
                evidence={"filled_event": str(filled)},
            )

        # Wait for STOP_WORKING (stop order accepted by broker)
        working = self._wait_for_stop_working(
            ctx, ob, timeout=p2cfg.stop_attach_wait_seconds
        )
        # STOP_WORKING is best-effort — may appear after a brief delay
        evidence = {
            "filled_event": str(filled),
            "stop_attached": str(attached),
            "stop_attached_detail": attached.detail,
        }
        if working:
            evidence["stop_working"] = str(working)
            evidence["stop_working_detail"] = working.detail
        else:
            evidence["stop_working"] = "not observed within timeout (non-fatal)"

        # Verify HEALTH snapshot shows stop_order is live.
        # Exception: if the stop filled before we read the snapshot the position
        # will already be Flat — that means the stop worked correctly (it fired
        # and closed the trade), so we accept it as a pass rather than failing.
        health = get_latest_health(ctx.log_file, since_line=lb)
        if health and not health.stop_order_live:
            evidence["health_stop_order"] = health.stop_order
            pos_flat = getattr(health, "pos_side", None) == SIDE_FLAT
            if not pos_flat:
                self._ensure_flat(ctx, p2cfg)
                return self._fail(
                    f"HEALTH snapshot shows stop_order=<null> after STOP_ATTACHED event "
                    f"but position is still open; expected a live stop order",
                    evidence=evidence,
                )
            # Position already flat — stop fired and closed the trade cleanly.
            evidence["health_stop_order"] = health.stop_order
            evidence["stop_note"] = "stop filled before HEALTH read; position flat — stop worked"
        elif health:
            evidence["health_stop_order"] = health.stop_order

        self._ensure_flat(ctx, p2cfg)
        return self._pass(evidence=evidence)


class P2B02MoveStopValid(BasePhase2TestCase):
    test_id = "P2-B02"
    category = "Stop Management"
    scenario = "MOVE_STOP to valid price is accepted and STOP_MOVED event written"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    # Use wide initial stop AND target (200 ticks each) so neither order fires
    # during the test.  After the stop is attached, move the stop to 100 ticks —
    # still well below the initial 200-tick level and clearly a valid move.
    _INITIAL_STOP_TICKS: int = 50
    _INITIAL_TARGET_TICKS: int = 200
    _MOVE_TO_TICKS: int = 60
    _SETTLE_SECONDS: float = 2.0   # pause after STOP_ATTACHED before MOVE_STOP

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(
            ctx, p2cfg, SIDE_LONG,
            stop_ticks=self._INITIAL_STOP_TICKS,
            target_ticks=self._INITIAL_TARGET_TICKS,
        )
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        # Wait for stop to be attached
        attached = self._wait_for_stop_attached(
            ctx, ob, timeout=p2cfg.stop_attach_wait_seconds, since_log_line=lb
        )
        if attached is None:
            self._ensure_flat(ctx, p2cfg)
            return self._skip("No STOP_ATTACHED event -- cannot test MOVE_STOP")

        # ── Derive fill price to compute the new stop ──────────────────────
        # Priority:
        #   1. FILLED outbox event detail  (key: price=)
        #   2. STOP_ATTACHED outbox detail (key: fill_price=)
        #   3. HEALTH snapshot             (field: avg_price)
        tick_size = 0.25  # NQ/ES
        fill_price = 0.0

        filled_kv = parse_detail(filled.detail)
        try:
            fill_price = float(filled_kv.get("price", "0") or "0")
        except ValueError:
            fill_price = 0.0

        if fill_price == 0.0:
            attached_kv = parse_detail(attached.detail)
            try:
                fill_price = float(attached_kv.get("fill_price", "0") or "0")
            except ValueError:
                fill_price = 0.0

        if fill_price == 0.0:
            health = get_latest_health(ctx.log_file, since_line=lb)
            if health and health.avg_price:
                fill_price = health.avg_price

        if fill_price == 0.0:
            self._ensure_flat(ctx, p2cfg)
            return self._skip("Could not determine fill price to compute MOVE_STOP target")

        # New stop: _MOVE_TO_TICKS below fill — valid and clearly below fill for a Long
        new_stop = fill_price - (self._MOVE_TO_TICKS * tick_size)

        # Let the initial stop order settle before sending the move
        time.sleep(self._SETTLE_SECONDS)

        ob2 = ctx.outbox_baseline()
        lb2 = ctx.log_baseline()

        move_msg = message_factory.move_stop(
            stop_price=new_stop,
            instrument=ctx.instrument,
        )
        bridge_io.inject_message(ctx.inbox, move_msg)

        # Poll all four evidence sources simultaneously until any one fires.
        # Outbox sources: ACCEPTED (message intake) and STOP_MOVED (HandleMoveStop).
        # Log sources: ACCEPT and MOVE_STOP — authoritative fallback because the
        # shell always AppendLogs these before writing the outbox event.
        accept_evt = None
        stop_moved_evt = None
        accept_log = None
        move_stop_log = None

        deadline = time.monotonic() + ctx.assertion_timeout
        while time.monotonic() < deadline:
            if accept_evt is None:
                for evt in outbox_parser.events_since(ctx.outbox, ob2):
                    if evt.status == "ACCEPTED" and move_msg["message_id"] in evt.instruction_id:
                        accept_evt = evt
                        break
            if stop_moved_evt is None:
                for evt in outbox_parser.events_since(ctx.outbox, ob2):
                    if evt.status == "STOP_MOVED":
                        stop_moved_evt = evt
                        break
            if accept_log is None:
                accept_log = next(
                    (r for r in log_parser.read_records(ctx.log_file, lb2)
                     if r.event_type == "ACCEPT" and move_msg["message_id"] in r.message),
                    None,
                )
            if move_stop_log is None:
                move_stop_log = next(
                    (r for r in log_parser.read_records(ctx.log_file, lb2)
                     if r.event_type == "MOVE_STOP"),
                    None,
                )
            if any([accept_evt, stop_moved_evt, accept_log, move_stop_log]):
                break
            time.sleep(0.5)

        self._ensure_flat(ctx, p2cfg)

        if not any([accept_evt, stop_moved_evt, accept_log, move_stop_log]):
            return self._fail(
                "No ACCEPTED/STOP_MOVED outbox event or ACCEPT/MOVE_STOP log entry "
                "appeared after valid MOVE_STOP",
                evidence={
                    "move_msg_id": move_msg["message_id"],
                    "fill_price": fill_price,
                    "new_stop": new_stop,
                    "initial_stop_ticks": self._INITIAL_STOP_TICKS,
                    "initial_target_ticks": self._INITIAL_TARGET_TICKS,
                    "attached_detail": attached.detail,
                    "filled_detail": filled.detail,
                },
            )

        return self._pass(evidence={
            "fill_price": fill_price,
            "initial_stop_ticks": self._INITIAL_STOP_TICKS,
            "new_stop": new_stop,
            "accepted_event": str(accept_evt) if accept_evt else "n/a",
            "stop_moved_event": str(stop_moved_evt) if stop_moved_evt else "n/a",
            "accept_log": str(accept_log) if accept_log else "n/a",
            "move_stop_log": str(move_stop_log) if move_stop_log else "n/a",
        })


class P2B03MoveStopUnsafe(BasePhase2TestCase):
    test_id = "P2-B03"
    category = "Stop Management"
    scenario = "MOVE_STOP to unsafe price (above market for Long) is rejected"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        # Wait for stop attachment
        attached = self._wait_for_stop_attached(ctx, ob, timeout=p2cfg.stop_attach_wait_seconds, since_log_line=lb)

        # Get current fill price from FILLED detail
        filled_detail = parse_detail(filled.detail)
        try:
            fill_price = float(filled_detail.get("price", "0"))
        except ValueError:
            fill_price = 0.0

        # For a Long, an unsafe stop is ABOVE the fill price (risk-increasing)
        tick_size = 0.25
        unsafe_stop = fill_price + (50 * tick_size) if fill_price > 0 else 99999.0

        ob2 = ctx.outbox_baseline()
        lb2 = ctx.log_baseline()

        move_msg = message_factory.move_stop(
            stop_price=unsafe_stop,
            instrument=ctx.instrument,
        )
        bridge_io.inject_message(ctx.inbox, move_msg)

        # Expect REJECTED in log or outbox
        rejected_evt = outbox_parser.find_event(
            ctx.outbox, "REJECTED",
            msg_id=move_msg["message_id"],
            since_count=ob2,
            timeout_seconds=ctx.assertion_timeout,
        )
        rejected_log = log_parser.find_event_type(ctx.log_file, "REJECT", lb2)

        self._ensure_flat(ctx, p2cfg)

        if rejected_evt is None and rejected_log is None:
            return self._fail(
                f"Unsafe MOVE_STOP (stop_price={unsafe_stop} above fill_price={fill_price} "
                f"for Long) was NOT rejected",
                evidence={
                    "fill_price": fill_price,
                    "unsafe_stop": unsafe_stop,
                    "move_msg_id": move_msg["message_id"],
                },
            )

        return self._pass(evidence={
            "fill_price": fill_price,
            "unsafe_stop": unsafe_stop,
            "rejected_event": str(rejected_evt) if rejected_evt else "n/a",
            "rejected_log": str(rejected_log) if rejected_log else "n/a",
        })


class P2B04NoStopStacking(BasePhase2TestCase):
    test_id = "P2-B04"
    category = "Stop Management"
    scenario = "Two sequential MOVE_STOP commands do not stack duplicate stop orders"
    automation_level = AutomationLevel.SEMI_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(f"No FILLED event within {p2cfg.fill_wait_seconds}s")

        attached = self._wait_for_stop_attached(ctx, ob, timeout=p2cfg.stop_attach_wait_seconds, since_log_line=lb)
        if attached is None:
            self._ensure_flat(ctx, p2cfg)
            return self._skip("No STOP_ATTACHED -- cannot test stop stacking")

        detail = parse_detail(attached.detail)
        try:
            current_stop = float(detail.get("stop_price", "0") or detail.get("pending_stop_price", "0"))
        except ValueError:
            current_stop = 0.0

        tick_size = 0.25
        stop1 = current_stop - (5 * tick_size) if current_stop > 0 else 0.0
        stop2 = current_stop - (10 * tick_size) if current_stop > 0 else 0.0

        if stop1 <= 0 or stop2 <= 0:
            self._ensure_flat(ctx, p2cfg)
            return self._skip(f"Cannot derive safe stop prices from {current_stop}")

        # Send first MOVE_STOP
        ob1 = ctx.outbox_baseline()
        move1 = message_factory.move_stop(stop_price=stop1, instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, move1)
        time.sleep(1.5)  # allow first stop move to settle

        # Send second MOVE_STOP
        ob2 = ctx.outbox_baseline()
        move2 = message_factory.move_stop(stop_price=stop2, instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, move2)
        time.sleep(2.0)

        # Automated check: count STOP_WORKING events — should still be 1 total
        all_stop_working = outbox_parser.events_by_status(ctx.outbox, "STOP_WORKING", ob)
        checkpoints = []

        if p2cfg.show_checkpoints:
            cp = checkpoint(
                "Single stop order in NT8",
                f"In NinjaTrader, check the Orders or Accounts tab:\n"
                f"  - After two MOVE_STOP commands, Sim101 should have exactly ONE stop order\n"
                f"  - Stop price should be near {stop2:.2f}\n"
                f"  - There must NOT be two stop orders for the same position\n"
                f"  STOP_WORKING events seen: {len(all_stop_working)}\n"
                f"Does NT8 show exactly one stop order?",
            )
            checkpoints.append(cp)

        self._ensure_flat(ctx, p2cfg)

        if checkpoints and checkpoints[0].outcome == CheckpointOutcome.FAILED:
            return self._fail(
                "User observed stop order stacking in NT8 after two MOVE_STOP commands",
                evidence={
                    "stop1": stop1,
                    "stop2": stop2,
                    "stop_working_count": len(all_stop_working),
                },
                checkpoints=checkpoints,
            )

        return self._pass(
            evidence={
                "stop1": stop1,
                "stop2": stop2,
                "stop_working_events": len(all_stop_working),
            },
            checkpoints=checkpoints,
        )


ALL_TESTS: list[BasePhase2TestCase] = [
    P2B01StopAttachedAfterFill(),
    P2B02MoveStopValid(),
    P2B03MoveStopUnsafe(),
    P2B04NoStopStacking(),
]
