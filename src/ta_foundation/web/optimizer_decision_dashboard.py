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
    # "finalist" for F_NNN rows from the final-backtest review (default);
    # "promoted" for P_NNN rows that came from the shortlist-promotion path.
    kind: str = "finalist"
    # When kind == "promoted", points back to the shortlist row that
    # produced this candidate; otherwise None.
    source: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["checks"] = [c.to_dict() for c in self.checks]
        d["source"] = dict(self.source) if self.source else None
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
    template_links: dict[str, Any]
    session_report_url: str | None
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
            "template_links": dict(self.template_links),
            "session_report_url": self.session_report_url,
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

    # Promoted rows (P_NNN) come from a parallel review written by
    # :mod:`ta_foundation.web.optimizer_promotion_results`. They render
    # alongside the finalist (F_NNN) rows but carry kind="promoted" and a
    # source pointer back to the originating shortlist stage row.
    promoted_review = pkg / "promoted_handoff" / "promoted_review"
    promoted_evaluated = _load_json(promoted_review / "evaluated_candidates.json") or {}

    if not evaluated.get("rows") and not promoted_evaluated.get("rows"):
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
    from ta_foundation.web.optimizer_candidate_report import session_candidate_report_path
    from ta_foundation.web.optimizer_final_templates import final_template_links
    existing_reports = list_existing_candidate_reports(session)
    session_report_url = (
        f"/optimizer/sessions/{session.id}/candidate-report"
        if session_candidate_report_path(session).exists() else None
    )

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
    # Append promoted (P_NNN) rows so they participate in the same
    # ranking pass. They never carry per-check robustness data — those
    # checks run only for finalists — and their decision_reason is
    # synthesized from the evaluator row's pass/reject status.
    for promo_row in promoted_evaluated.get("rows") or []:
        if not isinstance(promo_row, dict):
            continue
        run_id = str(promo_row.get("run_id") or "")
        if not run_id:
            continue
        promo_status = "recommended" if str(promo_row.get("status") or "").lower() == "pass" else "evaluated"
        promo_reason = str(promo_row.get("reasons") or "")
        checks = [
            _summarize_bootstrap(None),
            _summarize_walkforward(None),
            _summarize_neighborhood(None),
            _summarize_shadow(None),
        ]
        promo_score = _safe_float(promo_row.get("score"))
        promo_adjusted = _compute_adjusted_score(promo_score, checks)
        source_payload: dict[str, str] | None = None
        src_stage = promo_row.get("source_stage_id")
        src_cid = promo_row.get("source_candidate_id")
        if src_stage or src_cid:
            source_payload = {
                "stage_id": str(src_stage or ""),
                "candidate_id": str(src_cid or ""),
            }
        unranked_rows.append(CandidateRow(
            run_id=run_id,
            status=promo_status,
            rank=None,
            score=promo_score,
            adjusted_score=promo_adjusted,
            adjusted_rank=0,
            mode=str(promo_row.get("mode") or ""),
            session_bucket=str(promo_row.get("session_bucket") or ""),
            total_net_profit=_safe_float(promo_row.get("total_net_profit")),
            profit_factor=_safe_float(promo_row.get("profit_factor")),
            max_drawdown=_safe_float(promo_row.get("max_drawdown")),
            trades=int(promo_row.get("trades") or 0),
            percent_days_traded=_safe_float(promo_row.get("percent_days_traded")),
            direction=str(promo_row.get("direction") or ""),
            risk_shape=str(promo_row.get("risk_shape") or ""),
            decision_reason=promo_reason,
            report_url=(
                f"/optimizer/sessions/{session.id}/candidates/{run_id}/report"
                if run_id in existing_reports else None
            ),
            checks=checks,
            kind="promoted",
            source=source_payload,
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
        template_links=final_template_links(session),
        session_report_url=session_report_url,
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
        template_links={},
        session_report_url=None,
        status="no_review",
        status_reason=reason,
    )


# ---------------------------------------------------------------------------
# Refine-from-decision-dashboard helper
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionRefineProposal:
    """Output of :func:`build_refine_from_decision_proposal`.

    Carries everything the route needs to:
    1. Write the new ``parsed_results/<parent>/selected.json``.
    2. Splice the new stage into ``recipe.json`` just before the
       final fixed-backtest stage.
    3. Hand the operator a focus URL into the recipe editor with the
       new stage already selected.
    """

    parent_stage_id: str
    new_stage_id: str
    new_stage_dict: dict[str, Any]
    selected_rows: list[dict[str, Any]]
    resolved_finalists: list[dict[str, Any]]


class DecisionRefineError(Exception):
    pass


def build_refine_from_decision_proposal(
    session: OptimizerSession,
    *,
    candidate_ids: list[str],
) -> DecisionRefineProposal:
    """Resolve final-stage candidate ids (``F_001`` etc.) back to the parent
    optimizer rows and assemble a ready-to-save refinement-stage spec.

    Hard requirements:

    * Every candidate id must appear in the session final_backtest manifest.
    * All resolved candidates must share a single ``parent_stage_id`` —
      a recipe stage has exactly one ``from`` ref. Mixing parents requires
      picking each parent separately.
    * The parent stage must exist in the saved recipe (otherwise the new
      stage ``from`` would dangle).

    The new stage:

    * Pins every base-matrix axis from the recipe so each finalist bucket
      combo (e.g. ``StartTimeH=0, Reverse=false``) stays locked.
    * Refines around the parent value for each parameter the parent stage
      swept. Radius defaults to 10 percent of the parent ``(max-min)``
      span, floored at one ``step`` so the refined sweep always contains
      parent +/- step (three points minimum).
    * Inherits the parent selection criteria so winners propagate through
      the same gates.
    """
    from ta_foundation.web.optimizer_recipe import load_recipe
    from ta_foundation.web.optimizer_recipe_templates import MANIFEST_FILENAME

    if not candidate_ids:
        raise DecisionRefineError("candidate_ids must be a non-empty list.")

    manifest_path = (
        session.directory
        / "generated_templates"
        / "final_backtest"
        / MANIFEST_FILENAME
    )
    if not manifest_path.exists():
        raise DecisionRefineError(
            "Final backtest manifest not found. Run the recipe through "
            "final_backtest before refining from the decision dashboard."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionRefineError(
            f"Could not read final backtest manifest: {exc}"
        ) from exc

    finalists_by_short = _index_finalists_by_short_id(manifest.get("templates") or [])
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for raw_id in candidate_ids:
        cid = str(raw_id or "").strip()
        if not cid:
            continue
        entry = finalists_by_short.get(cid) or finalists_by_short.get(cid.upper())
        if entry is None:
            missing.append(cid)
            continue
        resolved.append(entry)
    if missing:
        raise DecisionRefineError(
            f"Unknown finalist id(s) in final_backtest manifest: {sorted(set(missing))}"
        )
    if not resolved:
        raise DecisionRefineError("No finalist ids resolved from the manifest.")

    # The manifest carries two stage references per finalist and they
    # often disagree:
    #
    # * ``parent_stage_id`` is the stage that final_backtest reads ``from``
    #   (e.g. ``stage_3`` when the final stage has
    #   ``from: stage_3.selected_rows``).
    # * ``final_selection_source_stage`` is the stage whose ``scored_rows``
    #   the ``parent_candidate_id`` actually lives in (often ``stage_1``
    #   when the final selection walker promoted a row straight from the
    #   matrix stage instead of through a refinement).
    #
    # We need the second one. When the field is missing (older manifests)
    # we fall back to the candidate_id prefix, which encodes the source
    # stage as the first ``stage_N__`` segment.
    source_by_short_id: dict[str, str] = {}
    for entry in resolved:
        src_stage = _source_stage_for_finalist(entry)
        if not src_stage:
            raise DecisionRefineError(
                "Selected finalists have no resolvable source stage in the "
                "manifest. Re-run the final backtest to regenerate it."
            )
        short = (
            _short_finalist_id(str(entry.get("template_id") or ""))
            or str(entry.get("template_id") or "")
        )
        source_by_short_id[short] = src_stage

    source_stages = set(source_by_short_id.values())
    if len(source_stages) > 1:
        examples = ", ".join(
            f"{cid} (from {stage})"
            for cid, stage in sorted(source_by_short_id.items())[:6]
        )
        raise DecisionRefineError(
            "Selection spans multiple source stages "
            f"({sorted(source_stages)}). Refine each source stage separately. "
            f"Example mapping: {examples}."
        )
    parent_stage_id = next(iter(source_stages))

    parent_dir = session.directory / "parsed_results" / parent_stage_id
    scored_path = parent_dir / "scored_rows.json"
    if not scored_path.exists():
        raise DecisionRefineError(
            f"Parent stage results missing: {scored_path}. "
            "Cannot resolve finalists back to optimizer rows."
        )
    try:
        scored_rows = json.loads(scored_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionRefineError(
            f"Could not read parent scored rows: {exc}"
        ) from exc
    rows_by_candidate = {
        str(row.get("candidate_id") or ""): row
        for row in scored_rows
        if isinstance(row, dict)
    }

    selected_rows: list[dict[str, Any]] = []
    for entry in resolved:
        pcid = str(entry.get("parent_candidate_id") or "")
        parent_row = rows_by_candidate.get(pcid)
        if parent_row is None:
            raise DecisionRefineError(
                f"Finalist {entry.get('template_id')!r} references parent "
                f"candidate {pcid!r} that is not in {scored_path.name}."
            )
        row = dict(parent_row)
        row["selection_status"] = "selected"
        row["selection_reason"] = (
            f"Hand-picked from Decision Dashboard finalist "
            f"{_short_finalist_id(str(entry.get('template_id') or ''))}"
        )
        selected_rows.append(row)

    recipe = load_recipe(session)
    parent_recipe_stage = next(
        (s for s in recipe.stages if s.stage_id == parent_stage_id),
        None,
    )
    if parent_recipe_stage is None:
        raise DecisionRefineError(
            f"Parent stage {parent_stage_id!r} is not in the saved recipe. "
            "The recipe may have been edited since the finalists ran."
        )

    pin_names = sorted({
        entry.param
        for entry in recipe.base_matrix
        if entry.role == "matrix_axis"
    })

    refine_around: dict[str, dict[str, Any]] = {}
    for name, cfg in parent_recipe_stage.optimize_inside_template.items():
        radius, step = _radius_and_step_from_parent_cfg(cfg)
        refine_around[name] = {
            "source": "parent",
            "min": cfg.get("min"),
            "max": cfg.get("max"),
            "step": step,
            "radius": radius,
        }

    new_stage_id = _next_optimizer_stage_id(recipe)
    new_stage_dict: dict[str, Any] = {
        "stage_id": new_stage_id,
        "stage_type": "optimizer",
        "description": (
            f"Refinement around {len(selected_rows)} Decision-Dashboard "
            f"finalist(s) from {parent_stage_id}."
        ),
        "from": f"{parent_stage_id}.selected_rows",
    }
    if pin_names:
        new_stage_dict["pin"] = pin_names
    if refine_around:
        new_stage_dict["refine_around_parent_result"] = refine_around
    if parent_recipe_stage.selection:
        new_stage_dict["selection"] = dict(parent_recipe_stage.selection)

    return DecisionRefineProposal(
        parent_stage_id=parent_stage_id,
        new_stage_id=new_stage_id,
        new_stage_dict=new_stage_dict,
        selected_rows=selected_rows,
        resolved_finalists=resolved,
    )


def _index_finalists_by_short_id(templates: list[Any]) -> dict[str, dict[str, Any]]:
    """Index manifest entries by both short (``F_001``) and full
    (``final_backtest__F_001``) template ids so the route accepts either."""
    out: dict[str, dict[str, Any]] = {}
    for item in templates:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("template_id") or "")
        if not tid:
            continue
        out[tid] = item
        short = _short_finalist_id(tid)
        if short:
            out[short] = item
    return out


def _short_finalist_id(template_id: str) -> str:
    """``final_backtest__F_001`` becomes ``F_001``; non-``F_`` ids return ``""``."""
    tail = template_id.rsplit("__", 1)[-1]
    return tail if tail.startswith("F_") else ""


def _source_stage_for_finalist(entry: dict[str, Any]) -> str:
    """Return the stage whose ``scored_rows`` the finalist's parent row lives in.

    The manifest's ``parent_stage_id`` is unreliable for this — it carries the
    stage the final_backtest reads ``from`` (typically the last refinement
    stage), not the stage that actually scored the parent row. Prefer
    ``final_selection_source_stage`` when present; fall back to parsing the
    ``parent_candidate_id`` prefix, which always starts with ``stage_N__`` and
    encodes the truly-scoring stage.
    """
    explicit = str(entry.get("final_selection_source_stage") or "").strip()
    if explicit:
        return explicit
    parent_cid = str(entry.get("parent_candidate_id") or "")
    head, sep, _ = parent_cid.partition("__")
    if sep and head.startswith("stage_"):
        return head
    # Final fallback: the manifest's nominal parent_stage_id. Not ideal but
    # better than refusing finalists from older manifests outright.
    nominal = str(entry.get("parent_stage_id") or "").strip()
    return nominal


def _radius_and_step_from_parent_cfg(cfg: dict[str, Any]) -> tuple[Any, Any]:
    """Pick a refine radius and step for a parent sweep block.

    Radius defaults to 10 percent of ``(max - min)``, floored to at least
    one ``step`` so the refined sweep always contains parent +/- step.
    Booleans and unparseable values get radius = step = 1, which keeps the
    sweep domain unchanged after pinning the parent value.
    """
    step = cfg.get("step", cfg.get("increment", 1))
    try:
        mn = float(cfg.get("min"))
        mx = float(cfg.get("max"))
    except (TypeError, ValueError):
        return 1, step
    try:
        step_f = float(step)
    except (TypeError, ValueError):
        step_f = 1.0
    span = max(mx - mn, 0.0)
    radius = max(span * 0.10, step_f)
    if step_f and step_f == int(step_f) and radius == int(radius):
        return int(radius), step
    return radius, step


def _next_optimizer_stage_id(recipe: "OptimizerRecipeDocument") -> str:
    """Pick ``stage_N`` so the new id does not collide with existing stages."""
    used = {s.stage_id for s in recipe.stages}
    n = sum(1 for s in recipe.stages if s.stage_type == "optimizer") + 1
    while f"stage_{n}" in used:
        n += 1
    return f"stage_{n}"
