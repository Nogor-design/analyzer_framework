from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence
from xml.etree import ElementTree as ET

import pandas as pd

from ta_foundation.analysis.exits.simulate import ExitSimConfig, Policy, simulate_exit_policies_for_run
from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
from ta_foundation.parsers.ninjatrader.tick_last_txt import TickLastTxtParser
from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _safe_int(value: Any) -> int | None:
    as_float = _safe_float(value)
    if as_float is None:
        return None
    return int(as_float)


def _median(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return None
    return float(vals.median())


def _find_col(df: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    if df is None or df.empty:
        return None

    def norm(value: str) -> str:
        return "".join(ch for ch in str(value).lower() if ch.isalnum())

    by_norm = {norm(col): col for col in df.columns}
    for candidate in candidates:
        found = by_norm.get(norm(candidate))
        if found is not None:
            return str(found)
    return None


def _default_exit_policies() -> list[Policy]:
    """Use the same default policy set as the trusted HTML exit-sim report.

    Imported lazily: the report section pulls in matplotlib, which we must not
    load on the fast manifest path (only when the exit sim actually runs)."""
    from ta_foundation.reports.html.sections.exit_policy_simulation import _make_policies

    return list(_make_policies({}))


def exit_robustness_for_template(
    trades: pd.DataFrame,
    market,
    *,
    instrument: str,
    contract: str,
    run_id: str,
    current_net: float | None,
    tick_value: float = 5.0,
    cfg: ExitSimConfig | None = None,
) -> dict:
    """Rank exit policies for this template's trades.

    Calls ``simulate_exit_policies_for_run`` with the default policy set used by
    ``reports/html/sections/exit_policy_simulation.py``. In the current simulator
    implementation, the returned DataFrame is trade-level, not already
    aggregated: useful columns are ``run_id``, ``trade_idx``, ``policy``,
    ``entry_dt``, ``exit_dt``, ``entry_price``, ``exit_price``, ``exit_reason``,
    ``pnl_ticks``, ``mae_ticks``, ``mfe_ticks``, ``atr_entry``,
    ``best_fav_ticks``, ``worst_adv_ticks``, ``be_armed``, ``be_arm_dt``,
    ``stop_at_arm``, ``best_fav_ticks_exec``, ``best_fav_ticks_last``, and
    diagnostic ``detail`` rows when tick/trade inputs are unavailable.

    Returns:
      best_policy: str
      best_policy_net: float
      current_net: float | None
      exit_robustness_margin: float | None
      exit_rank_spread: float

    ``best_policy_net`` is expressed in the same units as ``current_net`` when
    the simulator's ``actual`` policy row lets us infer a tick value; otherwise
    it is net ticks. Return ``{}`` when trades are empty or ticks are unavailable.
    """
    if trades is None or trades.empty or market is None:
        return {}

    try:
        sim = simulate_exit_policies_for_run(
            run_id=run_id,
            trades=trades,
            market=market,
            instrument=instrument,
            contract=contract,
            policies=_default_exit_policies(),
            cfg=cfg or ExitSimConfig(),
        )
    except Exception:
        return {}

    if sim is None or sim.empty or "policy" not in sim.columns or "pnl_ticks" not in sim.columns:
        return {}
    if (sim["policy"] == "DIAGNOSTIC").any():
        return {}

    rows = sim.copy()
    rows["pnl_ticks"] = pd.to_numeric(rows["pnl_ticks"], errors="coerce")
    policy_net_ticks = rows.groupby("policy")["pnl_ticks"].sum(min_count=1).dropna()
    if policy_net_ticks.empty:
        return {}

    # The simulator reports per-trade P&L in TICKS and names each row by its
    # policy; it does NOT emit a baseline "actual" row. Convert net ticks to
    # dollars with the instrument tick value so best_policy_net and the margin are
    # directly comparable to the template's actual net (assumes 1 contract, like
    # the rest of the deployment-matrix risk math).
    scale = float(tick_value) if tick_value else 1.0
    policy_net = policy_net_ticks * scale
    best_policy = str(policy_net.idxmax())
    best_net = float(policy_net.loc[best_policy])
    worst_net = float(policy_net.min())
    margin = None if current_net is None else best_net - float(current_net)
    return {
        "best_policy": best_policy,
        "best_policy_net": best_net,
        "current_net": None if current_net is None else float(current_net),
        "exit_robustness_margin": margin,
        "exit_rank_spread": best_net - worst_net,
    }


def _split_instrument_contract(session: Any) -> tuple[str | None, str | None]:
    try:
        doc = session.load_document()
    except Exception:
        return None, None
    raw = str(getattr(doc, "instrument", "") or "").strip()
    if not raw:
        return None, None
    parts = raw.split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    market_suffix = str(getattr(doc, "market_suffix", "") or "").strip()
    return parts[0], market_suffix or None


def _session_tick_value(session: Any, cell: dict[str, Any]) -> float:
    try:
        doc = session.load_document()
        instrument_obj = getattr(doc, "instrument", None)
        tick_value = getattr(instrument_obj, "tick_value", None)
        if tick_value is not None:
            return float(tick_value)

        symbol = str(getattr(doc, "market_suffix", "") or getattr(doc, "instrument", "") or "").split()[0]
        if symbol:
            from ta_foundation.web.discovery_instruments import get_instrument

            instrument = get_instrument(symbol)
            if instrument is not None:
                return float(instrument.tick_value)
    except Exception:
        pass
    return float(cell.get("tick_value") or 5.0)


def _trades_path_for_cell(session: Any, cell: dict[str, Any]) -> Path | None:
    run_id = str(cell.get("run_id") or "").strip()
    if not run_id:
        return None
    return (
        Path(session.directory)
        / "deployment_package"
        / "final_backtest_handoff"
        / "nt8_backtest_results"
        / run_id
        / "Trades.csv"
    )


def _load_trades_for_cell(session: Any, cell: dict[str, Any]) -> pd.DataFrame | None:
    path = _trades_path_for_cell(session, cell)
    run_id = str(cell.get("run_id") or "").strip()
    if path is None or not path.exists() or not run_id:
        return None
    try:
        return NinjaTraderTradesCsvParser().parse(path, run_id=run_id).df
    except Exception:
        return None


def _mae_mfe_features(trades: pd.DataFrame | None) -> dict[str, float | None]:
    if trades is None or trades.empty:
        return {"mae_mfe_ratio_median": None, "mfe_giveback_median": None}
    mae_col = _find_col(trades, ["mae", "MAE"])
    mfe_col = _find_col(trades, ["mfe", "MFE"])
    profit_col = _find_col(trades, ["profit", "Profit", "PnL"])
    out: dict[str, float | None] = {"mae_mfe_ratio_median": None, "mfe_giveback_median": None}
    if mae_col and mfe_col:
        mae = pd.to_numeric(trades[mae_col], errors="coerce").abs()
        mfe = pd.to_numeric(trades[mfe_col], errors="coerce")
        ratios = (mae / mfe.where(mfe != 0)).replace([float("inf"), float("-inf")], pd.NA).dropna()
        if not ratios.empty:
            out["mae_mfe_ratio_median"] = float(ratios.median())
    if mfe_col and profit_col:
        giveback = pd.to_numeric(trades[mfe_col], errors="coerce") - pd.to_numeric(trades[profit_col], errors="coerce")
        out["mfe_giveback_median"] = _median(giveback)
    return out


def _daily_features(run_id: str, trades: pd.DataFrame | None) -> dict[str, float | None]:
    if trades is None or trades.empty:
        return {"daily_green_pct": None, "daily_worst_day": None}
    pkg = AnalysisPackage(run_id=run_id, trades=trades)
    outcomes = derive_daily_outcomes_for_package(pkg)
    by_date = outcomes.get("by_date", {}) if isinstance(outcomes, dict) else {}
    trade_days = [v for v in by_date.values() if int(v.get("trades") or 0) > 0]
    if not trade_days:
        return {"daily_green_pct": None, "daily_worst_day": None}
    green = sum(1 for v in trade_days if float(v.get("net_profit") or 0.0) > 0.0)
    worst = min(float(v.get("net_profit") or 0.0) for v in trade_days)
    return {"daily_green_pct": float(green / len(trade_days)), "daily_worst_day": float(worst)}


def _risk_features(session: Any, cell: dict[str, Any]) -> dict[str, float | int | None]:
    params = _template_params(cell)
    max_stop = _safe_float(cell.get("max_stop", cell.get("MaxStop", params.get("MaxStop"))))
    max_tp_ratio = _safe_float(cell.get("max_tp_ratio", cell.get("MaxTPRatio", params.get("MaxTPRatio"))))
    max_trades = _safe_int(cell.get("max_trades", cell.get("MaxTrades", params.get("MaxTrades"))))
    profit_stop = _safe_float(cell.get("profit_stop", cell.get("ProfitStop", params.get("ProfitStop"))))
    loss_stop = _safe_float(cell.get("loss_stop", cell.get("LossStop", params.get("LossStop"))))
    if max_stop is None or max_tp_ratio is None or max_trades is None or profit_stop is None or loss_stop is None:
        return {"prop_max_daily_loss": None, "effective_trades": None}

    tick_value = _session_tick_value(session, cell)
    per_trade_loss = float(max_stop) * tick_value
    per_trade_profit = math.floor(float(max_stop) * float(max_tp_ratio)) * tick_value
    try:
        from template_naming.core import compute_effective_trades, compute_true_max_loss

        effective = compute_effective_trades(
            max_trades=int(max_trades),
            per_trade_profit=per_trade_profit,
            per_trade_loss=per_trade_loss,
            profit_stop=float(profit_stop),
            loss_stop=float(loss_stop),
        )
        true_max_loss = compute_true_max_loss(
            per_trade_max_loss=per_trade_loss,
            max_trades=int(max_trades),
            loss_stop=float(loss_stop),
        )
    except Exception:
        effective = min(int(max_trades), max(1, math.ceil(float(loss_stop) / per_trade_loss)))
        true_max_loss = min(float(loss_stop), per_trade_loss * effective)
    return {"prop_max_daily_loss": float(true_max_loss), "effective_trades": int(effective)}


def _template_params(cell: dict[str, Any]) -> dict[str, Any]:
    path_value = cell.get("template_path")
    if not path_value:
        return {}
    path = Path(str(path_value))
    if not path.exists():
        return {}
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for tag in ("MaxStop", "MaxTPRatio", "MaxTrades", "ProfitStop", "LossStop"):
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == tag and el.text is not None:
                out[tag] = el.text.strip()
                break
    return out


def template_quality_features(session, cell, *, market=None) -> dict:
    """Build the predictor feature block for one covered manifest cell.

    Best-effort: every field is optional, sources that are unavailable become
    ``None``, and the function never raises. Phase A intentionally omits
    walk-forward IS/OOS degradation because the deployment final is one OOS
    window with no per-template IS/OOS split, regime-fit share because it needs
    bar-level regime classification planned for Phase A.5, and PantheonMaster
    fields because those belong to Phase B.
    """
    try:
        run_id = str(cell.get("run_id") or "").strip()
        features: dict[str, Any] = {}
        trades = _load_trades_for_cell(session, cell)
        features.update(_mae_mfe_features(trades))
        features.update(_daily_features(run_id, trades))
        features.update(_risk_features(session, cell))

        instrument, contract = _split_instrument_contract(session)
        current_net = _safe_float(cell.get("total_net_profit"))
        if trades is not None and not trades.empty and market is not None and instrument and contract and run_id:
            exit_features = exit_robustness_for_template(
                trades,
                market,
                instrument=instrument,
                contract=contract,
                run_id=run_id,
                current_net=current_net,
                tick_value=_session_tick_value(session, cell),
            )
            features.update(exit_features)
        else:
            features.update(
                {
                    "best_policy": None,
                    "best_policy_net": None,
                    "current_net": current_net,
                    "exit_robustness_margin": None,
                    "exit_rank_spread": None,
                }
            )
        return features
    except Exception:
        return {}


def load_market_for_session(session: Any) -> MarketDataStore | None:
    """Best-effort tick-store loader for optimizer deployment sessions."""
    try:
        doc = session.load_document()
    except Exception:
        return None

    folder = (
        getattr(doc, "market_data_folder", "")
        or getattr(getattr(doc, "context", None), "market_data_folder", "")
        or ""
    )
    if not folder:
        try:
            from ta_foundation.web import app as web_app

            folder = getattr(web_app, "_market_data_dir", "") or ""
        except Exception:
            folder = ""
    if not folder:
        # No market-data dir configured for this session. Exit-robustness features
        # require an explicitly configured tick folder (and tick data matching the
        # backtest contract/window); skip rather than scanning a default location.
        return None

    instrument, contract = _split_instrument_contract(session)
    if not instrument or not contract:
        return None
    market_dir = Path(str(folder))
    if not market_dir.exists():
        return None
    try:
        market = MarketDataStore()
        bars_parser = MinuteBarsLastTxtParser()
        tick_parser = TickLastTxtParser()
        prefix = f"{instrument} {contract}".lower()
        for path in sorted(market_dir.glob("*.txt")):
            if not path.name.lower().startswith(prefix):
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    header = fh.read(1024)  # stream first 1KB; never load the whole (GB-sized) file
                if tick_parser.can_parse(path, header):
                    art = tick_parser.parse(path, run_id=None)
                    market.ingest_artifact(art)
                elif bars_parser.can_parse(path, header):
                    art = bars_parser.parse(path, run_id=None)
                    market.ingest_artifact(art)
            except Exception:
                continue
        market.finalize()
        if market.ticks or market.minute_bars:
            return market
    except Exception:
        return None
    return None
