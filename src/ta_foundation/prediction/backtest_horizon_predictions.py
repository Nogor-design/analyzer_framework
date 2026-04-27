"""
Walk-forward replay for the horizon prediction system.

The backtest takes a HorizonBatchRunner (one or more agents) plus a
schedule of `(timeframe, horizon, asof)` specs and:

  1. Produces predictions via the runner.
  2. After each prediction, measures the realized outcome on the same
     bar series using `measure_horizon_outcome`.
  3. Scores the prediction via `score_horizon_prediction` and persists
     the outcome to the `HorizonPredictionStore`.

Walk-forward leakage is enforced two ways:

  - The agent only sees `bars_up_to(asof)` when picking analogues
    (already enforced inside `StatisticalProbabilityAgent` /
    `AnalogueProbabilityAgent`).
  - The outcome measurement reads only `bars[asof+1 : asof+1+horizon]`
    and never feeds those bars back into a re-prediction.

Calibration feedback is intentionally NOT included in this scoring pass
— that would leak future hit-rates into past scores. Reports compute
ECE separately from the persisted predictions/outcomes.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

from .horizon_batch import (
    BarLoader,
    HorizonBatchResult,
    HorizonBatchRunner,
    HorizonBatchSpec,
    asofs_from_bars,
    build_schedule,
    make_market_bar_loader,
    make_static_bar_loader,
)
from .horizon_models import CandleHorizonOutcome, CandleHorizonPrediction
from .horizon_outcome_measurer import measure_horizon_outcome
from .horizon_scorer import HorizonCompositeWeights, score_horizon_prediction
from .horizon_store import DuplicateHorizonOutcomeError, HorizonPredictionStore


# ---------------------------------------------------------------------------
# Config + summary
# ---------------------------------------------------------------------------

@dataclass
class HorizonBacktestConfig:
    weights: Optional[HorizonCompositeWeights] = None
    save_outcomes: bool = True
    skip_unmeasurable: bool = True
    score_abstentions: bool = False
    # When False, the outcome falls back to the prediction's `feature_snapshot["prior_atr"]`
    # if present, otherwise to the agent's default range/2 fallback in the measurer.
    require_prior_atr: bool = False


@dataclass
class HorizonBacktestSummary:
    n_specs: int = 0
    n_results: int = 0
    n_predictions: int = 0
    n_abstentions: int = 0
    n_errors: int = 0
    n_outcomes_measured: int = 0
    n_outcomes_skipped: int = 0
    n_outcomes_duplicate: int = 0
    mean_composite_score: float = 0.0
    by_agent: Dict[str, Dict[str, float]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "n_specs": self.n_specs,
            "n_results": self.n_results,
            "n_predictions": self.n_predictions,
            "n_abstentions": self.n_abstentions,
            "n_errors": self.n_errors,
            "n_outcomes_measured": self.n_outcomes_measured,
            "n_outcomes_skipped": self.n_outcomes_skipped,
            "n_outcomes_duplicate": self.n_outcomes_duplicate,
            "mean_composite_score": self.mean_composite_score,
            "by_agent": dict(self.by_agent),
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_horizon_backtest(
    runner: HorizonBatchRunner,
    specs: Iterable[HorizonBatchSpec],
    config: Optional[HorizonBacktestConfig] = None,
) -> HorizonBacktestSummary:
    """
    Run the runner over `specs`, then for every successful prediction
    measure + score + persist the outcome on the same bar series.

    Returns a `HorizonBacktestSummary` aggregating counts and the mean
    composite score over scored predictions.
    """
    cfg = config or HorizonBacktestConfig()
    summary = HorizonBacktestSummary()
    specs_list = list(specs)
    summary.n_specs = len(specs_list)

    results = runner.run(specs_list)
    summary.n_results = len(results)

    composite_sum = 0.0
    composite_n = 0

    # by-agent accumulators
    agent_acc: Dict[str, Dict[str, float]] = {}

    for result in results:
        pred = result.prediction
        if result.error:
            summary.n_errors += 1
            summary.errors.append(f"{result.agent_id}: {result.error}")
            _bump_agent(agent_acc, result.agent_id, "errors")
            continue
        if pred is None:
            summary.n_errors += 1
            _bump_agent(agent_acc, result.agent_id, "errors")
            continue

        summary.n_predictions += 1
        _bump_agent(agent_acc, pred.agent_id, "predictions")
        if pred.abstain:
            summary.n_abstentions += 1
            _bump_agent(agent_acc, pred.agent_id, "abstentions")
            if not cfg.score_abstentions:
                continue

        bars = runner._bars_cache.get(  # noqa: SLF001 — intentional, bars cached for reuse
            (result.spec.instrument, result.spec.contract, result.spec.timeframe)
        )
        if bars is None or len(bars) == 0 or result.asof_idx is None:
            summary.n_outcomes_skipped += 1
            _bump_agent(agent_acc, pred.agent_id, "outcomes_skipped")
            continue

        # Walk-forward measurability check
        end_idx = result.asof_idx + result.spec.horizon
        if end_idx >= len(bars):
            if cfg.skip_unmeasurable:
                summary.n_outcomes_skipped += 1
                _bump_agent(agent_acc, pred.agent_id, "outcomes_skipped")
                continue

        prior_atr = float((pred.feature_snapshot or {}).get("prior_atr") or 0.0)
        if prior_atr <= 0.0 and cfg.require_prior_atr:
            summary.n_outcomes_skipped += 1
            _bump_agent(agent_acc, pred.agent_id, "outcomes_skipped")
            continue

        try:
            outcome = measure_horizon_outcome(
                bars=bars,
                asof_idx=int(result.asof_idx),
                horizon_candles=int(result.spec.horizon),
                prior_atr=prior_atr,
                upside_threshold_points=float(pred.upside_threshold_points),
                downside_threshold_points=float(pred.downside_threshold_points),
                prediction=pred,
            )
        except Exception as exc:  # noqa: BLE001
            summary.n_errors += 1
            summary.errors.append(
                f"{pred.agent_id} measure_outcome failed: {type(exc).__name__}: {exc}"
            )
            _bump_agent(agent_acc, pred.agent_id, "errors")
            continue

        try:
            score_horizon_prediction(pred, outcome, weights=cfg.weights)
        except Exception as exc:  # noqa: BLE001
            summary.n_errors += 1
            summary.errors.append(
                f"{pred.agent_id} score_prediction failed: {type(exc).__name__}: {exc}"
            )
            _bump_agent(agent_acc, pred.agent_id, "errors")
            continue

        if cfg.save_outcomes and runner.store is not None:
            try:
                runner.store.save_outcome(outcome)
            except DuplicateHorizonOutcomeError:
                summary.n_outcomes_duplicate += 1
                _bump_agent(agent_acc, pred.agent_id, "outcomes_duplicate")
            except Exception as exc:  # noqa: BLE001
                summary.n_errors += 1
                summary.errors.append(
                    f"{pred.agent_id} save_outcome failed: {type(exc).__name__}: {exc}"
                )
                _bump_agent(agent_acc, pred.agent_id, "errors")
                continue

        summary.n_outcomes_measured += 1
        _bump_agent(agent_acc, pred.agent_id, "outcomes_measured")
        if not pred.abstain:
            composite_sum += outcome.composite_score
            composite_n += 1
            _add_agent_score(agent_acc, pred.agent_id, outcome.composite_score)

    if composite_n > 0:
        summary.mean_composite_score = composite_sum / composite_n

    # finalize per-agent mean composite
    for agent_id, acc in agent_acc.items():
        n = acc.get("composite_n", 0.0)
        if n > 0:
            acc["mean_composite_score"] = acc.get("composite_sum", 0.0) / n
        else:
            acc["mean_composite_score"] = 0.0
        # drop running totals from output
        acc.pop("composite_sum", None)
        acc.pop("composite_n", None)
    summary.by_agent = agent_acc

    return summary


def _bump_agent(acc: Dict[str, Dict[str, float]], agent_id: str, key: str) -> None:
    a = acc.setdefault(agent_id, {})
    a[key] = a.get(key, 0.0) + 1.0


def _add_agent_score(
    acc: Dict[str, Dict[str, float]],
    agent_id: str,
    score: float,
) -> None:
    a = acc.setdefault(agent_id, {})
    a["composite_sum"] = a.get("composite_sum", 0.0) + float(score)
    a["composite_n"] = a.get("composite_n", 0.0) + 1.0


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def run_walk_forward_replay(
    *,
    agents: Sequence,
    bar_loader: BarLoader,
    instrument: str,
    contract: str,
    timeframes: Sequence[str],
    horizons: Sequence[int],
    asof_warmup: int = 200,
    asof_stride: int = 1,
    asof_start: Optional[pd.Timestamp] = None,
    asof_end: Optional[pd.Timestamp] = None,
    store: Optional[HorizonPredictionStore] = None,
    config: Optional[HorizonBacktestConfig] = None,
) -> HorizonBacktestSummary:
    """
    High-level convenience: build a (timeframes × horizons × asofs) schedule
    from the loaded bars and run the backtest end-to-end.

    The asof grid is derived from the first timeframe's bar series. When
    you backtest several timeframes simultaneously each timeframe gets its
    own resampled asof timestamps via the runner's loader cache.
    """
    runner = HorizonBatchRunner(
        agents=agents,
        bar_loader=bar_loader,
        store=store,
        save_predictions=True,
    )

    all_specs: List[HorizonBatchSpec] = []
    for tf in timeframes:
        bars = runner._load_bars(instrument, contract, tf)  # noqa: SLF001
        if bars is None or len(bars) == 0:
            continue
        asofs = asofs_from_bars(
            bars,
            start=asof_start,
            end=asof_end,
            stride=asof_stride,
            warmup=asof_warmup,
        )
        all_specs.extend(build_schedule(
            instrument=instrument,
            contract=contract,
            timeframes=[tf],
            horizons=horizons,
            asofs=asofs,
        ))

    return run_horizon_backtest(runner, all_specs, config=config)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_default_agents() -> List:
    """Build statistical + analogue + specialists. Lazy import to keep
    `prediction` import-cheap when the CLI is not in use."""
    from .analogue_probability_agent import AnalogueProbabilityAgent
    from .horizon_specialists import (
        make_regime_specialist_agent,
        make_session_specialist_agent,
    )
    from .statistical_probability_agent import StatisticalProbabilityAgent
    return [
        StatisticalProbabilityAgent(),
        AnalogueProbabilityAgent(),
        make_regime_specialist_agent(),
        make_session_specialist_agent(),
    ]


def _load_minute_bars_into_store(
    minute_bars_file: str,
):
    """
    Parse one NinjaTrader minute-bar file (`NQ 03-26.Last.txt`) and return
    a populated `MarketDataStore`. The file must already exist.
    """
    from pathlib import Path as _Path

    from ..marketdata.store import MarketDataStore
    from ..parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser

    path = _Path(minute_bars_file)
    if not path.exists():
        raise FileNotFoundError(f"minute bars file not found: {path}")

    parser = MinuteBarsLastTxtParser()
    if not parser.can_parse(path, ""):
        raise ValueError(
            f"file does not match the NinjaTrader minute-bars naming convention: {path.name}"
        )

    artifact = parser.parse(path, run_id=None)
    store = MarketDataStore()
    if not store.ingest_artifact(artifact):
        raise RuntimeError(
            f"MarketDataStore failed to ingest artifact from {path}; "
            f"warnings={artifact.warnings[:3]}"
        )
    store.finalize()
    return store, artifact.summary.get("instrument", ""), artifact.summary.get("contract", "")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backtest_horizon_predictions",
        description="Walk-forward replay of horizon agents on stored market data.",
    )
    p.add_argument("--minute-bars-file", required=True,
                   help="Path to a NinjaTrader minute-bars export "
                        "(e.g., 'C:/data/NQ 03-26.Last.txt')")
    p.add_argument("--instrument",
                   help="Override instrument root (defaults to filename parse)")
    p.add_argument("--contract",
                   help="Override contract (defaults to filename parse)")
    p.add_argument("--timeframes", default="5m,15m",
                   help="Comma-separated list (default: 5m,15m)")
    p.add_argument("--horizons", default="3,5",
                   help="Comma-separated list of horizon_candles values (default: 3,5)")
    p.add_argument("--store-dir", required=True,
                   help="Directory to write horizon_predictions.jsonl + horizon_outcomes.jsonl")
    p.add_argument("--asof-warmup", type=int, default=200)
    p.add_argument("--asof-stride", type=int, default=10)
    p.add_argument("--asof-start", default=None,
                   help="ISO timestamp (tz-aware) for the earliest asof to predict")
    p.add_argument("--asof-end", default=None,
                   help="ISO timestamp (tz-aware) for the latest asof to predict")
    p.add_argument("--config", default=None,
                   help="Optional path to a horizon prediction.yaml (Phase 5)")
    p.add_argument("--print-report", action="store_true",
                   help="After backtesting, print the full report bundle to stdout")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        store_market, parsed_instrument, parsed_contract = _load_minute_bars_into_store(
            args.minute_bars_file
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"market data load failed: {exc}", file=sys.stderr)
        return 2

    instrument = args.instrument or parsed_instrument
    contract = args.contract or parsed_contract
    if not instrument or not contract:
        print(
            "instrument/contract not set; pass --instrument / --contract or use a "
            "filename matching the NinjaTrader convention.",
            file=sys.stderr,
        )
        return 2

    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    asof_start = pd.Timestamp(args.asof_start) if args.asof_start else None
    asof_end = pd.Timestamp(args.asof_end) if args.asof_end else None

    horizon_store = HorizonPredictionStore(args.store_dir, instrument, contract)
    bar_loader = make_market_bar_loader(store_market)

    summary = run_walk_forward_replay(
        agents=_build_default_agents(),
        bar_loader=bar_loader,
        instrument=instrument,
        contract=contract,
        timeframes=timeframes,
        horizons=horizons,
        asof_warmup=args.asof_warmup,
        asof_stride=args.asof_stride,
        asof_start=asof_start,
        asof_end=asof_end,
        store=horizon_store,
        config=HorizonBacktestConfig(),
    )

    print("Backtest summary:")
    for k, v in summary.as_dict().items():
        if k in ("by_agent", "errors"):
            continue
        print(f"  {k}: {v}")
    if summary.by_agent:
        print("  by_agent:")
        for agent_id, acc in summary.by_agent.items():
            stats_str = ", ".join(f"{kk}={vv}" for kk, vv in sorted(acc.items()))
            print(f"    {agent_id}: {stats_str}")
    if summary.errors:
        print(f"  first 5 errors: {summary.errors[:5]}")

    if args.print_report:
        from .horizon_reports import build_full_report, format_full_report
        bundle = build_full_report(horizon_store)
        print()
        print(format_full_report(bundle))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
