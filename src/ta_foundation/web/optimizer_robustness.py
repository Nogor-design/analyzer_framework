from __future__ import annotations

"""Drive optional robustness checks for an optimizer session and write
the results into the deployment package.

Today only the trade-sequence bootstrap is implemented end-to-end. The
walk-forward and parameter-neighborhood stubs raise NotImplementedError
when invoked — they are deferred because they require NT roundtrips.

Output:
    <session>/deployment_package/robustness/
        robustness.json          # machine-readable summary
        robustness.md            # operator-facing markdown report
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.optimization.robustness import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_SEED,
    CandidateRobustness,
    bootstrap_trades_csv,
)
from ta_foundation.web.optimizer_session import OptimizerSession


ROBUSTNESS_DIRNAME = "robustness"


@dataclass(frozen=True)
class RobustnessReport:
    session_id: str
    output_dir: str
    checks_requested: list[str]
    bootstrap_results: list[dict[str, Any]]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RobustnessError(Exception):
    pass


def run_robustness_for_session(
    session: OptimizerSession,
    *,
    bootstrap: bool = True,
    walk_forward: bool = False,
    parameter_neighborhood: bool = False,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> RobustnessReport:
    pkg_dir = session.directory / "deployment_package"
    nt_results = pkg_dir / "final_backtest_handoff" / "nt8_backtest_results"
    if not nt_results.exists():
        raise RobustnessError(
            f"No final backtest results found at {nt_results}. "
            "Run final fixed Backtests and rebuild the deployment package first."
        )

    out_dir = pkg_dir / ROBUSTNESS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    checks_requested: list[str] = []
    notes: list[str] = []
    bootstrap_results: list[dict[str, Any]] = []

    if bootstrap:
        checks_requested.append("bootstrap")
        candidate_dirs = sorted(p for p in nt_results.iterdir() if p.is_dir())
        if not candidate_dirs:
            notes.append(f"No per-candidate result folders under {nt_results}.")
        for cand_dir in candidate_dirs:
            trades_csv = cand_dir / "Trades.csv"
            if not trades_csv.exists():
                notes.append(f"{cand_dir.name}: Trades.csv missing — skipped.")
                continue
            result = bootstrap_trades_csv(
                trades_csv,
                run_id=cand_dir.name,
                samples=bootstrap_samples,
                seed=seed,
            )
            bootstrap_results.append(result.to_dict())

    if walk_forward:
        checks_requested.append("walk_forward")
        notes.append("walk_forward requested but not yet implemented (requires NT roundtrip).")

    if parameter_neighborhood:
        checks_requested.append("parameter_neighborhood")
        notes.append("parameter_neighborhood requested but not yet implemented (requires NT roundtrip).")

    report = RobustnessReport(
        session_id=session.id,
        output_dir=str(out_dir.resolve()),
        checks_requested=checks_requested,
        bootstrap_results=bootstrap_results,
        notes=notes,
    )
    _write_json_report(out_dir / "robustness.json", session, report, bootstrap_samples, seed)
    _write_markdown_report(out_dir / "robustness.md", session, report)
    return report


def _write_json_report(
    path: Path,
    session: OptimizerSession,
    report: RobustnessReport,
    bootstrap_samples: int,
    seed: int,
) -> None:
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": session.id,
        "bootstrap_samples": bootstrap_samples,
        "seed": seed,
        **report.to_dict(),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_markdown_report(path: Path, session: OptimizerSession, report: RobustnessReport) -> None:
    lines: list[str] = [
        "# Robustness Report",
        "",
        f"Session: `{session.id}`",
        f"Checks requested: {', '.join(report.checks_requested) or '(none)'}",
        "",
    ]
    if report.notes:
        lines.append("## Notes")
        lines.append("")
        for n in report.notes:
            lines.append(f"- {n}")
        lines.append("")

    if report.bootstrap_results:
        lines.extend([
            "## Bootstrap trade-sequence resampling",
            "",
            "For each candidate, the actual trades from `Trades.csv` were resampled with replacement and the distribution of profit factor, net profit, and max drawdown computed. The `p_at_or_above_observed` (or `p_at_or_below_observed` for drawdown) is the fraction of bootstrap samples whose statistic met or exceeded the observed value — a 0.5 means the observed result is at the median of the shuffle distribution; a very low or very high value means the trade ordering matters less than the trade set.",
            "",
            "| Candidate | Trades | PF observed | PF p05 | PF p95 | PF p>=obs | Net observed | Net p05 | Net p95 | DD observed | DD p05 | DD p95 | DD p<=obs |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for r in report.bootstrap_results:
            pf = r["profit_factor"]
            np_ = r["net_profit"]
            dd = r["max_drawdown"]
            lines.append(
                f"| {r['run_id']} | {r['trade_count']} | "
                f"{_fmt(pf['observed'])} | {_fmt(pf['bootstrap_p05'])} | {_fmt(pf['bootstrap_p95'])} | {_fmt(pf['p_at_or_above_observed'])} | "
                f"{_fmt(np_['observed'])} | {_fmt(np_['bootstrap_p05'])} | {_fmt(np_['bootstrap_p95'])} | "
                f"{_fmt(dd['observed'])} | {_fmt(dd['bootstrap_p05'])} | {_fmt(dd['bootstrap_p95'])} | {_fmt(dd['p_at_or_above_observed'])} |"
            )
        lines.extend([
            "",
            "How to read this:",
            "",
            "- **PF observed** is the candidate's actual profit factor. **p05** and **p95** are the 5th and 95th percentiles of the bootstrapped distribution: the observed value should land between them if the result is typical for this trade set.",
            "- **PF p>=obs** is the fraction of bootstrap shuffles whose PF was at least as good as observed. Values near 0.5 mean the observed PF is unsurprising given the trade set; values near 0.0 or 1.0 suggest the observed result depends heavily on the specific trade ordering.",
            "- A small trade count (<10) makes the distribution wide; treat the result as directional, not statistical.",
            "- A negative observed PF combined with a wide bootstrap range means the candidate is likely a loser that happened to have a profitable ordering by luck.",
        ])

    if not report.bootstrap_results and "bootstrap" in report.checks_requested:
        lines.append("_No bootstrap results — no candidate Trades.csv files were found._")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    if abs(f) >= 10:
        return f"{f:.1f}"
    return f"{f:.3f}"
