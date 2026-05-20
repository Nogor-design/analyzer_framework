from __future__ import annotations

"""Guardrail-based optimizer-result analysis for the strategy loop.

The smoke loop and the full repair/refinement loop both need to decide whether
an optimizer CSV produced a candidate worth keeping. This module encapsulates
that decision so the orchestrators stay short and the threshold logic is
exercised by direct unit tests.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser


@dataclass(frozen=True)
class Guardrails:
    max_drawdown: float = 2500.0
    min_trades: int = 10
    min_profit_factor: float = 1.5


# Back-compat alias for callers that imported SmokeGuardrails.
SmokeGuardrails = Guardrails


def analyze_optimization_csv(path: str | Path, guardrails: Guardrails) -> dict[str, Any]:
    artifact = NinjaTraderOptimizationCsvParser().parse(Path(path), run_id=Path(path).stem)
    df = artifact.df.copy() if isinstance(artifact.df, pd.DataFrame) else pd.DataFrame()
    if df.empty:
        return {
            "row_count": 0,
            "passing_rows": 0,
            "best_row": None,
            "reject_reasons": ["no optimizer rows parsed"],
            "warnings": artifact.warnings,
        }

    df["drawdown_abs"] = pd.to_numeric(df.get("max_drawdown"), errors="coerce").abs()
    df["profit_factor_num"] = pd.to_numeric(df.get("profit_factor"), errors="coerce")
    df["total_trades_num"] = pd.to_numeric(df.get("total_trades"), errors="coerce")
    df["net_profit_num"] = pd.to_numeric(df.get("total_net_profit"), errors="coerce")
    df["passes_guardrails"] = (
        (df["drawdown_abs"] <= guardrails.max_drawdown)
        & (df["profit_factor_num"] >= guardrails.min_profit_factor)
        & (df["total_trades_num"] >= guardrails.min_trades)
        & (df["net_profit_num"] > 0)
    )
    df["score"] = (
        df["profit_factor_num"].fillna(0) * 1000
        + df["net_profit_num"].fillna(0) / 10
        - df["drawdown_abs"].fillna(guardrails.max_drawdown) / 2
    )
    ranked = df.sort_values(["score", "profit_factor_num"], ascending=[False, False])
    best = _json_safe(ranked.iloc[0].to_dict())
    reject_reasons = _reject_reasons(ranked.iloc[0], guardrails)
    return {
        "row_count": int(len(df)),
        "passing_rows": int(df["passes_guardrails"].sum()),
        "best_row": best,
        "reject_reasons": reject_reasons,
        "warnings": artifact.warnings,
        "parameter_names": artifact.summary.get("parameter_names", []),
    }


def _reject_reasons(row: pd.Series, guardrails: Guardrails) -> list[str]:
    reasons: list[str] = []
    if float(row.get("net_profit_num") or 0) <= 0:
        reasons.append("non-positive net profit")
    if float(row.get("profit_factor_num") or 0) < guardrails.min_profit_factor:
        reasons.append(f"profit factor below {guardrails.min_profit_factor:g}")
    if float(row.get("drawdown_abs") or 0) > guardrails.max_drawdown:
        reasons.append(f"drawdown above {guardrails.max_drawdown:g}")
    if float(row.get("total_trades_num") or 0) < guardrails.min_trades:
        reasons.append(f"trades below {guardrails.min_trades}")
    return reasons or ["passed configured smoke guardrails"]


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
