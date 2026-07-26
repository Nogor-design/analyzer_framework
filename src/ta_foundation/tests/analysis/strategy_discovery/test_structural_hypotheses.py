from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.strategy_discovery.structural_hypotheses import (
    HYPOTHESES,
    evaluate_hypothesis_on_panel,
    first_bar_follow_through,
    gap_fade_at_cash_open,
    last_half_hour_reversal,
    run_all_hypotheses,
)


def _bars(rows):
    """rows: [(dt_str_local, o, h, l, c), ...] -- minute bars tz-aware Denver."""
    dts = pd.to_datetime([r[0] for r in rows]).tz_localize("America/Denver")
    return pd.DataFrame({
        "dt": dts,
        "open":  [r[1] for r in rows],
        "high":  [r[2] for r in rows],
        "low":   [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [1] * len(rows),
    })


class TestGapFade:
    def test_positive_gap_produces_short_hitting_target(self):
        bars = _bars([
            ("2026-01-05 14:00", 100.0, 100.0, 100.0, 100.0),  # prior cash close
            ("2026-01-06 07:30", 105.0, 105.0, 105.0, 105.0),  # gap +5.0 = 20 ticks
            ("2026-01-06 07:31", 105.0, 105.5, 104.5, 105.0),  # entry at 105.0
            ("2026-01-06 07:32", 104.0, 104.0,  99.5, 100.0),  # low hits TP at 100.0
        ])
        trades = gap_fade_at_cash_open(bars, tick_size=0.25)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == -1
        assert t.entry_price == 105.0
        assert t.exit_price == 100.0
        assert t.result == "tp"
        assert t.profit_ticks(0.25) == 20.0

    def test_small_gap_below_threshold_no_trade(self):
        bars = _bars([
            ("2026-01-05 14:00", 100.0, 100.0, 100.0, 100.0),
            ("2026-01-06 07:30", 101.0, 101.0, 101.0, 101.0),  # 4 ticks < 8 threshold
            ("2026-01-06 07:31", 101.0, 101.0, 101.0, 101.0),
        ])
        assert gap_fade_at_cash_open(bars, tick_size=0.25) == []

    def test_missing_prior_close_or_open_no_trade(self):
        bars = _bars([("2026-01-06 07:30", 105.0, 105.0, 105.0, 105.0),
                      ("2026-01-06 07:31", 105.0, 105.0, 105.0, 105.0)])
        assert gap_fade_at_cash_open(bars, tick_size=0.25) == []


class TestLastHalfHourReversal:
    def test_upward_trend_short_hits_target(self):
        bars = _bars([
            ("2026-01-06 07:30", 100.0, 100.0, 100.0, 100.0),
            ("2026-01-06 13:30", 104.5, 105.0, 104.5, 105.0),  # trend +5 = 20 ticks
            ("2026-01-06 13:31", 105.0, 105.0, 104.0, 104.0),  # entry at 105.0
            ("2026-01-06 13:32", 103.5, 103.5, 102.0, 102.5),  # low hits TP at 102.5
        ])
        trades = last_half_hour_reversal(bars, tick_size=0.25)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == -1
        assert t.entry_price == 105.0
        assert t.exit_price == 102.5
        assert t.result == "tp"
        assert t.profit_ticks(0.25) == 10.0

    def test_weak_trend_no_trade(self):
        bars = _bars([
            ("2026-01-06 07:30", 100.0, 100.0, 100.0, 100.0),
            ("2026-01-06 13:30", 100.5, 101.0, 100.5, 101.0),  # 4 ticks < 10
            ("2026-01-06 13:31", 101.0, 101.0, 101.0, 101.0),
        ])
        assert last_half_hour_reversal(bars, tick_size=0.25) == []


class TestFirstBarFollowThrough:
    def test_bullish_first_bar_long_hits_target(self):
        bars = _bars([
            ("2026-01-06 07:30", 100.0, 101.0, 100.0, 101.0),  # body +1.0 = +4 ticks
            ("2026-01-06 07:31", 101.0, 101.0, 100.5, 101.0),  # entry at 101.0
            ("2026-01-06 07:32", 102.0, 103.5, 101.5, 103.0),  # high hits TP at 103.0
        ])
        trades = first_bar_follow_through(bars, tick_size=0.25)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == 1
        assert t.entry_price == 101.0
        assert t.exit_price == 103.0
        assert t.result == "tp"
        assert t.profit_ticks(0.25) == 8.0

    def test_small_body_no_trade(self):
        bars = _bars([
            ("2026-01-06 07:30", 100.0, 100.25, 100.0, 100.25),  # 1 tick < 3 threshold
            ("2026-01-06 07:31", 100.25, 100.25, 100.25, 100.25),
        ])
        assert first_bar_follow_through(bars, tick_size=0.25) == []


def test_registry_lists_all_hypotheses():
    names = {h.name for h in HYPOTHESES}
    assert names == {"gap_fade_at_cash_open",
                     "last_half_hour_reversal",
                     "first_bar_follow_through",
                     "overnight_drift",
                     "intraday_range_reversion",
                     "closing_auction_reversal",
                     "large_gap_continuation"}


class TestClosingAuctionReversal:
    def test_upward_prior_hour_fades_short(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import closing_auction_reversal
        # prior-hour close at 12:50 = 100, trigger 13:50 close = 105 (trend +20 ticks)
        bars = _bars([
            ("2026-01-06 12:50", 100.0, 100.0, 100.0, 100.0),
            ("2026-01-06 13:50", 104.5, 105.0, 104.5, 105.0),  # trigger
            ("2026-01-06 13:51", 105.0, 105.0, 104.0, 104.0),  # entry @ 105
            ("2026-01-06 13:52", 103.5, 103.5, 102.0, 102.5),  # low hits TP at 102.5
        ])
        trades = closing_auction_reversal(bars, tick_size=0.25)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == -1
        assert t.entry_price == 105.0
        assert t.exit_price == 102.5  # trigger_close - 0.5*trend = 105 - 2.5
        assert t.result == "tp"

    def test_weak_prior_hour_no_trade(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import closing_auction_reversal
        bars = _bars([
            ("2026-01-06 12:50", 100.0, 100.0, 100.0, 100.0),
            ("2026-01-06 13:50", 100.5, 101.0, 100.5, 101.0),  # 4-tick trend < 8 threshold
            ("2026-01-06 13:51", 101.0, 101.0, 101.0, 101.0),
        ])
        assert closing_auction_reversal(bars, tick_size=0.25) == []


class TestLargeGapContinuation:
    def test_large_positive_gap_continues_long(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import large_gap_continuation
        # prior day close 100, today open 110 -> gap +10 = 40 ticks > 30 threshold
        bars = _bars([
            ("2026-01-05 14:00", 100.0, 100.0, 100.0, 100.0),
            ("2026-01-06 07:30", 110.0, 110.0, 110.0, 110.0),
            ("2026-01-06 07:31", 110.0, 110.0, 110.0, 110.0),  # entry @ 110
            ("2026-01-06 07:32", 113.0, 115.5, 113.0, 115.0),  # high hits TP @ 115
        ])
        trades = large_gap_continuation(bars, tick_size=0.25)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == 1
        assert t.entry_price == 110.0
        assert t.exit_price == 115.0  # entry + 0.5*|gap| = 110 + 5
        assert t.result == "tp"

    def test_small_gap_below_threshold_no_trade(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import large_gap_continuation
        bars = _bars([
            ("2026-01-05 14:00", 100.0, 100.0, 100.0, 100.0),
            ("2026-01-06 07:30", 105.0, 105.0, 105.0, 105.0),  # 20-tick gap < 30 threshold
            ("2026-01-06 07:31", 105.0, 105.0, 105.0, 105.0),
        ])
        assert large_gap_continuation(bars, tick_size=0.25) == []


class TestIntradayRangeReversion:
    def test_extreme_range_at_high_fades_short(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import intraday_range_reversion
        # day_open=10000, range so far at 11:00 = 70 (>0.5% of 10000=50), close at high
        bars = _bars([
            ("2026-01-06 07:30", 10000.0, 10000.0, 10000.0, 10000.0),
            ("2026-01-06 09:00", 10060.0, 10060.0, 10060.0, 10060.0),  # high
            ("2026-01-06 09:30",  9990.0,  9990.0,  9990.0,  9990.0),  # low
            ("2026-01-06 11:00", 10060.0, 10060.0, 10060.0, 10060.0),  # trigger close = high
            ("2026-01-06 11:01", 10060.0, 10060.0, 10060.0, 10060.0),  # entry
            ("2026-01-06 11:02", 10060.0, 10060.0, 10025.0, 10025.0),  # low hits TP
        ])
        trades = intraday_range_reversion(bars, tick_size=0.25)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == -1
        assert t.exit_price == 10025.0  # midpoint (10060+9990)/2
        assert t.result == "tp"

    def test_small_range_no_trade(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import intraday_range_reversion
        bars = _bars([
            ("2026-01-06 07:30", 10000.0, 10000.0, 10000.0, 10000.0),
            ("2026-01-06 11:00", 10010.0, 10010.0, 10010.0, 10010.0),  # range 10 < 50 threshold
            ("2026-01-06 11:01", 10010.0, 10010.0, 10010.0, 10010.0),
        ])
        assert intraday_range_reversion(bars, tick_size=0.25) == []


class TestTemporalRobustness:
    """Within-instrument IS/OOS validator -- the test for instrument-specific
    edge candidates that cross-instrument validation would over-reject."""

    def _fake_hyp(self, pnl_ticks_net):
        """A hypothesis whose trades net (after a 3-tick cost) to the given list."""
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import (
            StructuralHypothesis, Trade,
        )
        cost = 3.0
        trades = []
        for i, p in enumerate(pnl_ticks_net):
            gross_ticks = p + cost
            exit_price = gross_ticks * 0.25
            trades.append(Trade(
                day=f"d{i:03d}", direction=1,
                entry_dt=pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i),
                entry_price=0.0,
                exit_dt=pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i, minutes=30),
                exit_price=exit_price,
                result="tp" if p > 0 else "sl",
            ))
        return StructuralHypothesis(name="fake", description="test", fn=lambda b, t: trades)

    def test_strong_is_confirmed_oos_passes(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import (
            evaluate_temporal_robustness,
        )
        # 30 IS trades alternating +5/+3 (mean +4, std ~1, t ~22), 20 OOS
        # trades alternating +3/+1 (mean +2, t ~9) -- confirmed positive both.
        is_pnl = [5.0, 3.0] * 15
        oos_pnl = [3.0, 1.0] * 10
        hyp = self._fake_hyp(is_pnl + oos_pnl)
        v = evaluate_temporal_robustness(hyp, bars=pd.DataFrame(), tick_size=0.25)
        assert v.candidate_edge is True
        assert v.is_stats["n"] == 30
        assert v.oos_stats["n"] == 20
        assert v.is_stats["mean"] > 0
        assert v.oos_stats["mean"] > 0
        assert v.is_stats["t"] > 1.5
        assert v.oos_stats["t"] > 0.5

    def test_strong_is_but_negative_oos_fails(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import (
            evaluate_temporal_robustness,
        )
        # IS strong (mean +4), OOS reverses (mean -2) -> overfit, not an edge
        is_pnl = [5.0, 3.0] * 15
        oos_pnl = [-1.0, -3.0] * 10
        hyp = self._fake_hyp(is_pnl + oos_pnl)
        v = evaluate_temporal_robustness(hyp, bars=pd.DataFrame(), tick_size=0.25)
        assert v.candidate_edge is False
        assert any(r.startswith("FAIL") and "oos" in r for r in v.reasons)


class TestOvernightDrift:
    def test_close_to_next_open_long_trade(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import overnight_drift
        bars = _bars([
            ("2026-01-05 14:00", 100.0, 100.0, 100.0, 100.0),  # day 1 close
            ("2026-01-06 07:30", 105.0, 105.0, 105.0, 105.0),  # day 2 open
        ])
        trades = overnight_drift(bars, tick_size=0.25)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == 1
        assert t.entry_price == 100.0
        assert t.exit_price == 105.0
        assert t.profit_ticks(0.25) == 20.0
        assert t.result == "tp"

    def test_negative_overnight_recorded_as_loss(self):
        from ta_foundation.analysis.strategy_discovery.structural_hypotheses import overnight_drift
        bars = _bars([
            ("2026-01-05 14:00", 100.0, 100.0, 100.0, 100.0),
            ("2026-01-06 07:30",  95.0,  95.0,  95.0,  95.0),
        ])
        trades = overnight_drift(bars, tick_size=0.25)
        assert len(trades) == 1
        assert trades[0].result == "sl"
        assert trades[0].profit_ticks(0.25) == -20.0


def test_evaluate_on_panel_uses_cross_instrument_gate():
    empty = _bars([("2026-01-06 09:00", 100.0, 100.0, 100.0, 100.0)])
    panel = {n: empty for n in ("NQ", "ES", "RTY", "YM")}
    ts = {n: 0.25 for n in panel}
    v = evaluate_hypothesis_on_panel(HYPOTHESES[0], panel, ts)
    assert v.is_robust_edge is False
    assert v.pooled_trades == 0


def test_run_all_hypotheses_returns_a_verdict_per_hypothesis():
    empty = _bars([("2026-01-06 09:00", 100.0, 100.0, 100.0, 100.0)])
    panel = {n: empty for n in ("NQ", "ES", "RTY", "YM")}
    ts = {n: 0.25 for n in panel}
    out = run_all_hypotheses(panel, ts)
    assert set(out) == {h.name for h in HYPOTHESES}
