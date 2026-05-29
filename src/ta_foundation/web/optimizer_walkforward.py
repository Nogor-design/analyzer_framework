from __future__ import annotations

"""Walk-forward validation: same fixed parameters across N rolling windows.

Concept
-------

Pick a final candidate (e.g. ``F_001``) and re-run its fixed-Backtest
template across multiple historical rolling windows. Each window has the
same length; the planner places them backwards from an anchor date and
optionally skips any window that overlaps the original
in-sample/OOS range (so the operator only sees true OOS comparisons).

This is the cheaper, fixed-parameter variant of true walk-forward.
A future enhancement would re-OPTIMIZE the parameters on each IS window
before validating on the next OOS window; that requires running the
full phase-1→2→3 pipeline per fold and is deferred.

Output
------

    <session>/deployment_package/walkforward/
        windows.json                  # planned windows + candidate selection
        templates/<run>__W<i>.xml     # one template per (candidate, window)
        nt_output/<run>__W<i>/        # NT exports per window
        nt8_run_batch_command.json    # IPC payload ready for AddOn
        WALKFORWARD_README.md
        stability.json                # machine-readable per-window stats
        stability.md                  # operator-facing markdown report

Per-window stats include trade count, net profit, profit factor, max
drawdown. The stability report summarizes per-candidate windows where
PF holds vs. where it collapses below 1.0, and computes the
coefficient of variation of net profit across windows as a quick
stability score.
"""

import json
import math
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.optimization.walkforward import (
    WalkForwardError,
    WalkForwardWindow,
    plan_walk_forward_windows,
)
from ta_foundation.web.optimizer_session import OptimizerSession


WF_DIRNAME = "walkforward"
WF_TEMPLATES_DIRNAME = "templates"
WF_OUTPUT_DIRNAME = "nt_output"
WF_COMMAND_FILENAME = "nt8_run_batch_command.json"


class OptimizerWalkForwardError(Exception):
    pass


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowTemplate:
    candidate_run_id: str
    window_index: int
    from_date: str
    to_date: str
    source_template: str
    output_template: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WindowResult:
    candidate_run_id: str
    window_index: int
    from_date: str
    to_date: str
    trades: int
    net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateStability:
    candidate_run_id: str
    is_reference_trades: int
    is_reference_net_profit: float | None
    is_reference_profit_factor: float | None
    windows_run: int
    windows_with_trades: int
    windows_with_pf_above_1: int
    mean_net_profit: float | None
    median_net_profit: float | None
    pf_min: float | None
    pf_median: float | None
    pf_max: float | None
    coefficient_of_variation_net: float | None
    stability_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WalkForwardGeneration:
    session_id: str
    output_dir: str
    windows: list[WalkForwardWindow]
    templates: list[WindowTemplate]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "output_dir": self.output_dir,
            "windows": [w.to_dict() for w in self.windows],
            "templates": [t.to_dict() for t in self.templates],
            "notes": self.notes,
        }


@dataclass(frozen=True)
class WalkForwardStatus:
    session_id: str
    output_dir: str
    template_count: int
    nt_output_present: bool
    per_window_results: list[WindowResult]
    candidate_stability: list[CandidateStability]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "output_dir": self.output_dir,
            "template_count": self.template_count,
            "nt_output_present": self.nt_output_present,
            "per_window_results": [r.to_dict() for r in self.per_window_results],
            "candidate_stability": [s.to_dict() for s in self.candidate_stability],
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def generate_walk_forward_templates(
    session: OptimizerSession,
    *,
    anchor_date: str,
    window_days: int,
    count: int,
    gap_days: int = 0,
    candidate_run_ids: list[str] | None = None,
    skip_is_window: bool = True,
) -> WalkForwardGeneration:
    pkg_dir = session.directory / "deployment_package"
    named_dir = pkg_dir / "final_backtest_handoff" / "named_backtest_templates"
    if not named_dir.exists():
        raise OptimizerWalkForwardError(
            f"No named backtest templates at {named_dir}. Run the final fixed Backtest phase first."
        )

    doc = session.load_document()
    skip_range: tuple[str, str] | None = None
    if skip_is_window and doc.oos_from_date and doc.oos_to_date:
        skip_range = (doc.oos_from_date, doc.oos_to_date)

    try:
        windows = plan_walk_forward_windows(
            anchor_date=anchor_date,
            window_days=window_days,
            count=count,
            gap_days=gap_days,
            skip_overlap_with=skip_range,
        )
    except WalkForwardError as exc:
        raise OptimizerWalkForwardError(str(exc)) from exc

    if not windows:
        raise OptimizerWalkForwardError(
            "No windows planned. Check anchor_date / window_days / count / "
            "whether skip_is_window filtered everything."
        )

    out_dir = pkg_dir / WF_DIRNAME
    templates_dir = out_dir / WF_TEMPLATES_DIRNAME
    if templates_dir.exists():
        shutil.rmtree(templates_dir)
    templates_dir.mkdir(parents=True, exist_ok=True)

    selection: set[str] | None = None
    if candidate_run_ids is not None:
        selection = {str(s).strip() for s in candidate_run_ids if str(s).strip()}
        if not selection:
            raise OptimizerWalkForwardError("candidate_run_ids was empty")

    templates: list[WindowTemplate] = []
    notes: list[str] = []

    for xml_path in sorted(named_dir.rglob("*.xml")):
        run_id = _candidate_run_id_for_template(xml_path)
        if selection is not None and run_id not in selection:
            continue
        source_text = xml_path.read_text(encoding="utf-8")
        for w in windows:
            template_text = _patch_tag_text(source_text, "From", _to_nt_dt(w.from_date))
            template_text = _patch_tag_text(template_text, "To", _to_nt_dt(w.to_date))
            stem = f"{run_id}__W{w.index:02d}"
            target = templates_dir / f"{stem}.xml"
            target.write_text(template_text, encoding="utf-8")
            templates.append(WindowTemplate(
                candidate_run_id=run_id,
                window_index=w.index,
                from_date=w.from_date,
                to_date=w.to_date,
                source_template=str(xml_path),
                output_template=str(target),
            ))

    if selection is not None:
        produced = {t.candidate_run_id for t in templates}
        missing = sorted(selection - produced)
        if missing:
            notes.append(f"Requested candidates not found in named_backtest_templates: {missing}")

    # Pre-write a RunBatch payload the operator can hand-drop if needed.
    nt_output_dir = out_dir / WF_OUTPUT_DIRNAME
    command = {
        "action": "RunBatch",
        "sourceFolder": str(templates_dir.resolve()),
        "destFolder": str(nt_output_dir.resolve()),
        "instrument": doc.instrument,
        "closeTempTabs": True,
    }
    (out_dir / WF_COMMAND_FILENAME).write_text(json.dumps(command, indent=2), encoding="utf-8")

    # Persist the planned windows for status to read later.
    (out_dir / "windows.json").write_text(
        json.dumps({
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "anchor_date": anchor_date,
            "window_days": window_days,
            "count": count,
            "gap_days": gap_days,
            "skip_is_window": skip_is_window,
            "skip_range": list(skip_range) if skip_range else None,
            "windows": [w.to_dict() for w in windows],
            "candidate_run_ids": sorted(selection) if selection is not None else None,
        }, indent=2),
        encoding="utf-8",
    )

    _write_readme(out_dir / "WALKFORWARD_README.md", windows=windows, templates=templates,
                  command_path=str(out_dir / WF_COMMAND_FILENAME),
                  nt_output_dir=str(nt_output_dir))

    return WalkForwardGeneration(
        session_id=session.id,
        output_dir=str(out_dir.resolve()),
        windows=windows,
        templates=templates,
        notes=notes,
    )


def trigger_walk_forward_run(
    session: OptimizerSession,
    *,
    command_file: Path | None = None,
) -> dict[str, Any]:
    from ta_foundation.web.optimizer_runner import DEFAULT_COMMAND_FILE

    pkg_dir = session.directory / "deployment_package"
    out_dir = pkg_dir / WF_DIRNAME
    templates_dir = out_dir / WF_TEMPLATES_DIRNAME
    if not templates_dir.exists() or not any(templates_dir.glob("*.xml")):
        raise OptimizerWalkForwardError(
            f"No walk-forward templates to dispatch at {templates_dir}. "
            "Call generate_walk_forward_templates first."
        )
    doc = session.load_document()
    nt_output_dir = out_dir / WF_OUTPUT_DIRNAME
    nt_output_dir.mkdir(parents=True, exist_ok=True)

    target = Path(command_file) if command_file is not None else DEFAULT_COMMAND_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "action": "RunBatch",
        "runId": "wf_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "sourceFolder": str(templates_dir.resolve()),
        "destFolder": str(nt_output_dir.resolve()),
        "instrument": doc.instrument,
        "closeTempTabs": True,
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"command_file": str(target), "payload": payload}


def walk_forward_status(session: OptimizerSession) -> WalkForwardStatus:
    pkg_dir = session.directory / "deployment_package"
    out_dir = pkg_dir / WF_DIRNAME
    templates_dir = out_dir / WF_TEMPLATES_DIRNAME
    nt_output_dir = out_dir / WF_OUTPUT_DIRNAME

    template_count = len(list(templates_dir.glob("*.xml"))) if templates_dir.exists() else 0
    nt_output_present = (
        nt_output_dir.exists() and any(p.is_dir() for p in nt_output_dir.iterdir())
    ) if nt_output_dir.exists() else False

    notes: list[str] = []
    if not templates_dir.exists():
        notes.append("Walk-forward templates have not been generated yet.")
        return WalkForwardStatus(
            session_id=session.id,
            output_dir=str(out_dir.resolve()) if out_dir.exists() else str(out_dir),
            template_count=0, nt_output_present=False,
            per_window_results=[], candidate_stability=[], notes=notes,
        )

    per_window: list[WindowResult] = []
    by_candidate: dict[str, list[WindowResult]] = {}
    if nt_output_present:
        for cand_dir in sorted(p for p in nt_output_dir.iterdir() if p.is_dir()):
            run_id, window_index = _parse_window_dir(cand_dir.name)
            if run_id is None:
                notes.append(f"Unexpected output folder name: {cand_dir.name}")
                continue
            trades_path = cand_dir / "Trades.csv"
            stats = _summarize_trades(trades_path) if trades_path.exists() else None
            if stats is None:
                notes.append(f"{cand_dir.name}: Trades.csv missing or unparseable")
                continue
            from_date, to_date = _read_dates_from_summary(cand_dir / "Summary.csv")
            result = WindowResult(
                candidate_run_id=run_id,
                window_index=window_index,
                from_date=from_date or "",
                to_date=to_date or "",
                trades=int(stats["trade_count"]),
                net_profit=_safe_float(stats["net_profit"]),
                profit_factor=_safe_float(stats["profit_factor"]),
                max_drawdown=_safe_float(stats["max_drawdown"]),
            )
            per_window.append(result)
            by_candidate.setdefault(run_id, []).append(result)

    # Stability summaries per candidate.
    final_results = pkg_dir / "final_backtest_handoff" / "nt8_backtest_results"
    candidate_stability: list[CandidateStability] = []
    for run_id, results in sorted(by_candidate.items()):
        results.sort(key=lambda r: r.window_index)
        is_ref = _summarize_trades((final_results / run_id / "Trades.csv")) \
            if (final_results / run_id / "Trades.csv").exists() else None
        candidate_stability.append(_build_stability(run_id, is_ref, results))

    status = WalkForwardStatus(
        session_id=session.id,
        output_dir=str(out_dir.resolve()),
        template_count=template_count,
        nt_output_present=nt_output_present,
        per_window_results=per_window,
        candidate_stability=candidate_stability,
        notes=notes,
    )
    return status


def ingest_walk_forward_results(session: OptimizerSession) -> WalkForwardStatus:
    status = walk_forward_status(session)
    if not status.nt_output_present:
        return status
    pkg_dir = session.directory / "deployment_package"
    out_dir = pkg_dir / WF_DIRNAME
    (out_dir / "stability.json").write_text(
        json.dumps({
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_id": session.id,
            **status.to_dict(),
        }, indent=2),
        encoding="utf-8",
    )
    _write_stability_md(out_dir / "stability.md", session, status)
    return status


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _candidate_run_id_for_template(path: Path) -> str:
    stem = path.stem
    m = re.match(r"^(\d+)_", stem)
    if m:
        return f"F_{int(m.group(1)):03d}"
    return stem


def _parse_window_dir(name: str) -> tuple[str | None, int]:
    m = re.match(r"^(F_\d+)__W(\d+)$", name)
    if not m:
        return None, -1
    return m.group(1), int(m.group(2))


def _patch_tag_text(text: str, tag: str, value: str) -> str:
    pattern = re.compile(
        r"(<" + re.escape(tag) + r"(?:\s+[^>]*)?>)(.*?)(</" + re.escape(tag) + r">)",
        re.DOTALL,
    )
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), text, count=1)


def _to_nt_dt(date: str) -> str:
    return f"{date}T00:00:00"


def _summarize_trades(trades_path: Path) -> dict[str, Any] | None:
    if not trades_path.exists():
        return None
    try:
        import csv as _csv
        rows: list[dict[str, str]] = []
        with trades_path.open(encoding="utf-8-sig", newline="") as h:
            reader = _csv.DictReader(h)
            for row in reader:
                rows.append(row)
    except Exception:
        return None
    profits = [_money_to_float(r.get("Profit")) for r in rows]
    profits = [p for p in profits if p is not None]
    if not profits:
        return {"trade_count": 0, "net_profit": 0.0, "profit_factor": None, "max_drawdown": 0.0}
    gross_p = sum(p for p in profits if p > 0)
    gross_l = sum(p for p in profits if p < 0)
    pf: float | None
    if gross_l < 0:
        pf = gross_p / abs(gross_l)
    elif gross_p > 0:
        pf = float("inf")
    else:
        pf = None
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for p in profits:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return {
        "trade_count": len(profits),
        "net_profit": sum(profits),
        "profit_factor": pf,
        "max_drawdown": max_dd,
    }


def _read_dates_from_summary(summary_path: Path) -> tuple[str | None, str | None]:
    if not summary_path.exists():
        return None, None
    try:
        text = summary_path.read_text(encoding="utf-8-sig")
    except Exception:
        return None, None
    start, end = None, None
    for line in text.splitlines():
        parts = line.split(",")
        if not parts:
            continue
        if parts[0].strip() == "Start date" and len(parts) > 1:
            start = parts[1].strip()
        if parts[0].strip() == "End date" and len(parts) > 1:
            end = parts[1].strip()
    return start, end


def _money_to_float(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()$ ").replace(",", "")
    if not s:
        return None
    try:
        out = float(s)
    except ValueError:
        return None
    return -out if negative else out


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _build_stability(
    run_id: str,
    is_reference: dict[str, Any] | None,
    results: list[WindowResult],
) -> CandidateStability:
    nets = [r.net_profit for r in results if r.net_profit is not None]
    pfs = [r.profit_factor for r in results if r.profit_factor is not None and math.isfinite(r.profit_factor)]
    flags: list[str] = []
    coef_of_var: float | None = None
    if nets:
        mean = sum(nets) / len(nets)
        if abs(mean) > 1e-9 and len(nets) > 1:
            variance = sum((n - mean) ** 2 for n in nets) / len(nets)
            coef_of_var = (variance ** 0.5) / abs(mean)
    mean_net = (sum(nets) / len(nets)) if nets else None
    median_net = _median(nets) if nets else None
    pf_min = min(pfs) if pfs else None
    pf_med = _median(pfs) if pfs else None
    pf_max = max(pfs) if pfs else None
    n_windows = len(results)
    n_with_trades = sum(1 for r in results if r.trades > 0)
    n_pf_above_1 = sum(1 for r in results if r.profit_factor is not None and r.profit_factor > 1.0)

    # Flag building.
    if n_windows and n_with_trades == 0:
        flags.append("no windows produced trades")
    elif n_with_trades <= n_windows / 2:
        flags.append(f"only {n_with_trades}/{n_windows} windows produced trades")
    if n_windows and n_pf_above_1 <= n_windows / 2:
        flags.append(f"PF stayed > 1.0 in only {n_pf_above_1}/{n_windows} windows")
    if coef_of_var is not None and coef_of_var > 1.5:
        flags.append(f"net profit highly variable across windows (CoV {coef_of_var:.2f})")
    if is_reference is not None and is_reference.get("profit_factor"):
        is_pf = is_reference["profit_factor"]
        if pf_med is not None and is_pf > 1.5 and pf_med < 1.0:
            flags.append(f"PF median collapsed (reference {is_pf:.2f} → median {pf_med:.2f})")

    return CandidateStability(
        candidate_run_id=run_id,
        is_reference_trades=int((is_reference or {}).get("trade_count") or 0),
        is_reference_net_profit=_safe_float((is_reference or {}).get("net_profit")),
        is_reference_profit_factor=_safe_float((is_reference or {}).get("profit_factor")),
        windows_run=n_windows,
        windows_with_trades=n_with_trades,
        windows_with_pf_above_1=n_pf_above_1,
        mean_net_profit=mean_net,
        median_net_profit=median_net,
        pf_min=pf_min,
        pf_median=pf_med,
        pf_max=pf_max,
        coefficient_of_variation_net=coef_of_var,
        stability_flags=flags,
    )


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _write_readme(path: Path, *, windows, templates, command_path, nt_output_dir) -> None:
    lines = [
        "# Walk-Forward Validation",
        "",
        f"Windows: {len(windows)} ({windows[0].from_date} → {windows[-1].to_date})",
        f"Templates: {len(templates)}",
        "",
        "Each candidate's fixed-Backtest template is re-run across N rolling",
        "windows. Compare per-window results against the original final-Backtest",
        "to assess parameter stability over time.",
        "",
        "## How to run",
        "",
        f"1. Drop `{command_path}` into the AddOn's watched location, or POST",
        "   `/api/optimizer/sessions/<id>/walkforward/run` to dispatch.",
        f"2. NT writes per-window exports under `{nt_output_dir}/<run>__W<i>/`.",
        "3. POST `/api/optimizer/sessions/<id>/walkforward/ingest` to compute",
        "   the stability report.",
        "",
        "## Planned windows",
        "",
    ]
    for w in windows:
        lines.append(f"- W{w.index:02d}: {w.from_date} → {w.to_date}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stability_md(path: Path, session: OptimizerSession, status: WalkForwardStatus) -> None:
    lines = [
        "# Walk-Forward Stability Report",
        "",
        f"Session: `{session.id}`",
        f"Templates: {status.template_count}",
        f"Candidates with results: {len(status.candidate_stability)}",
        "",
    ]
    if status.candidate_stability:
        lines.extend([
            "## Per-candidate stability",
            "",
            "| Run | IS PF | IS trades | Windows | With trades | PF>1 | PF min | PF med | PF max | Mean net | CoV(net) | Flags |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ])
        for s in status.candidate_stability:
            flags = "; ".join(s.stability_flags) or "ok"
            lines.append(
                f"| {s.candidate_run_id} | {_fmt(s.is_reference_profit_factor, 2)} | {s.is_reference_trades} | "
                f"{s.windows_run} | {s.windows_with_trades} | {s.windows_with_pf_above_1} | "
                f"{_fmt(s.pf_min, 2)} | {_fmt(s.pf_median, 2)} | {_fmt(s.pf_max, 2)} | "
                f"{_fmt(s.mean_net_profit, 0)} | {_fmt(s.coefficient_of_variation_net, 2)} | {flags} |"
            )
    if status.per_window_results:
        lines.extend([
            "",
            "## Per-window results",
            "",
            "| Run | W | From | To | Trades | Net | PF | DD |",
            "|---|---:|---|---|---:|---:|---:|---:|",
        ])
        for r in sorted(status.per_window_results, key=lambda x: (x.candidate_run_id, x.window_index)):
            lines.append(
                f"| {r.candidate_run_id} | {r.window_index} | {r.from_date} | {r.to_date} | "
                f"{r.trades} | {_fmt(r.net_profit, 0)} | {_fmt(r.profit_factor, 2)} | {_fmt(r.max_drawdown, 0)} |"
            )
    if status.notes:
        lines.extend(["", "## Notes", ""])
        for n in status.notes:
            lines.append(f"- {n}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(value: Any, digits: int) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    return f"{f:.{digits}f}"
