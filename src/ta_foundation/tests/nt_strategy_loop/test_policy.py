from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ta_foundation.nt_strategy_loop.compile_observer import CompileError, CompileObservation
from ta_foundation.nt_strategy_loop.policy import RepairPolicy, evaluate_stop


def _observation(
    *,
    state: str = "failed",
    compiled: bool = False,
    errors: tuple[CompileError, ...] = (),
    heartbeat: str | None = None,
    compile_block_reason: str | None = None,
    last_error: str | None = None,
) -> CompileObservation:
    return CompileObservation(
        run_id="r1",
        state=state,
        strategy_name="Strat",
        source_file="C:/tmp/Strat.cs",
        compiled=compiled,
        error_count=len(errors),
        errors_csv=None,
        errors_text=None,
        last_error=last_error,
        heartbeat_utc=heartbeat,
        output_root=None,
        errors=errors,
        status_path="C:/tmp/nt8_status.json",
        compile_block_reason=compile_block_reason,
    )


def _error(code: str = "CS0103", message: str = "name 'X' does not exist") -> CompileError:
    return CompileError(file="Strat.cs", line=1, column=1, code=code, message=message, raw=message, source="Strat.cs")


def test_evaluate_stop_returns_compile_clean_when_observation_ok() -> None:
    stop = evaluate_stop(
        attempt=1,
        observation=_observation(state="succeeded", compiled=True),
        prior_signatures=[],
        policy=RepairPolicy(),
    )
    assert stop is not None and stop.code == "compile_clean"


def test_evaluate_stop_flags_repeated_error_signature() -> None:
    from ta_foundation.nt_strategy_loop.compile_observer import compiler_error_signature

    errors = (_error(),)
    signature = compiler_error_signature(errors)
    stop = evaluate_stop(
        attempt=2,
        observation=_observation(errors=errors),
        prior_signatures=[signature],
        policy=RepairPolicy(),
    )
    assert stop is not None and stop.code == "repeated_signature"


def test_evaluate_stop_returns_none_when_repair_can_continue() -> None:
    stop = evaluate_stop(
        attempt=1,
        observation=_observation(errors=(_error(),)),
        prior_signatures=[],
        policy=RepairPolicy(max_repair_attempts=3),
    )
    assert stop is None


def test_evaluate_stop_returns_max_attempts() -> None:
    stop = evaluate_stop(
        attempt=3,
        observation=_observation(errors=(_error(),)),
        prior_signatures=[],
        policy=RepairPolicy(max_repair_attempts=3),
    )
    assert stop is not None and stop.code == "max_attempts"


def test_evaluate_stop_flags_terminal_worker_state() -> None:
    stop = evaluate_stop(
        attempt=1,
        observation=_observation(state="worker_error"),
        prior_signatures=[],
        policy=RepairPolicy(),
    )
    assert stop is not None and stop.code == "worker_error"


def test_evaluate_stop_flags_peer_compile_block() -> None:
    stop = evaluate_stop(
        attempt=1,
        observation=_observation(
            compile_block_reason="peer_strategy_errors",
            last_error="peer strategy compile error blocking SA: ; expected",
        ),
        prior_signatures=[],
        policy=RepairPolicy(),
    )
    assert stop is not None
    assert stop.code == "peer_compile_block"
    assert "peer" in stop.message.lower()


def test_evaluate_stop_flags_stale_assembly() -> None:
    stop = evaluate_stop(
        attempt=1,
        observation=_observation(
            state="timed_out",
            compile_block_reason="stale_assembly",
            last_error="NinjaTrader.Custom.dll was not rewritten after the .cs install; auto-compile likely failed silently.",
        ),
        prior_signatures=[],
        policy=RepairPolicy(),
    )
    assert stop is not None
    assert stop.code == "stale_assembly"


def test_evaluate_stop_flags_stale_heartbeat() -> None:
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(seconds=1200)).isoformat(timespec="seconds")
    stop = evaluate_stop(
        attempt=1,
        observation=_observation(errors=(_error(),), heartbeat=stale),
        prior_signatures=[],
        policy=RepairPolicy(max_observation_staleness_seconds=600),
        now_utc=now,
    )
    assert stop is not None and stop.code == "stale_observation"
