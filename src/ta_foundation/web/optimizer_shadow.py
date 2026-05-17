from __future__ import annotations

"""Optimizer shadow execution: rolling-window re-validation of final
candidates against fresh data.

Concept
-------

A "shadow run" takes a candidate's named final-Backtest template,
patches its ``<From>`` / ``<To>`` dates to a more recent window
(typically days or weeks after the original OOS validation window),
and re-runs the strategy against that fresh data. The result is a
new Trades.csv / Summary.csv for the shadow window, which we compare
against the original final-Backtest stats to detect divergence.

This is not full live paper-trading (NT Sim101 integration is a Phase
2 lift). It is the strictly-more-conservative pure-historical version:
"if the operator had deployed F_001 N days ago, what would have
happened?"

Output layout::

    <session>/deployment_package/shadow/
        templates/<candidate>.xml          # shadow Backtest XML per candidate
        nt_output/<candidate>/             # NinjaTrader exports per shadow run
        nt8_run_batch_command.json         # IPC payload ready for AddOn
        SHADOW_README.md                   # operator instructions
        comparison.json                    # machine-readable live-vs-backtest
        comparison.md                      # markdown report

Flow
----

1. ``generate_shadow_templates`` — copy each named Backtest XML into the
   shadow folder, patch From/To dates, optionally restrict to a
   selected subset of candidates.
2. (Operator dispatches via the existing RunBatch mechanism, or via
   ``trigger_shadow_run`` which writes the IPC command file.)
3. ``ingest_shadow_results`` — read the returned Trades.csv per
   candidate, compute live-vs-backtest comparison stats, write
   comparison.json + comparison.md.
"""

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ta_foundation.web.optimizer_session import OptimizerSession


SHADOW_DIRNAME = "shadow"
SHADOW_TEMPLATES_DIRNAME = "templates"
SHADOW_OUTPUT_DIRNAME = "nt_output"
SHADOW_COMMAND_FILENAME = "nt8_run_batch_command.json"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OptimizerShadowError(Exception):
    pass


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShadowTemplate:
    candidate_run_id: str
    source_template: str
    shadow_template: str
    from_date: str
    to_date: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowComparison:
    candidate_run_id: str
    backtest_trades: int
    shadow_trades: int
    backtest_net_profit: float | None
    shadow_net_profit: float | None
    backtest_profit_factor: float | None
    shadow_profit_factor: float | None
    backtest_max_drawdown: float | None
    shadow_max_drawdown: float | None
    backtest_trades_per_day: float | None
    shadow_trades_per_day: float | None
    divergence_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowGenerationResult:
    session_id: str
    shadow_dir: str
    from_date: str
    to_date: str
    templates: list[ShadowTemplate]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "shadow_dir": self.shadow_dir,
            "from_date": self.from_date,
            "to_date": self.to_date,
            "templates": [t.to_dict() for t in self.templates],
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ShadowStatus:
    session_id: str
    shadow_dir: str
    template_count: int
    nt_output_present: bool
    candidates_with_results: list[str]
    comparisons: list[ShadowComparison]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "shadow_dir": self.shadow_dir,
            "template_count": self.template_count,
            "nt_output_present": self.nt_output_present,
            "candidates_with_results": list(self.candidates_with_results),
            "comparisons": [c.to_dict() for c in self.comparisons],
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def generate_shadow_templates(
    session: OptimizerSession,
    *,
    from_date: str,
    to_date: str,
    candidate_run_ids: Iterable[str] | None = None,
) -> ShadowGenerationResult:
    if not _looks_like_iso_date(from_date) or not _looks_like_iso_date(to_date):
        raise OptimizerShadowError(
            f"shadow dates must be YYYY-MM-DD; got from={from_date!r} to={to_date!r}"
        )
    if from_date >= to_date:
        raise OptimizerShadowError(
            f"shadow from_date {from_date} must precede to_date {to_date}"
        )

    pkg_dir = session.directory / "deployment_package"
    named_dir = pkg_dir / "final_backtest_handoff" / "named_backtest_templates"
    if not named_dir.exists():
        raise OptimizerShadowError(
            f"No named backtest templates at {named_dir}. Run the final "
            "fixed Backtest phase first."
        )

    shadow_dir = pkg_dir / SHADOW_DIRNAME
    shadow_templates_dir = shadow_dir / SHADOW_TEMPLATES_DIRNAME
    # Wipe only the templates folder; preserve any returned nt_output/.
    if shadow_templates_dir.exists():
        shutil.rmtree(shadow_templates_dir)
    shadow_templates_dir.mkdir(parents=True, exist_ok=True)

    selection: set[str] | None = None
    if candidate_run_ids is not None:
        selection = {str(s).strip() for s in candidate_run_ids if str(s).strip()}
        if not selection:
            raise OptimizerShadowError("candidate_run_ids was supplied but empty")

    templates: list[ShadowTemplate] = []
    notes: list[str] = []

    for xml_path in sorted(named_dir.rglob("*.xml")):
        run_id = _candidate_run_id_for_template(xml_path)
        if selection is not None and run_id not in selection:
            continue
        shadow_xml = shadow_templates_dir / f"{run_id}.xml"
        text = xml_path.read_text(encoding="utf-8")
        text = _patch_tag_text(text, "From", _to_nt_dt(from_date))
        text = _patch_tag_text(text, "To", _to_nt_dt(to_date))
        shadow_xml.write_text(text, encoding="utf-8")
        templates.append(ShadowTemplate(
            candidate_run_id=run_id,
            source_template=str(xml_path),
            shadow_template=str(shadow_xml),
            from_date=from_date,
            to_date=to_date,
        ))

    if selection is not None and selection - {t.candidate_run_id for t in templates}:
        missing = sorted(selection - {t.candidate_run_id for t in templates})
        notes.append(f"Requested candidates not found in named_backtest_templates: {missing}")

    # Write a RunBatch command file the operator can use directly.
    nt_output_dir = shadow_dir / SHADOW_OUTPUT_DIRNAME
    command = {
        "action": "RunBatch",
        "sourceFolder": str(shadow_templates_dir),
        "destFolder": str(nt_output_dir),
    }
    (shadow_dir / SHADOW_COMMAND_FILENAME).write_text(
        json.dumps(command, indent=2), encoding="utf-8"
    )

    _write_readme(
        shadow_dir / "SHADOW_README.md",
        from_date=from_date,
        to_date=to_date,
        templates=templates,
        command_path=str(shadow_dir / SHADOW_COMMAND_FILENAME),
        nt_output_dir=str(nt_output_dir),
    )

    return ShadowGenerationResult(
        session_id=session.id,
        shadow_dir=str(shadow_dir.resolve()),
        from_date=from_date,
        to_date=to_date,
        templates=templates,
        notes=notes,
    )


def trigger_shadow_run(
    session: OptimizerSession,
    *,
    command_file: Path | None = None,
) -> dict[str, Any]:
    """Write the IPC RunBatch payload to the NT AddOn's watched location
    so the AddOn picks the shadow templates up.

    Returns the payload that was written and the destination path.
    """
    from ta_foundation.web.optimizer_runner import DEFAULT_COMMAND_FILE

    pkg_dir = session.directory / "deployment_package"
    shadow_dir = pkg_dir / SHADOW_DIRNAME
    templates_dir = shadow_dir / SHADOW_TEMPLATES_DIRNAME
    if not templates_dir.exists() or not any(templates_dir.glob("*.xml")):
        raise OptimizerShadowError(
            f"No shadow templates to dispatch at {templates_dir}. "
            "Call generate_shadow_templates first."
        )
    nt_output_dir = shadow_dir / SHADOW_OUTPUT_DIRNAME
    nt_output_dir.mkdir(parents=True, exist_ok=True)

    target = Path(command_file) if command_file is not None else DEFAULT_COMMAND_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = session.load_document()
    payload = {
        "action": "RunBatch",
        "runId": "shadow_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        # Absolute paths only — the NT AddOn does not understand relative
        # paths and will silently skip the run.
        "sourceFolder": str(templates_dir.resolve()),
        "destFolder": str(nt_output_dir.resolve()),
        # The contract MUST be on the IPC payload. Without it the AddOn
        # falls back to the currently-selected Strategy Analyzer tab's
        # instrument and silently clobbers the template's own
        # <InstrumentOrInstrumentList> override.
        "instrument": doc.instrument,
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"command_file": str(target), "payload": payload}


def shadow_status(session: OptimizerSession) -> ShadowStatus:
    pkg_dir = session.directory / "deployment_package"
    shadow_dir = pkg_dir / SHADOW_DIRNAME
    templates_dir = shadow_dir / SHADOW_TEMPLATES_DIRNAME
    output_dir = shadow_dir / SHADOW_OUTPUT_DIRNAME

    template_count = len(list(templates_dir.glob("*.xml"))) if templates_dir.exists() else 0
    nt_output_present = output_dir.exists() and any(output_dir.iterdir()) if output_dir.exists() else False

    notes: list[str] = []
    if not templates_dir.exists():
        notes.append("Shadow templates have not been generated yet.")
        return ShadowStatus(
            session_id=session.id,
            shadow_dir=str(shadow_dir.resolve()) if shadow_dir.exists() else str(shadow_dir),
            template_count=0,
            nt_output_present=False,
            candidates_with_results=[],
            comparisons=[],
            notes=notes,
        )

    candidates_with_results: list[str] = []
    comparisons: list[ShadowComparison] = []
    if nt_output_present:
        candidates_with_results, comparisons, ingest_notes = _ingest_and_compare(session)
        notes.extend(ingest_notes)

    return ShadowStatus(
        session_id=session.id,
        shadow_dir=str(shadow_dir.resolve()),
        template_count=template_count,
        nt_output_present=nt_output_present,
        candidates_with_results=candidates_with_results,
        comparisons=comparisons,
        notes=notes,
    )


def ingest_shadow_results(session: OptimizerSession) -> ShadowStatus:
    """Read returned NT shadow exports, compute comparison stats, write
    comparison.json + comparison.md, and return the status."""
    status = shadow_status(session)
    if not status.nt_output_present:
        return status
    pkg_dir = session.directory / "deployment_package"
    shadow_dir = pkg_dir / SHADOW_DIRNAME
    (shadow_dir / "comparison.json").write_text(
        json.dumps({
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_id": session.id,
            **status.to_dict(),
        }, indent=2),
        encoding="utf-8",
    )
    _write_comparison_md(shadow_dir / "comparison.md", session, status)
    return status


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_BACKTEST_NAME_PATTERN = re.compile(r"^\d+_[A-Za-z]+_(?P<bot>.+)$")


def _candidate_run_id_for_template(path: Path) -> str:
    """Derive the candidate run_id (e.g. ``F_001``) from a named backtest
    template path. The named templates follow the convention
    ``<rank>_<mode>_<bot_name>.xml``. The run_id we want is the rank-mode
    prefix that maps back to the review's ``F_NNN`` ids, so we use the
    leading ``NN`` digit pair and prefix with ``F_``.
    """
    stem = path.stem
    m = re.match(r"^(\d+)_", stem)
    if m:
        return f"F_{int(m.group(1)):03d}"
    return stem


def _patch_tag_text(text: str, tag: str, value: str) -> str:
    pattern = re.compile(
        r"(<" + re.escape(tag) + r"(?:\s+[^>]*)?>)(.*?)(</" + re.escape(tag) + r">)",
        re.DOTALL,
    )
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), text, count=1)


def _to_nt_dt(date: str) -> str:
    return f"{date}T00:00:00"


def _looks_like_iso_date(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))


def _write_readme(
    path: Path,
    *,
    from_date: str,
    to_date: str,
    templates: list[ShadowTemplate],
    command_path: str,
    nt_output_dir: str,
) -> None:
    lines = [
        "# Shadow Execution",
        "",
        f"Shadow window: **{from_date} → {to_date}**",
        f"Templates: {len(templates)}",
        "",
        "These fixed Backtest templates were generated from the named final-Backtest",
        "candidates, with the date range shifted to a recent rolling window. Running",
        "them against fresh data produces a live-vs-backtest comparison without touching",
        "a real or paper account.",
        "",
        "## How to run",
        "",
        f"1. Drop `{command_path}` into the NT AddOn's watched location (default `C:\\temp\\nt8_command.json`),",
        f"   or POST `/api/optimizer/sessions/<id>/shadow/run` to dispatch automatically.",
        f"2. The AddOn writes each candidate's exports under `{nt_output_dir}/<candidate>/`.",
        "3. POST `/api/optimizer/sessions/<id>/shadow/ingest` (or click **Ingest shadow results**)",
        "   to compute the live-vs-backtest comparison report.",
        "",
        "## Templates",
        "",
    ]
    for t in templates:
        lines.append(f"- `{t.candidate_run_id}` ← {t.source_template}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ingest_and_compare(
    session: OptimizerSession,
) -> tuple[list[str], list[ShadowComparison], list[str]]:
    pkg_dir = session.directory / "deployment_package"
    final_results = pkg_dir / "final_backtest_handoff" / "nt8_backtest_results"
    shadow_results = pkg_dir / SHADOW_DIRNAME / SHADOW_OUTPUT_DIRNAME

    notes: list[str] = []
    comparisons: list[ShadowComparison] = []
    candidates_with_results: list[str] = []

    if not shadow_results.exists():
        notes.append(f"No shadow results folder at {shadow_results}")
        return [], [], notes

    for cand_dir in sorted(p for p in shadow_results.iterdir() if p.is_dir()):
        run_id = cand_dir.name
        shadow_trades = _summarize_trades(cand_dir / "Trades.csv")
        if shadow_trades is None:
            notes.append(f"{run_id}: Trades.csv missing or unparseable — skipped.")
            continue
        backtest_dir = final_results / run_id
        backtest_trades = _summarize_trades(backtest_dir / "Trades.csv") if backtest_dir.exists() else None
        candidates_with_results.append(run_id)
        comparisons.append(_build_comparison(run_id, backtest_trades, shadow_trades))

    return candidates_with_results, comparisons, notes


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
    profits: list[float] = []
    days: set[str] = set()
    for r in rows:
        p = _money_to_float(r.get("Profit"))
        if p is None:
            continue
        profits.append(p)
        ts = (r.get("Entry time") or "").strip()
        if ts:
            day = ts.split(" ")[0]
            days.add(day)
    if not profits:
        return {"trade_count": 0, "net_profit": 0.0, "profit_factor": None,
                "max_drawdown": 0.0, "day_count": 0}
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
        "day_count": len(days),
    }


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


def _build_comparison(
    run_id: str,
    backtest: dict[str, Any] | None,
    shadow: dict[str, Any],
) -> ShadowComparison:
    def _per_day(stats: dict[str, Any] | None) -> float | None:
        if not stats or not stats.get("day_count"):
            return None
        return stats["trade_count"] / stats["day_count"]

    flags: list[str] = []
    if backtest is None:
        flags.append("no backtest baseline available")
    else:
        # Net profit per trade comparison
        bt_pft = _safe_div(backtest.get("net_profit"), backtest.get("trade_count"))
        sh_pft = _safe_div(shadow.get("net_profit"), shadow.get("trade_count"))
        if bt_pft is not None and sh_pft is not None:
            if sh_pft < 0 and bt_pft > 0:
                flags.append("net_profit_per_trade flipped sign (backtest positive, shadow negative)")
            elif bt_pft != 0 and abs(sh_pft - bt_pft) / max(1e-9, abs(bt_pft)) > 0.5:
                flags.append("net_profit_per_trade diverged > 50%")

        # Trades-per-day comparison
        bt_tpd = _per_day(backtest)
        sh_tpd = _per_day(shadow)
        if bt_tpd is not None and sh_tpd is not None and bt_tpd > 0:
            ratio = sh_tpd / bt_tpd
            if ratio < 0.5 or ratio > 2.0:
                flags.append(f"trades_per_day diverged (shadow/backtest ratio {ratio:.2f})")

        # PF drop
        if backtest.get("profit_factor") and shadow.get("profit_factor") is not None:
            bt_pf = backtest["profit_factor"]
            sh_pf = shadow["profit_factor"]
            if bt_pf > 1.5 and sh_pf < 1.0:
                flags.append(f"profit_factor collapsed (backtest {bt_pf:.2f} → shadow {sh_pf:.2f})")

        if shadow.get("trade_count", 0) == 0:
            flags.append("shadow window produced zero trades")

    return ShadowComparison(
        candidate_run_id=run_id,
        backtest_trades=int((backtest or {}).get("trade_count") or 0),
        shadow_trades=int(shadow.get("trade_count") or 0),
        backtest_net_profit=_safe_float((backtest or {}).get("net_profit")),
        shadow_net_profit=_safe_float(shadow.get("net_profit")),
        backtest_profit_factor=_safe_float((backtest or {}).get("profit_factor")),
        shadow_profit_factor=_safe_float(shadow.get("profit_factor")),
        backtest_max_drawdown=_safe_float((backtest or {}).get("max_drawdown")),
        shadow_max_drawdown=_safe_float(shadow.get("max_drawdown")),
        backtest_trades_per_day=_per_day(backtest),
        shadow_trades_per_day=_per_day(shadow),
        divergence_flags=flags,
    )


def _safe_div(a: Any, b: Any) -> float | None:
    try:
        af = float(a)
        bf = float(b)
        if bf == 0:
            return None
        return af / bf
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    import math as _m
    if _m.isnan(f) or _m.isinf(f):
        return None
    return f


def _write_comparison_md(path: Path, session: OptimizerSession, status: ShadowStatus) -> None:
    lines = [
        "# Shadow Comparison",
        "",
        f"Session: `{session.id}`",
        f"Templates: {status.template_count}",
        f"Candidates with shadow results: {len(status.candidates_with_results)}",
        "",
    ]
    if not status.comparisons:
        lines.append("_No shadow results to compare yet._")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    lines.extend([
        "| Run | BT trades | Shadow trades | BT net | Shadow net | BT PF | Shadow PF | BT DD | Shadow DD | BT/day | Shadow/day | Flags |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for c in status.comparisons:
        flags = "; ".join(c.divergence_flags) or "ok"
        lines.append(
            f"| {c.candidate_run_id} | {c.backtest_trades} | {c.shadow_trades} | "
            f"{_fmt(c.backtest_net_profit, 0)} | {_fmt(c.shadow_net_profit, 0)} | "
            f"{_fmt(c.backtest_profit_factor, 2)} | {_fmt(c.shadow_profit_factor, 2)} | "
            f"{_fmt(c.backtest_max_drawdown, 0)} | {_fmt(c.shadow_max_drawdown, 0)} | "
            f"{_fmt(c.backtest_trades_per_day, 2)} | {_fmt(c.shadow_trades_per_day, 2)} | {flags} |"
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
