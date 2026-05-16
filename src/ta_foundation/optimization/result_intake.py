from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from ta_foundation.parsers.ninjatrader.settings_csv import NinjaTraderSettingsCsvParser
from ta_foundation.parsers.ninjatrader.summary_csv import NinjaTraderSummaryCsvParser
from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser


SUMMARY_SUFFIXES = ("_Summary.csv", "_Summery.csv", "Summary.csv", "Summery.csv")
SETTINGS_SUFFIXES = ("_Settings.csv", "Settings.csv")
TRADES_MARKERS = ("_Trades.csv", "_Trades_keep.csv", "Trades.csv")
BATCH_BOOKKEEPING_FILES = {"BatchRunSummary.csv", "Executions.csv", "Orders.csv"}


@dataclass(frozen=True)
class IntakeResultRow:
    run_id: str
    result_path: str
    total_net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None
    trades: int | None
    percent_profitable: float | None
    avg_trade: float | None
    start_dt: str
    end_dt: str
    traded_days: int | None
    percent_days_traded: float | None
    last_5_trade_profit: float | None
    prior_5_trade_profit: float | None
    recent_trade_delta: float | None
    start_hour: int | None
    duration_hours: int | None
    reverse: str
    average_fast: int | None
    average_slow: int | None
    use_trend: str
    use_trend_reverse: str
    max_stop: int | None
    max_tp_ratio: float | None
    profit_stop: int | None
    loss_stop: int | None
    max_trades: int | None
    long_enabled: str
    short_enabled: str
    bot_name: str
    warnings: str


def ingest_result_folder(input_dir: str | Path) -> list[IntakeResultRow]:
    root = Path(input_dir)
    csv_files = [path for path in root.rglob("*.csv") if path.is_file()]
    grouped: dict[str, dict[str, Path]] = {}

    for path in csv_files:
        if path.name in BATCH_BOOKKEEPING_FILES:
            continue
        if _matches_suffix(path.name, SUMMARY_SUFFIXES):
            run_id = _run_id_from_result_file(path)
            bucket = grouped.setdefault(run_id, {})
            bucket["summary"] = path
        elif _matches_suffix(path.name, SETTINGS_SUFFIXES):
            run_id = _run_id_from_result_file(path)
            bucket = grouped.setdefault(run_id, {})
            bucket["settings"] = path
        elif any(marker in path.name for marker in TRADES_MARKERS) or path.name == "Trades.csv":
            run_id = _run_id_from_result_file(path)
            bucket = grouped.setdefault(run_id, {})
            bucket["trades"] = path

    return [_row_from_group(run_id, paths) for run_id, paths in sorted(grouped.items())]


def write_intake_summary(input_dir: str | Path, output_dir: str | Path) -> list[IntakeResultRow]:
    rows = ingest_result_folder(input_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_csv(destination / "result_intake.csv", rows)
    _write_json(destination / "result_intake.json", rows)
    return rows


def _row_from_group(run_id: str, paths: dict[str, Path]) -> IntakeResultRow:
    warnings: list[str] = []
    summary: dict[str, Any] = {}
    start_dt = ""
    end_dt = ""
    settings_df = pd.DataFrame()
    trades_df = pd.DataFrame()

    if "summary" in paths:
        artifact = NinjaTraderSummaryCsvParser().parse(paths["summary"], run_id)
        summary = dict(artifact.summary.get("kpis_all") or {})
        start = artifact.summary.get("start_dt")
        end = artifact.summary.get("end_dt")
        start_dt = start.isoformat() if start else ""
        end_dt = end.isoformat() if end else ""
        warnings.extend(_warning_messages(artifact.warnings))
    else:
        warnings.append("missing Summary.csv")

    if "settings" in paths:
        artifact = NinjaTraderSettingsCsvParser().parse(paths["settings"], run_id)
        settings_df = artifact.df
        warnings.extend(_warning_messages(artifact.warnings))
    else:
        warnings.append("missing Settings.csv")

    if "trades" in paths:
        artifact = NinjaTraderTradesCsvParser().parse(paths["trades"], run_id)
        trades_df = artifact.df
        warnings.extend(_warning_messages(artifact.warnings))
    else:
        warnings.append("missing Trades.csv")

    traded_days, percent_days = _traded_day_stats(trades_df, start_dt, end_dt)
    last_5, prior_5, recent_delta = _recent_trade_stats(trades_df)

    return IntakeResultRow(
        run_id=run_id,
        result_path=str(_representative_path(paths)),
        total_net_profit=_as_float(summary.get("totalnetprofit")),
        profit_factor=_as_float(summary.get("profitfactor")),
        max_drawdown=_as_float(summary.get("maxdrawdown")),
        trades=_as_int(summary.get("totaloftrades")),
        percent_profitable=_as_float(summary.get("percentprofitable")),
        avg_trade=_as_float(summary.get("avgtrade")),
        start_dt=start_dt,
        end_dt=end_dt,
        traded_days=traded_days,
        percent_days_traded=percent_days,
        last_5_trade_profit=last_5,
        prior_5_trade_profit=prior_5,
        recent_trade_delta=recent_delta,
        start_hour=_settings_int(settings_df, "StartTimeH"),
        duration_hours=_settings_int(settings_df, "DurationTimeH"),
        reverse=_settings_str(settings_df, "Reverse"),
        average_fast=_settings_int(settings_df, "averageFast"),
        average_slow=_settings_int(settings_df, "averageSlow"),
        use_trend=_settings_str(settings_df, "UseTrend"),
        use_trend_reverse=_settings_str(settings_df, "UseTrendReverse"),
        max_stop=_settings_int(settings_df, "MaxStop"),
        max_tp_ratio=_settings_float(settings_df, "MaxTPRatio"),
        profit_stop=_settings_int(settings_df, "ProfitStop"),
        loss_stop=_settings_int(settings_df, "LossStop"),
        max_trades=_settings_int(settings_df, "MaxTrades"),
        long_enabled=_settings_str(settings_df, "Long"),
        short_enabled=_settings_str(settings_df, "Short"),
        bot_name=_settings_str(settings_df, "BotName"),
        warnings="; ".join(warnings),
    )


def _run_id_from_result_file(path: Path) -> str:
    name = path.stem
    for suffix in ("_Summary", "_Summery", "_Settings", "_Analysis", "_Trades_keep", "_Trades"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    if path.name in {"Summary.csv", "Summery.csv", "Settings.csv", "Analysis.csv", "Trades.csv"}:
        return path.parent.name
    return name


def _matches_suffix(name: str, suffixes: Sequence[str]) -> bool:
    return any(name.endswith(suffix) for suffix in suffixes)


def _warning_messages(warnings: Sequence[dict[str, Any]] | None) -> list[str]:
    return [str(w.get("message") or w.get("code") or w) for w in (warnings or [])]


def _representative_path(paths: dict[str, Path]) -> Path:
    if "summary" in paths:
        return paths["summary"].parent
    if paths:
        return next(iter(paths.values())).parent
    return Path("")


def _traded_day_stats(trades_df: pd.DataFrame, start_dt: str, end_dt: str) -> tuple[int | None, float | None]:
    if trades_df.empty or "exit_time" not in trades_df.columns:
        return None, None
    exits = pd.to_datetime(trades_df["exit_time"], errors="coerce")
    traded_days = int(exits.dropna().dt.date.nunique())
    start = pd.to_datetime(start_dt, errors="coerce")
    end = pd.to_datetime(end_dt, errors="coerce")
    if pd.isna(start) or pd.isna(end) or end < start:
        return traded_days, None
    total_days = max(1, int((end.date() - start.date()).days) + 1)
    return traded_days, round((traded_days / total_days) * 100.0, 2)


def _recent_trade_stats(trades_df: pd.DataFrame) -> tuple[float | None, float | None, float | None]:
    if trades_df.empty or "profit" not in trades_df.columns:
        return None, None, None
    profits = pd.to_numeric(trades_df["profit"], errors="coerce").dropna()
    if profits.empty:
        return None, None, None
    last_5 = float(profits.tail(5).sum())
    prior_5 = float(profits.iloc[max(0, len(profits) - 10): max(0, len(profits) - 5)].sum()) if len(profits) > 5 else None
    recent_delta = None if prior_5 is None else last_5 - prior_5
    return last_5, prior_5, recent_delta


def _settings_int(settings_df: pd.DataFrame, item: str) -> int | None:
    value = _settings_value(settings_df, item)
    return _as_int(value)


def _settings_float(settings_df: pd.DataFrame, item: str) -> float | None:
    value = _settings_value(settings_df, item)
    return _as_float(value)


def _settings_str(settings_df: pd.DataFrame, item: str) -> str:
    value = _settings_value(settings_df, item)
    return "" if value is None else str(value)


def _settings_value(settings_df: pd.DataFrame, item: str) -> Any:
    if settings_df.empty or "item" not in settings_df.columns:
        return None
    aliases = {
        "StartTimeH": {"starttimeh", "starttimehh"},
        "StartTimeM": {"starttimem", "starttimemm"},
        "DurationTimeH": {"durationtimeh", "durationtimehh"},
        "DurationTimeM": {"durationtimem", "durationtimemm"},
        "BotName": {"botname"},
    }
    wanted = aliases.get(item, {_normalize_setting_key(item)})
    normalized = settings_df["item"].astype(str).map(_normalize_setting_key)
    matches = settings_df[normalized.isin(wanted)]
    if matches.empty:
        return None
    return matches.iloc[0].get("value")


def _normalize_setting_key(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _write_csv(path: Path, rows: Sequence[IntakeResultRow]) -> None:
    fieldnames = list(IntakeResultRow.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_json(path: Path, rows: Sequence[IntakeResultRow]) -> None:
    payload = {
        "schema_version": 1,
        "result_count": len(rows),
        "rows": [asdict(row) for row in rows],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest NinjaTrader result CSV folders for optimizer handoff review.")
    parser.add_argument("--input-dir", required=True, help="Folder containing returned NinjaTrader CSV results.")
    parser.add_argument("--output-dir", required=True, help="Folder where result_intake artifacts should be written.")
    args = parser.parse_args(argv)
    rows = write_intake_summary(args.input_dir, args.output_dir)
    print(f"Wrote result intake summary with {len(rows)} runs to {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
