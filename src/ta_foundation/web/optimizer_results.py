from __future__ import annotations

"""Result ingestion helpers for /optimizer sessions."""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ta_foundation.core.pipeline import ingest_folder
from ta_foundation.core.registry import ParserRegistry
from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser
from ta_foundation.web.optimizer_session import OptimizerSession


NT_OUTPUT_DIRNAME = "nt_output"


@dataclass(frozen=True)
class OptimizerResultsSummary:
    session_id: str
    output_dir: str
    row_count: int
    batch_count: int
    parse_warnings: int
    unparsed_files: list[str]
    batches: list[dict[str, Any]]
    top_rows: list[dict[str, Any]]
    guardrail_rows: list[dict[str, Any]]
    notes: list[str]
    batch_run_statuses: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "output_dir": self.output_dir,
            "row_count": self.row_count,
            "batch_count": self.batch_count,
            "parse_warnings": self.parse_warnings,
            "unparsed_files": self.unparsed_files,
            "batches": self.batches,
            "top_rows": self.top_rows,
            "guardrail_rows": self.guardrail_rows,
            "notes": self.notes,
            "batch_run_statuses": self.batch_run_statuses,
        }


class OptimizerResultsError(Exception):
    pass


def load_optimizer_results(session: OptimizerSession, *, top_n: int = 25) -> OptimizerResultsSummary:
    doc = session.load_document()
    output_dir = session.directory / NT_OUTPUT_DIRNAME
    if not output_dir.exists():
        raise OptimizerResultsError(f"No NinjaTrader output folder found: {output_dir}")

    registry = ParserRegistry(parsers=[NinjaTraderOptimizationCsvParser()])
    result = ingest_folder(output_dir, registry=registry, recursive=True, load_tick_data=False)
    store = result.optimization_store
    combined = store.combined_results() if store else None
    if combined is None:
        combined = pd.DataFrame()

    combined = _add_helper_columns(combined)
    batches = _batch_summaries(store)
    batch_run_statuses = _batch_run_statuses(output_dir)
    notes = [
        "Optimization exports are retained rows from NinjaTrader, controlled by KeepBestResults.",
        "Percent days traded requires a follow-up fixed-template backtest/trade-level pass.",
    ]
    incomplete = [
        row for row in batch_run_statuses
        if str(row.get("status") or "").strip().lower() not in {"", "completed"}
    ]
    if incomplete:
        labels = ", ".join(
            f"{row.get('template') or 'unknown'}={row.get('status') or 'unknown'}"
            for row in incomplete[:5]
        )
        notes.insert(0, f"NinjaTrader batch summary reported non-completed template(s): {labels}.")
    if combined.empty and batch_run_statuses:
        notes.insert(0, "No optimizer rows were parsed from the NinjaTrader export.")

    filtered = _apply_available_guardrails(combined, doc.guardrails)
    return OptimizerResultsSummary(
        session_id=session.id,
        output_dir=str(output_dir.resolve()),
        row_count=int(len(combined)),
        batch_count=len(batches),
        parse_warnings=sum(len(b.get("warnings") or []) for b in batches),
        unparsed_files=[str(p) for p in result.unparsed_files],
        batches=batches,
        top_rows=_top_rows(combined, top_n=top_n),
        guardrail_rows=_top_rows(filtered, top_n=top_n),
        notes=notes,
        batch_run_statuses=batch_run_statuses,
    )


def _add_helper_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    if "max_drawdown" in df.columns:
        df["drawdown_abs"] = pd.to_numeric(df["max_drawdown"], errors="coerce").abs()
    return df


def _batch_summaries(store: Any) -> list[dict[str, Any]]:
    if store is None:
        return []
    summaries: list[dict[str, Any]] = []
    for batch_id, batch in store.batches.items():
        df = _add_helper_columns(batch.results)
        item: dict[str, Any] = {
            "batch_id": batch_id,
            "source_path": str(batch.source_path),
            "instrument": batch.instrument,
            "row_count": int(batch.row_count),
            "successfully_parsed_rows": int(batch.successfully_parsed_rows),
            "warning_count": len(batch.warnings),
            "parameter_count": len(batch.parameter_names),
            "warnings": batch.warnings[:5],
        }
        if df is not None and not df.empty:
            item.update({
                "best_profit_factor": _safe_float(df.get("profit_factor").max()),
                "best_net_profit": _safe_float(df.get("total_net_profit").max()),
                "lowest_drawdown": _safe_float(df.get("drawdown_abs").min()),
                "max_trades": _safe_float(df.get("total_trades").max()),
            })
        summaries.append(item)
    summaries.sort(key=lambda b: b["batch_id"])
    return summaries


def _batch_run_statuses(output_dir: Path) -> list[dict[str, Any]]:
    path = output_dir / "BatchRunSummary.csv"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for raw in csv.DictReader(fh):
                rows.append({
                    "template": (raw.get("Template") or "").strip(),
                    "status": (raw.get("Status") or "").strip(),
                    "strategy": (raw.get("Strategy") or "").strip(),
                    "instrument": (raw.get("Instrument") or "").strip(),
                    "backtest_start": (raw.get("Backtest start") or "").strip(),
                    "backtest_end": (raw.get("Backtest end") or "").strip(),
                    "total_net_profit": (raw.get("Total net profit") or "").strip(),
                    "trades": (raw.get("Trades") or "").strip(),
                    "profit_factor": (raw.get("Profit factor") or "").strip(),
                    "max_drawdown": (raw.get("Max drawdown") or "").strip(),
                    "error": (raw.get("Error") or "").strip(),
                })
    except OSError:
        return []
    return rows


def _apply_available_guardrails(df: pd.DataFrame, guardrails: Any) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if guardrails.max_drawdown_dollars is not None and "drawdown_abs" in out.columns:
        out = out[out["drawdown_abs"] <= float(guardrails.max_drawdown_dollars)]
    if guardrails.min_trades is not None and "total_trades" in out.columns:
        out = out[out["total_trades"] >= int(guardrails.min_trades)]
    if guardrails.min_profit_factor is not None and "profit_factor" in out.columns:
        out = out[out["profit_factor"] >= float(guardrails.min_profit_factor)]
    if guardrails.min_net_profit is not None and "total_net_profit" in out.columns:
        out = out[out["total_net_profit"] >= float(guardrails.min_net_profit)]
    return out


def _top_rows(df: pd.DataFrame, *, top_n: int) -> list[dict[str, Any]]:
    if df.empty:
        return []
    ranked = df.copy()
    for col in ("profit_factor", "total_net_profit", "drawdown_abs", "total_trades"):
        if col not in ranked.columns:
            ranked[col] = 0
    ranked["optimizer_score"] = (
        pd.to_numeric(ranked["profit_factor"], errors="coerce").fillna(0) * 1000
        + pd.to_numeric(ranked["total_net_profit"], errors="coerce").fillna(0) / 10
        - pd.to_numeric(ranked["drawdown_abs"], errors="coerce").fillna(0) / 2
    )
    ranked = ranked.sort_values(
        ["optimizer_score", "profit_factor", "total_net_profit"],
        ascending=[False, False, False],
    )
    display_cols = [
        "batch_id",
        "instrument",
        "optimizer_score",
        "performance",
        "total_net_profit",
        "profit_factor",
        "drawdown_abs",
        "total_trades",
        "percent_profitable",
        "parameters",
    ]
    # Include every param_* column; truncating loses important fields like
    # param_Reverse (which appears late in the Pantheon parameter list).
    param_cols = [
        c for c in ranked.columns
        if str(c).startswith("param_")
    ]
    display_cols.extend(param_cols)
    rows = ranked[[c for c in display_cols if c in ranked.columns]].head(top_n)
    return [_json_safe(row) for row in rows.to_dict(orient="records")]


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in row.items():
        if pd.isna(value):
            clean[key] = None
        elif hasattr(value, "item"):
            clean[key] = value.item()
        else:
            clean[key] = value
    return clean


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
