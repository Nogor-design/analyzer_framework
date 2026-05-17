from __future__ import annotations

"""Assemble a single consolidated decision view for a finished optimizer
session.

This module is the data-only half of stage (a) of the decision-dashboard
rollout: it loads every per-check artifact already on disk and produces
one JSON-safe ``DecisionDashboard`` view-model keyed by candidate. The
view layer in :mod:`ta_foundation.web.app` renders that view-model into
HTML — no new analytics happen here.

Inputs read from ``<session>/deployment_package/``:

- ``final_backtest_handoff/final_backtest_review/evaluated_candidates.json``
- ``final_backtest_handoff/final_backtest_review/recommendations.json``
- ``robustness/robustness.json``           (bootstrap)
- ``walkforward/stability.json``           (rolling windows)
- ``neighborhood/stability.json``          (parameter neighborhood)
- ``shadow/comparison.json``               (shadow execution)
- ``END_USER_DECISION.md``                 (headline narrative)
- ``manifest.json``                        (decision_state, counts)

All inputs are optional: a candidate that has KPIs but no robustness
data simply shows empty check sections. A session with no
``evaluated_candidates.json`` returns an empty dashboard with a clear
``status`` reason.
"""

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_session import OptimizerSession


# Severity buckets the template can style. We pre-compute them here so
# the page is pure markup with no conditional logic.
SEVERITY_OK = "ok"
SEVERITY_WARN = "warn"
SEVERITY_FAIL = "fail"
SEVERITY_NONE = "none"  # check didn't run for this candidate

FAIL_PENALTY = 30
WARN_PENALTY = 10


@dataclass(frozen=True)
class CheckSummary:
    """One per-candidate check summary the page shows as a badge."""
    name: str                 # "bootstrap", "walkforward", ...
    severity: str             # SEVERITY_* constant
    headline: str             # short human-readable result
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateRow:
    run_id: str
    status: str               # "recommended" | "rejected" | "evaluated"
    rank: int | None          # rank if recommended, else None
    score: float | None
    adjusted_score: float | None
    adjusted_rank: int
    mode: str
    session_bucket: str
    total_net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None
    trades: int
    percent_days_traded: float | None
    direction: str
    risk_shape: str
    decision_reason: str
    report_url: str | None = None  # /optimizer/.../candidates/<run_id>/report when HTML exists
    checks: list[CheckSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["checks"] = [c.to_dict() for c in self.checks]
        return d


@dataclass(frozen=True)
class DecisionDashboard:
    session_id: str
    label: str
    instrument: str
    decision_state: str
    oos_window: tuple[str, str] | None
    generated_at: str
    headline: str             # Markdown-stripped sentence from END_USER_DECISION.md
    decision_notes: list[str]
    recommended_count: int
    rejected_count: int
    candidate_rows: list[CandidateRow]
    artifact_links: dict[str, str]   # display-name -> relative path
    status: str               # "ok" | "no_review" | "no_session"
    status_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "label": self.label,
            "instrument": self.instrument,
            "decision_state": self.decision_state,
            "oos_window": list(self.oos_window) if self.oos_window else None,
            "generated_at": self.generated_at,
            "headline": self.headline,
            "decision_notes": list(self.decision_notes),
            "recommended_count": self.recommended_count,
            "rejected_count": self.rejected_count,
            "candidate_rows": [c.to_dict() for c in self.candidate_rows],
            "artifact_links": dict(self.artifact_links),
            "status": self.status,
            "status_reason": self.status_reason,
        }


def build_decision_dashboard(session: OptimizerSession) -> DecisionDashboard:
    """Assemble a decision dashboard view-model from on-disk artifacts.

    Never raises for missing optional artifacts — degrades gracefully.
    Only raises if the session document itself is unreadable, which
    callers should already have handled before reaching here.
    """
    doc = session.load_document()
    pkg = session.directory / "deployment_package"
    manifest = _load_json(pkg / "manifest.json") or {}

    review_dir = pkg / "final_backtest_handoff" / "final_backtest_review"
    evaluated = _load_json(review_dir / "evaluated_candidates.json") or {}
    recommendations = _load_json(review_dir / "recommendations.json") or {}

    if not evaluated.get("rows"):
        return _empty_dashboard(
            session=session, doc=doc, manifest=manifest,
            reason="No final-Backtest review on disk yet — run the final fixed Backtest phase and rebuild the deployment package.",
        )

    by_id_eval: dict[str, dict[str, Any]] = {
        str(r.get("run_id")): r for r in evaluated.get("rows", [])
        if isinstance(r, dict) and r.get("run_id")
    }
    by_id_rec: dict[str, dict[str, Any]] = {}
    rec_rank: dict[str, int] = {}
    for r in recommendations.get("recommendations", []):
        if not isinstance(r, dict) or not r.get("run_id"):
            continue
        by_id_rec[str(r["run_id"])] = r
        if r.get("rank") is not None:
            rec_rank[str(r["run_id"])] = int(r["rank"])
    rejected_ids: dict[str, dict[str, Any]] = {
        str(r.get("run_id")): r for r in recommendations.get("rejected", [])
        if isinstance(r, dict) and r.get("run_id")
    }

    bootstrap_by_id = _index_by("run_id", _load_json(pkg / "robustness" / "robustness.json"),
                                key="bootstrap_results")
    wf_by_id = _index_by("candidate_run_id", _load_json(pkg / "walkforward" / "stability.json"),
                         key="candidate_stability")
    nb_by_id = _index_by("candidate_run_id", _load_json(pkg / "neighborhood" / "stability.json"),
                         key="candidate_stability")
    shadow_by_id = _index_by("candidate_run_id", _load_json(pkg / "shadow" / "comparison.json"),
                             key="comparisons")

    from ta_foundation.web.optimizer_candidate_report import list_existing_candidate_reports
    existing_reports = list_existing_candidate_reports(session)

    unranked_rows: list[CandidateRow] = []
    for run_id, eval_row in by_id_eval.items():
        rec = by_id_rec.get(run_id)
        rej = rejected_ids.get(run_id)
        if rec is not None:
            status = "recommended"
            decision_reason = str(rec.get("reason") or "")
        elif rej is not None:
            status = "rejected"
            decision_reason = str(rej.get("reason") or "did not pass hard filters")
        else:
            status = "evaluated"
            decision_reason = str(eval_row.get("reasons") or "")

        checks = [
            _summarize_bootstrap(bootstrap_by_id.get(run_id)),
            _summarize_walkforward(wf_by_id.get(run_id)),
            _summarize_neighborhood(nb_by_id.get(run_id)),
            _summarize_shadow(shadow_by_id.get(run_id)),
        ]
        score = _safe_float(eval_row.get("score"))
        adjusted_score = _compute_adjusted_score(score, checks)
        unranked_rows.append(CandidateRow(
            run_id=run_id,
            status=status,
            rank=rec_rank.get(run_id),
            score=score,
            adjusted_score=adjusted_score,
            adjusted_rank=0,
            mode=str(eval_row.get("mode") or ""),
            session_bucket=str(eval_row.get("session_bucket") or ""),
            total_net_profit=_safe_float(eval_row.get("total_net_profit")),
            profit_factor=_safe_float(eval_row.get("profit_factor")),
            max_drawdown=_safe_float(eval_row.get("max_drawdown")),
            trades=int(eval_row.get("trades") or 0),
            percent_days_traded=_safe_float(eval_row.get("percent_days_traded")),
            direction=str(eval_row.get("direction") or ""),
            risk_shape=str(eval_row.get("risk_shape") or ""),
            decision_reason=decision_reason,
            report_url=(
                f"/optimizer/sessions/{session.id}/candidates/{run_id}/report"
                if run_id in existing_reports else None
            ),
            checks=checks,
        ))
    rows = [
        replace(row, adjusted_rank=idx)
        for idx, row in enumerate(sorted(unranked_rows, key=_by_adjusted_rank), start=1)
    ]

    headline, notes = _parse_end_user_decision(pkg / "END_USER_DECISION.md")
    oos = (doc.oos_from_date, doc.oos_to_date) if doc.oos_from_date and doc.oos_to_date else None

    return DecisionDashboard(
        session_id=session.id,
        label=doc.label,
        instrument=doc.instrument,
        decision_state=str(manifest.get("decision_state") or ""),
        oos_window=oos,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        headline=headline,
        decision_notes=notes,
        recommended_count=len([r for r in rows if r.status == "recommended"]),
        rejected_count=len([r for r in rows if r.status == "rejected"]),
        candidate_rows=rows,
        artifact_links=_collect_artifact_links(pkg),
        status="ok",
    )


# ---------------------------------------------------------------------------
# Per-check summarizers
# ---------------------------------------------------------------------------

def _summarize_bootstrap(entry: dict[str, Any] | None) -> CheckSummary:
    if not entry:
        return CheckSummary(name="bootstrap", severity=SEVERITY_NONE, headline="(not run)")
    pf = entry.get("profit_factor") or {}
    observed = pf.get("observed")
    p_obs = pf.get("p_at_or_above_observed")
    trade_count = int(entry.get("trade_count") or 0)
    flags: list[str] = []
    severity = SEVERITY_OK
    if trade_count < 10:
        flags.append(f"only {trade_count} trades — distribution is wide")
        severity = SEVERITY_WARN
    if observed is None or p_obs is None:
        return CheckSummary(name="bootstrap", severity=severity or SEVERITY_NONE,
                            headline="bootstrap: insufficient data", flags=flags)
    # A p near 0.5 is "unsurprising shuffle"; p near 0 or 1 means the
    # observed result depends on ordering. We treat the absolute
    # distance from 0.5 as a "edge depends on luck" signal.
    headline = f"PF {observed:.2f}, p(≥obs)={p_obs:.2f}"
    if p_obs <= 0.05 or p_obs >= 0.95:
        flags.append(f"observed PF is an ordering artifact (p={p_obs:.2f})")
        severity = SEVERITY_WARN if severity == SEVERITY_OK else severity
    return CheckSummary(name="bootstrap", severity=severity, headline=headline, flags=flags)


def _summarize_walkforward(entry: dict[str, Any] | None) -> CheckSummary:
    if not entry:
        return CheckSummary(name="walkforward", severity=SEVERITY_NONE, headline="(not run)")
    pf_med = entry.get("pf_median")
    n_windows = int(entry.get("windows_run") or 0)
    n_above_1 = int(entry.get("windows_with_pf_above_1") or 0)
    raw_flags = list(entry.get("stability_flags") or [])
    headline = f"{n_above_1}/{n_windows} windows PF>1" if n_windows else "no windows"
    if pf_med is not None:
        headline = f"{n_above_1}/{n_windows} PF>1, median PF {pf_med:.2f}"
    severity = SEVERITY_OK
    if raw_flags:
        severity = SEVERITY_FAIL if any("collapsed" in f or "needle" in f or "no windows" in f for f in raw_flags) \
            else SEVERITY_WARN
    elif n_windows and n_above_1 == 0:
        severity = SEVERITY_FAIL
    return CheckSummary(name="walkforward", severity=severity, headline=headline, flags=raw_flags)


def _summarize_neighborhood(entry: dict[str, Any] | None) -> CheckSummary:
    if not entry:
        return CheckSummary(name="neighborhood", severity=SEVERITY_NONE, headline="(not run)")
    n_cells = int(entry.get("cells_run") or 0)
    n_above_1 = int(entry.get("cells_with_pf_above_1") or 0)
    raw_flags = list(entry.get("stability_flags") or [])
    pf_med = entry.get("pf_median")
    headline = f"{n_above_1}/{n_cells} cells PF>1" if n_cells else "no cells"
    if pf_med is not None:
        headline = f"{n_above_1}/{n_cells} PF>1, median PF {pf_med:.2f}"
    severity = SEVERITY_OK
    if raw_flags:
        severity = SEVERITY_FAIL if any("needle" in f or "no neighborhood" in f for f in raw_flags) \
            else SEVERITY_WARN
    elif n_cells and n_above_1 <= n_cells / 2:
        severity = SEVERITY_WARN
    return CheckSummary(name="neighborhood", severity=severity, headline=headline, flags=raw_flags)


def _summarize_shadow(entry: dict[str, Any] | None) -> CheckSummary:
    if not entry:
        return CheckSummary(name="shadow", severity=SEVERITY_NONE, headline="(not run)")
    raw_flags = list(entry.get("divergence_flags") or [])
    bt_trades = int(entry.get("backtest_trades") or 0)
    sh_trades = int(entry.get("shadow_trades") or 0)
    headline = f"BT {bt_trades} → shadow {sh_trades} trades"
    severity = SEVERITY_OK
    if raw_flags:
        severity = SEVERITY_FAIL if any("zero" in f.lower() for f in raw_flags) else SEVERITY_WARN
    return CheckSummary(name="shadow", severity=severity, headline=headline, flags=raw_flags)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _compute_adjusted_score(score: float | None, checks: list[CheckSummary]) -> float | None:
    raw_score = score if score is not None else 0.0
    fail_count = sum(1 for check in checks if check.severity == SEVERITY_FAIL)
    warn_count = sum(1 for check in checks if check.severity == SEVERITY_WARN)
    return raw_score - (FAIL_PENALTY * fail_count) - (WARN_PENALTY * warn_count)


def _by_adjusted_rank(row: CandidateRow) -> tuple[float, float, str]:
    adjusted_score = row.adjusted_score if row.adjusted_score is not None else float("-inf")
    raw_score = row.score if row.score is not None else float("-inf")
    return (-adjusted_score, -raw_score, row.run_id)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as h:
            data = json.load(h)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _index_by(field_name: str, payload: dict[str, Any] | None, *, key: str) -> dict[str, dict[str, Any]]:
    if not payload:
        return {}
    rows = payload.get(key) or []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get(field_name)
        if rid:
            out[str(rid)] = row
    return out


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _parse_end_user_decision(path: Path) -> tuple[str, list[str]]:
    """Best-effort: pull the first sentence after 'Decision state:' as
    the headline, and the bullets under '## Decision Notes' as the
    notes list. Missing file → ("", [])."""
    if not path.exists():
        return "", []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "", []

    headline = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Decision state:"):
            headline = stripped[len("Decision state:"):].strip(" `*")
            break

    notes: list[str] = []
    in_notes = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## "):
            in_notes = s.lower().startswith("## decision notes")
            continue
        if in_notes and s.startswith("- "):
            notes.append(s[2:].strip())
    return headline, notes


def _collect_artifact_links(pkg: Path) -> dict[str, str]:
    """Map display name → relative path (under deployment_package) for
    the per-check Markdown reports the operator may want to drill into."""
    candidates = [
        ("End-user decision (Markdown)", pkg / "END_USER_DECISION.md"),
        ("Decision summary (Markdown)", pkg / "DECISION_SUMMARY.md"),
        ("Recommendations (Markdown)",
            pkg / "final_backtest_handoff" / "final_backtest_review" / "recommendations.md"),
        ("Walk-forward stability (Markdown)", pkg / "walkforward" / "stability.md"),
        ("Parameter neighborhood (Markdown)", pkg / "neighborhood" / "stability.md"),
        ("Bootstrap robustness (Markdown)", pkg / "robustness" / "robustness.md"),
        ("Shadow execution (Markdown)", pkg / "shadow" / "comparison.md"),
    ]
    links: dict[str, str] = {}
    for name, path in candidates:
        if path.exists():
            try:
                rel = path.relative_to(pkg)
            except ValueError:
                rel = path
            links[name] = str(rel).replace("\\", "/")
    return links


def _empty_dashboard(
    *,
    session: OptimizerSession,
    doc,
    manifest: dict[str, Any],
    reason: str,
) -> DecisionDashboard:
    return DecisionDashboard(
        session_id=session.id,
        label=doc.label,
        instrument=doc.instrument,
        decision_state=str(manifest.get("decision_state") or ""),
        oos_window=None,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        headline="",
        decision_notes=[],
        recommended_count=0,
        rejected_count=0,
        candidate_rows=[],
        artifact_links={},
        status="no_review",
        status_reason=reason,
    )
