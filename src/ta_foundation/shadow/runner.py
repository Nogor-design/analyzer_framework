"""Shadow pass orchestrator (Phase D.1).

One ``run_shadow_pass`` call is one tick of the forward-observation loop:

    1. Pick up every candidate currently in ``triage_state = 'shadow'``.
    2. Pull the bars window for the candidate's instrument from the data
       source. The window starts at the candidate's stored
       ``shadow_cursor_ts`` (with a small overlap so we never drop a bar
       on the boundary) and ends at ``until_ts`` (defaults to "now").
    3. Run the family-appropriate signal detector on those bars. For each
       fired signal, ``INSERT OR IGNORE`` a shadow_signals row keyed on
       (candidate_id, signal_bar_ts_utc, direction). Re-runs are no-ops.
    4. For each not-yet-resolved signal for this candidate, replay the
       state machine against the available bars and persist any state
       transition (pending → no_fill, pending → open, open → resolved).
    5. Advance ``shadow_cursor_ts`` to the last bar seen so the next pass
       picks up incrementally.

The pass writes a tool_journal entry per candidate so the daily health
report (D.2) can introspect every action the runner took.

The runner is *deliberately not threaded*. SQLite WAL handles concurrent
readers; concurrent writers would have to negotiate the unique index and
the cursor advance, and that complexity is not worth Phase D.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from ta_foundation.research_ledger import (
    Candidate,
    Hypothesis,
    Repository,
    ShadowSignal,
)
import json as _json

from ta_foundation.shadow.data_source import BarsDataSource
from ta_foundation.shadow.decay import (
    init_decay_state,
    summarise_state_for_journal,
    update_decay_state,
)
from ta_foundation.shadow.simulator import (
    ShadowNotSupported,
    generate_signals,
    is_supported,
    resolve_open_signal,
)


@dataclass
class ShadowPassCandidateResult:
    candidate_id: str
    family: Optional[str]
    instrument: Optional[str]
    skipped_reason: Optional[str] = None
    bars_seen: int = 0
    signals_inserted: int = 0
    signals_duplicate: int = 0
    transitions: dict[str, int] = field(default_factory=dict)
    new_cursor_ts: Optional[str] = None
    error: Optional[str] = None
    decay_trades_consumed: int = 0
    decay_triggered: bool = False
    decay_summary: Optional[str] = None


@dataclass
class ShadowPassReport:
    n_candidates: int = 0
    candidates: list[ShadowPassCandidateResult] = field(default_factory=list)
    total_signals_inserted: int = 0
    total_signals_resolved: int = 0
    total_signals_no_fill: int = 0
    total_decay_triggered: int = 0


def run_shadow_pass(
    *,
    repo: Repository,
    bars_loader: BarsDataSource,
    until_ts: Optional[pd.Timestamp] = None,
    candidate_id: Optional[str] = None,
    journal_role: str = "shadow:runner",
) -> ShadowPassReport:
    """Run one shadow pass.

    Parameters
    ----------
    repo : research-ledger repository (already migrated).
    bars_loader : data source for 1-minute bars.
    until_ts : optional clamp for the data window. Defaults to "no clamp"
        (whatever the data source returns).
    candidate_id : restrict the pass to a single candidate. Useful for
        tests and for one-off operator triggers.
    journal_role : role string written to the tool_journal.

    Returns a ShadowPassReport summarising what changed.
    """
    if candidate_id:
        candidates = [c for c in [repo.get_candidate(candidate_id)] if c is not None]
    else:
        candidates = repo.list_candidates(triage_state="shadow", limit=200)

    report = ShadowPassReport(n_candidates=len(candidates))

    for c in candidates:
        result = _process_candidate(
            repo=repo,
            candidate=c,
            bars_loader=bars_loader,
            until_ts=until_ts,
        )
        report.candidates.append(result)
        report.total_signals_inserted += result.signals_inserted
        report.total_signals_resolved += result.transitions.get("resolved", 0)
        report.total_signals_no_fill += result.transitions.get("no_fill", 0)
        if result.decay_triggered:
            report.total_decay_triggered += 1

        repo.journal(
            role=journal_role,
            tool_name="shadow_pass",
            inputs={
                "candidate_id": result.candidate_id,
                "until_ts": str(until_ts) if until_ts is not None else None,
            },
            output_summary=_summarise(result),
            duration_ms=0,
            error=result.error,
        )

        # Auto-disable journal + triage transition for newly-decayed
        # candidates. Done outside _process_candidate so the side-effect
        # is journaled as its own tool entry, distinguishable from the
        # surrounding shadow_pass row in audit trails.
        if result.decay_triggered:
            reason = (
                "edge-decay CUSUM crossed threshold after "
                f"{result.decay_trades_consumed} trade(s); auto-disabled per "
                "Phase D.3"
            )
            try:
                repo.set_triage(
                    candidate_id=result.candidate_id,
                    state="decayed",
                    reason=reason,
                    triaged_by="agent:shadow_decay",
                )
            except Exception as exc:  # noqa: BLE001
                repo.journal(
                    role=journal_role,
                    tool_name="decay_disable",
                    inputs={"candidate_id": result.candidate_id},
                    output_summary=result.decay_summary or "",
                    duration_ms=0,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            repo.journal(
                role=journal_role,
                tool_name="decay_disable",
                inputs={
                    "candidate_id": result.candidate_id,
                    "trades_consumed": result.decay_trades_consumed,
                },
                output_summary=result.decay_summary or "decay triggered",
                duration_ms=0,
            )
    return report


def _process_candidate(
    *,
    repo: Repository,
    candidate: Candidate,
    bars_loader: BarsDataSource,
    until_ts: Optional[pd.Timestamp],
) -> ShadowPassCandidateResult:
    result = ShadowPassCandidateResult(
        candidate_id=candidate.candidate_id,
        family=None,
        instrument=None,
    )
    try:
        hyp = repo.get_hypothesis(candidate.hypothesis_id)
        if hyp is None:
            result.skipped_reason = "hypothesis_not_found"
            return result
        result.family = hyp.family
        result.instrument = hyp.instrument

        if not is_supported(hyp.family):
            result.skipped_reason = f"family_not_supported:{hyp.family}"
            return result

        cursor_ts = repo.get_shadow_cursor(candidate.candidate_id)
        since = _cursor_to_timestamp(cursor_ts)
        bars = bars_loader.get_bars(
            instrument=hyp.instrument,
            since_ts=since,
            until_ts=until_ts,
        )
        result.bars_seen = int(len(bars)) if bars is not None else 0

        # Always run signal detection on the new window — the unique
        # index handles duplicate inserts at the boundary.
        if result.bars_seen > 0:
            try:
                planned, runtime_cfg = generate_signals(
                    candidate=candidate,
                    hypothesis=hyp,
                    bars_1m=bars,
                )
            except ShadowNotSupported as exc:
                result.skipped_reason = str(exc)
                return result

            for p in planned:
                inserted = repo.insert_shadow_signal_if_absent(
                    candidate_id=candidate.candidate_id,
                    ts=p.signal_bar_ts_utc,
                    instrument=hyp.instrument,
                    direction=p.direction,
                    planned_entry=p.planned_entry,
                    planned_stop=p.planned_stop,
                    planned_target=p.planned_target,
                    realized_outcome={
                        "status": "pending",
                        "fill_timeout_bars": p.context.get("fill_timeout_bars") or 5,
                        "context": p.context,
                    },
                )
                if inserted is None:
                    result.signals_duplicate += 1
                else:
                    result.signals_inserted += 1

        # Resolve open positions (includes ones we just inserted).
        # Always fetch enough bars to attempt resolution — even if the
        # signal fired before the cursor, its open state must be tracked.
        open_signals = repo.list_open_shadow_signals(candidate_id=candidate.candidate_id)
        if open_signals:
            # Need a window that starts at the earliest unresolved signal
            # and runs through until_ts.
            earliest_ts = min(pd.Timestamp(s.ts) for s in open_signals)
            full_window = bars_loader.get_bars(
                instrument=hyp.instrument,
                since_ts=earliest_ts,
                until_ts=until_ts,
            )
            outcome_cfg = _outcome_cfg_from_runtime_or_candidate(
                runtime_cfg_or_none=locals().get("runtime_cfg"),
                candidate=candidate,
                hypothesis=hyp,
            )
            for sig in open_signals:
                payload = resolve_open_signal(
                    signal=sig,
                    bars_1m=full_window,
                    outcome_cfg=outcome_cfg,
                )
                if payload is None:
                    continue
                repo.update_shadow_outcome(
                    signal_id=sig.signal_id,
                    realized_outcome=payload,
                )
                status = str(payload.get("status") or "pending")
                result.transitions[status] = result.transitions.get(status, 0) + 1

        # Advance the cursor to the last bar we just saw.
        if result.bars_seen > 0 and bars is not None and not bars.empty:
            last_bar_ts = pd.Timestamp(bars["dt"].iloc[-1])
            result.new_cursor_ts = _timestamp_to_cursor(last_bar_ts)
            repo.set_shadow_cursor(
                candidate_id=candidate.candidate_id,
                cursor_ts=result.new_cursor_ts,
            )

        # Phase D.3 — feed any newly-resolved trades through the CUSUM.
        _update_decay_for_candidate(
            repo=repo, candidate=candidate, hypothesis=hyp, result=result,
        )
    except Exception as exc:  # noqa: BLE001 — runner must not crash other candidates
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _update_decay_for_candidate(
    *,
    repo: Repository,
    candidate: Candidate,
    hypothesis: Hypothesis,
    result: ShadowPassCandidateResult,
) -> None:
    """Consume newly-resolved shadow trades into the decay state machine.

    Trades are processed in (ts, signal_id) order. The state's
    ``last_signal_id`` watermark guarantees a trade is counted at most
    once across passes. The first transition that pushes ``S`` above ``H``
    sets ``result.decay_triggered`` and the run loop journals the auto-
    disable. Subsequent trades for an already-triggered state are no-ops
    in the math but still advance ``last_signal_id`` so re-runs stay
    idempotent.
    """
    raw_state = repo.get_decay_state(candidate.candidate_id)
    if raw_state is None:
        state = init_decay_state(candidate, hypothesis=hypothesis)
    else:
        state = raw_state

    signals = repo.list_shadow_signals(candidate_id=candidate.candidate_id, limit=5000)
    # Order by (ts, signal_id) is guaranteed by list_shadow_signals.

    last_id = state.get("last_signal_id")
    last_id_int = int(last_id) if last_id is not None else -1
    consumed = 0
    triggered_now_global = False
    for s in signals:
        if s.signal_id <= last_id_int:
            continue
        if not s.realized_outcome_json:
            continue
        try:
            payload = _json.loads(s.realized_outcome_json)
        except _json.JSONDecodeError:
            continue
        if str(payload.get("status") or "") != "resolved":
            # The runner only counts terminal-resolved trades; no_fill and
            # in-flight signals contribute zero information about edge.
            continue
        profit_ticks = payload.get("profit_ticks")
        try:
            profit_ticks = float(profit_ticks)
        except (TypeError, ValueError):
            continue
        update = update_decay_state(
            state, profit_ticks=profit_ticks, signal_id=int(s.signal_id),
        )
        state = update.state
        consumed += 1
        if update.triggered_now:
            triggered_now_global = True
            # Don't break — keep consuming so last_signal_id covers all
            # already-resolved trades; the state machine no-ops further
            # updates because triggered is True.

    if consumed > 0 or raw_state is None:
        repo.set_decay_state(candidate_id=candidate.candidate_id, state=state)

    result.decay_trades_consumed = consumed
    result.decay_triggered = bool(triggered_now_global)
    if consumed > 0 or triggered_now_global:
        result.decay_summary = summarise_state_for_journal(state)


def _outcome_cfg_from_runtime_or_candidate(
    *,
    runtime_cfg_or_none: Any,
    candidate: Candidate,
    hypothesis: Hypothesis,
) -> dict[str, Any]:
    if isinstance(runtime_cfg_or_none, dict) and "outcome" in runtime_cfg_or_none:
        return runtime_cfg_or_none["outcome"]
    # Rebuild from candidate + hypothesis (resolution after a no-signal pass).
    from ta_foundation.shadow.simulator import _orb_signal_config
    return _orb_signal_config(candidate, hypothesis)["outcome"]


def _cursor_to_timestamp(cursor_ts: Optional[str]) -> Optional[pd.Timestamp]:
    if not cursor_ts:
        return None
    t = pd.Timestamp(cursor_ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert("America/Denver")


def _timestamp_to_cursor(ts: pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("America/Denver")
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _summarise(result: ShadowPassCandidateResult) -> str:
    if result.error:
        return f"error: {result.error}"
    if result.skipped_reason:
        return f"skipped: {result.skipped_reason}"
    parts = [
        f"bars={result.bars_seen}",
        f"new={result.signals_inserted}",
        f"dup={result.signals_duplicate}",
    ]
    if result.transitions:
        parts.append("transitions=" + ",".join(f"{k}={v}" for k, v in result.transitions.items()))
    if result.new_cursor_ts:
        parts.append(f"cursor={result.new_cursor_ts}")
    return " ".join(parts)
