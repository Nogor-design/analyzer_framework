"""
User checkpoint mechanism for SEMI_AUTOMATED and MANUAL_REQUIRED Phase 2 tests.

SEMI_AUTOMATED tests pause at checkpoint() calls, show the user what to observe
or verify in the NT8 UI/account, then resume automatically when the user presses
Enter (or 'f' to fail, 's' to skip).

MANUAL_REQUIRED tests print their full procedure and are always skipped by the
runner — they exist as documentation anchors so the operator knows what to do.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CheckpointOutcome(Enum):
    PASSED = "passed"    # User confirmed observation matches expectation
    FAILED = "failed"    # User explicitly failed the checkpoint
    SKIPPED = "skipped"  # User skipped (or timeout elapsed in non-interactive mode)
    AUTO = "auto"        # Checkpoint resolved automatically (no user input needed)


@dataclass
class CheckpointResult:
    label: str
    outcome: CheckpointOutcome
    user_note: str = ""
    elapsed_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.outcome in (CheckpointOutcome.PASSED, CheckpointOutcome.AUTO)

    @property
    def skipped(self) -> bool:
        return self.outcome == CheckpointOutcome.SKIPPED

    def __str__(self) -> str:
        icon = {
            CheckpointOutcome.PASSED: "[ok]",
            CheckpointOutcome.AUTO:   "[ok]",
            CheckpointOutcome.FAILED: "[FAIL]",
            CheckpointOutcome.SKIPPED: "[SKIP]",
        }[self.outcome]
        note = f" -- {self.user_note}" if self.user_note else ""
        return f"{icon} {self.label}{note}"


# ---------------------------------------------------------------------------
# Interactive checkpoint
# ---------------------------------------------------------------------------

_INTERACTIVE = sys.stdin.isatty()


def checkpoint(
    label: str,
    instructions: str,
    *,
    auto_pass: bool = False,
    timeout_seconds: Optional[float] = None,
    non_interactive_outcome: CheckpointOutcome = CheckpointOutcome.SKIPPED,
) -> CheckpointResult:
    """
    Pause the test and prompt the user to verify a condition.

    Parameters
    ----------
    label:
        Short name for this checkpoint (used in reports).
    instructions:
        Human-readable description of what to observe / verify.
    auto_pass:
        If True, print instructions but resolve as PASSED immediately (used for
        fully automated pre-check steps that just need the info displayed).
    timeout_seconds:
        If set, auto-resolve after this many seconds if no input.
    non_interactive_outcome:
        Outcome to use when stdin is not a TTY (CI / piped). Default=SKIPPED.

    Returns
    -------
    CheckpointResult
    """
    t0 = time.monotonic()

    _divider()
    print(f"  CHECKPOINT: {label}")
    _divider()
    for line in instructions.strip().splitlines():
        print(f"    {line}")
    print()

    if auto_pass:
        print("  [auto-pass] No input required for this checkpoint.")
        _divider()
        return CheckpointResult(
            label=label,
            outcome=CheckpointOutcome.AUTO,
            elapsed_seconds=time.monotonic() - t0,
        )

    if not _INTERACTIVE:
        outcome = non_interactive_outcome
        print(f"  [non-interactive] Resolving as {outcome.value}.")
        _divider()
        return CheckpointResult(
            label=label,
            outcome=outcome,
            elapsed_seconds=time.monotonic() - t0,
        )

    # Interactive path
    if timeout_seconds is not None:
        print(f"  Auto-resolving as {non_interactive_outcome.value} in {timeout_seconds:.0f}s if no input.")

    print("  Press Enter to PASS, 'f'+Enter to FAIL, 's'+Enter to SKIP.")
    if timeout_seconds is not None:
        response = _timed_input(timeout_seconds)
    else:
        response = input("  > ").strip().lower()

    elapsed = time.monotonic() - t0

    if response is None:
        outcome = non_interactive_outcome
        note = f"timed out after {timeout_seconds:.0f}s"
    elif response == "f":
        outcome = CheckpointOutcome.FAILED
        note = input("  Failure note (optional): ").strip()
    elif response == "s":
        outcome = CheckpointOutcome.SKIPPED
        note = ""
    else:
        outcome = CheckpointOutcome.PASSED
        note = ""

    _divider()
    return CheckpointResult(
        label=label,
        outcome=outcome,
        user_note=note,
        elapsed_seconds=elapsed,
    )


def auto_checkpoint(label: str, instructions: str) -> CheckpointResult:
    """Display instructions and immediately pass — for informational pauses."""
    return checkpoint(label, instructions, auto_pass=True)


# ---------------------------------------------------------------------------
# Manual procedure printer (MANUAL_REQUIRED tests)
# ---------------------------------------------------------------------------

def print_manual_procedure(
    test_id: str,
    scenario: str,
    steps: list[str],
    expected_outcomes: list[str],
) -> None:
    """
    Print a full manual test procedure to stdout.
    Called by MANUAL_REQUIRED tests before they return SKIPPED.
    """
    _divider("=")
    print(f"  MANUAL TEST: {test_id} -- {scenario}")
    _divider("=")
    print()
    print("  PROCEDURE:")
    for i, step in enumerate(steps, 1):
        print(f"    {i}. {step}")
    print()
    print("  EXPECTED OUTCOMES:")
    for outcome in expected_outcomes:
        print(f"    - {outcome}")
    print()
    print("  This test requires manual NT8 interaction and cannot be automated.")
    print("  Mark it manually in your test report when complete.")
    _divider("=")
    print()


# ---------------------------------------------------------------------------
# Checkpoint sequence runner
# ---------------------------------------------------------------------------

@dataclass
class CheckpointSequence:
    """
    Accumulates checkpoint results for a Phase 2 test.
    All checkpoints must pass for the sequence to be considered passing.
    """
    results: list[CheckpointResult] = field(default_factory=list)

    def run(
        self,
        label: str,
        instructions: str,
        *,
        auto_pass: bool = False,
        required: bool = True,
    ) -> CheckpointResult:
        """Run one checkpoint, record result, return it."""
        result = checkpoint(label, instructions, auto_pass=auto_pass)
        self.results.append(result)
        return result

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def any_failed(self) -> bool:
        return any(r.outcome == CheckpointOutcome.FAILED for r in self.results)

    @property
    def any_skipped(self) -> bool:
        return any(r.skipped for r in self.results)

    @property
    def failure_summary(self) -> str:
        failed = [r for r in self.results if r.outcome == CheckpointOutcome.FAILED]
        if not failed:
            return ""
        return "; ".join(f"CHECKPOINT {r.label}: {r.user_note or 'user failed'}" for r in failed)

    def summary_lines(self) -> list[str]:
        return [str(r) for r in self.results]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _divider(char: str = "-", width: int = 70) -> None:
    print("  " + char * (width - 2))


def _timed_input(timeout_seconds: float) -> Optional[str]:
    """
    Read a line from stdin with a timeout.
    Returns None if timeout elapses.
    On Windows, falls back to blocking input if select is unavailable.
    """
    import threading

    result: list[Optional[str]] = [None]
    done = threading.Event()

    def _reader() -> None:
        try:
            result[0] = input("  > ").strip().lower()
        except EOFError:
            result[0] = ""
        finally:
            done.set()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    if done.wait(timeout_seconds):
        return result[0]
    return None  # timed out
