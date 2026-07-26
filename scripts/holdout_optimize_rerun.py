"""Holdout OOS re-run via the OPTIMIZE path (honors past Start/End dates).

Root cause of the earlier holdout failure (2026-06-13): a fresh Strategy Analyzer
tab created by the batch AddOn defaults to "Days to load" time-frame mode and
ignores the template's From/To. The Days-vs-Start/End selector is tab UI state NT
does NOT serialize into templates, so there's no template tag to flip. BUT the
OPTIMIZER uses From/To as its optimization period regardless of the tab selector
(confirmed by Eric: optimizing a past date range backtests that window correctly).

So this rebuilds each finished session's 124 final BACKTEST templates as 1-combo
OPTIMIZE templates: every strategy param keeps its pinned <Strategy> value, and we
add a single OptimizationParameter (FastPeriod, Min==Max==its value) so NT runs
exactly one combination over the requested window. The AddOn's WriteTradesCsv emits
per-trade MAE/MFE from each result, so a 1-combo optimize yields the Trades.csv the
survival harness needs. --trend applies the trend trio (UseTrend=true,
UseTrendReverse=Reverse, TrendPeriod=2*Slow) to test the trend-gate lift.

Usage:
  python scripts/holdout_optimize_rerun.py --from-date 2026-03-12 --to-date 2026-04-30 \
      [--limit 1] [--trend] [--no-dispatch] [--session opt_a09359e6b60b]
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.analysis.risk.account_state import load_firm_profile
from ta_foundation.analysis.risk.survival import per_template_survival
from ta_foundation.analysis.risk.trade_loader import parse_trades_csv
from ta_foundation.web.market_data_export import (
    _poll_to_terminal,
    _write_command_file,
    build_command_payload,
)
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE,
    DEFAULT_STATUS_FILE,
    ensure_bridge_available,
)

_SROOT = Path(".ta_artifacts/web_optimizer/sessions")
_INT_TYPE = ("System.Int32, mscorlib, Version=4.0.0.0, Culture=neutral, "
             "PublicKeyToken=b77a5c561934e089")
_BOOL_TYPE = ("System.Boolean, mscorlib, Version=4.0.0.0, Culture=neutral, "
              "PublicKeyToken=b77a5c561934e089")

# Optimizer scaffolding copied verbatim from a UI-saved PantheonMaster optimize
# template (daterange_probe_Opt.xml). One pinned OptimizationParameter => 1 combo;
# all other params come from the <Strategy> values.
_OPT_SECTIONS = """  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizerParameters>
    <ArrayOfParameterWrapper xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <ParameterWrapper><DisplayName>IsStrategyGenerator</DisplayName><Name>IsStrategyGenerator</Name><Value xsi:type="xsd:boolean">false</Value></ParameterWrapper>
      <ParameterWrapper><DisplayName>Keep best # results</DisplayName><Name>KeepBestResults</Name><Value xsi:type="xsd:int">1</Value></ParameterWrapper>
      <ParameterWrapper><DisplayName>LogTypeName</DisplayName><Name>LogTypeName</Name><Value xsi:type="xsd:string">Optimizer</Value></ParameterWrapper>
      <ParameterWrapper><DisplayName>Visible</DisplayName><Name>IsVisible</Name><Value xsi:type="xsd:boolean">true</Value></ParameterWrapper>
      <ParameterWrapper><DisplayName>Name</DisplayName><Name>Name</Name><Value xsi:type="xsd:string">Default</Value></ParameterWrapper>
    </ArrayOfParameterWrapper>
  </OptimizerParameters>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <OptimizationParameters>
    <ArrayOfParameter xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <Parameter><EnumValuesSerializable /><Increment>1</Increment><Max xsi:type="xsd:boolean">true</Max><Min xsi:type="xsd:boolean">false</Min><Name>EnableDebugPrint</Name><ParameterTypeSerializable>{btype}</ParameterTypeSerializable><ValueSerializable>false</ValueSerializable></Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
"""
# NT rejects an optimization with no real range (Min==Max everywhere) -> "must have at
# least one parameter to optimize" (modal popup that BLOCKS the AddOn). So we sweep an
# INERT bool (EnableDebugPrint, debug logging only) false->true = 2 combos with IDENTICAL
# trades, and KeepBestResults=1 so only ONE result is kept -> a single correct Trades.csv.


def _sval(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return m.group(1) if m else ""


def _set_sval(text: str, tag: str, value: str) -> str:
    return re.sub(rf"<{tag}>[^<]*</{tag}>", f"<{tag}>{value}</{tag}>", text, count=1)


def to_optimize_template(text: str, *, from_date: str, to_date: str, trend: bool) -> str:
    fast = _sval(text, "FastPeriod") or "5"
    slow = _sval(text, "SlowPeriod") or "200"
    reverse = _sval(text, "Reverse") or "false"
    # Category Backtest -> Optimize
    text = _set_sval(text, "Category", "Optimize")
    # window
    text = _set_sval(text, "From", f"{from_date}T00:00:00")
    text = _set_sval(text, "To", f"{to_date}T00:00:00")
    # trend trio (strategy values; not swept, so taken as-is by the optimizer)
    if trend:
        text = _set_sval(text, "UseTrend", "true")
        text = _set_sval(text, "UseTrendReverse", reverse)  # follow-mode: filter tracks the actual (reversed) trade
        text = _set_sval(text, "TrendPeriod", str(2 * int(slow)))  # > Slow so it's a real higher-trend context
    # inject optimizer sections right after </StrategyType>
    sections = _OPT_SECTIONS.format(btype=_BOOL_TYPE)
    text = text.replace("</StrategyType>\n", "</StrategyType>\n" + sections, 1)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="opt_a09359e6b60b")
    ap.add_argument("--from-date", required=True)
    ap.add_argument("--to-date", required=True)
    ap.add_argument("--instrument", default="NQ 06-26")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--trend", action="store_true", help="apply UseTrend trio")
    ap.add_argument("--no-dispatch", action="store_true")
    ap.add_argument("--timeout", type=int, default=3 * 60 * 60)
    args = ap.parse_args()

    sdir = Path(args.session) if Path(args.session).exists() else _SROOT / args.session
    tdir = sdir / "deployment_package" / "final_backtest_handoff" / "named_backtest_templates" / "recipe"
    templates = sorted(tdir.glob("*.xml"))
    if args.limit:
        templates = templates[: args.limit]
    if not templates:
        print(f"no templates under {tdir}", file=sys.stderr)
        return 2
    rid_of = lambda p: (re.match(r"(F_\d+)", p.stem) or re.match(r"(.+)", p.stem)).group(1)

    base = Path(tempfile.mkdtemp(prefix="holdout_opt_"))
    src, dest = base / "generated_templates", base / "nt_output"
    src.mkdir(parents=True); dest.mkdir(parents=True)
    for p in templates:
        out = to_optimize_template(p.read_text(encoding="utf-8-sig"),
                                   from_date=args.from_date, to_date=args.to_date, trend=args.trend)
        (src / p.name).write_text(out, encoding="utf-8")
    print(f"session    : {sdir.name}")
    print(f"templates  : {len(templates)} as 1-combo OPTIMIZE  (window {args.from_date}..{args.to_date}, trend={args.trend})")
    print(f"work dir   : {base}")

    if args.no_dispatch:
        # offline structural sanity on the first generated template
        sample = (src / templates[0].name).read_text(encoding="utf-8")
        print("\n[dry-run] sanity on", templates[0].name)
        for tag in ("Category", "From", "To", "UseTrend", "UseTrendReverse", "TrendPeriod", "SlowPeriod", "FastPeriod"):
            print(f"   <{tag}> = {_sval(sample, tag)!r}")
        print("   has OptimizerType  :", "<OptimizerType>" in sample)
        print("   #OptimizationParameter:", sample.count("<Parameter>"))
        print(f"   generated dir: {src}")
        return 0

    run_id = "holdoutopt_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cmd_path, status_path = DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE
    ensure_bridge_available(cmd_path, status_path, now=datetime.now(timezone.utc))
    try:
        if Path(status_path).exists():
            Path(status_path).unlink()
    except OSError:
        pass
    _write_command_file(cmd_path, build_command_payload(
        run_id=run_id, source_folder=src, dest_folder=dest,
        instrument=args.instrument, timeout_seconds=args.timeout))
    print(f"dispatched RunBatch {run_id}; polling...")
    state = _poll_to_terminal(status_path, run_id=run_id, timeout_seconds=args.timeout,
                              poll_interval_seconds=20, logger=print)
    print(f"run state  : {state}")

    found = {p.parent.name: p for p in dest.rglob("Trades.csv")}
    template_trades = {}
    for p in templates:
        tp = found.get(p.stem) or found.get(rid_of(p))
        if tp is None:
            continue
        trades = parse_trades_csv(tp, template_id=rid_of(p))
        if trades:
            template_trades[rid_of(p)] = trades
    print(f"mapped     : {len(template_trades)}/{len(templates)} templates produced trades")
    if not template_trades:
        print("NO trades — check Trades.csv population (does a 1-combo optimize retain per-trade data?)")
        print(f"   inspect: {dest}")
        return 1

    # window guard
    from datetime import date as _date
    lo = _date(*map(int, args.from_date.split("-")))
    hi = _date(*map(int, args.to_date.split("-")))
    days = [t.entry_dt.date() for ts in template_trades.values() for t in ts]
    inw = sum(1 for d in days if lo <= d <= hi)
    print(f"window guard: {100*inw/len(days):.0f}% in {args.from_date}..{args.to_date} "
          f"(realized {min(days)}..{max(days)})")

    profile = load_firm_profile("APEX")
    sweep = per_template_survival(template_trades, profile=profile, account_type="evaluation",
                                  account_size="50000", starting_balance=50000.0,
                                  contracts=1, dollar_scale=1.0)
    print(f"survival   : survived {sweep.n_survived}/{sweep.n_templates}  passed {sweep.n_passed}/{sweep.n_templates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
