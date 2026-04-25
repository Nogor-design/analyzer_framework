"""
End-of-day prediction runner.

Usage — with a config file (recommended):
    python -m ta_foundation.prediction.run_prediction --config prediction.yaml

Usage — with CLI flags:
    python -m ta_foundation.prediction.run_prediction \
        --instrument NQ \
        --contract H25 \
        --market-data /path/to/market_data \
        [--asof 2025-03-21] \
        [--calendar /path/to/forexfactory.csv] \
        [--output-dir .ta_artifacts/predictions] \
        [--model claude-opus-4-7] \
        [--dry-run]

CLI flags always override YAML values when both are supplied.

Set ANTHROPIC_API_KEY in your environment before running.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

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
        description="Run next-day market prediction via Claude",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--config", default=None, help="Path to prediction YAML config file")
    p.add_argument("--instrument", default=None, help="Instrument root, e.g. NQ")
    p.add_argument("--contract", default=None, help="Contract code, e.g. 06-26")
    p.add_argument("--market-data", default=None, help="Path to directory containing .Last.txt files")
    p.add_argument("--asof", default=None, help="Date to predict FROM (YYYY-MM-DD). Defaults to today.")
    p.add_argument("--calendar", default=None, help="Path to ForexFactory CSV (optional)")
    p.add_argument("--output-dir", default=None, help="Base directory for PredictionStore")
    p.add_argument("--model", default=None, help="Claude model ID")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="Use statistical_stub_agent instead of Claude (no API call)")
    p.add_argument("--n-similar", type=int, default=None, help="Number of historical analogues")
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
    if args.dry_run:
        cfg["dry_run"] = True
    if args.n_similar is not None:
        cfg["n_similar"] = args.n_similar

    cfg.setdefault("model", "claude-opus-4-7")
    cfg.setdefault("output_dir", ".ta_artifacts/predictions")
    cfg.setdefault("dry_run", False)
    cfg.setdefault("n_similar", 5)

    missing = [k for k in ("instrument", "contract", "market_data") if not cfg.get(k)]
    if missing:
        print(f"Missing required config: {', '.join(missing)}.", file=sys.stderr)
        sys.exit(1)

    return cfg


def _load_market_store(market_data_dir: Path, instrument: str, contract: str):
    from ta_foundation.prediction._market_loader import load_market_store
    return load_market_store(market_data_dir, instrument, contract)


def main() -> None:
    args = _parse_args()
    cfg = _resolve_config(args)

    from ta_foundation.analysis.economic_calendar import EconomicCalendar
    from ta_foundation.prediction.store import PredictionStore
    from ta_foundation.prediction.orchestrator import predict_next_day, statistical_stub_agent
    from ta_foundation.prediction.claude_agent import ClaudeMarketAgent

    market_data_dir = Path(cfg["market_data"])
    market = _load_market_store(market_data_dir, cfg["instrument"], cfg["contract"])

    if cfg.get("calendar"):
        calendar = EconomicCalendar.from_csv(Path(cfg["calendar"]))
        print(f"Loaded calendar: {len(calendar._events)} events", file=sys.stderr)
    else:
        calendar = EconomicCalendar.from_list([])
        print("No calendar configured — event risk will be 0.0", file=sys.stderr)

    store = PredictionStore(
        base_dir=Path(cfg["output_dir"]),
        instrument=cfg["instrument"],
        contract=cfg["contract"],
    )

    DENVER_TZ = "America/Denver"
    if cfg.get("asof"):
        asof = pd.Timestamp(cfg["asof"], tz=DENVER_TZ)
    else:
        asof = pd.Timestamp.now(tz=DENVER_TZ).normalize()

    print(f"Predicting: {cfg['instrument']} {cfg['contract']}  asof {asof.date()}", file=sys.stderr)

    if cfg["dry_run"]:
        agent_fn = statistical_stub_agent
        agent_id = "statistical_stub"
        print("Mode: statistical_stub (dry-run, no API call)", file=sys.stderr)
    else:
        agent = ClaudeMarketAgent(model=cfg["model"], agent_id=cfg["model"])
        agent_fn = agent
        agent_id = agent.agent_id
        print(f"Mode: ClaudeMarketAgent  model={cfg['model']}", file=sys.stderr)

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
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
