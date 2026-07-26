from __future__ import annotations

"""Session-scoped leaderboard across every backtest the recipe produced.

Read-only complement to the Decision Dashboard. Answers the question:
"did the recipe optimize away from the best?" by surfacing every row
the optimizer ever scored — selected, evaluated, and final-backtest
finalists — ranked on a metric the operator picks.

Reads from ``<session>/``:
  * ``parsed_results/<stage_id>/scored_rows.json`` — every scored row
  * ``parsed_results/<stage_id>/selected.json`` — winners per stage
  * ``deployment_package/final_backtest_handoff/final_backtest_review/
    evaluated_candidates.json`` — final-backtest rows
  * ``deployment_package/final_backtest_handoff/final_backtest_review/
    recommendations.json`` — recommended / rejected finalist status
  * ``recipe.json`` — per-stage ``selection`` guardrails (the strictest
    ``min_trades`` becomes the default filter).

Degrades gracefully: any missing artifact is recorded in
``LeaderboardReport.banner_notes`` and the row set is whatever could
be loaded. Never raises for missing optional artifacts — an incomplete
leaderboard is more useful than a hard error.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ta_foundation.web.optimizer_session import OptimizerSession

# Reuse the same namespace normalization the lineage page already does.
# Stage scored_rows expose ``param_*`` columns under NinjaTrader DISPLAY
# names (``Start_Time_(HH)``) while the manifest/finalist dicts carry
# CODE names (``StartTimeH``) or snake_case English keys. Without the
# translation the leaderboard would show "added" params on finalists
# that are really the same params under a different alias.
try:
    from ta_foundation.analysis.strategies.pantheon_bot_v2.settings_loader import (
        _DISPLAY_TO_PROP as _PANTHEON_DISPLAY_TO_PROP,
    )
except Exception:  # pragma: no cover - safety net for import-time edge cases
    _PANTHEON_DISPLAY_TO_PROP = {}

try:
    from ta_foundation.web.optimizer_refine import _PARAM_NAME_MAP as _SNAKE_TO_PROP
except Exception:  # pragma: no cover - safety net
    _SNAKE_TO_PROP = {}


PARSED_RESULTS_DIRNAME = "parsed_results"
FINAL_BACKTEST_STAGE_ID = "final_backtest"
EVALUATED_PATH = (
    "deployment_package",
    "final_backtest_handoff",
    "final_backtest_review",
    "evaluated_candidates.json",
)
RECOMMENDATIONS_PATH = (
    "deployment_package",
    "final_backtest_handoff",
    "final_backtest_review",
    "recommendations.json",
)

# Supported sort metrics. Keep in sync with the template dropdown.
SUPPORTED_METRICS: tuple[str, ...] = (
    "profit_factor",
    "total_net_profit",
    "max_drawdown_abs",
    "net_dd_ratio",
    "dd_adjusted_score",
)
DEFAULT_METRIC = "profit_factor"
DEFAULT_TOP_N = 50
# net_profit - DD_ADJUSTED_PENALTY * |max_drawdown|. Matches the shape
# the deployment_package evaluator already uses; cheap composite so the
# leaderboard isn't only telling raw PF/Net lies.
DD_ADJUSTED_PENALTY = 2.0

# Status tags the template colors. Stage rows: "selected" | "evaluated".
# Final-backtest rows: "finalist_recommended" | "finalist_rejected" |
# "finalist_evaluated".
STATUS_SELECTED = "selected"
STATUS_EVALUATED = "evaluated"
STATUS_FINALIST_RECOMMENDED = "finalist_recommended"
STATUS_FINALIST_REJECTED = "finalist_rejected"
STATUS_FINALIST_EVALUATED = "finalist_evaluated"


class LeaderboardError(Exception):
    pass


@dataclass(frozen=True)
class LeaderboardKpis:
    profit_factor: float | None = None
    total_net_profit: float | None = None
    max_drawdown: float | None = None        # signed (can be negative in source)
    max_drawdown_abs: float | None = None    # always >= 0
    trades: int | None = None
    net_dd_ratio: float | None = None        # net / max(|dd|, 1)
    dd_adjusted_score: float | None = None   # net - K*|dd|

    def to_dict(self) -> dict[str, Any]:
        return {
            "profit_factor": self.profit_factor,
            "total_net_profit": self.total_net_profit,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_abs": self.max_drawdown_abs,
            "trades": self.trades,
            "net_dd_ratio": self.net_dd_ratio,
            "dd_adjusted_score": self.dd_adjusted_score,
        }


@dataclass(frozen=True)
class LeaderboardRow:
    stage_id: str
    stage_label: str
    stage_type: str           # "optimizer" | "fixed_backtest"
    candidate_id: str
    template_id: str | None
    bucket_id: str | None
    parent_candidate_id: str | None
    optimizer_target: str | None
    status: str               # STATUS_* constant
    kpis: LeaderboardKpis
    params: dict[str, Any]
    beats_best_finalist: bool
    metric_value: float | None  # the value used for sorting; pre-computed
    links: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "stage_label": self.stage_label,
            "stage_type": self.stage_type,
            "candidate_id": self.candidate_id,
            "template_id": self.template_id,
            "bucket_id": self.bucket_id,
            "parent_candidate_id": self.parent_candidate_id,
            "optimizer_target": self.optimizer_target,
            "status": self.status,
            "kpis": self.kpis.to_dict(),
            "params": dict(self.params),
            "beats_best_finalist": self.beats_best_finalist,
            "metric_value": self.metric_value,
            "links": dict(self.links),
        }


@dataclass(frozen=True)
class LeaderboardReport:
    session_id: str
    session_label: str | None
    metric: str
    applied_min_trades: int
    default_min_trades: int
    top_n: int
    total_scanned: int
    total_after_min_trades: int
    rows: list[LeaderboardRow]
    best_finalist_metric: float | None
    banner_notes: list[str] = field(default_factory=list)
    supported_metrics: tuple[str, ...] = SUPPORTED_METRICS

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_label": self.session_label,
            "metric": self.metric,
            "applied_min_trades": self.applied_min_trades,
            "default_min_trades": self.default_min_trades,
            "top_n": self.top_n,
            "total_scanned": self.total_scanned,
            "total_after_min_trades": self.total_after_min_trades,
            "rows": [r.to_dict() for r in self.rows],
            "best_finalist_metric": self.best_finalist_metric,
            "banner_notes": list(self.banner_notes),
            "supported_metrics": list(self.supported_metrics),
        }


def build_leaderboard(
    session: OptimizerSession,
    *,
    metric: str = DEFAULT_METRIC,
    min_trades: int | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> LeaderboardReport:
    if metric not in SUPPORTED_METRICS:
        raise LeaderboardError(
            f"Unsupported metric {metric!r}. Choose one of: {list(SUPPORTED_METRICS)}"
        )
    if top_n <= 0:
        raise LeaderboardError("top_n must be a positive integer.")

    notes: list[str] = []
    doc = session.load_document()

    recipe_stages = _load_recipe_stages(session)
    default_min_trades = _strictest_min_trades(recipe_stages)
    applied_min_trades = (
        int(min_trades) if min_trades is not None and min_trades >= 0
        else default_min_trades
    )

    recommendations = _recommendations_index(session)
    evaluated = _evaluated_by_run_id(session)

    # Map source stage -> set of finalist IS-parent candidate ids, so the
    # "beats best finalist" flag compares each row to a finalist sibling
    # that actually scored in the same stage (IS-to-IS).
    finalist_parents_by_stage = _finalist_parents_by_source_stage(evaluated)

    stage_rows, scan_notes = _collect_stage_rows(
        session=session,
        recipe_stages=recipe_stages,
        finalist_parents_by_stage=finalist_parents_by_stage,
    )
    notes.extend(scan_notes)

    finalist_rows = _collect_finalist_rows(
        session=session,
        evaluated=evaluated,
        recommendations=recommendations,
        recipe_stages=recipe_stages,
    )
    if not evaluated:
        notes.append(
            "No final-backtest review on disk yet — leaderboard shows only "
            "stage-level rows. Recompute after final_backtest finishes."
        )

    all_rows = stage_rows + finalist_rows
    total_scanned = len(all_rows)
    if total_scanned == 0:
        notes.append(
            "No scored rows were found under parsed_results/. Run at least "
            "one optimizer stage and ingest its results first."
        )

    # Per-source-stage best finalist-parent metric (IS-to-IS baseline).
    finalist_parent_metric_by_stage = _finalist_parent_metric_by_stage(
        stage_rows=stage_rows,
        finalist_parents_by_stage=finalist_parents_by_stage,
        metric=metric,
    )

    # Tag every row with its metric value + beats_best_finalist flag.
    tagged: list[LeaderboardRow] = []
    for row in all_rows:
        metric_value = _metric_value(row.kpis, metric)
        beats = False
        if row.status in (STATUS_SELECTED, STATUS_EVALUATED) and metric_value is not None:
            baseline = finalist_parent_metric_by_stage.get(row.stage_id)
            if baseline is not None and _metric_beats(metric, metric_value, baseline):
                beats = True
        tagged.append(_with_metric(row, metric_value=metric_value, beats=beats))

    # Apply min_trades filter (always — even the strictest stage gate; the
    # whole point of this page is to surface rows the recipe couldn't act
    # on, but PF on a 3-trade sample is noise, not signal).
    filtered = [r for r in tagged if (r.kpis.trades or 0) >= applied_min_trades]
    total_after_min_trades = len(filtered)

    # Rank. Rows where metric_value is None drop to the bottom.
    filtered.sort(key=_sort_key(metric))
    rows = filtered[:top_n]

    best_finalist_metric = _best_finalist_metric(finalist_rows, metric)

    banner_notes = [
        "Cross-stage rows are informational only. Each stage searches a "
        "different parameter space, so a Stage 1 row beating a Stage 3 "
        "finalist is not a 'swap winners' recommendation.",
        "Stage rows are in-sample (IS); final-backtest rows are out-of-sample "
        "(OOS). The 'beats best finalist' flag compares IS-to-IS against the "
        "finalist's parent row in the same stage, not against the OOS score.",
        (
            f"Min-trades default = {default_min_trades} "
            "(strictest stage's selection.min_trades from recipe.json)."
            if default_min_trades > 1
            else "No min_trades guard was declared in any stage's selection "
            "block; defaulting to 1."
        ),
        *notes,
    ]

    return LeaderboardReport(
        session_id=session.id,
        session_label=doc.label,
        metric=metric,
        applied_min_trades=applied_min_trades,
        default_min_trades=default_min_trades,
        top_n=top_n,
        total_scanned=total_scanned,
        total_after_min_trades=total_after_min_trades,
        rows=rows,
        best_finalist_metric=best_finalist_metric,
        banner_notes=banner_notes,
    )


# ---------------------------------------------------------------------------
# Stage row collection
# ---------------------------------------------------------------------------

def _collect_stage_rows(
    *,
    session: OptimizerSession,
    recipe_stages: dict[str, dict[str, Any]],
    finalist_parents_by_stage: dict[str, set[str]],
) -> tuple[list[LeaderboardRow], list[str]]:
    notes: list[str] = []
    out: list[LeaderboardRow] = []
    parsed_root = session.directory / PARSED_RESULTS_DIRNAME
    if not parsed_root.exists():
        return out, notes

    for stage_dir in sorted(p for p in parsed_root.iterdir() if p.is_dir()):
        stage_id = stage_dir.name
        if stage_id == FINAL_BACKTEST_STAGE_ID:
            # Final-backtest rows come from evaluated_candidates.json, not
            # from a per-stage scored_rows.json. Skip silently.
            continue
        scored_path = stage_dir / "scored_rows.json"
        scored = _load_json(scored_path)
        if not isinstance(scored, list):
            notes.append(
                f"{stage_id}: no scored_rows.json — skipped."
            )
            continue
        selected_ids = _selected_candidate_ids(stage_dir / "selected.json")
        stage_meta = recipe_stages.get(stage_id) or {}
        stage_label = str(stage_meta.get("description") or stage_id)
        stage_type = str(stage_meta.get("stage_type") or "optimizer")

        for row in scored:
            if not isinstance(row, dict):
                continue
            candidate_id = _str_or_none(row.get("candidate_id"))
            if not candidate_id:
                continue
            status = STATUS_SELECTED if candidate_id in selected_ids else STATUS_EVALUATED
            kpis = _kpis_from_stage_row(row)
            out.append(LeaderboardRow(
                stage_id=stage_id,
                stage_label=stage_label,
                stage_type=stage_type,
                candidate_id=candidate_id,
                template_id=_str_or_none(row.get("template_id")),
                bucket_id=_str_or_none(row.get("bucket_id")),
                parent_candidate_id=_str_or_none(row.get("parent_candidate_id")),
                optimizer_target=_str_or_none(row.get("optimizer_target")),
                status=status,
                kpis=kpis,
                params=_extract_stage_params(row),
                beats_best_finalist=False,  # filled later
                metric_value=None,          # filled later
                links=_links_for_stage_row(session, stage_id=stage_id),
            ))
    return out, notes


def _collect_finalist_rows(
    *,
    session: OptimizerSession,
    evaluated: dict[str, dict[str, Any]],
    recommendations: dict[str, dict[str, Any]],
    recipe_stages: dict[str, dict[str, Any]],
) -> list[LeaderboardRow]:
    out: list[LeaderboardRow] = []
    stage_meta = recipe_stages.get(FINAL_BACKTEST_STAGE_ID) or {}
    stage_label = str(stage_meta.get("description") or "Final Backtest Validation")
    stage_type = str(stage_meta.get("stage_type") or "fixed_backtest")

    for run_id, row in evaluated.items():
        rec = recommendations.get(run_id)
        if rec is None:
            status = STATUS_FINALIST_EVALUATED
        elif rec.get("status") == "recommended":
            status = STATUS_FINALIST_RECOMMENDED
        elif rec.get("status") == "rejected":
            status = STATUS_FINALIST_REJECTED
        else:
            status = STATUS_FINALIST_EVALUATED

        kpis = _kpis_from_finalist_row(row)
        out.append(LeaderboardRow(
            stage_id=FINAL_BACKTEST_STAGE_ID,
            stage_label=stage_label,
            stage_type=stage_type,
            candidate_id=run_id,
            template_id=_str_or_none(row.get("template_id")),
            bucket_id=_str_or_none(row.get("session_bucket") or row.get("bucket_id")),
            parent_candidate_id=_str_or_none(row.get("parent_candidate_id")),
            optimizer_target=_str_or_none(row.get("optimization_target") or row.get("optimizer_target")),
            status=status,
            kpis=kpis,
            params=_extract_finalist_params(row),
            beats_best_finalist=False,
            metric_value=None,
            links=_links_for_finalist(session, run_id=run_id),
        ))
    return out


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def _metric_value(kpis: LeaderboardKpis, metric: str) -> float | None:
    if metric == "profit_factor":
        return kpis.profit_factor
    if metric == "total_net_profit":
        return kpis.total_net_profit
    if metric == "max_drawdown_abs":
        return kpis.max_drawdown_abs
    if metric == "net_dd_ratio":
        return kpis.net_dd_ratio
    if metric == "dd_adjusted_score":
        return kpis.dd_adjusted_score
    return None


def _metric_beats(metric: str, value: float, baseline: float) -> bool:
    # max_drawdown_abs: lower is better. Everything else: higher is better.
    if metric == "max_drawdown_abs":
        return value < baseline
    return value > baseline


def _sort_key(metric: str):
    # Sort so the best metric comes first. None always sinks to the bottom.
    if metric == "max_drawdown_abs":
        def key(row: LeaderboardRow) -> tuple[int, float, str]:
            mv = row.metric_value
            if mv is None:
                return (1, 0.0, row.candidate_id)
            return (0, float(mv), row.candidate_id)  # ascending
        return key

    def key(row: LeaderboardRow) -> tuple[int, float, str]:
        mv = row.metric_value
        if mv is None:
            return (1, 0.0, row.candidate_id)
        return (0, -float(mv), row.candidate_id)  # descending
    return key


def _with_metric(
    row: LeaderboardRow, *, metric_value: float | None, beats: bool
) -> LeaderboardRow:
    return LeaderboardRow(
        stage_id=row.stage_id,
        stage_label=row.stage_label,
        stage_type=row.stage_type,
        candidate_id=row.candidate_id,
        template_id=row.template_id,
        bucket_id=row.bucket_id,
        parent_candidate_id=row.parent_candidate_id,
        optimizer_target=row.optimizer_target,
        status=row.status,
        kpis=row.kpis,
        params=row.params,
        beats_best_finalist=beats,
        metric_value=metric_value,
        links=row.links,
    )


def _finalist_parents_by_source_stage(
    evaluated: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    """Group each finalist's IS parent candidate id by the stage that
    scored it. Used downstream to compute the per-stage IS baseline and
    to make sure beats_best_finalist is an IS-to-IS comparison.
    """
    out: dict[str, set[str]] = {}
    for row in evaluated.values():
        pcid = _str_or_none(row.get("parent_candidate_id"))
        if not pcid:
            continue
        head, sep, _ = pcid.partition("__")
        if not sep or not head.startswith("stage_"):
            continue
        out.setdefault(head, set()).add(pcid)
    return out


def _finalist_parent_metric_by_stage(
    *,
    stage_rows: list[LeaderboardRow],
    finalist_parents_by_stage: dict[str, set[str]],
    metric: str,
) -> dict[str, float]:
    """Best (per the chosen metric) metric value across finalist IS
    parent rows for each source stage. A stage row beating this value
    is the "did we optimize away from this?" signal.
    """
    out: dict[str, float] = {}
    if not finalist_parents_by_stage:
        return out
    by_id = {r.candidate_id: r for r in stage_rows}
    for stage_id, parent_ids in finalist_parents_by_stage.items():
        best: float | None = None
        for pcid in parent_ids:
            row = by_id.get(pcid)
            if row is None:
                continue
            mv = _metric_value(row.kpis, metric)
            if mv is None:
                continue
            if best is None or _metric_beats(metric, mv, best):
                best = mv
        if best is not None:
            out[stage_id] = best
    return out


def _best_finalist_metric(
    finalist_rows: list[LeaderboardRow], metric: str
) -> float | None:
    best: float | None = None
    for row in finalist_rows:
        mv = _metric_value(row.kpis, metric)
        if mv is None:
            continue
        if best is None or _metric_beats(metric, mv, best):
            best = mv
    return best


# ---------------------------------------------------------------------------
# KPI extraction
# ---------------------------------------------------------------------------

def _kpis_from_stage_row(row: dict[str, Any]) -> LeaderboardKpis:
    pf = _safe_float(row.get("profit_factor"))
    net = _safe_float(row.get("total_net_profit") or row.get("net_profit"))
    dd = _safe_float(row.get("max_drawdown") or row.get("drawdown_abs"))
    trades = _safe_int(row.get("trades") or row.get("total_trades"))
    return _compose_kpis(pf=pf, net=net, dd=dd, trades=trades)


def _kpis_from_finalist_row(row: dict[str, Any]) -> LeaderboardKpis:
    pf = _safe_float(row.get("profit_factor"))
    net = _safe_float(row.get("total_net_profit"))
    dd = _safe_float(row.get("max_drawdown"))
    trades = _safe_int(row.get("trades"))
    return _compose_kpis(pf=pf, net=net, dd=dd, trades=trades)


def _compose_kpis(
    *, pf: float | None, net: float | None, dd: float | None, trades: int | None
) -> LeaderboardKpis:
    dd_abs = abs(dd) if dd is not None else None
    ratio = None
    adjusted = None
    if net is not None and dd_abs is not None:
        ratio = net / max(dd_abs, 1.0)
        adjusted = net - DD_ADJUSTED_PENALTY * dd_abs
    elif net is not None and dd_abs is None:
        # No drawdown signal — fall back to raw net for the composite
        # rather than dropping the row off the DD-aware leaderboards.
        ratio = net
        adjusted = net
    return LeaderboardKpis(
        profit_factor=pf,
        total_net_profit=net,
        max_drawdown=dd,
        max_drawdown_abs=dd_abs,
        trades=trades,
        net_dd_ratio=ratio,
        dd_adjusted_score=adjusted,
    )


# ---------------------------------------------------------------------------
# Param extraction
# ---------------------------------------------------------------------------

def _extract_stage_params(row: dict[str, Any]) -> dict[str, Any]:
    """Pull ``param_*`` columns and translate DISPLAY -> CODE names so
    the leaderboard joins cleanly against the finalist params.
    """
    out: dict[str, Any] = {}
    for key, value in row.items():
        if not isinstance(key, str) or not key.startswith("param_") or value is None:
            continue
        bare = key[len("param_"):]
        canonical = _PANTHEON_DISPLAY_TO_PROP.get(bare, bare)
        if canonical not in out:
            out[canonical] = value
    return out


def _extract_finalist_params(row: dict[str, Any]) -> dict[str, Any]:
    """Pull operator-meaningful keys out of an evaluated_candidates row
    and translate snake_case English keys to canonical CODE names.

    Skips bookkeeping keys like ``run_id``, ``score``, KPI columns,
    etc. — anything not in the snake-case map and not already
    parameter-shaped is treated as bookkeeping and dropped.
    """
    skip = {
        "run_id", "score", "rank", "reasons", "label",
        "profit_factor", "total_net_profit", "max_drawdown", "trades",
        "percent_days_traded", "direction", "risk_shape", "mode",
        "session_bucket", "template_id", "parent_candidate_id",
        "optimization_target", "optimizer_target",
    }
    out: dict[str, Any] = {}
    for key, value in row.items():
        if not isinstance(key, str) or value is None:
            continue
        if key in skip:
            continue
        canonical = _SNAKE_TO_PROP.get(key, key)
        if canonical not in out:
            out[canonical] = value
    return out


# ---------------------------------------------------------------------------
# Recipe / recommendations / evaluated loaders
# ---------------------------------------------------------------------------

def _load_recipe_stages(session: OptimizerSession) -> dict[str, dict[str, Any]]:
    payload = _load_json(session.directory / "recipe.json")
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for stage in payload.get("stages") or []:
        if isinstance(stage, dict) and stage.get("stage_id"):
            out[str(stage["stage_id"])] = stage
    return out


def _strictest_min_trades(recipe_stages: dict[str, dict[str, Any]]) -> int:
    strictest = 0
    for stage in recipe_stages.values():
        selection = stage.get("selection") if isinstance(stage, dict) else None
        if not isinstance(selection, dict):
            continue
        value = selection.get("min_trades")
        n = _safe_int(value)
        if n is not None and n > strictest:
            strictest = n
    return strictest if strictest > 0 else 1


def _evaluated_by_run_id(session: OptimizerSession) -> dict[str, dict[str, Any]]:
    payload = _load_json(session.directory.joinpath(*EVALUATED_PATH))
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = _str_or_none(row.get("run_id"))
        if rid:
            out[rid] = row
    return out


def _recommendations_index(session: OptimizerSession) -> dict[str, dict[str, Any]]:
    payload = _load_json(session.directory.joinpath(*RECOMMENDATIONS_PATH))
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("recommendations") or []:
        if isinstance(row, dict) and row.get("run_id"):
            entry = dict(row)
            entry["status"] = "recommended"
            out[str(row["run_id"])] = entry
    for row in payload.get("rejected") or []:
        if isinstance(row, dict) and row.get("run_id"):
            entry = dict(row)
            entry["status"] = "rejected"
            out[str(row["run_id"])] = entry
    return out


def _selected_candidate_ids(selected_path: Path) -> set[str]:
    rows = _load_json(selected_path)
    if not isinstance(rows, list):
        return set()
    return {
        str(r.get("candidate_id") or "")
        for r in rows
        if isinstance(r, dict) and r.get("candidate_id")
    }


# ---------------------------------------------------------------------------
# Link helpers
# ---------------------------------------------------------------------------

def _links_for_stage_row(session: OptimizerSession, *, stage_id: str) -> dict[str, str]:
    return {
        "stage_results_url": (
            f"/optimizer/sessions/{session.id}/resume?"
            f"focus_stage={stage_id}&focus_tab=results"
        ),
    }


def _links_for_finalist(session: OptimizerSession, *, run_id: str) -> dict[str, str]:
    return {
        "report_url": f"/optimizer/sessions/{session.id}/candidates/{run_id}/report",
        "lineage_url": f"/optimizer/sessions/{session.id}/lineage/{run_id}",
        "decision_url": f"/optimizer/sessions/{session.id}/decision",
    }


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


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


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
