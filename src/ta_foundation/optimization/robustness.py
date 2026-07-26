from __future__ import annotations

"""Optional robustness checks for optimizer final candidates.

The bootstrap check is the only one runnable in this module — it
operates on the Trades.csv produced by a fixed-Backtest run with no
NinjaTrader roundtrip needed. Walk-forward and parameter-neighborhood
validation live in dedicated web engines because they dispatch NT
runs (see ``ta_foundation.web.optimizer_walkforward`` and
``ta_foundation.web.optimizer_neighborhood``).

The bootstrap idea: take the actual trade-level returns from a
finished Backtest, resample with replacement N times, and compute the
distribution of profit factor, max drawdown, and total net profit.
This tells the operator whether the headline result is a robust
characteristic of the strategy or an ordering artifact of one
particular trade sequence.

Output is JSON-safe so it can land directly in the deployment package
manifest.
"""

import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ta_foundation.parsers.ninjatrader.trades_csv import (
    NinjaTraderTradesCsvParser,
)


DEFAULT_BOOTSTRAP_SAMPLES = 1000
DEFAULT_SEED = 42


@dataclass(frozen=True)
class BootstrapStat:
    observed: float | None
    bootstrap_mean: float | None
    bootstrap_median: float | None
    bootstrap_p05: float | None
    bootstrap_p95: float | None
    p_at_or_above_observed: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateRobustness:
    run_id: str
    trades_path: str
    trade_count: int
    bootstrap_samples: int
    seed: int
    profit_factor: BootstrapStat
    net_profit: BootstrapStat
    max_drawdown: BootstrapStat
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trades_path": self.trades_path,
            "trade_count": self.trade_count,
            "bootstrap_samples": self.bootstrap_samples,
            "seed": self.seed,
            "profit_factor": self.profit_factor.to_dict(),
            "net_profit": self.net_profit.to_dict(),
            "max_drawdown": self.max_drawdown.to_dict(),
            "notes": list(self.notes),
        }


class RobustnessError(Exception):
    pass


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def bootstrap_trades_csv(
    trades_csv_path: Path | str,
    *,
    run_id: str | None = None,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> CandidateRobustness:
    """Run a trade-sequence bootstrap on a single Trades.csv export.

    Returns a CandidateRobustness with the observed stats and the
    distribution of bootstrap samples for profit factor, net profit, and
    max drawdown. The ``p_at_or_above_observed`` field reports the
    fraction of bootstrap samples whose statistic met or exceeded the
    observed value — a quick "could this have happened by chance under
    a shuffle of the same trades?" signal.
    """
    path = Path(trades_csv_path)
    if not path.exists() or not path.is_file():
        raise RobustnessError(f"Trades file not found: {path}")

    profits = _load_profits(path)
    if not profits:
        return CandidateRobustness(
            run_id=run_id or path.stem,
            trades_path=str(path),
            trade_count=0,
            bootstrap_samples=0,
            seed=seed,
            profit_factor=_empty_stat(),
            net_profit=_empty_stat(),
            max_drawdown=_empty_stat(),
            notes=["Trades.csv had no parseable profit rows."],
        )

    observed = _trade_sequence_stats(profits)
    rng = random.Random(seed)
    n = len(profits)
    pf_samples: list[float] = []
    np_samples: list[float] = []
    dd_samples: list[float] = []
    for _ in range(samples):
        resampled = [profits[rng.randrange(n)] for _ in range(n)]
        stats = _trade_sequence_stats(resampled)
        if stats["profit_factor"] is not None:
            pf_samples.append(stats["profit_factor"])
        np_samples.append(stats["net_profit"])
        dd_samples.append(stats["max_drawdown"])

    notes: list[str] = []
    if n < 10:
        notes.append(
            f"Trade count is {n}; bootstrap distributions are wide. Treat the p-value as directional, not statistical."
        )

    return CandidateRobustness(
        run_id=run_id or path.stem,
        trades_path=str(path),
        trade_count=n,
        bootstrap_samples=samples,
        seed=seed,
        profit_factor=_stat_block(observed["profit_factor"], pf_samples, direction="ge"),
        net_profit=_stat_block(observed["net_profit"], np_samples, direction="ge"),
        max_drawdown=_stat_block(observed["max_drawdown"], dd_samples, direction="le"),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Stubs — require NT roundtrips, deferred
# ---------------------------------------------------------------------------

def walk_forward_validation(*args: Any, **kwargs: Any) -> None:
    """Roll multiple OOS windows back through history, re-optimize on each
    in-sample window, validate on the next OOS window, report the IS/OOS
    PF degradation. Requires dispatching new optimizer + Backtest
    templates through NinjaTrader.
    """
    raise NotImplementedError(
        "walk_forward_validation is not yet implemented. It requires "
        "dispatching multiple NT optimization runs per candidate; see "
        "docs/designs/optimizer_known_issues.md for the deferred design."
    )


def parameter_neighborhood_check(*args: Any, **kwargs: Any) -> None:
    """Deprecated re-export shim. The neighborhood check is implemented in
    ``ta_foundation.web.optimizer_neighborhood`` because it dispatches NT
    runs; use :func:`generate_neighborhood_templates` /
    :func:`trigger_neighborhood_run` / :func:`ingest_neighborhood_results`
    from that module, or the ``/api/optimizer/sessions/<id>/neighborhood/*``
    routes from the web UI.
    """
    raise RuntimeError(
        "parameter_neighborhood_check moved to ta_foundation.web.optimizer_neighborhood "
        "(generate / run / status / ingest). See docs/designs/optimizer_known_issues.md."
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _load_profits(trades_csv_path: Path) -> list[float]:
    parser = NinjaTraderTradesCsvParser()
    try:
        with open(trades_csv_path, encoding="utf-8") as h:
            header = h.readline()
    except OSError:
        return []
    if not parser.can_parse(trades_csv_path, header):
        # Fall back to a direct read — some exports have a BOM that
        # confuses the can_parse check on the first byte.
        try:
            df = pd.read_csv(trades_csv_path)
        except Exception:
            return []
        col = _find_column(df, ("profit", "Profit"))
        if col is None:
            return []
        return [_money_to_float(v) for v in df[col].tolist() if _money_to_float(v) is not None]
    artifact = parser.parse(trades_csv_path, trades_csv_path.stem)
    df = artifact.df
    if df is None or df.empty or "profit" not in df.columns:
        return []
    return [float(v) for v in df["profit"].dropna().tolist()]


def _find_column(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    cols = {c.strip(): c for c in df.columns}
    for n in names:
        if n in cols:
            return cols[n]
    return None


def _money_to_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    s = str(value).strip()
    if not s:
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


def _trade_sequence_stats(profits: list[float]) -> dict[str, float | None]:
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = sum(p for p in profits if p < 0)
    net_profit = gross_profit + gross_loss
    profit_factor: float | None = None
    if gross_loss < 0:
        profit_factor = gross_profit / abs(gross_loss)
    elif gross_profit > 0:
        profit_factor = float("inf")
    # Max drawdown from cumulative equity curve.
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
        "profit_factor": profit_factor,
        "net_profit": net_profit,
        "max_drawdown": max_dd,
    }


def _stat_block(observed: float | None, samples: list[float], *, direction: str) -> BootstrapStat:
    """direction='ge' (higher-is-better, e.g. PF/net) or 'le' (lower-is-better, e.g. DD)."""
    finite = [s for s in samples if s is not None and math.isfinite(s)]
    if not finite or observed is None:
        return _empty_stat() if observed is None else BootstrapStat(
            observed=_safe_float(observed),
            bootstrap_mean=None, bootstrap_median=None,
            bootstrap_p05=None, bootstrap_p95=None,
            p_at_or_above_observed=None,
        )
    finite_sorted = sorted(finite)
    n = len(finite_sorted)
    mean = sum(finite_sorted) / n
    median = _percentile(finite_sorted, 0.5)
    p05 = _percentile(finite_sorted, 0.05)
    p95 = _percentile(finite_sorted, 0.95)
    if observed is not None and math.isfinite(observed):
        if direction == "ge":
            count = sum(1 for s in finite_sorted if s >= observed)
        else:
            count = sum(1 for s in finite_sorted if s <= observed)
        p_value = count / n
    else:
        p_value = None
    return BootstrapStat(
        observed=_safe_float(observed),
        bootstrap_mean=mean,
        bootstrap_median=median,
        bootstrap_p05=p05,
        bootstrap_p95=p95,
        p_at_or_above_observed=p_value,
    )


def _empty_stat() -> BootstrapStat:
    return BootstrapStat(None, None, None, None, None, None)


def _safe_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    idx = q * (len(sorted_values) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_values[lo]
    frac = idx - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac
