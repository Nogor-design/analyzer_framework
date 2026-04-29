"""
End-of-day prediction runner.

Two modes are supported via `mode:` in the YAML or `--mode` on the CLI:

  - `horizon`  — runs the calibrated multi-agent horizon system
                 (statistical + analogue + regime/session specialists +
                 ensemble), then applies the deployment pipeline
                 (abstention policy → tradable-zone verdict). This is
                 the recommended path. Persists to a HorizonPredictionStore.
  - `daily`    — runs the legacy DailyPrediction flow (statistical_stub
                 or Claude). Kept for back-compat.
  - `both`     — runs both, in that order.

Usage — with a config file (recommended):
    python -m ta_foundation.prediction.run_prediction --config prediction.yaml

Usage — with CLI flags:
    python -m ta_foundation.prediction.run_prediction \\
        --instrument NQ \\
        --contract H25 \\
        --market-data /path/to/market_data \\
        [--mode horizon|daily|both] \\
        [--horizon-timeframe 5m] [--horizon-candles 3] \\
        [--asof 2025-03-21] \\
        [--calendar /path/to/forexfactory.csv] \\
        [--output-dir .ta_artifacts/predictions] \\
        [--model claude-opus-4-7] \\
        [--dry-run] [--load-ticks]

CLI flags always override YAML values when both are supplied.

Set ANTHROPIC_API_KEY in your environment before running (only needed when
mode includes `daily` and `dry_run` is false).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError:
        print("pyyaml is required to use --config. Install with: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run next-day market prediction (horizon or legacy daily)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--config", default=None, help="Path to prediction YAML config file")
    p.add_argument("--instrument", default=None, help="Instrument root, e.g. NQ")
    p.add_argument("--contract", default=None, help="Contract code, e.g. 06-26")
    p.add_argument("--market-data", default=None, help="Path to directory containing .Last.txt files")
    p.add_argument("--asof", default=None, help="Date to predict FROM (YYYY-MM-DD). Defaults to today.")
    p.add_argument("--calendar", default=None, help="Path to ForexFactory CSV (optional)")
    p.add_argument("--output-dir", default=None, help="Base directory for prediction stores")
    p.add_argument("--model", default=None, help="Claude model ID (daily mode only)")
    p.add_argument("--mode", default=None, choices=["horizon", "daily", "both"],
                   help="Prediction mode (default: horizon)")
    p.add_argument("--horizon-timeframe", default=None, help="Timeframe for horizon mode (e.g. 5m)")
    p.add_argument("--horizon-candles", type=int, default=None, help="Candles ahead for horizon mode")
    p.add_argument("--load-ticks", action="store_true", default=False,
                   help="Also parse Tick.Last.txt files (slow; not used by daily/horizon agents)")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="Daily mode: use statistical_stub_agent (no API call)")
    p.add_argument("--n-similar", type=int, default=None, help="Daily mode: number of historical analogues")
    p.add_argument("--no-catchup", dest="catchup", action="store_false", default=None,
                   help="Skip auto-scoring of past predictions whose outcomes are now measurable")
    return p.parse_args()


def _resolve_config(args: argparse.Namespace) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if args.config:
        cfg = _load_yaml(Path(args.config))

    if args.instrument:
        cfg["instrument"] = args.instrument
    if args.contract:
        cfg["contract"] = args.contract
    if args.market_data:
        cfg["market_data"] = args.market_data
    if args.asof:
        cfg["asof"] = args.asof
    if args.calendar:
        cfg["calendar"] = args.calendar
    if args.output_dir:
        cfg["output_dir"] = args.output_dir
    if args.model:
        cfg["model"] = args.model
    if args.mode:
        cfg["mode"] = args.mode
    if args.horizon_timeframe:
        cfg["horizon_timeframe"] = args.horizon_timeframe
    if args.horizon_candles is not None:
        cfg["horizon_candles"] = args.horizon_candles
    if args.load_ticks:
        cfg["load_ticks"] = True
    if args.dry_run:
        cfg["dry_run"] = True
    if args.n_similar is not None:
        cfg["n_similar"] = args.n_similar
    if args.catchup is False:
        cfg["catchup"] = False

    cfg.setdefault("model", "claude-opus-4-7")
    cfg.setdefault("output_dir", ".ta_artifacts/predictions")
    cfg.setdefault("dry_run", False)
    cfg.setdefault("n_similar", 5)
    cfg.setdefault("mode", "horizon")
    cfg.setdefault("horizon_timeframe", "5m")
    cfg.setdefault("horizon_candles", 3)
    cfg.setdefault("load_ticks", False)
    cfg.setdefault("catchup", True)

    missing = [k for k in ("instrument", "contract", "market_data") if not cfg.get(k)]
    if missing:
        print(f"Missing required config: {', '.join(missing)}.", file=sys.stderr)
        sys.exit(1)

    if cfg["mode"] not in ("horizon", "daily", "both"):
        print(f"invalid mode: {cfg['mode']!r} (expected horizon|daily|both)", file=sys.stderr)
        sys.exit(1)

    return cfg


def _load_market_store(
    market_data_dir: Path,
    instrument: str,
    contract: str,
    *,
    load_ticks: bool,
):
    from ta_foundation.prediction._market_loader import load_market_store
    return load_market_store(
        market_data_dir,
        instrument,
        contract,
        load_ticks=load_ticks,
    )


# ---------------------------------------------------------------------------
# Catch-up scoring — close the loop on past predictions
# ---------------------------------------------------------------------------

DENVER_TZ = "America/Denver"


def _catchup_daily_outcomes(
    market,
    store,
    instrument: str,
    contract: str,
    asof: pd.Timestamp,  # noqa: ARG001 — kept for callers; measurability uses bars
) -> Dict[str, int]:
    """
    For every persisted DailyPrediction whose target session has fully closed
    in the loaded bars and that has no recorded outcome, measure + score and
    persist.

    "Fully closed" means the target_date is strictly earlier than the latest
    bar's date — i.e., we have bars from after the session ended, so the
    measurement window is complete. Live runs (asof = today) automatically
    only see truly past sessions; back-fill runs (asof in the past) catch up
    every session through the latest bar.
    """
    from ta_foundation.prediction.orchestrator import measure_and_learn

    summary = {
        "scored": 0,
        "skipped_no_bars": 0,
        "skipped_other_instrument": 0,
        "skipped_session_open": 0,
        "already_scored": 0,
        "errors": 0,
    }

    minute_bars = market.get_bars(instrument, contract, "1m")
    if minute_bars is None or minute_bars.empty:
        print("  catchup-daily: no minute bars loaded; skipping", file=sys.stderr)
        return summary

    minute_bars = minute_bars.sort_values("dt").reset_index(drop=True)
    latest_bar_date = minute_bars["dt"].iloc[-1].tz_convert(DENVER_TZ).date()

    # Distinct unscored target_dates whose session has closed in the loaded bars.
    unscored_dates: List[str] = []
    seen: set = set()
    for pred in store.get_all_predictions():
        if pred.instrument != instrument or pred.contract != contract:
            summary["skipped_other_instrument"] += 1
            continue
        if pred.target_date in seen:
            continue
        try:
            tdate = pd.Timestamp(pred.target_date).date()
        except (TypeError, ValueError):
            continue
        if tdate >= latest_bar_date:
            # The session is the latest day in our bars or later — assume it
            # may still be open. Skip until we have data past it.
            summary["skipped_session_open"] += 1
            continue
        if store.get_outcome(pred.prediction_id) is not None:
            summary["already_scored"] += 1
            continue
        seen.add(pred.target_date)
        unscored_dates.append(pred.target_date)

    if not unscored_dates:
        return summary

    for tdate_str in sorted(unscored_dates):
        day_start = pd.Timestamp(f"{tdate_str} 00:00:00", tz=DENVER_TZ)
        day_end = day_start + pd.Timedelta(days=1)
        day_bars = minute_bars[(minute_bars["dt"] >= day_start) & (minute_bars["dt"] < day_end)]

        if day_bars.empty:
            summary["skipped_no_bars"] += 1
            continue

        prior_atr = _prior_atr_from_bars(minute_bars, day_start, atr_period=14)

        # `measure_and_learn` returns one outcome per prediction for this date
        # — both newly-created and pre-existing. To report a true "newly
        # scored" count we diff outcome counts before / after.
        before_ids = {o.prediction_id for o in store.get_all_outcomes()}
        try:
            measure_and_learn(
                bars_next_day=day_bars,
                store=store,
                instrument=instrument,
                contract=contract,
                target_date=tdate_str,
                prior_atr=prior_atr,
            )
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(f"  catchup-daily: {tdate_str} failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            summary["errors"] += 1
            continue
        after_ids = {o.prediction_id for o in store.get_all_outcomes()}
        summary["scored"] += len(after_ids - before_ids)

    return summary


def _prior_atr_from_bars(
    minute_bars: pd.DataFrame,
    target_day_start: pd.Timestamp,
    *,
    atr_period: int = 14,
) -> float:
    """
    Compute a prior_atr using the `atr_period` daily bars immediately before
    `target_day_start`. Returns 0.0 when insufficient history (the measurer
    will fall back to range/2).
    """
    prior_minutes = minute_bars[minute_bars["dt"] < target_day_start]
    if prior_minutes.empty:
        return 0.0
    daily = prior_minutes.copy()
    daily["date"] = daily["dt"].dt.tz_convert(DENVER_TZ).dt.date
    grouped = daily.groupby("date").agg(
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    if len(grouped) < 2:
        return 0.0
    prev_close = grouped["close"].shift(1)
    tr = pd.concat([
        grouped["high"] - grouped["low"],
        (grouped["high"] - prev_close).abs(),
        (grouped["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    tail = tr.dropna().tail(atr_period)
    if tail.empty:
        return 0.0
    return float(tail.mean())


def _catchup_horizon_outcomes(
    market,
    horizon_store,
    instrument: str,
    contract: str,
) -> Dict[str, int]:
    """
    For every persisted CandleHorizonPrediction without a recorded outcome,
    resolve `asof_idx` on the bar series for its timeframe and — if the
    horizon end is within the loaded bars — measure, score, and persist.

    Abstaining predictions are skipped (no edge to score against).
    """
    from ta_foundation.prediction.horizon_batch import resolve_asof_idx
    from ta_foundation.prediction.horizon_outcome_measurer import measure_horizon_outcome
    from ta_foundation.prediction.horizon_scorer import score_horizon_prediction
    from ta_foundation.prediction.horizon_store import DuplicateHorizonOutcomeError

    summary = {
        "scored": 0,
        "skipped_no_bars": 0,
        "skipped_other_instrument": 0,
        "skipped_unmeasurable": 0,
        "skipped_abstain": 0,
        "already_scored": 0,
        "errors": 0,
    }

    bars_cache: Dict[str, Optional[pd.DataFrame]] = {}

    def _bars_for(tf: str) -> Optional[pd.DataFrame]:
        if tf in bars_cache:
            return bars_cache[tf]
        b = market.get_bars(instrument, contract, tf)
        if b is not None and len(b) > 0 and "dt" in b.columns:
            b = b.sort_values("dt").reset_index(drop=True)
        bars_cache[tf] = b
        return b

    for pred in horizon_store.get_all_predictions():
        if pred.instrument != instrument or pred.contract != contract:
            summary["skipped_other_instrument"] += 1
            continue
        if pred.abstain:
            summary["skipped_abstain"] += 1
            continue
        if horizon_store.get_outcome(pred.prediction_id) is not None:
            summary["already_scored"] += 1
            continue

        bars = _bars_for(pred.timeframe)
        if bars is None or len(bars) == 0:
            summary["skipped_no_bars"] += 1
            continue

        try:
            asof_ts = pd.Timestamp(pred.asof_timestamp)
            asof_idx = resolve_asof_idx(bars, asof_ts)
        except (TypeError, ValueError):
            summary["errors"] += 1
            continue

        if asof_idx + pred.horizon_candles >= len(bars):
            # Horizon hasn't fully completed yet — nothing to measure.
            summary["skipped_unmeasurable"] += 1
            continue

        prior_atr = float((pred.feature_snapshot or {}).get("prior_atr") or 0.0)

        try:
            outcome = measure_horizon_outcome(
                bars=bars,
                asof_idx=int(asof_idx),
                horizon_candles=int(pred.horizon_candles),
                prior_atr=prior_atr,
                upside_threshold_points=float(pred.upside_threshold_points),
                downside_threshold_points=float(pred.downside_threshold_points),
                prediction=pred,
            )
            score_horizon_prediction(pred, outcome)
            horizon_store.save_outcome(outcome)
            summary["scored"] += 1
        except DuplicateHorizonOutcomeError:
            summary["already_scored"] += 1
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(
                f"  catchup-horizon: {pred.prediction_id[:8]}.. ({pred.timeframe}/h{pred.horizon_candles}) "
                f"failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            summary["errors"] += 1

    return summary


# ---------------------------------------------------------------------------
# Horizon mode
# ---------------------------------------------------------------------------

def _run_horizon_mode(
    cfg: Dict[str, Any],
    market,
    asof: pd.Timestamp,
) -> Dict[str, Any]:
    """
    Build the four default agents + ensemble, run a single-asof prediction,
    apply the deployment pipeline (abstention + tradable zone), and persist
    via HorizonPredictionStore.

    Returns a JSON-safe dict with each member's prediction plus the ensemble
    pipeline result.
    """
    from ta_foundation.prediction.analogue_probability_agent import AnalogueProbabilityAgent
    from ta_foundation.prediction.horizon_batch import (
        HorizonBatchRunner,
        HorizonBatchSpec,
        make_market_bar_loader,
    )
    from ta_foundation.prediction.horizon_config import HorizonConfig
    from ta_foundation.prediction.horizon_ensemble import EnsembleHorizonAgent
    from ta_foundation.prediction.horizon_specialists import (
        make_regime_specialist_agent,
        make_session_specialist_agent,
    )
    from ta_foundation.prediction.horizon_store import HorizonPredictionStore
    from ta_foundation.prediction.statistical_probability_agent import StatisticalProbabilityAgent

    members = [
        StatisticalProbabilityAgent(),
        AnalogueProbabilityAgent(),
        make_regime_specialist_agent(),
        make_session_specialist_agent(),
    ]
    ensemble = EnsembleHorizonAgent(members=members)
    agents = [*members, ensemble]

    # Read the `horizon:` block from the parsed YAML directly. Falls through
    # to dataclass defaults when the section is absent.
    horizon_pipeline_cfg = HorizonConfig.from_dict(cfg.get("horizon") or {})

    horizon_store_dir = Path(cfg["output_dir"]) / "horizon"
    horizon_store = HorizonPredictionStore(
        base_dir=horizon_store_dir,
        instrument=cfg["instrument"],
        contract=cfg["contract"],
    )

    runner = HorizonBatchRunner(
        agents=agents,
        bar_loader=make_market_bar_loader(market),
        store=horizon_store,
        save_predictions=True,
    )

    timeframe = str(cfg["horizon_timeframe"])
    horizon_candles = int(cfg["horizon_candles"])

    spec = HorizonBatchSpec(
        instrument=cfg["instrument"],
        contract=cfg["contract"],
        timeframe=timeframe,
        horizon=horizon_candles,
        asof=asof,
    )
    print(
        f"Mode: horizon  timeframe={timeframe}  horizon={horizon_candles} candles  "
        f"members={[m.agent_id for m in agents]}",
        file=sys.stderr,
    )

    results = runner.run([spec])

    members_out: List[Dict[str, Any]] = []
    ensemble_pred = None
    ensemble_agent_id = ensemble.agent_id
    for r in results:
        if r.error and r.prediction is None:
            members_out.append({
                "agent_id": r.agent_id,
                "error": r.error,
            })
            continue
        if r.prediction is None:
            continue
        if r.agent_id == ensemble_agent_id:
            ensemble_pred = r.prediction
        members_out.append({
            "agent_id": r.agent_id,
            "prediction": r.prediction.as_dict(),
            "error": r.error,
        })

    pipeline_out: Optional[Dict[str, Any]] = None
    if ensemble_pred is not None:
        pipeline_result = horizon_pipeline_cfg.apply(ensemble_pred)
        pipeline_out = {
            "policy_changed": pipeline_result.policy_changed,
            "prediction": pipeline_result.prediction.as_dict(),
            "verdict": pipeline_result.verdict.as_dict(),
        }

    return {
        "mode": "horizon",
        "instrument": cfg["instrument"],
        "contract": cfg["contract"],
        "timeframe": timeframe,
        "horizon_candles": horizon_candles,
        "asof": asof.isoformat(),
        "horizon_config": horizon_pipeline_cfg.as_dict(),
        "members": members_out,
        "ensemble_pipeline": pipeline_out,
    }


# ---------------------------------------------------------------------------
# Daily mode (legacy)
# ---------------------------------------------------------------------------

def _run_daily_mode(
    cfg: Dict[str, Any],
    market,
    calendar,
    asof: pd.Timestamp,
) -> Dict[str, Any]:
    from ta_foundation.prediction.claude_agent import ClaudeMarketAgent
    from ta_foundation.prediction.orchestrator import (
        predict_next_day,
        statistical_stub_agent,
    )
    from ta_foundation.prediction.store import PredictionStore

    store = PredictionStore(
        base_dir=Path(cfg["output_dir"]),
        instrument=cfg["instrument"],
        contract=cfg["contract"],
    )

    if cfg["dry_run"]:
        agent_fn = statistical_stub_agent
        agent_id = "statistical_stub"
        print(
            "Mode: daily/statistical_stub (dry-run, no API call) — note: this "
            "agent is uncalibrated; prefer mode=horizon for production use.",
            file=sys.stderr,
        )
    else:
        agent = ClaudeMarketAgent(model=cfg["model"], agent_id=cfg["model"])
        agent_fn = agent
        agent_id = agent.agent_id
        print(f"Mode: daily/ClaudeMarketAgent  model={cfg['model']}", file=sys.stderr)

    prediction = predict_next_day(
        market=market,
        calendar=calendar,
        store=store,
        instrument=cfg["instrument"],
        contract=cfg["contract"],
        asof=asof,
        agent_fn=agent_fn,
        agent_id=agent_id,
        n_similar=cfg["n_similar"],
    )

    result = prediction.as_dict()
    result.pop("context_snapshot", None)
    return {"mode": "daily", "prediction": result}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    cfg = _resolve_config(args)

    from ta_foundation.analysis.economic_calendar import EconomicCalendar

    market_data_dir = Path(cfg["market_data"])
    market = _load_market_store(
        market_data_dir,
        cfg["instrument"],
        cfg["contract"],
        load_ticks=bool(cfg["load_ticks"]),
    )

    needs_calendar = cfg["mode"] in ("daily", "both")
    if needs_calendar and cfg.get("calendar"):
        calendar = EconomicCalendar.from_csv(Path(cfg["calendar"]))
        print(f"Loaded calendar: {len(calendar._events)} events", file=sys.stderr)
    elif needs_calendar:
        calendar = EconomicCalendar.from_list([])
        print(
            "WARNING: daily mode requested but no calendar configured — "
            "event_risk_score will be 0.0",
            file=sys.stderr,
        )
    else:
        calendar = None

    if cfg.get("asof"):
        asof = pd.Timestamp(cfg["asof"], tz=DENVER_TZ)
    else:
        asof = pd.Timestamp.now(tz=DENVER_TZ).normalize()

    print(
        f"Predicting: {cfg['instrument']} {cfg['contract']}  "
        f"asof {asof.date()}  mode={cfg['mode']}  catchup={bool(cfg['catchup'])}",
        file=sys.stderr,
    )

    output: Dict[str, Any] = {}

    if cfg["mode"] in ("horizon", "both"):
        horizon_out = _run_horizon_mode(cfg, market, asof)
        if cfg["catchup"]:
            from ta_foundation.prediction.horizon_store import HorizonPredictionStore
            horizon_store = HorizonPredictionStore(
                base_dir=Path(cfg["output_dir"]) / "horizon",
                instrument=cfg["instrument"],
                contract=cfg["contract"],
            )
            cu = _catchup_horizon_outcomes(
                market=market,
                horizon_store=horizon_store,
                instrument=cfg["instrument"],
                contract=cfg["contract"],
            )
            print(
                f"Catchup (horizon): scored={cu['scored']}  already_scored={cu['already_scored']}  "
                f"unmeasurable={cu['skipped_unmeasurable']}  abstain={cu['skipped_abstain']}  "
                f"errors={cu['errors']}",
                file=sys.stderr,
            )
            horizon_out["catchup"] = cu
        output["horizon"] = horizon_out

    if cfg["mode"] in ("daily", "both"):
        daily_out = _run_daily_mode(cfg, market, calendar, asof)
        if cfg["catchup"]:
            from ta_foundation.prediction.store import PredictionStore
            daily_store = PredictionStore(
                base_dir=Path(cfg["output_dir"]),
                instrument=cfg["instrument"],
                contract=cfg["contract"],
            )
            cu = _catchup_daily_outcomes(
                market=market,
                store=daily_store,
                instrument=cfg["instrument"],
                contract=cfg["contract"],
                asof=asof,
            )
            print(
                f"Catchup (daily): scored={cu['scored']}  already_scored={cu['already_scored']}  "
                f"session_open={cu['skipped_session_open']}  no_bars={cu['skipped_no_bars']}  "
                f"errors={cu['errors']}",
                file=sys.stderr,
            )
            daily_out["catchup"] = cu
        output["daily"] = daily_out

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
