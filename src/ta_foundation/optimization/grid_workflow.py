from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from ta_foundation.optimization.handoff import write_handoff_package
from ta_foundation.optimization.template_generator import (
    ParameterRange,
    _patch_parameter_text,
    _patch_strategy_value_text,
    generate_candidate_refinement_template,
    generate_daily_risk_template,
)
from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser


class MissingSeedParamError(ValueError):
    """A requested parameter has no matching element in the seed template."""


@dataclass(frozen=True)
class OptimizationGridConfig:
    max_drawdown: float = 2500.0
    min_trades: int = 10
    min_profit_factor: float = 1.5
    count: int = 8


@dataclass(frozen=True)
class OptimizationGridCandidate:
    source_file: str
    row_number: int
    status: str
    score: float
    mode: str
    session_bucket: str
    total_net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None
    trades: int | None
    start_hour: int | None
    duration_hours: int | None
    average_fast: int | None
    average_slow: int | None
    max_stop: int | None
    max_tp_ratio: float | None
    profit_stop: int | None
    loss_stop: int | None
    max_trades: int | None
    long_enabled: str
    short_enabled: str
    reverse: str
    bot_name: str
    reasons: str

    @property
    def run_id(self) -> str:
        stem = Path(self.source_file).stem.replace("_Optimization", "")
        return f"{stem}_row{self.row_number:04d}"


@dataclass(frozen=True)
class OptimizationPhaseLineageRow:
    source_file: str
    source_row: int
    generated_template: str
    target_phase: str
    mode: str
    score: float
    total_net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None
    trades: int | None
    start_hour: int | None
    duration_hours: int | None
    average_fast: int | None
    average_slow: int | None
    max_stop: int | None
    max_tp_ratio: float | None
    profit_stop: int | None
    loss_stop: int | None
    max_trades: int | None
    long_enabled: str
    short_enabled: str
    reverse: str
    reason: str


_PARAM_ALIASES = {
    "Use_Time_Filter": "UseTimeFilter",
    "Start_Time_(HH)": "StartTimeH",
    "Start_Time_(mm)": "StartTimeM",
    "Duration_Time_(HH)": "DurationTimeH",
    "Duration_Time_(mm)": "DurationTimeM",
    "Use_MaxStop": "UseMaxStop",
    "Use_MaxTP": "UseMaxTP",
    "Use_Kill": "UseKill",
    "Kill_Profit_Stop": "KillProfitStop",
    "Kill_Loss_Stop": "KillLossStop",
    "Show_Current_PNL": "Show_Current_PNL",
    "Show_Stats_Box": "Show_Stats_Box",
    "Image_Opacity": "IOpacity",
    "Corner_Image": "FileName",
    "Background_Image": "FileName2",
    "Bot_Name": "BotName",
}


def load_optimization_grid(input_path: str | Path) -> pd.DataFrame:
    source = Path(input_path)
    files = [source] if source.is_file() else sorted(source.rglob("*.csv"))
    frames: list[pd.DataFrame] = []
    parser = NinjaTraderOptimizationCsvParser()
    for path in files:
        try:
            header = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        except IndexError:
            continue
        if not _looks_like_optimization_grid(path, header):
            continue
        artifact = parser.parse(path, path.stem.replace("_Optimization", ""))
        if artifact.df is not None and not artifact.df.empty:
            frames.append(_normalize_parameter_columns(artifact.df.copy()))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _looks_like_optimization_grid(path: Path, header: str) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    return "Parameters" in header and "Performance" in header


def evaluate_optimization_grid(
    df: pd.DataFrame,
    config: OptimizationGridConfig = OptimizationGridConfig(),
) -> list[OptimizationGridCandidate]:
    candidates = [_evaluate_row(index, row, config) for index, row in df.iterrows()]
    return sorted(candidates, key=lambda c: (c.status != "pass", -c.score, c.source_file, c.row_number))


def select_phase_candidates(
    candidates: Sequence[OptimizationGridCandidate],
    count: int,
    include_rows: Sequence[int] | None = None,
) -> list[OptimizationGridCandidate]:
    passing = [candidate for candidate in candidates if candidate.status == "pass"]
    if include_rows:
        wanted = set(include_rows)
        return [candidate for candidate in passing if candidate.row_number in wanted][:count]

    selected: list[OptimizationGridCandidate] = []
    for candidate in passing:
        if len(selected) >= count:
            return selected
        key = (candidate.mode, candidate.session_bucket, _slow_ma_family(candidate.average_slow))
        if not any((c.mode, c.session_bucket, _slow_ma_family(c.average_slow)) == key for c in selected):
            selected.append(candidate)

    for candidate in passing:
        if len(selected) >= count:
            return selected
        if candidate in selected:
            continue
        key = (
            candidate.mode,
            candidate.session_bucket,
            _slow_ma_family(candidate.average_slow),
            _risk_shape(candidate.max_stop, candidate.max_tp_ratio),
            _direction(candidate.long_enabled, candidate.short_enabled),
        )
        if not any(
            (
                c.mode,
                c.session_bucket,
                _slow_ma_family(c.average_slow),
                _risk_shape(c.max_stop, c.max_tp_ratio),
                _direction(c.long_enabled, c.short_enabled),
            )
            == key
            for c in selected
        ):
            selected.append(candidate)

    for candidate in passing:
        if len(selected) >= count:
            break
        if candidate not in selected:
            selected.append(candidate)
    return selected


def create_next_phase_from_optimization_csv(
    seed_template: str | Path,
    optimization_csv_dir: str | Path,
    output_dir: str | Path,
    *,
    target_phase: str,
    config: OptimizationGridConfig = OptimizationGridConfig(),
    average_fast_min: int = 2,
    average_fast_max: int = 10,
    include_rows: Sequence[int] | None = None,
) -> list[Path]:
    if target_phase not in {"phase2", "phase3"}:
        raise ValueError("target_phase must be 'phase2' or 'phase3'")

    df = load_optimization_grid(optimization_csv_dir)
    candidates = evaluate_optimization_grid(df, config)
    selected = select_phase_candidates(candidates, config.count, include_rows)

    destination = Path(output_dir)
    generated_dir = destination / f"generated_{target_phase}_templates"
    _clear_generated_dir(generated_dir)
    generated: list[Path] = []
    lineage: list[OptimizationPhaseLineageRow] = []

    for candidate in selected:
        if not _has_core_settings(candidate):
            continue
        mode = candidate.mode
        target = generated_dir / mode / f"{target_phase.capitalize()}_{mode.capitalize()}_{_safe_name(candidate.run_id)}.xml"
        if target_phase == "phase3":
            if candidate.average_fast is None:
                continue
            generated_path = generate_daily_risk_template(
                seed_template,
                target,
                start_hour=candidate.start_hour or 0,
                duration_hours=candidate.duration_hours or 2,
                reverse=_as_bool(candidate.reverse),
                average_fast=candidate.average_fast,
                average_slow=candidate.average_slow or 200,
                max_stop=candidate.max_stop or 100,
                max_tp_ratio=candidate.max_tp_ratio or 1.0,
                long_enabled=_as_bool(candidate.long_enabled),
                short_enabled=_as_bool(candidate.short_enabled),
            )
        else:
            generated_path = generate_candidate_refinement_template(
                seed_template,
                target,
                start_hour=candidate.start_hour or 0,
                duration_hours=candidate.duration_hours or 2,
                reverse=_as_bool(candidate.reverse),
                average_slow=candidate.average_slow or 200,
                max_stop=candidate.max_stop or 100,
                max_tp_ratio=candidate.max_tp_ratio or 1.0,
                average_fast_min=average_fast_min,
                average_fast_max=average_fast_max,
            )
        generated.append(generated_path)
        lineage.append(_lineage_row(candidate, generated_path, target_phase))

    handoff_dir = destination / "team_handoff"
    write_handoff_package(generated_dir, handoff_dir, recursive=True)
    _write_lineage(destination, lineage)
    _write_lineage(handoff_dir, lineage)
    _write_candidates(destination, candidates, config)
    return generated


def create_final_backtest_templates_from_phase3_csv(
    seed_backtest_template: str | Path,
    optimization_csv_dir: str | Path,
    output_dir: str | Path,
    *,
    config: OptimizationGridConfig = OptimizationGridConfig(),
    include_rows: Sequence[int] | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[Path]:
    df = load_optimization_grid(optimization_csv_dir)
    candidates = evaluate_optimization_grid(df, config)
    selected = select_phase_candidates(candidates, config.count, include_rows)

    destination = Path(output_dir)
    generated_dir = destination / "named_backtest_templates"
    _clear_generated_dir(generated_dir)
    generated: list[Path] = []
    lineage: list[OptimizationPhaseLineageRow] = []
    for rank, candidate in enumerate(selected, start=1):
        values = _candidate_strategy_values(candidate)
        mode = candidate.mode
        target = generated_dir / mode / f"{rank:02d}_{mode.capitalize()}_{_safe_name(candidate.bot_name or candidate.run_id)}.xml"
        generated_path = generate_fixed_backtest_template(
            seed_backtest_template,
            target,
            values,
            from_date=from_date,
            to_date=to_date,
        )
        generated.append(generated_path)
        lineage.append(_lineage_row(candidate, generated_path, "final_backtest"))

    _write_lineage(destination, lineage)
    _write_candidates(destination, candidates, config)
    _write_final_readme(
        destination / "README_FINAL_BACKTEST_TEMPLATES.md",
        generated,
        lineage,
        from_date=from_date,
        to_date=to_date,
    )
    _write_final_batch_command(destination, generated_dir)
    return generated


def generate_fixed_backtest_template(
    seed_backtest_template: str | Path,
    output_path: str | Path,
    strategy_values: Mapping[str, Any],
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    strict_params: bool = False,
) -> Path:
    """Stamp a fixed Backtest-mode template from a seed, pinning each value.

    With ``strict_params=True`` the function raises :class:`MissingSeedParamError`
    if any requested parameter has no matching element in the seed. Without this
    guard a name the seed doesn't define is silently dropped and the template
    runs with the seed's default for that parameter — a wrong-but-runnable
    template. Promotion uses strict mode so a non-Pantheon (or mis-mapped) row
    can never quietly backtest the wrong point in parameter space.
    """
    text = Path(seed_backtest_template).read_text(encoding="utf-8")
    text = _remove_optimizer_sections(text)
    text = _patch_strategy_value_text(text, "Category", "Backtest")
    if from_date:
        text = _patch_strategy_value_text(text, "From", _format_nt_date(from_date))
    if to_date:
        text = _patch_strategy_value_text(text, "To", _format_nt_date(to_date))
    missing: list[str] = []
    for raw_name, raw_value in strategy_values.items():
        name = _PARAM_ALIASES.get(raw_name, raw_name)
        if strict_params and not _strategy_tag_present(text, name):
            display = raw_name if raw_name == name else f"{raw_name} -> {name}"
            missing.append(display)
            continue
        value = _format_strategy_value(raw_value)
        text = _patch_strategy_value_text(text, name, value)
        text = _patch_parameter_text(text, name, ParameterRange(value.lower() if _is_bool_like(raw_value) else value, value.lower() if _is_bool_like(raw_value) else value, "1", _format_parameter_value(raw_value)))
    if missing:
        raise MissingSeedParamError(
            f"Seed template {Path(seed_backtest_template)} has no element for "
            f"parameter(s) {missing}; refusing to stamp a template that would "
            "silently run with seed defaults."
        )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _strategy_tag_present(text: str, tag: str) -> bool:
    """True if ``<tag>...</tag>`` exists in the (optimizer-stripped) template.

    After :func:`_remove_optimizer_sections` the only place a parameter name
    appears is as a strategy-element child, so a hit here means the seed really
    carries that parameter.
    """
    pattern = re.compile(
        r"<" + re.escape(tag) + r"(?:\s+[^>]*)?>.*?</" + re.escape(tag) + r">",
        re.DOTALL,
    )
    return pattern.search(text) is not None


def _normalize_parameter_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename: dict[str, str] = {}
    for column in df.columns:
        if not column.startswith("param_"):
            continue
        param_name = column.removeprefix("param_")
        canonical = _PARAM_ALIASES.get(param_name, param_name)
        rename[column] = f"param_{canonical}"
    return df.rename(columns=rename)


def _evaluate_row(index: int, row: pd.Series, config: OptimizationGridConfig) -> OptimizationGridCandidate:
    total_net_profit = _float_or_none(row.get("total_net_profit"))
    profit_factor = _float_or_none(row.get("profit_factor"))
    max_drawdown = _float_or_none(row.get("max_drawdown"))
    trades = _int_or_none(row.get("total_trades"))
    rejections: list[str] = []
    notes: list[str] = []

    drawdown_abs = abs(max_drawdown) if max_drawdown is not None else None
    if total_net_profit is None or total_net_profit <= 0:
        rejections.append("non-positive net profit")
    if profit_factor is None or profit_factor < config.min_profit_factor:
        rejections.append(f"profit factor below {config.min_profit_factor:g}")
    if drawdown_abs is None or drawdown_abs > config.max_drawdown:
        rejections.append(f"drawdown above {config.max_drawdown:g}")
    if trades is None or trades < config.min_trades:
        rejections.append(f"trades below {config.min_trades}")
    if not _as_bool(row.get("parse_ok", True)):
        notes.append(str(row.get("parse_warnings", "parameter parse warning")))

    start_hour = _int_or_none(_param(row, "StartTimeH"))
    duration_hours = _int_or_none(_param(row, "DurationTimeH"))
    reverse = _str_value(_param(row, "Reverse", False))
    score = _score(total_net_profit, profit_factor, drawdown_abs, trades, config)
    status = "reject" if rejections else "pass"
    return OptimizationGridCandidate(
        source_file=str(row.get("source_file", "")),
        row_number=int(index) + 1,
        status=status,
        score=round(score, 2),
        mode="regression" if _as_bool(reverse) else "breakout",
        session_bucket=_session_bucket(start_hour),
        total_net_profit=total_net_profit,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        trades=trades,
        start_hour=start_hour,
        duration_hours=duration_hours,
        average_fast=_int_or_none(_param(row, "averageFast")),
        average_slow=_int_or_none(_param(row, "averageSlow")),
        max_stop=_int_or_none(_param(row, "MaxStop")),
        max_tp_ratio=_float_or_none(_param(row, "MaxTPRatio")),
        profit_stop=_int_or_none(_param(row, "ProfitStop")),
        loss_stop=_int_or_none(_param(row, "LossStop")),
        max_trades=_int_or_none(_param(row, "MaxTrades")),
        long_enabled=_str_value(_param(row, "Long", True)),
        short_enabled=_str_value(_param(row, "Short", True)),
        reverse=reverse,
        bot_name=_str_value(_param(row, "BotName", "")),
        reasons="; ".join(rejections + notes) if rejections or notes else "passed phase filters",
    )


def _candidate_strategy_values(candidate: OptimizationGridCandidate) -> dict[str, Any]:
    return {
        "UseTimeFilter": True,
        "StartTimeH": candidate.start_hour,
        "StartTimeM": 0,
        "DurationTimeH": candidate.duration_hours,
        "DurationTimeM": 0,
        "averageFast": candidate.average_fast,
        "averageSlow": candidate.average_slow,
        "UseTrend": False,
        "UseTrendReverse": False,
        "MaxStop": candidate.max_stop,
        "MaxTPRatio": candidate.max_tp_ratio,
        "ProfitStop": candidate.profit_stop,
        "LossStop": candidate.loss_stop,
        "MaxTrades": candidate.max_trades,
        "Long": candidate.long_enabled,
        "Short": candidate.short_enabled,
        "Reverse": candidate.reverse,
        "BotName": candidate.bot_name or candidate.run_id,
    }


def _remove_optimizer_sections(text: str) -> str:
    text = re.sub(r"\n\s*<OptimizerType>.*?</OptimizerType>", "", text, flags=re.DOTALL)
    text = re.sub(r"\n\s*<OptimizerParameters>.*?</OptimizerParameters>", "", text, flags=re.DOTALL)
    text = re.sub(r"\n\s*<OptimizationFitness>.*?</OptimizationFitness>", "", text, flags=re.DOTALL)
    text = re.sub(r"\n\s*<OptimizationParameters>.*?</OptimizationParameters>", "", text, flags=re.DOTALL)
    return text


def _score(
    total_net_profit: float | None,
    profit_factor: float | None,
    drawdown_abs: float | None,
    trades: int | None,
    config: OptimizationGridConfig,
) -> float:
    profit_score = min(40.0, max(0.0, total_net_profit or 0.0) / 500.0)
    pf_score = min(30.0, max(0.0, profit_factor or 0.0) * 8.0)
    dd_value = drawdown_abs if drawdown_abs is not None else config.max_drawdown
    dd_score = max(0.0, 20.0 * (1.0 - min(dd_value, config.max_drawdown) / config.max_drawdown))
    trade_score = min(10.0, float(trades or 0) / max(1, config.min_trades) * 5.0)
    return profit_score + pf_score + dd_score + trade_score


def _lineage_row(
    candidate: OptimizationGridCandidate,
    generated_template: Path,
    target_phase: str,
) -> OptimizationPhaseLineageRow:
    return OptimizationPhaseLineageRow(
        source_file=candidate.source_file,
        source_row=candidate.row_number,
        generated_template=str(generated_template),
        target_phase=target_phase,
        mode=candidate.mode,
        score=candidate.score,
        total_net_profit=candidate.total_net_profit,
        profit_factor=candidate.profit_factor,
        max_drawdown=candidate.max_drawdown,
        trades=candidate.trades,
        start_hour=candidate.start_hour,
        duration_hours=candidate.duration_hours,
        average_fast=candidate.average_fast,
        average_slow=candidate.average_slow,
        max_stop=candidate.max_stop,
        max_tp_ratio=candidate.max_tp_ratio,
        profit_stop=candidate.profit_stop,
        loss_stop=candidate.loss_stop,
        max_trades=candidate.max_trades,
        long_enabled=candidate.long_enabled,
        short_enabled=candidate.short_enabled,
        reverse=candidate.reverse,
        reason=candidate.reasons,
    )


def _write_lineage(destination: Path, rows: Sequence[OptimizationPhaseLineageRow]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    fieldnames = list(OptimizationPhaseLineageRow.__dataclass_fields__.keys())
    with (destination / "optimization_phase_lineage.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    (destination / "optimization_phase_lineage.json").write_text(
        json.dumps({"schema_version": 1, "row_count": len(rows), "rows": [asdict(row) for row in rows]}, indent=2),
        encoding="utf-8",
    )
    _write_lineage_markdown(destination / "OPTIMIZATION_PHASE_SUMMARY.md", rows)


def _write_lineage_markdown(path: Path, rows: Sequence[OptimizationPhaseLineageRow]) -> None:
    lines = [
        "# Optimization Phase Summary",
        "",
        f"Generated templates: {len(rows)}",
        "",
        "| Source | Row | Target | Mode | Score | PF | Net Profit | Max DD | Trades | Settings |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        settings = (
            f"{row.start_hour}:00/{row.duration_hours}h, fast {row.average_fast}, "
            f"slow {row.average_slow}, stop {row.max_stop}, TP {row.max_tp_ratio}, "
            f"profit stop {row.profit_stop}, loss stop {row.loss_stop}, max trades {row.max_trades}, "
            f"long {row.long_enabled}, short {row.short_enabled}, reverse {row.reverse}"
        )
        lines.append(
            f"| {row.source_file} | {row.source_row} | {row.target_phase} | {row.mode} | "
            f"{row.score:g} | {_fmt(row.profit_factor)} | {_fmt(row.total_net_profit)} | "
            f"{_fmt(row.max_drawdown)} | {_fmt(row.trades)} | {settings} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_candidates(destination: Path, rows: Sequence[OptimizationGridCandidate], config: OptimizationGridConfig) -> None:
    fieldnames = list(OptimizationGridCandidate.__dataclass_fields__.keys())
    with (destination / "optimization_grid_candidates.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    (destination / "optimization_grid_candidates.json").write_text(
        json.dumps({"schema_version": 1, "config": asdict(config), "rows": [asdict(row) for row in rows]}, indent=2),
        encoding="utf-8",
    )


def _write_final_readme(
    path: Path,
    generated: Sequence[Path],
    lineage: Sequence[OptimizationPhaseLineageRow],
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> None:
    lines = [
        "# Final Backtest Templates",
        "",
        "These templates are fixed Backtest-mode templates generated from phase-3 optimization output.",
        "Use these with the existing batch/backtest runner, then send the resulting report outputs to the team.",
        "Final Backtest contract: templates force `UseTrend=false` and `UseTrendReverse=false` before validation.",
        "",
    ]
    if from_date or to_date:
        lines.extend(
            [
                f"Backtest date range: {_format_nt_date(from_date) if from_date else 'seed From'} to {_format_nt_date(to_date) if to_date else 'seed To'}",
                "",
            ]
        )
    lines.extend([f"Templates: {len(generated)}", ""])
    for row in lineage:
        lines.append(f"- `{row.generated_template}` from `{row.source_file}` row {row.source_row}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_final_batch_command(destination: Path, template_dir: Path) -> None:
    result_dir = destination / "nt8_backtest_results"
    command = {
        "action": "RunBatch",
        "sourceFolder": str(template_dir),
        "destFolder": str(result_dir),
        "closeTempTabs": True,
    }
    (destination / "nt8_run_batch_command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    lines = [
        "# Run Final Backtests",
        "",
        "Use the generated fixed Backtest templates with the NinjaTrader batch Strategy Analyzer AddOn.",
        "These templates are final validation runs, not optimizer templates. They force `UseTrend=false` and `UseTrendReverse=false`.",
        "",
        f"Template folder: `{template_dir}`",
        f"Result folder: `{result_dir}`",
        "",
        "Command file content:",
        "",
        "```json",
        json.dumps(command, indent=2),
        "```",
        "",
        "After NinjaTrader exports the backtest CSVs, feed the result folder back through the optimizer result review workflow.",
        "",
        "```powershell",
        f"python -m ta_foundation.optimization.review --input-dir \"{result_dir}\" --output-dir \"{destination / 'final_backtest_review'}\"",
        "```",
    ]
    (destination / "RUN_FINAL_BACKTESTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _has_core_settings(candidate: OptimizationGridCandidate) -> bool:
    return (
        candidate.start_hour is not None
        and candidate.duration_hours is not None
        and candidate.average_slow is not None
        and candidate.max_stop is not None
        and candidate.max_tp_ratio is not None
    )


def _param(row: pd.Series, name: str, default: Any = None) -> Any:
    return row.get(f"param_{name}", default)


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _is_bool_like(value: Any) -> bool:
    return isinstance(value, bool) or str(value).strip().lower() in {"true", "false"}


def _format_strategy_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    text = str(value).strip()
    if text.lower() in {"true", "false"}:
        return text.lower()
    return text


def _format_parameter_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    text = str(value).strip()
    if text.lower() == "true":
        return "True"
    if text.lower() == "false":
        return "False"
    return text


def _format_nt_date(value: str) -> str:
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00"
    return text


def _str_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, bool):
        return str(value)
    return str(value).strip()


def _float_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _session_bucket(start_hour: int | None) -> str:
    if start_hour is None:
        return "Unclassified"
    if 0 <= start_hour < 4:
        return "London Early"
    if 4 <= start_hour < 7:
        return "London Late"
    if 7 <= start_hour < 9:
        return "Pre-Market"
    if 9 <= start_hour < 11:
        return "NY Open"
    if 11 <= start_hour < 14:
        return "Midday"
    if 14 <= start_hour < 16:
        return "Power Hour"
    if 16 <= start_hour < 20:
        return "Overlap"
    return "Asia"


def _slow_ma_family(average_slow: int | None) -> str:
    if average_slow is None:
        return "unknown"
    if average_slow <= 150:
        return "fast_trend"
    if average_slow <= 300:
        return "middle_trend"
    return "slow_trend"


def _risk_shape(max_stop: int | None, max_tp_ratio: float | None) -> str:
    stop = max_stop or 0
    ratio = max_tp_ratio or 0.0
    stop_label = "tight_stop" if stop and stop <= 100 else "wide_stop" if stop >= 200 else "mid_stop"
    ratio_label = "high_rr" if ratio >= 1.5 else "low_rr" if ratio and ratio < 1.0 else "mid_rr"
    return f"{stop_label}_{ratio_label}"


def _direction(long_enabled: str, short_enabled: str) -> str:
    long_on = str(long_enabled).strip().lower() == "true"
    short_on = str(short_enabled).strip().lower() == "true"
    if long_on and short_on:
        return "both"
    if long_on:
        return "long"
    if short_on:
        return "short"
    return "off"


def _clear_generated_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_") or "candidate"


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _parse_rows(value: str) -> tuple[int, ...]:
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance NinjaTrader optimizer CSV rows through phase 2, phase 3, or final Backtest template generation.")
    parser.add_argument("--seed-template", required=True, help="Seed XML template. Use optimizer seed for phase2/phase3; Backtest seed for final.")
    parser.add_argument("--optimization-csv-dir", required=True, help="Optimization CSV file or folder containing *_Optimization.csv exports.")
    parser.add_argument("--output-dir", required=True, help="Folder where generated templates and lineage should be written.")
    parser.add_argument("--target-phase", choices=("phase2", "phase3", "final"), required=True)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--include-rows", default="", help="Optional comma-separated 1-based optimization CSV row numbers.")
    parser.add_argument("--average-fast-min", type=int, default=2)
    parser.add_argument("--average-fast-max", type=int, default=10)
    parser.add_argument("--max-drawdown", type=float, default=2500.0)
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--min-profit-factor", type=float, default=1.5)
    parser.add_argument("--from-date", default="", help="Optional final Backtest From date, e.g. 2026-04-14.")
    parser.add_argument("--to-date", default="", help="Optional final Backtest To date, e.g. 2026-05-14.")
    args = parser.parse_args(argv)

    config = OptimizationGridConfig(
        max_drawdown=args.max_drawdown,
        min_trades=args.min_trades,
        min_profit_factor=args.min_profit_factor,
        count=args.count,
    )
    include_rows = _parse_rows(args.include_rows)
    if args.target_phase == "final":
        generated = create_final_backtest_templates_from_phase3_csv(
            args.seed_template,
            args.optimization_csv_dir,
            args.output_dir,
            config=config,
            include_rows=include_rows,
            from_date=args.from_date or None,
            to_date=args.to_date or None,
        )
        print(f"Generated {len(generated)} fixed Backtest templates in {Path(args.output_dir)}")
    else:
        generated = create_next_phase_from_optimization_csv(
            args.seed_template,
            args.optimization_csv_dir,
            args.output_dir,
            target_phase=args.target_phase,
            config=config,
            average_fast_min=args.average_fast_min,
            average_fast_max=args.average_fast_max,
            include_rows=include_rows,
        )
        print(f"Generated {len(generated)} {args.target_phase} optimizer templates in {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
