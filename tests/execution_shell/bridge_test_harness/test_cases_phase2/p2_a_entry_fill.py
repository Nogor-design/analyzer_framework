"""
Phase 2 Group A: Entry order submission and fill detection.

All tests require DryRunMode=false, Sim101 account, live/replay ticks.

P2-A01  FULLY_AUTOMATED  ENTER_LONG accepted -> ACCEPTED event + mode=EntryPending
P2-A02  FULLY_AUTOMATED  ENTER_LONG fills    -> FILLED event with required fields
P2-A03  SEMI_AUTOMATED   FILLED detail correctness (qty, side, remaining_qty)
P2-A04  FULLY_AUTOMATED  Duplicate ENTER while EntryPending is gate-rejected
P2-A05  FULLY_AUTOMATED  ENTER_SHORT accepted -> ACCEPTED + fills -> FILLED
"""
from __future__ import annotations

import time

from bridge_test_harness import bridge_io, log_parser, outbox_parser
from bridge_test_harness import message_factory
from bridge_test_harness.health_parser import (
    wait_for_long, wait_for_short, parse_detail,
    SIDE_LONG, SIDE_SHORT, SIDE_FLAT,
)
from bridge_test_harness.checkpoint import checkpoint, CheckpointOutcome
from bridge_test_harness.test_cases_phase2.base_phase2 import (
    AutomationLevel, Phase2Config, Phase2TestResult,
    BasePhase2TestCase, require_flat_precondition,
)
from bridge_test_harness.test_cases.base import TestContext


class P2A01EnterLongAccepted(BasePhase2TestCase):
    test_id = "P2-A01"
    category = "Entry / Fill"
    scenario = "ENTER_LONG is accepted and shell transitions to EntryPending"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        msg = message_factory.base_enter_long(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=p2cfg.default_stop_ticks,
        )
        bridge_io.inject_message(ctx.inbox, msg)
        msg_id = msg["message_id"]

        # Expect ACCEPTED outbox event
        accept_evt = outbox_parser.find_event(
            ctx.outbox, "ACCEPTED", msg_id=msg_id,
            since_count=ob, timeout_seconds=ctx.assertion_timeout,
        )
        if accept_evt is None:
            return self._fail(
                "No ACCEPTED outbox event for ENTER_LONG",
                evidence={"msg_id": msg_id},
            )

        # Expect shell mode = EntryPending in outbox event
        if accept_evt.shell_mode not in ("EntryPending", "InPosition"):
            return self._fail(
                f"Expected shell_mode EntryPending or InPosition after ENTER_LONG, "
                f"got {accept_evt.shell_mode!r}",
                evidence={"accept_event": str(accept_evt)},
            )

        # Verify ACCEPT log record
        accept_log = log_parser.find_event_type(ctx.log_file, "ACCEPT", lb)
        if accept_log is None:
            return self._fail(
                "ACCEPT log record not found after ENTER_LONG",
                evidence={"msg_id": msg_id},
            )

        # Clean up: flatten for subsequent tests (best-effort)
        self._ensure_flat(ctx, p2cfg)

        return self._pass(evidence={
            "accept_event": str(accept_evt),
            "log_mode": accept_log.mode,
        })


class P2A02EnterLongFills(BasePhase2TestCase):
    test_id = "P2-A02"
    category = "Entry / Fill"
    scenario = "ENTER_LONG fills on Sim101: FILLED outbox, FILL log, or HEALTH pos=Long"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        msg = message_factory.base_enter_long(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=p2cfg.default_stop_ticks,
        )
        bridge_io.inject_message(ctx.inbox, msg)

        # Evidence tier 1: FILLED outbox event (preferred)
        filled = self._wait_for_filled_event(ctx, ob, p2cfg.fill_wait_seconds)

        # Evidence tier 2: FILL log event (shell writes this even if outbox is slow)
        fill_log = log_parser.find_event_type(
            ctx.log_file, "FILL", since_line=lb, timeout_seconds=p2cfg.fill_wait_seconds
        )

        # Evidence tier 3: HEALTH snapshot shows pos=Long qty>=1
        long_in_health = wait_for_long(ctx.log_file, since_line=lb,
                                       timeout_seconds=p2cfg.fill_wait_seconds)

        self._ensure_flat(ctx, p2cfg)

        if filled is None and fill_log is None and not long_in_health:
            return self._fail(
                f"No fill evidence within {p2cfg.fill_wait_seconds}s for ENTER_LONG "
                f"(checked: FILLED outbox, FILL log, pos=Long in log)",
                evidence={"msg_id": msg["message_id"]},
            )

        return self._pass(evidence={
            "filled_outbox": str(filled) if filled else "not observed",
            "fill_log": str(fill_log) if fill_log else "not observed",
            "long_in_log": bool(long_in_health),
        })


class P2A03FilledDetailCorrectness(BasePhase2TestCase):
    test_id = "P2-A03"
    category = "Entry / Fill"
    scenario = "FILLED event detail contains order_id, side, qty, price, remaining_qty, pos_side"
    automation_level = AutomationLevel.SEMI_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_LONG)
        if filled is None:
            return self._skip(
                f"No FILLED event within {p2cfg.fill_wait_seconds}s -- "
                "ensure Sim101 is receiving ticks"
            )

        detail = parse_detail(filled.detail)
        checkpoints = []
        failures = []

        # Automated field checks
        required_keys = ["order_id", "signal", "side", "qty", "price", "remaining_qty", "pos_side"]
        missing = [k for k in required_keys if k not in detail]
        if missing:
            failures.append(f"FILLED detail missing keys: {missing}")

        if detail.get("side", "").upper() not in ("LONG", "BUY", "Long"):
            failures.append(f"Expected LONG side in FILLED detail, got {detail.get('side')!r}")

        try:
            remaining = int(detail.get("remaining_qty", "-1"))
            if remaining < 0:
                failures.append(f"remaining_qty={remaining} is negative")
        except ValueError:
            failures.append(f"remaining_qty is not numeric: {detail.get('remaining_qty')!r}")

        # Human checkpoint: verify NT8 Accounts tab shows 1 Long contract
        if p2cfg.show_checkpoints:
            cp = checkpoint(
                "NT8 position visible",
                f"In NinjaTrader, check the Accounts tab or Positions tab:\n"
                f"  - Sim101 should show a LONG position of {p2cfg.entry_qty} contract(s)\n"
                f"  - FILLED detail: {filled.detail}\n"
                f"Does the NT8 UI show the expected long position?",
            )
            checkpoints.append(cp)
            if cp.outcome == CheckpointOutcome.FAILED:
                failures.append("User reported position mismatch in NT8 UI")

        self._ensure_flat(ctx, p2cfg)

        if failures:
            return self._fail(
                "; ".join(failures),
                evidence={"filled_detail": filled.detail, "parsed": str(detail)},
                checkpoints=checkpoints,
            )

        return self._pass(
            evidence={"filled_detail": filled.detail, "parsed_keys": list(detail.keys())},
            checkpoints=checkpoints,
        )


class P2A04DuplicateEntryGated(BasePhase2TestCase):
    test_id = "P2-A04"
    category = "Entry / Fill"
    scenario = "Second ENTER_LONG while EntryPending is gate-rejected"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        # First entry — expect acceptance
        msg1 = message_factory.base_enter_long(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=p2cfg.default_stop_ticks,
        )
        bridge_io.inject_message(ctx.inbox, msg1)

        # Wait briefly for shell to enter EntryPending / InPosition state
        time.sleep(0.8)

        # Second entry — should be gate-rejected
        msg2 = message_factory.base_enter_long(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=p2cfg.default_stop_ticks,
        )
        bridge_io.inject_message(ctx.inbox, msg2)

        # The second message should appear as REJECTED in outbox
        # (either GATE_BLOCKED or REJECTED status depending on shell state)
        deadline = time.monotonic() + ctx.assertion_timeout
        second_rejected = None
        while time.monotonic() < deadline:
            events = outbox_parser.events_since(ctx.outbox, ob)
            for evt in events:
                if evt.status in ("REJECTED", "GATE_BLOCKED", "DUPLICATE"):
                    if msg2["message_id"] in evt.instruction_id or msg2["message_id"] in evt.detail:
                        second_rejected = evt
                        break
                # Also accept if it shows as a GATE log record
            if second_rejected:
                break
            time.sleep(0.3)

        # Also check log for GATE record referencing second msg
        gate_rec = log_parser.find_event_for_msg(
            ctx.log_file, "GATE", msg2["message_id"], lb
        )

        self._ensure_flat(ctx, p2cfg)

        if second_rejected is None and gate_rec is None:
            return self._fail(
                "Second ENTER_LONG while in-trade was not gate-rejected "
                "(no REJECTED outbox event and no GATE log record found)",
                evidence={
                    "msg1_id": msg1["message_id"],
                    "msg2_id": msg2["message_id"],
                },
            )

        return self._pass(evidence={
            "gate_log": str(gate_rec) if gate_rec else "n/a",
            "rejected_event": str(second_rejected) if second_rejected else "n/a",
        })


class P2A05EnterShortFills(BasePhase2TestCase):
    test_id = "P2-A05"
    category = "Entry / Fill"
    scenario = "ENTER_SHORT accepted, fills on Sim101, FILLED event with side=Short"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        filled, lb, ob = self._enter_and_wait_for_fill(ctx, p2cfg, SIDE_SHORT)
        if filled is None:
            return self._fail(
                f"No FILLED outbox event within {p2cfg.fill_wait_seconds}s for ENTER_SHORT",
            )

        # Verify side
        detail = parse_detail(filled.detail)
        side_val = detail.get("side", detail.get("pos_side", ""))
        if "short" not in side_val.lower() and "sell" not in side_val.lower():
            self._ensure_flat(ctx, p2cfg)
            return self._fail(
                f"Expected Short side in FILLED detail, got {side_val!r}",
                evidence={"filled_detail": filled.detail},
            )

        # Verify log
        short_rec = wait_for_short(ctx.log_file, since_line=lb, timeout_seconds=10.0)
        if short_rec is None:
            self._ensure_flat(ctx, p2cfg)
            return self._fail(
                "Log never showed pos=Short after FILLED event",
                evidence={"filled_event": str(filled)},
            )

        self._ensure_flat(ctx, p2cfg)

        return self._pass(evidence={
            "filled_detail": filled.detail,
            "log_pos": short_rec.pos,
        })


ALL_TESTS: list[BasePhase2TestCase] = [
    P2A01EnterLongAccepted(),
    P2A02EnterLongFills(),
    P2A03FilledDetailCorrectness(),
    P2A04DuplicateEntryGated(),
    P2A05EnterShortFills(),
]
