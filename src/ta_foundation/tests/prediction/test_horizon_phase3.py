"""
Phase 3 unit tests for the horizon prediction system.

Covers:
  - horizon_batch                     (asof resolution, runner, schedule helpers)
  - backtest_horizon_predictions      (walk-forward replay, summary, persistence)
  - horizon_reports                   (leaderboard, matrices, edge, calibration, drift)
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

from ta_foundation.prediction.analogue_probability_agent import (
    AnalogueProbabilityAgent,
    AnalogueProbabilityAgentConfig,
)
from ta_foundation.prediction.backtest_horizon_predictions import (
    HorizonBacktestConfig,
    HorizonBacktestSummary,
    run_horizon_backtest,
    run_walk_forward_replay,
)
from ta_foundation.prediction.horizon_batch import (
    HorizonBatchResult,
    HorizonBatchRunner,
    HorizonBatchSpec,
    asofs_from_bars,
    build_schedule,
    make_static_bar_loader,
    resolve_asof_idx,
)
from ta_foundation.prediction.horizon_models import (
    CandleHorizonOutcome,
    CandleHorizonPrediction,
)
from ta_foundation.prediction.horizon_reports import (
    build_agent_leaderboard,
    build_best_edge_cells,
    build_calibration_report,
    build_drift_report,
    build_full_report,
    build_session_matrix,
    build_timeframe_horizon_matrix,
    format_agent_leaderboard,
    format_full_report,
)
from ta_foundation.prediction.horizon_store import HorizonPredictionStore
from ta_foundation.prediction.statistical_probability_agent import (
    StatisticalProbabilityAgent,
    StatisticalProbabilityAgentConfig,
)

DENVER = "America/Denver"
NY = "America/New_York"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(
    n: int,
    seed: int = 7,
    start: str = "2026-01-02 09:30",
    freq: str = "5min",
    drift: float = 0.0,
    sigma: float = 1.0,
    base: float = 20000.0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.date_range(start=start, periods=n, freq=freq, tz=NY).tz_convert(DENVER)
    closes = np.cumsum(rng.normal(loc=drift, scale=sigma, size=n)) + base
    bodies = rng.normal(loc=0.0, scale=sigma * 0.5, size=n)
    opens = closes - bodies
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.0, sigma * 0.4, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, sigma * 0.4, size=n))
    return pd.DataFrame({
        "dt": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": rng.integers(100, 1000, size=n),
    })


def _pred(
    *,
    agent_id: str = "stat",
    timeframe: str = "5m",
    horizon: int = 3,
    session: str = "ny_open",
    regime: str = "trend_up",
    p_bull: float = 0.6,
    p_bear: float = 0.2,
    p_neu: float = 0.2,
    asof_iso: str = "2026-04-21T10:00:00-06:00",
    abstain: bool = False,
) -> CandleHorizonPrediction:
    return CandleHorizonPrediction(
        agent_id=agent_id,
        instrument="NQ",
        contract="H25",
        timeframe=timeframe,
        asof_timestamp=asof_iso,
        session_label=session,
        horizon_candles=horizon,
        bullish_probability=p_bull,
        bearish_probability=p_bear,
        neutral_probability=p_neu,
        feature_snapshot={"regime": regime, "prior_atr": 5.0},
        sample_size=50,
        method_used="test_method",
        upside_threshold_points=5.0,
        downside_threshold_points=5.0,
        abstain=abstain,
    )


def _outcome(
    *,
    pred_id: str,
    actual_direction: str,
    composite_score: float = 0.0,
    brier_dir: float = 0.0,
    actual_return_atr: float = 0.0,
    timeframe: str = "5m",
    horizon: int = 3,
) -> CandleHorizonOutcome:
    return CandleHorizonOutcome(
        prediction_id=pred_id,
        timeframe=timeframe,
        horizon_candles=horizon,
        actual_direction=actual_direction,
        actual_return_atr=actual_return_atr,
        composite_score=composite_score,
        brier_score_direction=brier_dir,
    )


# ---------------------------------------------------------------------------
# horizon_batch — asof resolution
# ---------------------------------------------------------------------------

class TestResolveAsofIdx:
    def test_int_asof_pass_through(self):
        bars = _make_bars(50)
        assert resolve_asof_idx(bars, 7) == 7

    def test_int_asof_out_of_range(self):
        bars = _make_bars(50)
        with pytest.raises(ValueError):
            resolve_asof_idx(bars, 100)

    def test_timestamp_asof_finds_latest_at_or_before(self):
        bars = _make_bars(20, start="2026-01-02 09:30", freq="5min")
        # Bar 5 is at 2026-01-02 09:55 NY = 07:55 Denver
        target = pd.Timestamp(bars.iloc[5]["dt"])
        idx = resolve_asof_idx(bars, target)
        assert idx == 5

        # A timestamp halfway between bars rounds down
        between = target + pd.Timedelta(minutes=2)
        assert resolve_asof_idx(bars, between) == 5

    def test_naive_timestamp_rejected(self):
        bars = _make_bars(20)
        with pytest.raises(ValueError, match="tz-aware"):
            resolve_asof_idx(bars, pd.Timestamp("2026-01-02 09:35"))

    def test_no_eligible_bar_raises(self):
        bars = _make_bars(20, start="2026-01-02 09:30", freq="5min")
        too_early = pd.Timestamp("2025-01-01", tz=DENVER)
        with pytest.raises(ValueError):
            resolve_asof_idx(bars, too_early)


# ---------------------------------------------------------------------------
# horizon_batch — runner
# ---------------------------------------------------------------------------

class _StubAgent:
    """Tiny deterministic agent that always returns a non-abstain prediction."""

    def __init__(self, agent_id: str = "stub", fail_on_idx: int | None = None) -> None:
        self.agent_id = agent_id
        self.fail_on_idx = fail_on_idx
        self.calls: list[int] = []

    def predict(self, bars, asof_idx, horizon_candles, instrument, contract, timeframe):
        self.calls.append(asof_idx)
        if self.fail_on_idx is not None and asof_idx == self.fail_on_idx:
            raise RuntimeError("simulated agent failure")
        return CandleHorizonPrediction(
            agent_id=self.agent_id,
            instrument=instrument,
            contract=contract,
            timeframe=timeframe,
            asof_timestamp=pd.Timestamp(bars.iloc[asof_idx]["dt"]).isoformat(),
            session_label="ny_open",
            horizon_candles=int(horizon_candles),
            bullish_probability=0.5,
            bearish_probability=0.3,
            neutral_probability=0.2,
            sample_size=10,
            method_used="stub",
            feature_snapshot={"regime": "trend_up", "prior_atr": 1.0},
            upside_threshold_points=1.0,
            downside_threshold_points=1.0,
        )


class TestHorizonBatchRunner:
    def test_runs_one_spec_with_stub_agent(self):
        bars = _make_bars(200)
        loader = make_static_bar_loader({("NQ", "H25", "5m"): bars})
        agent = _StubAgent("stub")
        runner = HorizonBatchRunner(agents=[agent], bar_loader=loader)
        specs = [HorizonBatchSpec("NQ", "H25", "5m", 3, 100)]
        results = runner.run(specs)
        assert len(results) == 1
        assert results[0].error is None
        assert results[0].prediction is not None
        assert results[0].asof_idx == 100
        assert agent.calls == [100]

    def test_caches_bars_across_specs(self):
        bars = _make_bars(200)
        load_count = {"n": 0}

        def loader(instrument, contract, timeframe):
            load_count["n"] += 1
            return bars

        runner = HorizonBatchRunner(agents=[_StubAgent()], bar_loader=loader)
        specs = [
            HorizonBatchSpec("NQ", "H25", "5m", 3, i)
            for i in (50, 60, 70, 80)
        ]
        runner.run(specs)
        assert load_count["n"] == 1

    def test_per_spec_failure_isolated(self):
        bars = _make_bars(200)
        loader = make_static_bar_loader({("NQ", "H25", "5m"): bars})
        agent = _StubAgent("stub", fail_on_idx=60)
        runner = HorizonBatchRunner(agents=[agent], bar_loader=loader)
        specs = [
            HorizonBatchSpec("NQ", "H25", "5m", 3, 50),
            HorizonBatchSpec("NQ", "H25", "5m", 3, 60),
            HorizonBatchSpec("NQ", "H25", "5m", 3, 70),
        ]
        results = runner.run(specs)
        assert len(results) == 3
        # Middle spec failed, the others succeeded
        assert results[0].error is None
        assert results[1].error is not None
        assert "agent_error" in results[1].error
        assert results[2].error is None

    def test_save_predictions_to_store(self, tmp_path: Path):
        bars = _make_bars(200)
        loader = make_static_bar_loader({("NQ", "H25", "5m"): bars})
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        runner = HorizonBatchRunner(
            agents=[_StubAgent()],
            bar_loader=loader,
            store=store,
            save_predictions=True,
        )
        specs = [HorizonBatchSpec("NQ", "H25", "5m", 3, i) for i in (50, 60, 70)]
        runner.run(specs)
        assert len(store.get_all_predictions()) == 3

    def test_multi_agent_emits_one_result_per_agent(self):
        bars = _make_bars(200)
        loader = make_static_bar_loader({("NQ", "H25", "5m"): bars})
        a = _StubAgent("a")
        b = _StubAgent("b")
        runner = HorizonBatchRunner(agents=[a, b], bar_loader=loader)
        results = runner.run([HorizonBatchSpec("NQ", "H25", "5m", 3, 100)])
        assert {r.agent_id for r in results} == {"a", "b"}

    def test_empty_loader_returns_error_results(self):
        loader = make_static_bar_loader({})
        runner = HorizonBatchRunner(agents=[_StubAgent()], bar_loader=loader)
        results = runner.run([HorizonBatchSpec("NQ", "H25", "5m", 3, 100)])
        assert len(results) == 1
        assert results[0].error is not None
        assert "empty" in results[0].error


class TestScheduleHelpers:
    def test_build_schedule_cartesian(self):
        specs = build_schedule("NQ", "H25", ["5m", "15m"], [3, 5], [10, 20, 30])
        assert len(specs) == 2 * 2 * 3
        assert all(s.instrument == "NQ" for s in specs)

    def test_asofs_from_bars_respects_warmup_and_stride(self):
        bars = _make_bars(200)
        asofs = asofs_from_bars(bars, warmup=50, stride=10)
        assert len(asofs) == 15  # (200-50)/10 = 15
        # Each is tz-aware
        assert all(ts.tzinfo is not None for ts in asofs)

    def test_asofs_from_bars_window_filter(self):
        bars = _make_bars(200, start="2026-01-02 09:30", freq="5min")
        start = pd.Timestamp(bars.iloc[60]["dt"])
        end = pd.Timestamp(bars.iloc[100]["dt"])
        asofs = asofs_from_bars(bars, warmup=0, stride=1, start=start, end=end)
        assert len(asofs) == 41   # inclusive both ends
        assert asofs[0] == start
        assert asofs[-1] == end


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------

class TestRunHorizonBacktest:
    def test_walk_forward_replay_persists_outcomes(self, tmp_path: Path):
        bars = _make_bars(800, seed=11)
        loader = make_static_bar_loader({("NQ", "H25", "5m"): bars})
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        agent = StatisticalProbabilityAgent(
            config=StatisticalProbabilityAgentConfig(
                min_samples_local=8,
                min_samples_global=30,
                history_lookback_bars=600,
            ),
        )

        summary = run_walk_forward_replay(
            agents=[agent],
            bar_loader=loader,
            instrument="NQ",
            contract="H25",
            timeframes=["5m"],
            horizons=[3],
            asof_warmup=400,
            asof_stride=20,
            store=store,
        )
        assert isinstance(summary, HorizonBacktestSummary)
        assert summary.n_specs > 0
        assert summary.n_predictions == summary.n_specs    # one agent per spec
        assert summary.n_outcomes_measured > 0
        # Every measured outcome was persisted
        assert len(store.get_all_outcomes()) == summary.n_outcomes_measured
        # Composite score is finite and in [0,1]
        for o in store.get_all_outcomes():
            assert 0.0 <= o.composite_score <= 1.0

    def test_skip_unmeasurable_drops_late_asofs(self, tmp_path: Path):
        bars = _make_bars(300, seed=13)
        loader = make_static_bar_loader({("NQ", "H25", "5m"): bars})
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        runner = HorizonBatchRunner(agents=[_StubAgent()], bar_loader=loader, store=store)
        # asof at the very end has no future bars to measure against
        specs = [
            HorizonBatchSpec("NQ", "H25", "5m", 3, 250),  # measurable
            HorizonBatchSpec("NQ", "H25", "5m", 3, 298),  # NOT measurable (only 1 future bar)
        ]
        summary = run_horizon_backtest(runner, specs)
        assert summary.n_predictions == 2
        assert summary.n_outcomes_measured == 1
        assert summary.n_outcomes_skipped == 1

    def test_summary_aggregates_per_agent(self, tmp_path: Path):
        bars = _make_bars(400, seed=15)
        loader = make_static_bar_loader({("NQ", "H25", "5m"): bars})
        runner = HorizonBatchRunner(
            agents=[_StubAgent("a"), _StubAgent("b")],
            bar_loader=loader,
        )
        specs = [HorizonBatchSpec("NQ", "H25", "5m", 3, i) for i in (200, 250, 300)]
        summary = run_horizon_backtest(runner, specs)
        assert set(summary.by_agent.keys()) == {"a", "b"}
        for agent_id, acc in summary.by_agent.items():
            assert acc.get("predictions", 0) == 3
            assert acc.get("outcomes_measured", 0) == 3
            assert "mean_composite_score" in acc

    def test_abstain_predictions_dont_inflate_mean(self, tmp_path: Path):
        # 50 historical bars is not enough for the stat agent to come out of
        # abstain, so the mean composite should stay at 0.0
        bars = _make_bars(60, seed=17)
        loader = make_static_bar_loader({("NQ", "H25", "5m"): bars})
        agent = StatisticalProbabilityAgent(
            config=StatisticalProbabilityAgentConfig(
                min_samples_global=200,
                history_lookback_bars=80,
            ),
        )
        runner = HorizonBatchRunner(agents=[agent], bar_loader=loader)
        summary = run_horizon_backtest(
            runner,
            [HorizonBatchSpec("NQ", "H25", "5m", 3, 50)],
        )
        assert summary.n_abstentions >= 1
        assert summary.mean_composite_score == 0.0


# ---------------------------------------------------------------------------
# horizon_reports
# ---------------------------------------------------------------------------

def _make_pairs(
    *,
    agent_id: str,
    n: int,
    accuracy: float,
    composite: float,
    timeframe: str = "5m",
    horizon: int = 3,
    session: str = "ny_open",
    regime: str = "trend_up",
    seed: int = 1,
    asof_anchor: str = "2026-04-01T09:30:00-06:00",
):
    """
    Build n predictions/outcomes with a controlled empirical accuracy and a
    target mean composite score. Each prediction has p_bull = 0.7 (strongly
    bullish) so that argmax direction is bullish.
    """
    rng = np.random.default_rng(seed)
    pairs = []
    base_ts = pd.Timestamp(asof_anchor)
    for i in range(n):
        is_correct = rng.random() < accuracy
        actual = "bullish" if is_correct else "bearish"
        pred = _pred(
            agent_id=agent_id,
            timeframe=timeframe,
            horizon=horizon,
            session=session,
            regime=regime,
            p_bull=0.7, p_bear=0.15, p_neu=0.15,
            asof_iso=(base_ts + pd.Timedelta(minutes=5 * i)).isoformat(),
        )
        out = _outcome(
            pred_id=pred.prediction_id,
            actual_direction=actual,
            composite_score=composite + rng.normal(0, 0.01),
            brier_dir=0.2,
            actual_return_atr=1.0 if actual == "bullish" else -1.0,
            timeframe=timeframe,
            horizon=horizon,
        )
        pairs.append((pred, out))
    return pairs


class TestAgentLeaderboard:
    def test_leaderboard_orders_by_composite(self):
        pairs = (
            _make_pairs(agent_id="winner", n=60, accuracy=0.75, composite=0.6, seed=1)
            + _make_pairs(agent_id="loser", n=60, accuracy=0.30, composite=0.2, seed=2)
        )
        rows = build_agent_leaderboard(pairs)
        assert len(rows) == 2
        assert rows[0].agent_id == "winner"
        assert rows[0].mean_composite_score > rows[1].mean_composite_score
        assert rows[0].direction_accuracy > rows[1].direction_accuracy

    def test_abstention_rate(self):
        pairs = _make_pairs(agent_id="a", n=10, accuracy=0.7, composite=0.5)
        # Add 10 abstentions
        for i in range(10):
            p = _pred(agent_id="a", abstain=True, asof_iso=f"2026-05-01T10:{i:02d}:00-06:00")
            o = _outcome(pred_id=p.prediction_id, actual_direction="bullish")
            pairs.append((p, o))
        rows = build_agent_leaderboard(pairs)
        assert len(rows) == 1
        assert rows[0].sample_count == 20
        assert rows[0].sample_count_non_abstain == 10
        assert rows[0].abstention_rate == pytest.approx(0.5)

    def test_drift_flag_triggers_on_recent_dropoff(self):
        # 200 strong predictions, then 60 weak ones — recent mean below long mean
        strong = _make_pairs(
            agent_id="a", n=200, accuracy=0.7, composite=0.6, seed=10,
            asof_anchor="2026-01-01T09:30:00-06:00",
        )
        weak = _make_pairs(
            agent_id="a", n=60, accuracy=0.3, composite=0.2, seed=11,
            asof_anchor="2026-04-01T09:30:00-06:00",
        )
        rows = build_agent_leaderboard(strong + weak, drift_recent_n=50, drift_threshold=0.05)
        row = next(r for r in rows if r.agent_id == "a")
        assert row.drift_delta < -0.05
        assert row.drift_flag is True

    def test_format_renders_no_errors(self):
        pairs = _make_pairs(agent_id="a", n=20, accuracy=0.7, composite=0.5)
        rows = build_agent_leaderboard(pairs)
        text = format_agent_leaderboard(rows)
        assert "agent_id" in text
        assert "a" in text


class TestTimeframeHorizonMatrix:
    def test_groups_by_tf_and_horizon(self):
        pairs = (
            _make_pairs(agent_id="a", n=20, accuracy=0.7, composite=0.6,
                        timeframe="5m", horizon=3, seed=1)
            + _make_pairs(agent_id="a", n=20, accuracy=0.5, composite=0.4,
                          timeframe="15m", horizon=3, seed=2)
            + _make_pairs(agent_id="a", n=20, accuracy=0.6, composite=0.5,
                          timeframe="5m", horizon=5, seed=3)
        )
        cells = build_timeframe_horizon_matrix(pairs)
        assert len(cells) == 3
        cell_keys = {(c.timeframe, c.horizon_candles) for c in cells}
        assert ("5m", 3) in cell_keys
        assert ("15m", 3) in cell_keys
        assert ("5m", 5) in cell_keys

    def test_min_samples_drops_small_cells(self):
        pairs = (
            _make_pairs(agent_id="a", n=30, accuracy=0.7, composite=0.6,
                        timeframe="5m", horizon=3)
            + _make_pairs(agent_id="a", n=3, accuracy=0.5, composite=0.4,
                          timeframe="15m", horizon=3, seed=99)
        )
        cells = build_timeframe_horizon_matrix(pairs, min_samples=10)
        assert len(cells) == 1
        assert cells[0].timeframe == "5m"


class TestSessionMatrix:
    def test_orders_by_composite_descending(self):
        pairs = (
            _make_pairs(agent_id="a", n=20, accuracy=0.7, composite=0.6,
                        session="ny_open", seed=1)
            + _make_pairs(agent_id="a", n=20, accuracy=0.4, composite=0.3,
                          session="london", seed=2)
        )
        cells = build_session_matrix(pairs)
        assert cells[0].session_label == "ny_open"
        assert cells[1].session_label == "london"


class TestBestEdgeCells:
    def test_edge_positive_when_argmax_matches_returns(self):
        # 30 predictions p_bull=0.7, all return +1 ATR → edge ≈ +1.0
        pairs = []
        for i in range(30):
            p = _pred(agent_id="a", p_bull=0.7, p_bear=0.2, p_neu=0.1,
                      asof_iso=f"2026-04-01T09:{i:02d}:00-06:00")
            o = _outcome(pred_id=p.prediction_id, actual_direction="bullish",
                         actual_return_atr=1.0)
            pairs.append((p, o))
        cells = build_best_edge_cells(pairs, min_samples=10)
        assert len(cells) == 1
        assert cells[0].realized_edge_atr == pytest.approx(1.0, abs=1e-6)

    def test_edge_negative_when_predictions_wrong(self):
        # bullish predictions, bearish realizations → negative edge
        pairs = []
        for i in range(30):
            p = _pred(agent_id="a", p_bull=0.7, p_bear=0.2, p_neu=0.1,
                      asof_iso=f"2026-04-01T09:{i:02d}:00-06:00")
            o = _outcome(pred_id=p.prediction_id, actual_direction="bearish",
                         actual_return_atr=-1.0)
            pairs.append((p, o))
        cells = build_best_edge_cells(pairs, min_samples=10)
        assert cells[0].realized_edge_atr == pytest.approx(-1.0, abs=1e-6)

    def test_min_samples_filter(self):
        # Tiny cell should not appear
        pairs = []
        for i in range(5):
            p = _pred(agent_id="a", asof_iso=f"2026-04-01T09:{i:02d}:00-06:00")
            o = _outcome(pred_id=p.prediction_id, actual_direction="bullish",
                         actual_return_atr=1.0)
            pairs.append((p, o))
        cells = build_best_edge_cells(pairs, min_samples=10)
        assert cells == []


class TestCalibrationReport:
    def test_well_and_poorly_calibrated_buckets(self):
        rng = np.random.default_rng(101)
        pairs = []
        # well-calibrated bucket
        for i in range(80):
            p = _pred(agent_id="A", session="ny_open",
                      p_bull=0.7, p_bear=0.15, p_neu=0.15,
                      asof_iso=f"2026-04-01T09:{(i % 60):02d}:00-06:00")
            actual = "bullish" if rng.random() < 0.7 else "bearish"
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction=actual)))
        # overconfident bucket
        for i in range(80):
            p = _pred(agent_id="B", session="ny_open",
                      p_bull=0.95, p_bear=0.025, p_neu=0.025,
                      asof_iso=f"2026-04-02T09:{(i % 60):02d}:00-06:00")
            actual = "bullish" if rng.random() < 0.30 else "bearish"
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction=actual)))

        entries = build_calibration_report(pairs, min_samples=20)
        assert len(entries) == 2
        # Worst-calibrated first
        assert entries[0].bucket.agent_id == "B"
        assert entries[0].ece > entries[1].ece


class TestDriftReport:
    def test_drift_report_flags_significant_shift(self):
        # Long stable period, then a sharp drop in recent composite scores
        strong = _make_pairs(
            agent_id="a", n=300, accuracy=0.7, composite=0.6, seed=10,
            asof_anchor="2026-01-01T09:30:00-06:00",
        )
        weak = _make_pairs(
            agent_id="a", n=60, accuracy=0.3, composite=0.2, seed=11,
            asof_anchor="2026-04-01T09:30:00-06:00",
        )
        rows = build_drift_report(strong + weak, recent_window=50, z_threshold=2.0)
        assert len(rows) == 1
        assert rows[0].agent_id == "a"
        assert rows[0].delta < 0
        assert rows[0].drift_flag is True

    def test_drift_report_quiet_on_stable_stream(self):
        pairs = _make_pairs(agent_id="a", n=200, accuracy=0.7, composite=0.6, seed=42)
        rows = build_drift_report(pairs, recent_window=50, z_threshold=2.0)
        # No abrupt change → flag should be False
        assert rows[0].drift_flag is False


class TestFullReport:
    def test_full_report_runs_end_to_end(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        # populate with a mix of agents and outcomes
        all_pairs = (
            _make_pairs(agent_id="a", n=40, accuracy=0.7, composite=0.6, seed=1)
            + _make_pairs(agent_id="b", n=40, accuracy=0.4, composite=0.3, seed=2,
                          session="london")
        )
        for pred, out in all_pairs:
            store.save_prediction(pred)
            store.save_outcome(out)

        bundle = build_full_report(
            store,
            min_samples_cell=5,
            min_samples_edge=10,
            min_samples_calibration=10,
        )
        assert bundle.leaderboard, "leaderboard rows expected"
        assert bundle.timeframe_horizon, "tf x horizon cells expected"
        assert bundle.session_matrix, "session cells expected"
        assert bundle.best_edge, "edge cells expected"
        # Calibration may need more samples to be useful, but should at least run
        assert isinstance(bundle.calibration, list)

        text = format_full_report(bundle)
        assert "Agent Leaderboard" in text
        assert "Best-Edge" in text
