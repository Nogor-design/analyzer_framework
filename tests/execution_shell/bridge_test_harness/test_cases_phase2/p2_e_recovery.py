"""
Phase 2 Group E: Restart and recovery while in position.

P2-E01  FULLY_AUTOMATED  SHELL_READY event written on strategy load (clean start)
P2-E02  FULLY_AUTOMATED  SHELL_READY after restart with no open position: mode=Idle
P2-E03  MANUAL_REQUIRED  Restart NT8 WHILE in position: shell recovers state, stop re-attached
P2-E04  MANUAL_REQUIRED  Restart NT8 WHILE in position: new ENTER blocked until exit
P2-E05  FULLY_AUTOMATED  After clean restart (no position): new ENTER_LONG accepted normally
"""
from __future__ import annotations

import time

from bridge_test_harness import bridge_io, log_parser, outbox_parser
from bridge_test_harness import message_factory
from bridge_test_harness.health_parser import (
    wait_for_flat, wait_for_long, parse_detail, SIDE_LONG, SIDE_FLAT,
)
from bridge_test_harness.checkpoint import print_manual_procedure
from bridge_test_harness.test_cases_phase2.base_phase2 import (
    AutomationLevel, Phase2Config, Phase2TestResult,
    BasePhase2TestCase, require_flat_precondition,
)
from bridge_test_harness.test_cases.base import TestContext


class P2E01ShellReadyOnLoad(BasePhase2TestCase):
    test_id = "P2-E01"
    category = "Recovery"
    scenario = "SHELL_READY outbox event written when strategy initializes"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        # SHELL_READY is emitted during DataLoaded -- it may already be in outbox
        # from the current session.  Look through all outbox events (since_count=0).
        all_events = outbox_parser.read_all_events(ctx.outbox)
        ready_events = [e for e in all_events if e.status == "SHELL_READY"]

        if not ready_events:
            return self._fail(
                "No SHELL_READY event found in outbox. "
                "Ensure the Phase 2 shell changes are deployed and NT8 has been "
                "started at least once this session.",
                evidence={"outbox_event_count": len(all_events)},
            )

        latest = ready_events[-1]
        from bridge_test_harness.health_parser import parse_detail
        detail = parse_detail(latest.detail)

        return self._pass(evidence={
            "shell_ready_event": str(latest),
            "detail": latest.detail,
            "mode": detail.get("mode", "?"),
            "intake": detail.get("intake", "?"),
        })


class P2E02ShellReadyAfterRestartNoPosition(BasePhase2TestCase):
    test_id = "P2-E02"
    category = "Recovery"
    scenario = "SHELL_READY after restart with no open position shows mode=Idle"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        # Find the most recent SHELL_READY event in outbox
        all_events = outbox_parser.read_all_events(ctx.outbox)
        ready_events = [e for e in all_events if e.status == "SHELL_READY"]

        if not ready_events:
            return self._skip(
                "No SHELL_READY event in outbox -- run P2-E01 first or restart NT8"
            )

        latest = ready_events[-1]
        from bridge_test_harness.health_parser import parse_detail
        detail = parse_detail(latest.detail)

        mode = detail.get("mode", "")
        recovered_mode = detail.get("recovered_mode", "")

        # After a clean start (no position), mode should be Idle
        if mode not in ("Idle", ""):
            return self._fail(
                f"SHELL_READY shows mode={mode!r} but expected Idle on clean start",
                evidence={"detail": latest.detail},
            )

        return self._pass(evidence={
            "mode": mode,
            "recovered_mode": recovered_mode,
            "intake": detail.get("intake", "?"),
            "hb_faulted": detail.get("hb_faulted", "?"),
        })


class P2E03RestartWhileInPosition(BasePhase2TestCase):
    test_id = "P2-E03"
    category = "Recovery"
    scenario = "Restart NT8 while in position: shell recovers, stop re-attached (MANUAL)"
    automation_level = AutomationLevel.MANUAL_REQUIRED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        print_manual_procedure(
            test_id=self.test_id,
            scenario=self.scenario,
            steps=[
                "Use the test runner to inject ENTER_LONG and wait for a fill "
                "(run P2-A02 manually or inject via bridge_io directly).",
                "Confirm the Long position and stop order are visible in NT8 UI.",
                "While the position is open, close NinjaTrader completely (do not exit position first).",
                "Reopen NinjaTrader and load the TaFoundationExecutionShell strategy on the same chart.",
                "Observe: does SHELL_READY event appear in outbox with recovered_mode=InPosition?",
                "Observe: does a new stop order get re-attached (STOP_ATTACHED / STOP_WORKING events)?",
                "Verify the position is still visible in NT8 Accounts tab.",
                "Send EXIT_ALL to cleanly exit the recovered position.",
            ],
            expected_outcomes=[
                "SHELL_READY outbox event written with mode=InPosition (or Recovery).",
                "Stop order re-attached for the open position (STOP_ATTACHED event in outbox).",
                "Shell transitions back to InPosition state, accepting MOVE_STOP/EXIT signals.",
                "EXIT_ALL successfully flattens the position after recovery.",
            ],
        )
        return self._skip("MANUAL_REQUIRED -- see printed procedure above")


class P2E04RestartBlocksNewEntryWhileInPosition(BasePhase2TestCase):
    test_id = "P2-E04"
    category = "Recovery"
    scenario = "After restart in-position, ENTER_LONG is blocked until existing position exits (MANUAL)"
    automation_level = AutomationLevel.MANUAL_REQUIRED

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        print_manual_procedure(
            test_id=self.test_id,
            scenario=self.scenario,
            steps=[
                "Perform P2-E03 recovery steps (restart NT8 while in a Long position).",
                "After shell shows SHELL_READY and has recovered the position, "
                "inject a new ENTER_LONG message via the bridge.",
                "Observe the shell response.",
                "Then send EXIT_ALL to flatten and verify the position closes.",
            ],
            expected_outcomes=[
                "New ENTER_LONG while already InPosition is gate-rejected (GATE log record or REJECTED event).",
                "Shell does NOT open a second position.",
                "EXIT_ALL after rejection successfully flattens the original position.",
            ],
        )
        return self._skip("MANUAL_REQUIRED -- see printed procedure above")


class P2E05CleanRestartAcceptsEntry(BasePhase2TestCase):
    test_id = "P2-E05"
    category = "Recovery"
    scenario = "After clean restart (flat), ENTER_LONG accepted: ACCEPTED outbox, ACCEPT log, FILL log, or pos=Long"
    automation_level = AutomationLevel.FULLY_AUTOMATED

    # How long to wait for the shell to stabilise after SHELL_READY before
    # injecting the entry.  Prevents the stale-signal window on slow restarts.
    _POST_READY_SETTLE: float = 2.0

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        err = require_flat_precondition(ctx, p2cfg)
        if err:
            return self._skip(f"Precondition failed: {err}")

        # ── Pre-flight: verify SHELL_READY shows intake=True ──────────────────
        # LoadPersistentState() restores signalIntakeEnabled from the state file.
        # If the prior session ended with a heartbeat fault, intake=False is
        # restored and every ENTER_LONG will be silently ignored.
        all_events = outbox_parser.read_all_events(ctx.outbox)
        ready_events = [e for e in all_events if e.status == "SHELL_READY"]
        if ready_events:
            latest_ready = ready_events[-1]
            detail_kv = parse_detail(latest_ready.detail)
            intake_val = detail_kv.get("intake", "true").lower()
            if intake_val in ("false", "0", "no"):
                return self._skip(
                    f"SHELL_READY shows intake={intake_val} — signal intake is disabled. "
                    "Shell state file likely has SignalIntakeEnabled=false from a prior "
                    "heartbeat fault or FlattenAndDisable call. "
                    "Fix: restart NT8 after clearing shell_state.json, OR the shell "
                    "auto-resets intake on clean flat restart (see shell fix notes)."
                )

        # Give the shell a moment to fully settle after DataLoaded before we
        # inject — avoids a race where the first OnBarUpdate hasn't run yet
        time.sleep(self._POST_READY_SETTLE)

        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()

        enter_msg = message_factory.base_enter_long(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=p2cfg.default_stop_ticks,
            target_ticks=p2cfg.default_target_ticks,
        )
        bridge_io.inject_message(ctx.inbox, enter_msg)
        msg_id = enter_msg["message_id"]

        # Poll all evidence tiers simultaneously.
        # Tier 1 — outbox ACCEPTED (shell writes this in TryReadInstructionFile)
        # Tier 2 — log ACCEPT record (written same moment as outbox ACCEPTED)
        # Tier 3 — log FILL record (entry was accepted AND filled)
        # Tier 4 — pos=Long visible in any log header (confirms fill)
        accept_outbox = None
        accept_log = None
        fill_log = None
        long_in_log = None

        deadline = time.monotonic() + p2cfg.fill_wait_seconds
        while time.monotonic() < deadline:
            if accept_outbox is None:
                for evt in outbox_parser.events_since(ctx.outbox, ob):
                    if evt.status == "ACCEPTED" and msg_id in evt.instruction_id:
                        accept_outbox = evt
                        break
            if accept_log is None:
                accept_log = log_parser.find_event_for_msg(
                    ctx.log_file, "ACCEPT", msg_id, lb
                )
            if fill_log is None:
                fill_log = next(
                    (r for r in log_parser.read_records(ctx.log_file, lb)
                     if r.event_type == "FILL"),
                    None,
                )
            if long_in_log is None:
                long_in_log = wait_for_long(
                    ctx.log_file, since_line=lb, timeout_seconds=0.1
                )

            if any([accept_outbox, accept_log, fill_log, long_in_log]):
                break
            time.sleep(0.5)

        self._ensure_flat(ctx, p2cfg)

        accepted = any([accept_outbox, accept_log, fill_log, long_in_log])
        if not accepted:
            # Gather diagnostic context to explain WHY nothing was seen
            intake_state = "unknown"
            if ready_events:
                intake_state = parse_detail(ready_events[-1].detail).get("intake", "unknown")

            # Check if the message landed in rejected/ folder
            rejected_in_log = next(
                (r for r in log_parser.read_records(ctx.log_file, lb)
                 if r.event_type in ("REJECT", "REJECT_GATE") and msg_id in r.raw),
                None,
            )

            return self._fail(
                "No acceptance evidence for ENTER_LONG after clean restart",
                evidence={
                    "msg_id": msg_id,
                    "shell_ready_intake": intake_state,
                    "rejected_log": str(rejected_in_log) if rejected_in_log else "none",
                    "checked": "ACCEPTED outbox, ACCEPT log, FILL log, pos=Long",
                    "diagnosis": (
                        "intake disabled — check state file" if intake_state in ("false", "False", "0")
                        else "intake enabled but message not processed — check inbox path, "
                             "stale-signal window, or OnBarUpdate not firing (no live ticks?)"
                    ),
                },
            )

        return self._pass(evidence={
            "accept_outbox": str(accept_outbox) if accept_outbox else "not observed",
            "accept_log": str(accept_log) if accept_log else "not observed",
            "fill_log": str(fill_log) if fill_log else "not observed",
            "long_in_log": bool(long_in_log),
        })


ALL_TESTS: list[BasePhase2TestCase] = [
    P2E01ShellReadyOnLoad(),
    P2E02ShellReadyAfterRestartNoPosition(),
    P2E03RestartWhileInPosition(),
    P2E04RestartBlocksNewEntryWhileInPosition(),
    P2E05CleanRestartAcceptsEntry(),
]
