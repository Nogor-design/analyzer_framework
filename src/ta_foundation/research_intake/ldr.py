"""Import Local Deep Research reports into a reviewable candidate backlog."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ta_foundation.research_intake.models import (
    CandidateValidation,
    IntakeBundle,
    IntakeCandidate,
    ResearchSource,
    as_posixish,
)


@dataclass(frozen=True)
class LdrImportOptions:
    report_path: Path
    source_run_id: str
    output_markdown: Path
    output_json: Optional[Path] = None
    author_json: Optional[Path] = None
    extracted_text_path: Optional[Path] = None
    predecessor_run_id: Optional[str] = None
    ledger_db_path: Optional[Path] = None


def import_ldr_report(options: LdrImportOptions) -> IntakeBundle:
    """Read an LDR report and emit reviewable Markdown/JSON artifacts.

    The importer is deliberately conservative: it uses the report only to
    select and annotate backlog candidates. It does not register hypotheses,
    write generated probes, or modify the ledger.
    """
    report_text = extract_report_text(options.report_path)
    if options.extracted_text_path:
        options.extracted_text_path.parent.mkdir(parents=True, exist_ok=True)
        options.extracted_text_path.write_text(report_text, encoding="utf-8")

    source = ResearchSource(
        tool="local_deep_research",
        run_id=options.source_run_id,
        predecessor_run_id=options.predecessor_run_id,
        report_path=as_posixish(options.report_path),
        extracted_text_path=(
            as_posixish(options.extracted_text_path)
            if options.extracted_text_path
            else None
        ),
        prompt_hash=_promptish_hash(report_text),
    )
    warnings = _quality_warnings(report_text)
    candidates = select_candidates(report_text)
    validations = validate_candidates(candidates, options.ledger_db_path)
    bundle = IntakeBundle(
        source=source,
        warnings=warnings,
        validations=validations,
        candidates=candidates,
        summary=(
            "Local Deep Research intake converted the report into reviewable "
            "candidate drafts. No ledger rows were registered."
        ),
    )
    write_markdown(bundle, options.output_markdown)
    if options.output_json:
        write_json(bundle, options.output_json)
    if options.author_json:
        write_author_proposals(bundle, options.author_json)
    return bundle


def extract_report_text(path: Path) -> str:
    """Extract text from a plain text/Markdown/PDF report."""
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    raise ValueError(f"Unsupported LDR report extension: {path.suffix}")


def _extract_pdf_text(path: Path) -> str:
    errors: list[str] = []
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = __import__(module_name)
            reader = module.PdfReader(str(path))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:  # noqa: BLE001 - optional dependency fallback
            errors.append(f"{module_name}: {type(exc).__name__}: {exc}")
    raise RuntimeError(
        "Could not extract PDF text. Install pypdf or provide an extracted .txt "
        f"report. Errors: {'; '.join(errors)}"
    )


def select_candidates(report_text: str) -> list[IntakeCandidate]:
    """Select normalized candidates from a report using stable templates."""
    text = report_text.lower()
    selected: list[IntakeCandidate] = []
    for template in _candidate_templates():
        if any(token in text for token in template["tokens"]):
            selected.append(_candidate_from_template(template))
    return selected


def validate_candidates(
    candidates: list[IntakeCandidate],
    ledger_db_path: Optional[Path] = None,
) -> list[CandidateValidation]:
    """Validate candidates against the family registry when a ledger is provided."""
    if ledger_db_path is None:
        return [
            CandidateValidation(
                title=c.title,
                family=c.family,
                valid=True,
                warnings=[
                    "Not checked against a ledger; pass --ledger-db to validate "
                    "family existence and parameter whitelists."
                ],
            )
            for c in candidates
        ]

    from ta_foundation.research_ledger import (
        get_family_spec,
        get_repository,
        validate_params,
    )

    try:
        repo = get_repository(ledger_db_path)
    except Exception as exc:  # noqa: BLE001 - intake must not block review artifacts
        return [
            CandidateValidation(
                title=c.title,
                family=c.family,
                valid=True,
                warnings=[
                    "Could not open ledger for validation "
                    f"({type(exc).__name__}: {exc}); candidate was not "
                    "checked against the family registry."
                ],
            )
            for c in candidates
        ]
    try:
        validations: list[CandidateValidation] = []
        for c in candidates:
            errors: list[str] = []
            warnings: list[str] = []
            if get_family_spec(repo, c.family) is None:
                errors.append(f"family {c.family!r} is not in the registry")
            else:
                for violation in validate_params(repo, c.family, c.params):
                    errors.append(
                        f"param {violation.param_name}: {violation.message}"
                    )
            if len(c.mechanism.strip()) < 50:
                errors.append("mechanism is shorter than 50 characters")
            if c.direction not in {None, "long", "short", "both"}:
                errors.append(f"invalid direction {c.direction!r}")
            if c.source_confidence == "low":
                warnings.append(
                    "low source confidence; require stronger local evidence "
                    "before accepting"
                )
            validations.append(
                CandidateValidation(
                    title=c.title,
                    family=c.family,
                    valid=not errors,
                    errors=errors,
                    warnings=warnings,
                )
            )
        return validations
    finally:
        repo.close()


def write_json(bundle: IntakeBundle, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(bundle.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_author_proposals(bundle: IntakeBundle, path: Path) -> None:
    """Write JSON compatible with Hypothesis Author proposal parsing.

    This is an interchange artifact only. The guarded authoring flow must still
    register proposals through `author_probe`.
    """
    valid_titles = {
        v.title for v in bundle.validations if v.valid
    } if bundle.validations else {c.title for c in bundle.candidates}
    proposals = [
        _candidate_to_author_proposal(c)
        for c in bundle.candidates
        if c.title in valid_titles
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"proposals": proposals}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _candidate_to_author_proposal(c: IntakeCandidate) -> dict:
    return {
        "family": c.family,
        "instrument": c.instrument,
        "timeframe": c.timeframe,
        "session_window": c.session_window,
        "direction": c.direction,
        "params": c.params,
        "mechanism": c.mechanism,
        "revival_reason": None,
    }


def write_markdown(bundle: IntakeBundle, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# External Research Intake - Local Deep Research",
        "",
        "This file is a review artifact. It does not register hypotheses or "
        "modify the research ledger.",
        "",
        "## Source",
        "",
        f"- Tool: `{bundle.source.tool}`",
        f"- Run ID: `{bundle.source.run_id}`",
        f"- Predecessor run ID: `{bundle.source.predecessor_run_id or ''}`",
        f"- Report path: `{bundle.source.report_path}`",
        f"- Extracted text path: `{bundle.source.extracted_text_path or ''}`",
        f"- Prompt/report hash: `{bundle.source.prompt_hash or ''}`",
        f"- Imported at: `{bundle.source.imported_at}`",
        "",
        "## Warnings",
        "",
    ]
    if bundle.warnings:
        lines.extend(f"- {w}" for w in bundle.warnings)
    else:
        lines.append("- No importer warnings.")
    lines.extend(
        [
            "",
            "## Registry Validation",
            "",
        ]
    )
    if bundle.validations:
        for validation in bundle.validations:
            status = "ready" if validation.valid else "blocked"
            lines.append(
                f"- `{validation.title}` ({validation.family}): `{status}`"
            )
            for error in validation.errors:
                lines.append(f"  - error: {error}")
            for warning in validation.warnings:
                lines.append(f"  - warning: {warning}")
    else:
        lines.append("- No validation records.")
    lines.extend(
        [
            "",
            "## Candidate Queue",
            "",
            "Each candidate is still subject to family whitelist validation, "
            "duplicate/graveyard checks, quota controls, deterministic testing, "
            "and human review.",
            "",
        ]
    )
    for index, candidate in enumerate(bundle.candidates, start=1):
        lines.extend(_candidate_markdown(index, candidate))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _candidate_markdown(index: int, c: IntakeCandidate) -> list[str]:
    return [
        f"### {index}. {c.title}",
        "",
        f"- Status: `{c.status}`",
        f"- Family: `{c.family}`",
        f"- Instrument/timeframe/session: `{c.instrument}`, `{c.timeframe}`, "
        f"`{c.session_window or ''}`",
        f"- Direction: `{c.direction or ''}`",
        f"- Source confidence: `{c.source_confidence}`",
        "",
        "Params:",
        "",
        "```json",
        json.dumps(c.params, indent=2, sort_keys=True),
        "```",
        "",
        "Mechanism:",
        "",
        c.mechanism,
        "",
        "Falsifiable claim:",
        "",
        c.falsifiable_claim,
        "",
        "Primary adverse tests:",
        "",
        *[f"- {test}" for test in c.adverse_tests],
        "",
        "Source notes:",
        "",
        *[f"- {note}" for note in c.source_notes],
        "",
    ]


def _quality_warnings(report_text: str) -> list[str]:
    text = report_text.lower()
    warnings: list[str] = []
    if re.search(r">\s*\d{2}\s*%|\bwin rate\b|\bprofitable\b", text):
        warnings.append(
            "Report contains performance language; rewrite all such language "
            "as hypotheses before ledger registration."
        )
    if any(token in text for token in ("reddit", "tradingview", "youtube")):
        warnings.append(
            "Report cites low-confidence retail/social sources; use them only "
            "for mechanism vocabulary or implementation examples."
        )
    if "large_candle_origin_retest" in text:
        warnings.append(
            "large_candle_origin_retest appeared in the report; prior local "
            "hardening found slippage fragility, so require a revival reason."
        )
    if "70%" in text and "last 20" in text:
        warnings.append(
            "Adaptive recent-win-rate gating appeared in the report; treat it "
            "as a diagnostic slice, not live trade gating."
        )
    return warnings


def _promptish_hash(text: str) -> str:
    head = text[:8000].encode("utf-8", errors="replace")
    return hashlib.sha256(head).hexdigest()


def _candidate_from_template(template: dict) -> IntakeCandidate:
    return IntakeCandidate(
        title=template["title"],
        family=template["family"],
        instrument=template["instrument"],
        timeframe=template["timeframe"],
        session_window=template["session_window"],
        direction=template["direction"],
        params=template["params"],
        mechanism=template["mechanism"],
        falsifiable_claim=template["falsifiable_claim"],
        adverse_tests=template["adverse_tests"],
        source_confidence=template["source_confidence"],
        source_notes=template["source_notes"],
        rationale=template["rationale"],
    )


def _candidate_templates() -> list[dict]:
    return [
        {
            "tokens": ["orb failure", "failure reclaim", "opening range"],
            "title": "ORB Failure Reclaim, Body-Midpoint Fill",
            "family": "orb_failure_reclaim",
            "instrument": "NQ",
            "timeframe": "5m",
            "session_window": "ny_open_0730_0830_denver",
            "direction": "both",
            "params": {
                "orb_minutes": 5,
                "sweep_min_ticks": 4,
                "reclaim_within_bars": 2,
                "fill_mode": "body_midpoint",
                "stop_ticks": 24,
                "target_ticks": 80,
            },
            "mechanism": (
                "An opening range breakout that cannot hold beyond the range "
                "traps early breakout participants. Reclaim into the range "
                "forces those late entries to exit, and a body-midpoint "
                "pullback attempts to avoid chasing the immediate reversal."
            ),
            "falsifiable_claim": (
                "This fixed ORB failure-reclaim variant should produce "
                "non-negative expectancy after realistic NQ friction and "
                "entry delay across multiple market regimes."
            ),
            "adverse_tests": [
                "Long/short side split.",
                "First 30 minutes versus full first hour.",
                "One-bar entry delay.",
                "+1 to +4 tick slippage ladder.",
                "2024-2026 holdout.",
            ],
            "source_confidence": "medium",
            "source_notes": [
                "ORB has the strongest source trail in the LDR report.",
                "Failure-reclaim mechanics are plausible but require local validation.",
            ],
            "rationale": "Strong family fit and directly supported by the registry.",
        },
        {
            "tokens": ["overnight high", "overnight low", "onh", "onl"],
            "title": "Overnight High/Low Sweep Reclaim",
            "family": "overnight_high_low_sweep_reclaim",
            "instrument": "NQ",
            "timeframe": "5m",
            "session_window": "ny_open_0730_0830_denver",
            "direction": "both",
            "params": {
                "level_type": "both",
                "sweep_min_ticks": 6,
                "reclaim_within_bars": 3,
                "stop_ticks": 28,
                "target_ticks": 90,
            },
            "mechanism": (
                "Overnight highs and lows concentrate resting liquidity before "
                "the RTH open. A sweep followed by fast reclaim suggests the "
                "breakout side did not attract continuation flow, trapping "
                "participants back inside the overnight range."
            ),
            "falsifiable_claim": (
                "Early RTH reclaim after an overnight high/low sweep should "
                "outperform an unconditional reference-level fade after cost."
            ),
            "adverse_tests": [
                "ONH and ONL split.",
                "Gap-size stratification.",
                "Exclude scheduled macro-news days.",
                "Sweep threshold grid.",
                "Compare to no-VWAP-filter baseline.",
            ],
            "source_confidence": "medium",
            "source_notes": [
                "Sweep sources are mostly retail quality.",
                "The reference level itself is deterministic and registry-backed.",
            ],
            "rationale": "Clean registry fit with a visible market reference.",
        },
        {
            "tokens": ["prior high", "prior low", "failed breakout"],
            "title": "Prior High/Low Failed Breakout",
            "family": "prior_high_low_failed_breakout",
            "instrument": "NQ",
            "timeframe": "5m",
            "session_window": "rth_open_0730_0900_denver",
            "direction": "both",
            "params": {
                "level_type": "both",
                "break_buffer_ticks": 4,
                "max_failure_bars": 3,
                "stop_ticks": 28,
                "target_ticks": 90,
            },
            "mechanism": (
                "Prior session extremes are visible breakout levels. When price "
                "clears the level but quickly closes back inside the prior range, "
                "breakout chasers are trapped and their exits may reinforce "
                "a reversal."
            ),
            "falsifiable_claim": (
                "Failed breakout entries at prior-session high/low should "
                "outperform simple breakouts at the same levels after cost."
            ),
            "adverse_tests": [
                "Prior high versus prior low.",
                "First hour versus later RTH.",
                "Stop/target asymmetry.",
                "Slippage ladder.",
                "Random reference-level fade baseline.",
            ],
            "source_confidence": "medium",
            "source_notes": [
                "Source trail supports reference-level behavior more than edge.",
            ],
            "rationale": "Strong registry fit and easy falsification baseline.",
        },
        {
            "tokens": ["initial balance", "ib reversal", "ib extension"],
            "title": "Initial Balance Reversal",
            "family": "initial_balance_reversal",
            "instrument": "NQ",
            "timeframe": "5m",
            "session_window": "rth_morning_0730_0930_denver",
            "direction": "both",
            "params": {
                "ib_minutes": 60,
                "extension_attempt_ticks": 8,
                "stop_ticks": 30,
                "target_ticks": 100,
            },
            "mechanism": (
                "A failed extension beyond the first-hour initial balance traps "
                "participants who entered late in the direction of the apparent "
                "trend day. Reversal back into balance can be driven by failed "
                "continuation exits and inventory rebalancing."
            ),
            "falsifiable_claim": (
                "Failed initial-balance extension should produce more robust "
                "post-cost behavior than an unconditional IB fade."
            ),
            "adverse_tests": [
                "30/60/90-minute IB split.",
                "NQ versus ES.",
                "Extension-attempt threshold grid.",
                "Day-of-week and volatility regimes.",
                "Delayed entry.",
            ],
            "source_confidence": "medium",
            "source_notes": [
                "IB is a standard market-profile concept, but edge claims need local proof.",
            ],
            "rationale": "Natural companion to ORB failure tests.",
        },
        {
            "tokens": ["vwap reclaim", "vwap pullback", "vwap continuation"],
            "title": "VWAP Reclaim Continuation",
            "family": "vwap_reclaim_continuation",
            "instrument": "NQ",
            "timeframe": "5m",
            "session_window": "rth_open_0730_1000_denver",
            "direction": "both",
            "params": {
                "reclaim_max_bars": 3,
                "stop_ticks": 24,
                "target_ticks": 80,
            },
            "mechanism": (
                "A failed move through VWAP followed by reclaim can trap "
                "short-horizon reversal traders. Continuation after reclaim "
                "uses VWAP as a volume-weighted reference without relying on "
                "order-flow data outside the deterministic pipeline."
            ),
            "falsifiable_claim": (
                "VWAP reclaim continuation should outperform a generic VWAP "
                "cross when tested with fixed entry, stop, target, and no "
                "adaptive tuning."
            ),
            "adverse_tests": [
                "Opening hour versus late morning.",
                "Long versus short split.",
                "Distance from VWAP at reclaim.",
                "Entry delay.",
                "Naive VWAP cross baseline.",
            ],
            "source_confidence": "low",
            "source_notes": [
                "VWAP definitions are well sourced; signal edge sources are weaker.",
            ],
            "rationale": "Registry-backed way to test the VWAP section of the report.",
        },
        {
            "tokens": ["vwap mean", "vwap fade", "vwap reject", "mean reversion"],
            "title": "VWAP Reject Fade",
            "family": "vwap_reject_fade",
            "instrument": "NQ",
            "timeframe": "5m",
            "session_window": "rth_morning_0730_1030_denver",
            "direction": "both",
            "params": {
                "min_distance_ticks": 12,
                "max_distance_ticks": 80,
                "stop_ticks": 24,
                "target_ticks": 70,
            },
            "mechanism": (
                "When price stretches away from VWAP and fails to continue, "
                "late momentum participants become the counterparty for a "
                "reversion attempt toward the volume-weighted reference."
            ),
            "falsifiable_claim": (
                "VWAP rejection fades inside a bounded distance window should "
                "outperform unbounded VWAP mean reversion after cost."
            ),
            "adverse_tests": [
                "Distance bucket monotonicity.",
                "Trend-day exclusion versus inclusion.",
                "Long/short split.",
                "NQ versus ES.",
                "ATR-normalized distance.",
            ],
            "source_confidence": "low",
            "source_notes": [
                "Mostly retail VWAP strategy sources; use as a test prompt, not evidence.",
            ],
            "rationale": "Low-cost way to falsify common VWAP fade lore.",
        },
        {
            "tokens": ["compression", "squeeze", "expansion"],
            "title": "Compression Then Expansion",
            "family": "compression_then_expansion",
            "instrument": "NQ",
            "timeframe": "5m",
            "session_window": "rth_morning_0730_1100_denver",
            "direction": "both",
            "params": {
                "compression_bars": 4,
                "compression_range_ticks_max": 40,
                "breakout_buffer_ticks": 4,
                "stop_ticks": 24,
                "target_ticks": 80,
            },
            "mechanism": (
                "A short compression window can represent coiled liquidity and "
                "reduced realized volatility. A buffered breakout attempts to "
                "capture directional expansion before the move is crowded."
            ),
            "falsifiable_claim": (
                "A fixed compression-window expansion rule should beat random "
                "breakout entries with the same time-of-day distribution after cost."
            ),
            "adverse_tests": [
                "Random-time matched baseline.",
                "Compression bars 3/4/5.",
                "Breakout buffer sensitivity.",
                "Volatility regime split.",
                "No-trade first 10 minutes overlay.",
            ],
            "source_confidence": "medium",
            "source_notes": [
                "Mechanism is generic but registry-compatible and easy to falsify.",
            ],
            "rationale": "Useful non-reference-level diversification candidate.",
        },
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ta_foundation.research_intake.import_ldr",
        description="Import a Local Deep Research report as reviewable candidates.",
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--predecessor-run-id", default=None)
    parser.add_argument("--out-md", required=True, type=Path)
    parser.add_argument("--out-json", default=None, type=Path)
    parser.add_argument(
        "--author-json",
        default=None,
        type=Path,
        help=(
            "Optional JSON artifact shaped as {'proposals': [...]} for the "
            "Hypothesis Author. This does not register hypotheses."
        ),
    )
    parser.add_argument("--extract-text-to", default=None, type=Path)
    parser.add_argument(
        "--ledger-db",
        default=None,
        type=Path,
        help=(
            "Optional research ledger DB used only to validate family IDs and "
            "parameter whitelists."
        ),
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    bundle = import_ldr_report(
        LdrImportOptions(
            report_path=args.report,
            source_run_id=args.source_run_id,
            predecessor_run_id=args.predecessor_run_id,
            output_markdown=args.out_md,
            output_json=args.out_json,
            author_json=args.author_json,
            extracted_text_path=args.extract_text_to,
            ledger_db_path=args.ledger_db,
        )
    )
    print(
        f"Imported {len(bundle.candidates)} candidates from {args.report} "
        f"to {args.out_md}"
    )
    if args.out_json:
        print(f"Wrote JSON artifact to {args.out_json}")
    if args.author_json:
        print(f"Wrote author-proposals JSON artifact to {args.author_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
