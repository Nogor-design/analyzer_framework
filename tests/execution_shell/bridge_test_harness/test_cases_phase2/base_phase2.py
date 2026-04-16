"""
Base classes and shared utilities for Phase 2 SIM-account managed-order tests.

Phase 2 tests require DryRunMode=false with Sim101 account.  They observe real
NT8 order lifecycle events (FILLED, STOP_ATTACHED, STOP_WORKING) via the outbox
and the log `pos=` / `qty=` fields.

Three automation tiers
----------------------
FULLY_AUTOMATED   -- No human involvement; all assertions derived from files.
SEMI_AUTOMATED    -- Automated orchestration with human checkpoints at critical
                     observations (e.g. "confirm stop order visible in NT8").
MANUAL_REQUIRED   -- Procedure printed; test always SKIPs; operator marks pass
                     externally (e.g. cases that require mid-test NT8 restarts).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from bridge_test_harness.test_cases.base import TestContext, TestResult
from bridge_test_harness import bridge_io, log_parser, outbox_parser
from bridge_test_harness import message_factory
from bridge_test_harness.health_parser import (
    get_latest_health,
    wait_for_position_side,
    wait_for_flat,
    wait_for_long,
    wait_for_short,
    wait_for_qty,
    SIDE_LONG, SIDE_SHORT, SIDE_FLAT,
)
from bridge_test_harness.checkpoint import (
    CheckpointResult,
    CheckpointOutcome,
    CheckpointSequence,
    checkpoint,
    auto_checkpoint,
    print_manual_procedure,
)
from bridge_test_harness.acceptance_evaluator import EvidenceBundle, AcceptanceEvaluator
from bridge_test_harness.acceptance_result import (
    AcceptanceResult, AcceptanceStatus, ProofStrength,
)


# ---------------------------------------------------------------------------
# Automation level
# ---------------------------------------------------------------------------

class AutomationLevel(Enum):
    FULLY_AUTOMATED = "FULLY_AUTOMATED"
    SEMI_AUTOMATED  = "SEMI_AUTOMATED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


# ---------------------------------------------------------------------------
# Phase 2 config (subset of test_config.json "phase2" block)
# ---------------------------------------------------------------------------

@dataclass
class Phase2Config:
    """Phase 2 runtime parameters loaded from test_config.json["phase2"]."""
    # NT8 sim account name (must match strategy's Account parameter)
    sim_account: str = "Sim101"
    # Entry order quantity
    entry_qty: int = 1
    # Initial stop distance in ticks for lifecycle tests (keep wide so the
    # bracket cannot fire during a 30-60s test window — 200 ticks ≈ 50 NQ pts)
    default_stop_ticks: int = 50
    # Profit-target distance for lifecycle tests (same reasoning as stop)
    default_target_ticks: int = 200
    # Ticks to move stop to when testing MOVE_STOP
    move_stop_ticks: int = 10
    # Partial exit quantity
    partial_qty: int = 1
    # Seconds to wait for a Sim101 fill after order submission
    fill_wait_seconds: float = 30.0
    # Seconds to wait for stop order to become Working after fill
    stop_attach_wait_seconds: float = 15.0
    # Brief pause after flat-confirmed before next entry, to let NT8 finish
    # processing bracket-cancel I/O from the previous trade
    post_flat_settle_seconds: float = 1.5
    # Whether to show user checkpoints (False in fully-automated CI runs)
    show_checkpoints: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Phase2Config":
        return cls(
            sim_account=d.get("sim_account", "Sim101"),
            entry_qty=int(d.get("entry_qty", 1)),
            default_stop_ticks=int(d.get("default_stop_ticks", 50)),
            default_target_ticks=int(d.get("default_target_ticks", 200)),
            move_stop_ticks=int(d.get("move_stop_ticks", 10)),
            partial_qty=int(d.get("partial_qty", 1)),
            fill_wait_seconds=float(d.get("fill_wait_seconds", 30.0)),
            stop_attach_wait_seconds=float(d.get("stop_attach_wait_seconds", 15.0)),
            post_flat_settle_seconds=float(d.get("post_flat_settle_seconds", 1.5)),
            show_checkpoints=bool(d.get("show_checkpoints", True)),
        )


# ---------------------------------------------------------------------------
# Phase 2 test result (extends TestResult with automation metadata)
# ---------------------------------------------------------------------------

@dataclass
class Phase2TestResult:
    """Test result for a Phase 2 test case."""
    test_id: str
    category: str
    scenario: str
    automation_level: AutomationLevel
    passed: bool
    skipped: bool = False
    skip_reason: str = ""
    failure_diagnostics: str = ""
    elapsed_seconds: float = 0.0
    checkpoint_results: list[CheckpointResult] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)  # key -> description of evidence found
    acceptance_result: Optional[AcceptanceResult] = None  # set when spec evaluated

    def to_test_result(self) -> TestResult:
        """Convert to the Phase 1 TestResult format for the shared report writer."""
        # If we have an acceptance result, use richer diagnostics from it.
        if self.acceptance_result is not None:
            acc = self.acceptance_result
            lines: list[str] = []
            lines.append(f"  spec_status    : {acc.status.value}")
            lines.append(f"  proof_strength : {acc.proof_strength.value}")
            if acc.automation_level:
                lines.append(f"  automation     : {acc.automation_level}")
            if acc.blocking_for_sim_readiness and not acc.passed:
                lines.append("  ** BLOCKING FOR SIM READINESS **")
            if acc.matched_requirements:
                lines.append("  matched:")
                for m in acc.matched_requirements:
                    lines.append(f"    + {m}")
            if acc.unmet_requirements:
                lines.append("  unmet:")
                for u in acc.unmet_requirements:
                    lines.append(f"    - {u}")
            if acc.forbidden_matches:
                lines.append("  FORBIDDEN present:")
                for f in acc.forbidden_matches:
                    lines.append(f"    ! {f}")
            if acc.downgrade_reason:
                lines.append(f"  downgrade: {acc.downgrade_reason}")
            diag = "\n".join(lines)
            if self.failure_diagnostics:
                diag = self.failure_diagnostics + "\n" + diag
        else:
            cp_lines = [str(r) for r in self.checkpoint_results]
            evidence_lines = [f"  evidence[{k}]: {v}" for k, v in self.evidence.items()]
            diag = self.failure_diagnostics
            if cp_lines:
                diag = (diag + "\n  Checkpoints:\n  " + "\n  ".join(cp_lines)).strip()
            if evidence_lines:
                diag = (diag + "\n" + "\n".join(evidence_lines)).strip()

        return TestResult(
            test_id=self.test_id,
            category=f"{self.category} [{self.automation_level.value}]",
            scenario=self.scenario,
            passed=self.passed,
            skipped=self.skipped,
            skip_reason=self.skip_reason,
            failure_diagnostics=diag,
            elapsed_seconds=self.elapsed_seconds,
        )


# ---------------------------------------------------------------------------
# Base Phase 2 test case
# ---------------------------------------------------------------------------

class BasePhase2TestCase:
    """
    Base class for all Phase 2 test cases.

    Subclasses implement `_run_phase2(ctx, p2cfg) -> Phase2TestResult`.
    The `run()` method wraps with timing, exception handling, and optional
    spec-driven acceptance evaluation.

    Spec integration
    ----------------
    When a spec_loader is supplied to run(), the test case should call
    `self._try_spec_evaluate(bundle)` near the end of _run_phase2() to let
    the acceptance engine make the final pass/fail determination.  If no
    spec_loader is configured, _try_spec_evaluate() returns None and the
    test case falls back to its own _pass()/_fail() logic.
    """

    test_id: str = "P2-XX"
    category: str = "Phase2"
    scenario: str = ""
    automation_level: AutomationLevel = AutomationLevel.FULLY_AUTOMATED

    # Set by run() when a spec_loader is provided; accessible in _run_phase2
    _spec_loader: Optional[dict] = None   # dict[str, AcceptanceSpec] from load_all_specs

    def run(
        self,
        ctx: TestContext,
        p2cfg: Phase2Config,
        spec_loader: Optional[dict] = None,
    ) -> Phase2TestResult:
        self._spec_loader = spec_loader
        t0 = time.monotonic()
        try:
            result = self._run_phase2(ctx, p2cfg)
        except Exception as exc:
            result = Phase2TestResult(
                test_id=self.test_id,
                category=self.category,
                scenario=self.scenario,
                automation_level=self.automation_level,
                passed=False,
                failure_diagnostics=f"Unhandled exception: {exc}",
                elapsed_seconds=time.monotonic() - t0,
            )
        result.elapsed_seconds = time.monotonic() - t0
        return result

    def _run_phase2(self, ctx: TestContext, p2cfg: Phase2Config) -> Phase2TestResult:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Spec integration helpers
    # ------------------------------------------------------------------

    def _try_spec_evaluate(
        self,
        bundle: EvidenceBundle,
    ) -> Optional[Phase2TestResult]:
        """
        Evaluate the acceptance spec for this test against the evidence bundle.

        Returns a Phase2TestResult if a spec is loaded, None otherwise.
        Call this at the end of _run_phase2() after all evidence is collected
        but BEFORE any cleanup signals (EXIT_ALL, etc.) are sent.
        """
        if self._spec_loader is None:
            return None
        spec = self._spec_loader.get(self.test_id)
        if spec is None:
            return None
        acc = AcceptanceEvaluator.evaluate(spec, bundle)
        return self._acceptance_to_result(acc)

    def _acceptance_to_result(self, acc: AcceptanceResult) -> Phase2TestResult:
        """Convert an AcceptanceResult into a Phase2TestResult."""
        status_to_passed_skipped = {
            AcceptanceStatus.PASS:             (True,  False),
            AcceptanceStatus.PASS_UNVERIFIED:  (True,  False),
            AcceptanceStatus.FAIL:             (False, False),
            AcceptanceStatus.SKIP:             (False, True),
            AcceptanceStatus.MANUAL_REQUIRED:  (False, True),
        }
        passed, skipped = status_to_passed_skipped.get(acc.status, (False, False))

        # Build failure diagnostics from unmet requirements and forbidden matches
        diag_parts: list[str] = []
        if not passed and not skipped:
            for u in acc.unmet_requirements:
                if not u.optional and not u.downgraded:
                    diag_parts.append(str(u))
            for f in acc.forbidden_matches:
                diag_parts.append(str(f))

        skip_reason = ""
        if skipped:
            if acc.status == AcceptanceStatus.MANUAL_REQUIRED:
                skip_reason = "MANUAL_REQUIRED -- see printed procedure"
            else:
                skip_reason = acc.notes or "skipped"

        return Phase2TestResult(
            test_id=self.test_id,
            category=self.category,
            scenario=self.scenario,
            automation_level=self.automation_level,
            passed=passed,
            skipped=skipped,
            skip_reason=skip_reason,
            failure_diagnostics="; ".join(diag_parts),
            checkpoint_results=[],
            evidence={
                "spec_status": acc.status.value,
                "proof_strength": acc.proof_strength.value,
                "matched": [str(m) for m in acc.matched_requirements],
                "unmet": [str(u) for u in acc.unmet_requirements],
                "downgrade_reason": acc.downgrade_reason or "",
            },
            acceptance_result=acc,
        )

    # ------------------------------------------------------------------
    # Convenience result builders
    # ------------------------------------------------------------------

    def _pass(
        self,
        evidence: Optional[dict] = None,
        checkpoints: Optional[list[CheckpointResult]] = None,
    ) -> Phase2TestResult:
        return Phase2TestResult(
            test_id=self.test_id,
            category=self.category,
            scenario=self.scenario,
            automation_level=self.automation_level,
            passed=True,
            checkpoint_results=checkpoints or [],
            evidence=evidence or {},
        )

    def _fail(
        self,
        reason: str,
        evidence: Optional[dict] = None,
        checkpoints: Optional[list[CheckpointResult]] = None,
    ) -> Phase2TestResult:
        return Phase2TestResult(
            test_id=self.test_id,
            category=self.category,
            scenario=self.scenario,
            automation_level=self.automation_level,
            passed=False,
            failure_diagnostics=reason,
            checkpoint_results=checkpoints or [],
            evidence=evidence or {},
        )

    def _skip(self, reason: str) -> Phase2TestResult:
        return Phase2TestResult(
            test_id=self.test_id,
            category=self.category,
            scenario=self.scenario,
            automation_level=self.automation_level,
            passed=False,
            skipped=True,
            skip_reason=reason,
        )

    # ------------------------------------------------------------------
    # Position management helpers
    # ------------------------------------------------------------------

    def _ensure_flat(
        self,
        ctx: TestContext,
        p2cfg: Phase2Config,
        timeout: float = 30.0,
    ) -> bool:
        """
        Send EXIT_ALL and wait for position to become Flat via log.
        Returns True if flat within timeout.
        """
        lb = ctx.log_baseline()
        msg = message_factory.exit_all(instrument=ctx.instrument)
        bridge_io.inject_message(ctx.inbox, msg)
        flat = wait_for_flat(ctx.log_file, since_line=lb, timeout_seconds=timeout)
        if not flat:
            # Maybe already flat
            side, _ = _read_live_position(ctx)
            return side == SIDE_FLAT
        return True

    def _send_enter_long(
        self,
        ctx: TestContext,
        p2cfg: Phase2Config,
        stop_ticks: Optional[int] = None,
        target_ticks: Optional[int] = None,
    ) -> str:
        """Inject TF_ENTER_LONG and return the msg_id."""
        msg = message_factory.base_enter_long(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=stop_ticks if stop_ticks is not None else p2cfg.default_stop_ticks,
            target_ticks=target_ticks if target_ticks is not None else p2cfg.default_target_ticks,
        )
        bridge_io.inject_message(ctx.inbox, msg)
        return msg["message_id"]

    def _send_enter_short(
        self,
        ctx: TestContext,
        p2cfg: Phase2Config,
        stop_ticks: Optional[int] = None,
        target_ticks: Optional[int] = None,
    ) -> str:
        """Inject TF_ENTER_SHORT and return the msg_id."""
        msg = message_factory.base_enter_short(
            instrument=ctx.instrument,
            quantity=p2cfg.entry_qty,
            stop_ticks=stop_ticks if stop_ticks is not None else p2cfg.default_stop_ticks,
            target_ticks=target_ticks if target_ticks is not None else p2cfg.default_target_ticks,
        )
        bridge_io.inject_message(ctx.inbox, msg)
        return msg["message_id"]

    def _wait_for_filled_event(
        self,
        ctx: TestContext,
        since_count: int,
        timeout: float,
    ) -> Optional[outbox_parser.OutboxEvent]:
        """
        Poll outbox for a FILLED event after since_count.
        Returns the first FILLED event found, or None on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = outbox_parser.events_since(ctx.outbox, since_count)
            filled = [e for e in events if e.status == "FILLED"]
            if filled:
                return filled[0]
            time.sleep(0.5)
        return None

    def _wait_for_stop_attached(
        self,
        ctx: TestContext,
        since_count: int,
        timeout: float,
        since_log_line: int = 0,
    ) -> Optional[outbox_parser.OutboxEvent]:
        """
        Poll for stop-attached evidence.

        Accepts (in priority order):
          1. STOP_ATTACHED outbox event
          2. STOP_INIT log event          (shell writes at fill time)
          3. HEALTH snapshot with stop_order_live=True
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Tier 1: outbox STOP_ATTACHED
            for evt in outbox_parser.events_since(ctx.outbox, since_count):
                if evt.status == "STOP_ATTACHED":
                    return evt

            if since_log_line > 0:
                # Tier 2: STOP_INIT log record
                for rec in log_parser.read_records(ctx.log_file, since_log_line):
                    if rec.event_type == "STOP_INIT":
                        return outbox_parser.OutboxEvent(
                            file_path=ctx.log_file,
                            status="STOP_ATTACHED",
                            detail=f"via_log=true;stop_init={rec.message[:120]}",
                        )

                # Tier 3: HEALTH snapshot confirms stop_order is live
                health = get_latest_health(ctx.log_file, since_line=since_log_line)
                if health and health.stop_order_live:
                    return outbox_parser.OutboxEvent(
                        file_path=ctx.log_file,
                        status="STOP_ATTACHED",
                        detail=(
                            f"via_log=true;stop_order={health.stop_order}"
                            f";pending_stop={health.pending_stop}"
                        ),
                    )

            time.sleep(0.5)
        return None

    def _wait_for_stop_working(
        self,
        ctx: TestContext,
        since_count: int,
        timeout: float,
    ) -> Optional[outbox_parser.OutboxEvent]:
        """Poll outbox for STOP_WORKING event."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = outbox_parser.events_since(ctx.outbox, since_count)
            working = [e for e in events if e.status == "STOP_WORKING"]
            if working:
                return working[0]
            time.sleep(0.5)
        return None

    def _wait_for_outbox_event(
        self,
        ctx: TestContext,
        status: str,
        since_count: int,
        timeout: float,
    ) -> Optional[outbox_parser.OutboxEvent]:
        """Generic: poll outbox for any event matching status."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = outbox_parser.events_since(ctx.outbox, since_count)
            matched = [e for e in events if e.status == status]
            if matched:
                return matched[0]
            time.sleep(0.5)
        return None

    def _enter_and_wait_for_fill(
        self,
        ctx: TestContext,
        p2cfg: Phase2Config,
        side: str,  # SIDE_LONG or SIDE_SHORT
        stop_ticks: Optional[int] = None,
        target_ticks: Optional[int] = None,
    ) -> tuple[Optional[outbox_parser.OutboxEvent], int, int]:
        """
        Inject an entry signal and wait for fill evidence.

        Accepts (in priority order):
          1. FILLED outbox event  (preferred — full detail)
          2. FILL log event       (shell writes this from OnExecutionUpdate)
          3. pos=Long/Short in log header (position confirmed in any log record)

        Returns (filled_event_or_sentinel, lb_before, ob_before).
        filled_event is None only if all three tiers time out.
        """
        lb = ctx.log_baseline()
        ob = ctx.outbox_baseline()
        expected_side = side  # "Long" or "Short"

        if side == SIDE_LONG:
            self._send_enter_long(ctx, p2cfg, stop_ticks=stop_ticks, target_ticks=target_ticks)
        else:
            self._send_enter_short(ctx, p2cfg, stop_ticks=stop_ticks, target_ticks=target_ticks)

        deadline = time.monotonic() + p2cfg.fill_wait_seconds
        filled: Optional[outbox_parser.OutboxEvent] = None

        while time.monotonic() < deadline:
            # Tier 1: FILLED outbox event
            for evt in outbox_parser.events_since(ctx.outbox, ob):
                if evt.status == "FILLED":
                    filled = evt
                    break
            if filled:
                break

            # Tier 2: FILL log event (written in OnExecutionUpdate)
            for rec in log_parser.read_records(ctx.log_file, lb):
                if rec.event_type == "FILL":
                    filled = outbox_parser.OutboxEvent(
                        file_path=ctx.log_file,
                        status="FILLED",
                        detail=f"via_log=true;fill_msg={rec.message[:120]}",
                    )
                    break
            if filled:
                break

            # Tier 3: position side visible in any log record header
            for rec in log_parser.read_records(ctx.log_file, lb):
                if f"|pos={expected_side}|" in rec.raw:
                    filled = outbox_parser.OutboxEvent(
                        file_path=ctx.log_file,
                        status="FILLED",
                        detail=f"via_log=true;pos={expected_side}",
                    )
                    break
            if filled:
                break

            time.sleep(0.5)

        return filled, lb, ob


# ---------------------------------------------------------------------------
# Module-level helpers (used by test case files)
# ---------------------------------------------------------------------------

def _read_live_position(ctx: TestContext) -> tuple[str, int]:
    """Read live position from latest log record."""
    from bridge_test_harness.health_parser import get_current_position_from_log
    return get_current_position_from_log(ctx.log_file)


def require_flat_precondition(
    ctx: TestContext,
    p2cfg: Phase2Config,
    timeout: float = 30.0,
) -> Optional[str]:
    """
    Ensure position is Flat before a test starts.
    Returns an error string if unable to flatten, else None.
    Sends EXIT_ALL and waits for Flat confirmation from the log.

    After confirming flat, waits p2cfg.post_flat_settle_seconds so that NT8
    finishes processing any pending bracket-cancel I/O before the next entry
    is submitted.  Without this pause, the next EnterLong can arrive while
    NT8's managed-order system is still unwinding the previous bracket pair,
    causing the new entry to be silently dropped.
    """
    side, qty = _read_live_position(ctx)
    if side == SIDE_FLAT and qty == 0:
        time.sleep(p2cfg.post_flat_settle_seconds)
        return None  # already flat — still settle to clear any pending I/O

    lb = log_parser.line_count(ctx.log_file)
    msg = message_factory.exit_all(instrument=ctx.instrument)
    bridge_io.inject_message(ctx.inbox, msg)
    if wait_for_flat(ctx.log_file, since_line=lb, timeout_seconds=timeout):
        time.sleep(p2cfg.post_flat_settle_seconds)
        return None
    side2, qty2 = _read_live_position(ctx)
    if side2 == SIDE_FLAT:
        time.sleep(p2cfg.post_flat_settle_seconds)
        return None
    return f"Could not flatten before test: side={side2} qty={qty2} after {timeout}s"
