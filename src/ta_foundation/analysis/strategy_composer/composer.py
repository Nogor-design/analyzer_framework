from __future__ import annotations

"""
Strategy Composer
=================
Runs the full research pipeline from a StrategyTemplate without NinjaTrader:

  bars_1m → resample → generate signals → emit entries →
  simulate outcomes → compute metrics → (optionally) validate

Usage
-----
    from ta_foundation.analysis.strategy_composer.composer import StrategyComposer
    from ta_foundation.analysis.strategy_composer.template import StrategyTemplate

    tmpl = StrategyTemplate.from_file("my_strategy.json")
    composer = StrategyComposer(bars_1m=bars_df, template=tmpl)

    result = composer.run()
    print(result["summary"])

    vr = composer.validate(db_path="experiments.duckdb", experiment_id=3)
    print(vr.summary)
"""

from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.analysis.strategy_composer.template import StrategyTemplate
from ta_foundation.marketdata.resample import ohlcv_resample_from_bars


# ---------------------------------------------------------------------------
# Metric helpers (self-contained — no import from evaluation to avoid coupling)
# ---------------------------------------------------------------------------

def _safe(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        import math
        f = float(v)
        return None if not math.isfinite(f) else f
    except Exception:
        return None


def _compute_metrics(trades: pd.DataFrame, outcome_mode: Optional[str] = None) -> Dict[str, Any]:
    if trades is None or trades.empty:
        return {"n_trades": 0, "passed": False, "error": "no trades"}

    subset = trades if outcome_mode is None else trades[trades.get("outcome_mode", pd.Series()) == outcome_mode] if "outcome_mode" in trades.columns else trades

    col = "profit_net" if "profit_net" in subset.columns else "profit"
    profits = pd.to_numeric(subset.get(col, pd.Series(dtype=float)), errors="coerce").dropna()
    n = len(profits)

    if n == 0:
        return {"n_trades": 0}

    wins = profits[profits > 0]
    losses = profits[profits < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(losses.sum())

    pf = _safe(gross_profit / abs(gross_loss)) if gross_loss != 0 else None
    win_rate = _safe(len(wins) / n)
    expectancy = _safe(float(profits.mean()))
    net_pnl = _safe(float(profits.sum()))

    results = subset.get("result", pd.Series(dtype=str)) if "result" in subset.columns else pd.Series()
    fill_rate = _safe((results == "win").sum() / n) if not results.empty else None

    return {
        "n_trades": n,
        "profit_factor": pf,
        "win_rate": win_rate,
        "expectancy": expectancy,
        "net_pnl": net_pnl,
        "fill_rate": fill_rate,
        "outcome_mode": outcome_mode,
    }


def _rank_outcome_modes(all_trades: pd.DataFrame) -> List[Dict[str, Any]]:
    """Rank each outcome_mode by a composite score."""
    if "outcome_mode" not in all_trades.columns:
        return [_compute_metrics(all_trades)]

    rows = []
    for mode, grp in all_trades.groupby("outcome_mode"):
        m = _compute_metrics(grp, outcome_mode=str(mode))
        pf = m.get("profit_factor") or 0
        wr = m.get("win_rate") or 0
        exp = m.get("expectancy") or 0
        score = 0.5 * min(pf, 5.0) / 5.0 + 0.3 * wr + 0.2 * (1 if exp > 0 else 0)
        m["score"] = _safe(score)
        rows.append(m)

    return sorted(rows, key=lambda x: x.get("score") or 0, reverse=True)


# ---------------------------------------------------------------------------
# StrategyComposer
# ---------------------------------------------------------------------------

class StrategyComposer:
    """
    Run a StrategyTemplate against market bar data.

    Parameters
    ----------
    bars_1m
        1-minute OHLCV DataFrame with columns: dt, open, high, low, close
        (and optionally volume).  Must be tz-aware (America/Denver).
    template
        A StrategyTemplate instance.
    """

    def __init__(
        self,
        bars_1m: pd.DataFrame,
        template: StrategyTemplate,
    ) -> None:
        self.bars_1m = bars_1m
        self.template = template
        self._trades: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """
        Execute the full pipeline and return a results dict.

        Returns
        -------
        dict with keys:
          trades          : pd.DataFrame of all simulated trades
          ranked_modes    : list of per-outcome-mode metric dicts, best first
          summary         : human-readable text summary
          n_signals       : total signals generated before simulation
          template_name   : template name
          hypothesis      : hypothesis text
        """
        bars_tf = self._resample()
        signals = self._generate_signals(bars_tf)
        pending = self._emit_entries(signals, bars_tf)
        trades = self._simulate(pending)

        self._trades = trades
        ranked = _rank_outcome_modes(trades) if not trades.empty else []
        summary = self._build_summary(signals, trades, ranked)

        return {
            "trades": trades,
            "ranked_modes": ranked,
            "summary": summary,
            "n_signals": len(signals),
            "n_trades": len(trades),
            "template_name": self.template.name,
            "hypothesis": self.template.hypothesis,
        }

    def validate(
        self,
        db_path: Optional[str] = None,
        experiment_id: Optional[int] = None,
        **validation_kwargs,
    ):
        """
        Run the statistical validation gates on the best outcome mode's trades.

        Runs ``run()`` first if not already done.
        Returns a ValidationResult.
        """
        from ta_foundation.analysis.strategy_discovery.validation import run_validation

        if self._trades is None:
            self.run()

        trades = self._trades
        if trades.empty:
            from ta_foundation.analysis.strategy_discovery.validation import ValidationResult
            return ValidationResult(
                passed=False,
                issues=["no trades generated — check signals and bar data"],
                summary="FAILED — no trades",
            )

        # Pick the best outcome mode by profit factor
        if "outcome_mode" in trades.columns:
            ranked = _rank_outcome_modes(trades)
            best_mode = ranked[0].get("outcome_mode") if ranked else None
            if best_mode is not None:
                trades = trades[trades["outcome_mode"] == best_mode].copy()

        return run_validation(
            trades,
            db_path=db_path,
            experiment_id=experiment_id,
            **validation_kwargs,
        )

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    def _resample(self) -> pd.DataFrame:
        tf = self.template.timeframe
        if tf == "1m":
            return self.bars_1m.copy()
        return ohlcv_resample_from_bars(self.bars_1m, tf)

    def _generate_signals(self, bars_tf: pd.DataFrame) -> pd.DataFrame:
        signals = self.template.generate_signals(bars_tf)
        if signals.empty:
            return pd.DataFrame()
        # Ensure required columns exist for emit functions
        for col in ("open", "high", "low", "close"):
            if col not in signals.columns and col in bars_tf.columns:
                # Merge from bars_tf on dt
                signals = signals.merge(
                    bars_tf[["dt", col]].rename(columns={col: col}),
                    on="dt", how="left",
                )
        return signals

    def _emit_entries(self, signals: pd.DataFrame, bars_tf: pd.DataFrame) -> pd.DataFrame:
        if signals.empty:
            return pd.DataFrame()

        timing = self.template.timing

        from ta_foundation.analysis.entry_strategies.candle.signals import (
            emit_next_open, emit_break_extreme, emit_body_midpoint,
        )

        emit_fns = {
            "next_open":    emit_next_open,
            "break_extreme": emit_break_extreme,
            "body_midpoint": emit_body_midpoint,
        }
        emit_fn = emit_fns.get(timing, emit_next_open)

        try:
            return emit_fn(signals, bars_tf)
        except Exception as exc:
            # Fallback to next_open if the chosen timing fails
            try:
                return emit_next_open(signals, bars_tf)
            except Exception:
                return pd.DataFrame()

    def _simulate(self, pending: pd.DataFrame) -> pd.DataFrame:
        if pending.empty:
            return pd.DataFrame()

        from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes

        outcome_cfg = self.template.to_outcome_config()
        try:
            return simulate_outcomes(pending, self.bars_1m, outcome_cfg)
        except Exception:
            return pd.DataFrame()

    def _build_summary(
        self,
        signals: pd.DataFrame,
        trades: pd.DataFrame,
        ranked: List[Dict[str, Any]],
    ) -> str:
        lines = [
            f"Strategy: {self.template.name}",
            f"Hypothesis: {self.template.hypothesis}",
            f"Timeframe: {self.template.timeframe}  "
            f"Direction: {self.template.direction}  "
            f"Timing: {self.template.timing}",
            f"Signals generated: {len(signals)}",
            f"Simulated trades:  {len(trades)}",
            "",
        ]
        if not ranked:
            lines.append("No trades produced — check bar data coverage and signal parameters.")
            return "\n".join(lines)

        lines.append("Outcome modes (best first):")
        lines.append(f"  {'Mode':<25} {'N':>5} {'PF':>6} {'WR':>6} {'E/trade':>8} {'Net P&L':>10}")
        lines.append("  " + "-" * 65)
        for m in ranked[:6]:
            mode = str(m.get("outcome_mode") or "all")[:24]
            n = m.get("n_trades", 0)
            pf = m.get("profit_factor")
            wr = m.get("win_rate")
            exp = m.get("expectancy")
            net = m.get("net_pnl")
            lines.append(
                f"  {mode:<25} {n:>5} "
                f"{(f'{pf:.2f}' if pf is not None else 'N/A'):>6} "
                f"{(f'{wr:.1%}' if wr is not None else 'N/A'):>6} "
                f"{(f'${exp:.0f}' if exp is not None else 'N/A'):>8} "
                f"{(f'${net:.0f}' if net is not None else 'N/A'):>10}"
            )

        best = ranked[0]
        pf = best.get("profit_factor")
        lines.append("")
        if pf is not None and pf > 1.0:
            lines.append(f"Best mode profit factor: {pf:.2f} — run .validate() to check statistical gates.")
        else:
            lines.append("Best mode profit factor ≤ 1.0 — strategy does not have positive expectancy.")

        return "\n".join(lines)
