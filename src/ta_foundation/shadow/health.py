"""Daily shadow-health aggregator (Phase D.2).

Reads ``shadow_signals`` + ``candidates`` from the research ledger and
returns a typed, JSON-safe report covering one session day. The report
is the input the Scribe's daily-health prose generator (D.2 follow-up)
will consume; everything in it must be traceable back to a ledger row.

A "session day" is the Denver-local calendar day. Signals stamped at
``ts`` (UTC) are bucketed by ``ts.tz_convert('America/Denver').date()``.
That matches the Phase D doc's "16:30 after RTH close" cadence: a report
generated at 16:30 Denver on day D covers every shadow_signal whose
Denver-day == D.

Numbers exposed:
    - Per candidate:
        n_signals_today          signals whose Denver session day == report date
        n_resolved_today         status='resolved' AND exit Denver day == report
        n_no_fill_today          status='no_fill' AND signal Denver day == report
        n_open_positions         status in {pending, open} as of now
        net_pnl_today            sum of profit_net over signals resolved today
        trailing_window_n        N — the trailing trade-count window
        trailing_pf              profit_factor over last N resolved
        trailing_win_rate        win count / N over last N resolved
        trailing_expectancy_net  mean profit_net over last N resolved
        first_signal_ts / last_signal_ts (Denver ISO) for context
    - Aggregate:
        candidates_active        candidates currently in triage_state='shadow'
        total_signals_today
        total_resolved_today
        total_no_fill_today
        total_open_positions
        total_net_pnl_today
        anomalies                list of {candidate_id, code, message} entries
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd

from ta_foundation.research_ledger import Repository, ShadowSignal


DEFAULT_TRAILING_WINDOW = 30


@dataclass(frozen=True)
class CandidateShadowHealth:
    candidate_id: str
    family: Optional[str]
    instrument: Optional[str]

    n_signals_today: int
    n_resolved_today: int
    n_no_fill_today: int
    n_open_positions: int
    net_pnl_today: float

    trailing_window_n: int
    trailing_pf: Optional[float]
    trailing_win_rate: Optional[float]
    trailing_expectancy_net: Optional[float]

    first_signal_ts_denver: Optional[str]
    last_signal_ts_denver: Optional[str]


@dataclass(frozen=True)
class AnomalyFlag:
    candidate_id: str
    code: str
    message: str


@dataclass(frozen=True)
class ShadowHealthReport:
    report_date: str  # YYYY-MM-DD Denver
    trailing_window: int
    candidates_active: int
    total_signals_today: int
    total_resolved_today: int
    total_no_fill_today: int
    total_open_positions: int
    total_net_pnl_today: float
    candidates: list[CandidateShadowHealth] = field(default_factory=list)
    anomalies: list[AnomalyFlag] = field(default_factory=list)


def compute_daily_health(
    repo: Repository,
    *,
    for_date: Optional[date | str] = None,
    trailing_window: int = DEFAULT_TRAILING_WINDOW,
    open_position_age_warn_hours: int = 24,
) -> ShadowHealthReport:
    """Aggregate shadow-signal data into a daily health snapshot.

    Parameters
    ----------
    repo : research ledger repository.
    for_date : the Denver session day to summarise. Accepts a date or an
        ISO string. Defaults to today (Denver local).
    trailing_window : number of most-recent resolved trades to compute
        the trailing PF/win-rate over (per candidate).
    open_position_age_warn_hours : an open shadow signal whose Denver
        signal age exceeds this threshold triggers a `stale_open_position`
        anomaly.
    """
    report_date = _coerce_report_date(for_date)
    candidates = repo.list_candidates(triage_state="shadow", limit=200)

    candidates_active = len(candidates)
    total_signals_today = 0
    total_resolved_today = 0
    total_no_fill_today = 0
    total_open_positions = 0
    total_net_pnl_today = 0.0

    per_candidate: list[CandidateShadowHealth] = []
    anomalies: list[AnomalyFlag] = []
    now_utc = datetime.now(timezone.utc)

    for c in candidates:
        hyp = repo.get_hypothesis(c.hypothesis_id)
        signals = repo.list_shadow_signals(candidate_id=c.candidate_id, limit=10000)

        # Bucket signals by Denver day on their fire-time and on their
        # resolve-time (where resolved). Carry the parsed payload so we can
        # read profit_net once.
        parsed: list[tuple[ShadowSignal, dict, Optional[date], Optional[date]]] = []
        for s in signals:
            payload = _parse_payload(s.realized_outcome_json)
            signal_day = _denver_day(s.ts)
            exit_day = _denver_day(payload.get("exit_ts_utc")) if isinstance(payload.get("exit_ts_utc"), str) else None
            parsed.append((s, payload, signal_day, exit_day))

        n_signals_today = sum(1 for _, _, sday, _ in parsed if sday == report_date)
        n_resolved_today = sum(
            1
            for _, p, _, eday in parsed
            if p.get("status") == "resolved" and eday == report_date
        )
        n_no_fill_today = sum(
            1
            for _, p, sday, _ in parsed
            if p.get("status") == "no_fill" and sday == report_date
        )
        n_open = sum(
            1
            for _, p, _, _ in parsed
            if p.get("status") in (None, "pending", "open") or p.get("status") not in ("resolved", "no_fill")
        )
        net_today = sum(
            _coerce_float(p.get("profit_net")) or 0.0
            for _, p, _, eday in parsed
            if p.get("status") == "resolved" and eday == report_date
        )

        trailing = _trailing_resolved_stats(parsed, trailing_window)
        first_ts, last_ts = _bounds_denver(parsed)

        per_candidate.append(
            CandidateShadowHealth(
                candidate_id=c.candidate_id,
                family=hyp.family if hyp else None,
                instrument=hyp.instrument if hyp else None,
                n_signals_today=n_signals_today,
                n_resolved_today=n_resolved_today,
                n_no_fill_today=n_no_fill_today,
                n_open_positions=n_open,
                net_pnl_today=round(net_today, 2),
                trailing_window_n=trailing_window,
                trailing_pf=trailing["pf"],
                trailing_win_rate=trailing["win_rate"],
                trailing_expectancy_net=trailing["expectancy_net"],
                first_signal_ts_denver=first_ts,
                last_signal_ts_denver=last_ts,
            )
        )

        total_signals_today += n_signals_today
        total_resolved_today += n_resolved_today
        total_no_fill_today += n_no_fill_today
        total_open_positions += n_open
        total_net_pnl_today += net_today

        # Anomalies — stale open positions.
        threshold = pd.Timedelta(hours=open_position_age_warn_hours)
        for s, p, sday, _ in parsed:
            if p.get("status") in (None, "pending", "open") or (
                p.get("status") not in ("resolved", "no_fill")
            ):
                signal_ts = pd.Timestamp(s.ts)
                if signal_ts.tzinfo is None:
                    signal_ts = signal_ts.tz_localize("UTC")
                age = pd.Timestamp(now_utc) - signal_ts
                if age > threshold:
                    anomalies.append(
                        AnomalyFlag(
                            candidate_id=c.candidate_id,
                            code="stale_open_position",
                            message=(
                                f"signal_id={s.signal_id} status={p.get('status') or 'null'} "
                                f"age={age} > {open_position_age_warn_hours}h"
                            ),
                        )
                    )

    return ShadowHealthReport(
        report_date=report_date.isoformat(),
        trailing_window=trailing_window,
        candidates_active=candidates_active,
        total_signals_today=total_signals_today,
        total_resolved_today=total_resolved_today,
        total_no_fill_today=total_no_fill_today,
        total_open_positions=total_open_positions,
        total_net_pnl_today=round(total_net_pnl_today, 2),
        candidates=per_candidate,
        anomalies=anomalies,
    )


# ----- helpers ------------------------------------------------------------


def _coerce_report_date(for_date: Optional[date | str]) -> date:
    if for_date is None:
        return pd.Timestamp.now(tz="America/Denver").date()
    if isinstance(for_date, date):
        return for_date
    parsed = pd.Timestamp(for_date)
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("America/Denver")
    return parsed.date()


def _denver_day(ts_iso: Optional[str]) -> Optional[date]:
    if not ts_iso:
        return None
    t = pd.Timestamp(ts_iso)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert("America/Denver").date()


def _parse_payload(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _coerce_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _trailing_resolved_stats(
    parsed: list[tuple],
    window: int,
) -> dict:
    """Compute PF / win rate / expectancy over the latest `window` resolved trades.

    "Latest" is by exit timestamp; if no exit_ts, fall back to signal ts.
    """
    if window <= 0:
        return {"pf": None, "win_rate": None, "expectancy_net": None}

    resolved = []
    for s, p, _, _ in parsed:
        if p.get("status") != "resolved":
            continue
        anchor = p.get("exit_ts_utc") or s.ts
        try:
            anchor_ts = pd.Timestamp(anchor)
        except (ValueError, TypeError):
            continue
        if anchor_ts.tzinfo is None:
            anchor_ts = anchor_ts.tz_localize("UTC")
        pnl = _coerce_float(p.get("profit_net"))
        if pnl is None:
            continue
        result = p.get("result")
        resolved.append((anchor_ts, pnl, result))

    if not resolved:
        return {"pf": None, "win_rate": None, "expectancy_net": None}

    resolved.sort(key=lambda r: r[0], reverse=True)
    window_slice = resolved[:window]
    pnls = [r[1] for r in window_slice]
    wins = sum(1 for r in window_slice if r[2] == "win")
    n = len(window_slice)
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
    return {
        "pf": pf,
        "win_rate": round(wins / n, 4) if n else None,
        "expectancy_net": round(sum(pnls) / n, 2) if n else None,
    }


def _bounds_denver(parsed: list[tuple]) -> tuple[Optional[str], Optional[str]]:
    if not parsed:
        return None, None
    timestamps: list[pd.Timestamp] = []
    for s, _, _, _ in parsed:
        t = pd.Timestamp(s.ts)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        timestamps.append(t.tz_convert("America/Denver"))
    if not timestamps:
        return None, None
    return min(timestamps).isoformat(), max(timestamps).isoformat()
