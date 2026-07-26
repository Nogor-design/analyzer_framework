from __future__ import annotations

"""Stop-condition policy for the strategy repair loop.

The orchestrator asks `evaluate_stop` after each compile observation. The
returned `StopReason` (or `None`) controls whether to continue, branch into
repair, or terminate. Keeping the policy in one module avoids scattering
heuristics through the loop runner and makes the stop reasons easy to assert
against in tests.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from ta_foundation.nt_strategy_loop.compile_observer import (
    CompileObservation,
    compiler_error_signature,
)


@dataclass(frozen=True)
class RepairPolicy:
    max_repair_attempts: int = 5
    max_observation_staleness_seconds: int = 600


@dataclass(frozen=True)
class StopReason:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


_TERMINAL_FAILURE_STATES = {"timed_out", "worker_error"}


def evaluate_stop(
    *,
    attempt: int,
    observation: CompileObservation,
    prior_signatures: Iterable[str],
    policy: RepairPolicy,
    now_utc: datetime | None = None,
) -> StopReason | None:
    if observation.ok:
        return StopReason("compile_clean", "NinjaTrader auto-compile succeeded.")

    block_reason = observation.compile_block_reason
    if block_reason == "peer_strategy_errors":
        # The observer says the block is in another strategy file. Repairing
        # our code won't move the failure mode — terminate so the operator can
        # fix the peer file.
        return StopReason(
            "peer_compile_block",
            observation.last_error
            or "NinjaTrader cannot rebuild bin\\Custom because a peer strategy has compile errors.",
        )
    if block_reason == "stale_assembly":
        return StopReason(
            "stale_assembly",
            observation.last_error
            or "NinjaTrader.Custom.dll was not rewritten after the .cs install; auto-compile likely failed silently.",
        )

    if observation.state in _TERMINAL_FAILURE_STATES:
        return StopReason(
            "worker_error",
            f"Compile observer reported terminal state {observation.state!r}.",
        )

    signature = compiler_error_signature(observation.errors) if observation.errors else ""
    if signature and signature in set(prior_signatures):
        return StopReason(
            "repeated_signature",
            "Compiler error signature repeated; the previous repair did not change the failure mode.",
        )

    if attempt >= policy.max_repair_attempts:
        return StopReason(
            "max_attempts",
            f"Reached max_repair_attempts={policy.max_repair_attempts} without compile-clean.",
        )

    if _observation_is_stale(observation, policy, now_utc):
        return StopReason(
            "stale_observation",
            f"Compile observer heartbeat is older than {policy.max_observation_staleness_seconds}s.",
        )

    return None


def _observation_is_stale(
    observation: CompileObservation,
    policy: RepairPolicy,
    now_utc: datetime | None,
) -> bool:
    if not observation.heartbeat_utc:
        return False
    try:
        heartbeat = datetime.fromisoformat(observation.heartbeat_utc.replace("Z", "+00:00"))
    except ValueError:
        return False
    reference = now_utc or datetime.now(timezone.utc)
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    age = (reference - heartbeat).total_seconds()
    return age > policy.max_observation_staleness_seconds
