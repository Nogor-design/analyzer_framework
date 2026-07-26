"""
Multi-agent prediction runner + leaderboard.

Usage:
    python -m ta_foundation.prediction.run_multi_agent --config multi_agent.yaml

Each agent listed in the config runs on the same session context. After all
predictions are made, a side-by-side comparison table is printed. If --score-date
is provided, outcomes are measured from bars and a ranked leaderboard is printed.

Set ANTHROPIC_API_KEY in your environment if any agent uses Claude.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError:
        print("pyyaml required. Install with: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run multiple prediction agents and show a leaderboard",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--config", required=True, help="Path to multi_agent.yaml")
    p.add_argument("--asof", default=None, help="Override prediction date (YYYY-MM-DD)")
    p.add_argument(
        "--score-date", default=None,
        help="Measure + score a past target_date from bars and print leaderboard (YYYY-MM-DD)"
    )
    p.add_argument(
        "--leaderboard-only", action="store_true",
        help="Skip prediction step; only print the historical leaderboard from stored outcomes"
    )
    p.add_argument(
        "--backtest", default=None, metavar="DATE",
        help="Predict + score a past date in one step (YYYY-MM-DD). "
             "Agents see context up to EOD of the prior day, then predictions are immediately scored."
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _build_agent(spec: Dict[str, Any]):
    """Instantiate an agent from a YAML spec dict."""
    agent_type = spec.get("type", "stub").lower()

    if agent_type == "claude":
        from ta_foundation.prediction.claude_agent import ClaudeMarketAgent
        return ClaudeMarketAgent(
            model=spec.get("model", "claude-opus-4-7"),
            agent_id=spec.get("agent_id", spec.get("model", "claude-opus-4-7")),
        )

    if agent_type == "ollama":
        from ta_foundation.prediction.ollama_agent import OllamaMarketAgent
        return OllamaMarketAgent(
            model=spec.get("model", "llama3.1:70b"),
            base_url=spec.get("base_url", "http://localhost:11434"),
            agent_id=spec.get("agent_id"),
            temperature=float(spec.get("temperature", 0.2)),
            num_ctx=int(spec.get("num_ctx", 8192)),
        )

    if agent_type == "stub":
        from ta_foundation.prediction.orchestrator import statistical_stub_agent

        class _StubWrapper:
            def __init__(self, agent_id: str):
                self.agent_id = agent_id
            def __call__(self, ctx):
                return statistical_stub_agent(ctx)

        return _StubWrapper(agent_id=spec.get("agent_id", "statistical_stub"))

    raise ValueError(f"Unknown agent type: {agent_type!r}. Use 'claude', 'ollama', or 'stub'.")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_COL_W = 18


def _cell(val: Any, width: int = _COL_W) -> str:
    s = str(val) if val is not None else "-"
    return s[:width].ljust(width)


def _print_comparison(predictions: List[Any]) -> None:
    """Side-by-side comparison of raw prediction dicts keyed by agent_id."""
    if not predictions:
        return

    rows = [
        ("Agent",             lambda p: p.agent_id),
        ("Direction",         lambda p: p.trend_direction),
        ("Trend conf",        lambda p: f"{p.trend_confidence:.0%}"),
        ("Trending?",         lambda p: "yes" if p.is_trending else "no"),
        ("Chop conf",         lambda p: f"{p.chop_confidence:.0%}"),
        ("Pred high",         lambda p: f"{p.predicted_high:.2f}"),
        ("Pred low",          lambda p: f"{p.predicted_low:.2f}"),
        ("Breakout prob",     lambda p: f"{p.breakout_probability:.0%}"),
        ("Breakout dir",      lambda p: p.breakout_direction),
        ("Event risk",        lambda p: f"{p.event_risk_score:.2f}"),
        ("# Key levels",      lambda p: str(len(p.key_levels or []))),
    ]

    header = _cell("Field", 20) + "  " + "  ".join(_cell(p.agent_id) for p in predictions)
    print("\n" + "=" * len(header))
    print("PREDICTION COMPARISON")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for label, fn in rows:
        line = _cell(label, 20) + "  " + "  ".join(_cell(fn(p)) for p in predictions)
        print(line)
    print("=" * len(header))


def _print_leaderboard(
    agent_stats: List[Tuple[str, Any]],
    scored_outcomes: Optional[List[Tuple[str, Any]]] = None,
) -> None:
    """Print a ranked leaderboard from AgentStats objects."""
    print("\n" + "=" * 80)
    print("AGENT LEADERBOARD")
    print("=" * 80)

    if scored_outcomes:
        print("\nSession scores (this run):")
        print(f"  {'Agent':<30} {'Composite':>10} {'Trend':>8} {'Chop':>8} {'Levels':>8} {'Breakout':>10}")
        print("  " + "-" * 76)
        for agent_id, outcome in sorted(scored_outcomes, key=lambda x: -(x[1].composite_score or 0)):
            o = outcome
            print(
                f"  {agent_id:<30} "
                f"{o.composite_score:>10.3f} "
                f"{o.trend_score:>8.3f} "
                f"{o.chop_score:>8.3f} "
                f"{o.levels_score:>8.3f} "
                f"{o.breakout_score:>10.3f}"
            )

    if agent_stats:
        print("\nRolling performance (all history):")
        print(
            f"  {'Agent':<30} {'N':>5} {'Composite':>10} {'Trend%':>8} "
            f"{'Chop%':>8} {'Breakout%':>10} {'ECE':>7} {'Drift':>6}"
        )
        print("  " + "-" * 90)
        ranked = sorted(agent_stats, key=lambda x: -(x[1].mean_composite_score or 0))
        for agent_id, stats in ranked:
            drift = "⚠" if stats.drift_warning else ""
            n = stats.sample_count or 0
            print(
                f"  {agent_id:<30} "
                f"{n:>5} "
                f"{stats.mean_composite_score:>10.3f} "
                f"{stats.trend_accuracy:>8.1%} "
                f"{stats.chop_accuracy:>8.1%} "
                f"{stats.breakout_accuracy:>10.1%} "
                f"{stats.ece:>7.4f} "
                f"{drift:>6}"
            )
    print("=" * 80)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    cfg  = _load_yaml(Path(args.config))

    from ta_foundation.analysis.economic_calendar import EconomicCalendar
    from ta_foundation.prediction.store import PredictionStore
    from ta_foundation.prediction.orchestrator import predict_next_day, measure_and_learn
    from ta_foundation.prediction.calibrator import compute_agent_stats
    from ta_foundation.prediction._market_loader import load_market_store

    DENVER_TZ = "America/Denver"

    # ── Resolve shared config ───────────────────────────────────────────────
    instrument  = cfg["instrument"]
    contract    = cfg["contract"]
    output_dir  = Path(cfg.get("output_dir", ".ta_artifacts/predictions"))
    n_similar   = int(cfg.get("n_similar", 5))

    market_data_dir = Path(cfg["market_data"])
    load_instruments_cfg = cfg.get("load_instruments")
    allowed_instruments = (
        [(e["instrument"], str(e["contract"])) for e in load_instruments_cfg]
        if load_instruments_cfg
        else None  # defaults to primary instrument only
    )
    market = load_market_store(market_data_dir, instrument, contract, allowed_instruments)

    if cfg.get("calendar"):
        calendar = EconomicCalendar.from_csv(Path(cfg["calendar"]))
        print(f"Loaded calendar: {len(calendar._events)} events", file=sys.stderr)
    else:
        calendar = EconomicCalendar.from_list([])

    store = PredictionStore(base_dir=output_dir, instrument=instrument, contract=contract)

    # ── Agent list ─────────────────────────────────────────────────────────
    agent_specs = cfg.get("agents") or []
    if not agent_specs:
        print("No agents defined in config under 'agents:'", file=sys.stderr)
        sys.exit(1)

    agents = []
    for spec in agent_specs:
        try:
            agent = _build_agent(spec)
            agents.append(agent)
            print(f"  Agent: {agent.agent_id}", file=sys.stderr)
        except Exception as exc:
            print(f"  WARNING: Could not build agent from spec {spec}: {exc}", file=sys.stderr)

    # ── Leaderboard-only mode ───────────────────────────────────────────────
    if args.leaderboard_only:
        agent_stats = []
        for agent in agents:
            history = store.get_recent_history(n_days=90, require_scored=True)
            agent_history = [(p, o) for p, o in history if p.agent_id == agent.agent_id]
            stats = compute_agent_stats(agent_history, agent.agent_id)
            agent_stats.append((agent.agent_id, stats))
        _print_leaderboard(agent_stats)
        return

    # ── Prediction step ─────────────────────────────────────────────────────
    if not args.score_date:
        if args.asof:
            asof = pd.Timestamp(args.asof, tz=DENVER_TZ)
        else:
            asof = pd.Timestamp.now(tz=DENVER_TZ).normalize()

        print(f"\nRunning {len(agents)} agent(s) for {instrument} {contract} asof {asof.date()}\n",
              file=sys.stderr)

        predictions = []
        results = []
        for agent in agents:
            print(f"  → {agent.agent_id} ...", file=sys.stderr, end="", flush=True)
            try:
                pred = predict_next_day(
                    market=market,
                    calendar=calendar,
                    store=store,
                    instrument=instrument,
                    contract=contract,
                    asof=asof,
                    agent_fn=agent,
                    agent_id=agent.agent_id,
                    n_similar=n_similar,
                )
                predictions.append(pred)
                print(" done", file=sys.stderr)
            except Exception as exc:
                print(f" FAILED: {exc}", file=sys.stderr)

        _print_comparison(predictions)

        # JSON output
        output = [p.as_dict() for p in predictions]
        for d in output:
            d.pop("context_snapshot", None)
        print("\n" + json.dumps(output, indent=2))

    # ── Backtest a past date (predict + score in one step) ─────────────────
    if args.backtest:
        target_date = args.backtest
        prior_atr   = float(cfg.get("prior_atr", 0.0))

        bars = market.get_bars(instrument, contract, timeframe="1m")
        if bars is None or bars.empty:
            print(f"No bars available for backtest {target_date}", file=sys.stderr)
            sys.exit(1)

        ts       = pd.Timestamp(target_date, tz=DENVER_TZ)
        day_bars = bars[bars["dt"].dt.date == ts.date()]
        if day_bars.empty:
            available_dates = sorted(bars["dt"].dt.date.unique())
            nearby = [str(d) for d in available_dates if abs((pd.Timestamp(d, tz=DENVER_TZ) - ts.normalize()).days) <= 5]
            hint = (f"  Nearby dates with bars: {', '.join(nearby)}" if nearby
                    else f"  Last available date: {available_dates[-1]}" if available_dates else "")
            print(f"No bars found for {target_date} in market data.\n{hint}", file=sys.stderr)
            sys.exit(1)

        # Agents see context up to end-of-day of the prior calendar day
        asof = ts - pd.Timedelta(days=1)
        print(f"\nBacktest {target_date}: predicting with context asof {asof.date()}, "
              f"scoring against {len(day_bars)} actual bars\n", file=sys.stderr)

        predictions = []
        for agent in agents:
            print(f"  → {agent.agent_id} ...", file=sys.stderr, end="", flush=True)
            try:
                pred = predict_next_day(
                    market=market,
                    calendar=calendar,
                    store=store,
                    instrument=instrument,
                    contract=contract,
                    asof=asof,
                    agent_fn=agent,
                    agent_id=agent.agent_id,
                    n_similar=n_similar,
                )
                predictions.append(pred)
                print(" done", file=sys.stderr)
            except Exception as exc:
                print(f" FAILED: {exc}", file=sys.stderr)

        _print_comparison(predictions)

        outcomes = measure_and_learn(
            bars_next_day=day_bars,
            store=store,
            instrument=instrument,
            contract=contract,
            target_date=target_date,
            prior_atr=prior_atr,
        )

        scored_outcomes = [(o.agent_id, o) for o in outcomes if o.composite_score is not None]

        agent_stats = []
        for agent in agents:
            history = store.get_recent_history(n_days=90, require_scored=True)
            agent_history = [(p, o) for p, o in history if p.agent_id == agent.agent_id]
            stats = compute_agent_stats(agent_history, agent.agent_id)
            agent_stats.append((agent.agent_id, stats))

        _print_leaderboard(agent_stats, scored_outcomes=scored_outcomes)
        return

    # ── Score a past date ───────────────────────────────────────────────────
    if args.score_date:
        target_date = args.score_date
        prior_atr   = float(cfg.get("prior_atr", 0.0))

        bars = market.get_bars(instrument, contract, timeframe="1m")
        if bars is None or bars.empty:
            print(f"No bars available for scoring {target_date}", file=sys.stderr)
            sys.exit(1)

        # Filter to just the target day
        ts = pd.Timestamp(target_date, tz=DENVER_TZ)
        day_bars = bars[bars["dt"].dt.date == ts.date()]
        if day_bars.empty:
            weekend_note = ""
            if ts.weekday() >= 5:
                weekend_note = f" ({target_date} is a {'Saturday' if ts.weekday() == 5 else 'Sunday'} — no trading)"
            # Show dates that DO have bars so the user can pick the right one
            available_dates = sorted(bars["dt"].dt.date.unique())
            nearby = [str(d) for d in available_dates if abs((pd.Timestamp(d, tz=DENVER_TZ) - ts.normalize()).days) <= 5]
            hint = f"  Nearby dates with bars: {', '.join(nearby)}" if nearby else f"  Last available date: {available_dates[-1]}" if available_dates else ""
            print(
                f"No bars found for {target_date} in market data.{weekend_note}\n{hint}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"\nMeasuring outcomes for {target_date} ({len(day_bars)} bars)...", file=sys.stderr)
        outcomes = measure_and_learn(
            bars_next_day=day_bars,
            store=store,
            instrument=instrument,
            contract=contract,
            target_date=target_date,
            prior_atr=prior_atr,
        )

        scored_outcomes = [(o.agent_id, o) for o in outcomes if o.composite_score is not None]

        agent_stats = []
        for agent in agents:
            history = store.get_recent_history(n_days=90, require_scored=True)
            agent_history = [(p, o) for p, o in history if p.agent_id == agent.agent_id]
            stats = compute_agent_stats(agent_history, agent.agent_id)
            agent_stats.append((agent.agent_id, stats))

        _print_leaderboard(agent_stats, scored_outcomes=scored_outcomes)


if __name__ == "__main__":
    main()
