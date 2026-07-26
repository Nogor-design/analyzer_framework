"""
Smoke tests for the Strategy Composer pipeline.

All tests use synthetic 1-minute bar data — no real market files needed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import pytz

from ta_foundation.analysis.strategy_composer.template import StrategyTemplate
from ta_foundation.analysis.strategy_composer.composer import StrategyComposer
from ta_foundation.analysis.strategy_composer.llm import _extract_json


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DENVER = pytz.timezone("America/Denver")
_N_BARS = 2000  # ~33 trading hours of 1m bars


def _make_bars(n: int = _N_BARS, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2025-01-06 07:00:00", tz="America/Denver")
    # build simple random walk
    dts = pd.date_range(base, periods=n, freq="1min")
    close = 21_000.0 + np.cumsum(rng.standard_normal(n) * 5)
    spread = rng.uniform(0.5, 2.5, n)
    high = close + rng.uniform(1, 4, n)
    low = close - rng.uniform(1, 4, n)
    open_ = close - spread
    volume = rng.integers(100, 1000, n).astype(float)
    return pd.DataFrame({
        "dt": dts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture(scope="module")
def bars_1m():
    return _make_bars()


def _template(signal_type: str, extra: dict | None = None) -> StrategyTemplate:
    base = {
        "template_name": f"smoke_{signal_type}",
        "hypothesis": "Smoke test hypothesis — not a real edge",
        "instrument": "NQ",
        "contract": "H25",
        "timeframe": "5m",
        "direction": "long",
        "entry_signal": {"type": signal_type, "timing": "next_open"},
        "entry_filters": [],
        "stop_mode": "signal_extreme_capped",
        "hard_stop_ticks_cap": 40,
        "initial_target_ticks": 30,
        "partial_rules": {"enabled": False},
        "runner_rules": {"enabled": False},
        "session_rules": {
            "enabled": True,
            "session_start": "08:30:00",
            "session_end": "15:00:00",
            "timezone": "America/Denver",
        },
        "max_hold_bars": 20,
        "flatten_on_session_end": True,
    }
    if extra:
        base["entry_signal"].update(extra)
    return StrategyTemplate.from_dict(base)


# ---------------------------------------------------------------------------
# StrategyTemplate tests
# ---------------------------------------------------------------------------

class TestStrategyTemplate:
    def test_from_dict_roundtrip(self):
        tmpl = _template("candle_pattern", {"pattern": "pin_bar_bullish"})
        d = tmpl.to_dict()
        assert d["template_name"] == "smoke_candle_pattern"
        assert d["entry_signal"]["type"] == "candle_pattern"

    def test_underscore_keys_stripped(self):
        d = {
            "template_name": "test",
            "hypothesis": "h",
            "instrument": "NQ",
            "contract": "H25",
            "timeframe": "5m",
            "direction": "long",
            "entry_signal": {"type": "candle_pattern", "pattern": "doji", "timing": "next_open"},
            "entry_filters": [],
            "stop_mode": "fixed_ticks",
            "hard_stop_ticks_cap": 30,
            "initial_target_ticks": 20,
            "partial_rules": {"enabled": False},
            "runner_rules": {"enabled": False},
            "session_rules": {"enabled": False},
            "max_hold_bars": 10,
            "flatten_on_session_end": False,
            "_comment": "this should be stripped",
            "_valid_values": {"direction": ["long", "short"]},
        }
        tmpl = StrategyTemplate.from_dict(d)
        out = tmpl.to_dict()
        assert "_comment" not in out
        assert "_valid_values" not in out

    def test_to_outcome_config_keys(self):
        tmpl = _template("candle_pattern", {"pattern": "engulfing_bullish"})
        cfg = tmpl.to_outcome_config()
        assert "ticks" in cfg
        assert "max_bars_timeout" in cfg
        assert cfg["ticks"]["enabled"] is True


# ---------------------------------------------------------------------------
# StrategyComposer pipeline tests (signal type coverage)
# ---------------------------------------------------------------------------

class TestStrategyComposerSignals:
    """Each test verifies that a specific signal type runs without crashing
    and returns a result dict with the expected keys."""

    def _run(self, bars, signal_type, extra=None):
        tmpl = _template(signal_type, extra)
        sc = StrategyComposer(bars_1m=bars, template=tmpl)
        result = sc.run()
        assert "n_signals" in result
        assert "n_trades" in result
        assert "summary" in result
        assert isinstance(result["summary"], str)
        assert result["template_name"] == f"smoke_{signal_type}"
        return result

    def test_candle_pattern_pin_bar(self, bars_1m):
        self._run(bars_1m, "candle_pattern", {"pattern": "pin_bar_bullish"})

    def test_candle_pattern_engulfing(self, bars_1m):
        self._run(bars_1m, "candle_pattern", {"pattern": "engulfing_bullish"})

    def test_candle_pattern_doji(self, bars_1m):
        self._run(bars_1m, "candle_pattern", {"pattern": "doji"})

    def test_candle_pattern_inside_bar(self, bars_1m):
        self._run(bars_1m, "candle_pattern", {"pattern": "inside_bar"})

    def test_candle_pattern_large_body(self, bars_1m):
        self._run(bars_1m, "candle_pattern", {"pattern": "large_body"})

    def test_ma_cross(self, bars_1m):
        result = self._run(bars_1m, "ma_cross", {
            "fast_type": "ema", "fast_period": 9,
            "slow_type": "sma", "slow_period": 21,
        })
        assert result["n_signals"] >= 0

    def test_ma_touch(self, bars_1m):
        self._run(bars_1m, "ma_touch", {
            "ma_type": "ema", "period": 20, "touch_tolerance_pct": 0.1,
        })

    def test_breakout(self, bars_1m):
        self._run(bars_1m, "breakout", {"lookback_bars": 10, "confirmation_bars": 1})

    def test_rsi_threshold(self, bars_1m):
        self._run(bars_1m, "rsi_threshold", {
            "period": 14, "threshold": 30.0, "cross_direction": "cross_above",
        })

    def test_orb(self, bars_1m):
        self._run(bars_1m, "orb", {
            "orb_minutes": 15, "session_start": "07:30", "retest_bars": 1,
        })

    def test_price_level_vwap(self, bars_1m):
        self._run(bars_1m, "price_level", {
            "level_type": "vwap",
            "touch_direction": "bounce_up",
            "touch_tolerance_ticks": 4,
        })

    def test_price_level_or_high(self, bars_1m):
        self._run(bars_1m, "price_level", {
            "level_type": "or_high",
            "touch_direction": "bounce_up",
            "touch_tolerance_ticks": 4,
            "orb_minutes": 15,
        })


# ---------------------------------------------------------------------------
# Filter coverage
# ---------------------------------------------------------------------------

class TestEntryFilters:
    def test_trend_filter(self, bars_1m):
        d = {
            "template_name": "filter_trend",
            "hypothesis": "Trend filter smoke",
            "instrument": "NQ",
            "contract": "H25",
            "timeframe": "5m",
            "direction": "long",
            "entry_signal": {"type": "candle_pattern", "pattern": "pin_bar_bullish", "timing": "next_open"},
            "entry_filters": [
                {"type": "trend", "indicator": "sma", "period": 50,
                 "condition": "price_above", "description": "above 50 sma"},
            ],
            "stop_mode": "signal_extreme_capped",
            "hard_stop_ticks_cap": 40,
            "initial_target_ticks": 30,
            "partial_rules": {"enabled": False},
            "runner_rules": {"enabled": False},
            "session_rules": {"enabled": False},
            "max_hold_bars": 20,
            "flatten_on_session_end": True,
        }
        tmpl = StrategyTemplate.from_dict(d)
        sc = StrategyComposer(bars_1m=bars_1m, template=tmpl)
        result = sc.run()
        assert result["n_signals"] >= 0

    def test_rsi_filter(self, bars_1m):
        d = {
            "template_name": "filter_rsi",
            "hypothesis": "RSI filter smoke",
            "instrument": "NQ",
            "contract": "H25",
            "timeframe": "5m",
            "direction": "long",
            "entry_signal": {"type": "ma_cross",
                             "fast_type": "ema", "fast_period": 9,
                             "slow_type": "sma", "slow_period": 21,
                             "timing": "next_open"},
            "entry_filters": [
                {"type": "rsi", "period": 14, "condition": "below",
                 "threshold": 60.0, "description": "rsi not overbought"},
            ],
            "stop_mode": "signal_extreme_capped",
            "hard_stop_ticks_cap": 40,
            "initial_target_ticks": 30,
            "partial_rules": {"enabled": False},
            "runner_rules": {"enabled": False},
            "session_rules": {"enabled": False},
            "max_hold_bars": 20,
            "flatten_on_session_end": True,
        }
        tmpl = StrategyTemplate.from_dict(d)
        sc = StrategyComposer(bars_1m=bars_1m, template=tmpl)
        result = sc.run()
        assert result["n_signals"] >= 0


# ---------------------------------------------------------------------------
# Timeframe coverage
# ---------------------------------------------------------------------------

class TestTimeframes:
    @pytest.mark.parametrize("tf", ["1m", "3m", "5m", "15m", "30m"])
    def test_resampling(self, bars_1m, tf):
        d = {
            "template_name": f"tf_{tf}",
            "hypothesis": "Timeframe smoke",
            "instrument": "NQ",
            "contract": "H25",
            "timeframe": tf,
            "direction": "long",
            "entry_signal": {"type": "candle_pattern", "pattern": "doji", "timing": "next_open"},
            "entry_filters": [],
            "stop_mode": "fixed_ticks",
            "hard_stop_ticks_cap": 40,
            "initial_target_ticks": 30,
            "partial_rules": {"enabled": False},
            "runner_rules": {"enabled": False},
            "session_rules": {"enabled": False},
            "max_hold_bars": 20,
            "flatten_on_session_end": False,
        }
        tmpl = StrategyTemplate.from_dict(d)
        sc = StrategyComposer(bars_1m=bars_1m, template=tmpl)
        result = sc.run()
        assert "n_signals" in result


# ---------------------------------------------------------------------------
# LLM helper tests (no network required)
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json(self):
        raw = '{"template_name": "test", "direction": "long"}'
        result = _extract_json(raw)
        assert result["template_name"] == "test"

    def test_strips_markdown_fences(self):
        raw = "```json\n{\"a\": 1}\n```"
        result = _extract_json(raw)
        assert result["a"] == 1

    def test_brace_depth_extraction(self):
        raw = 'some text before {"key": "value"} some text after'
        result = _extract_json(raw)
        assert result["key"] == "value"

    def test_raises_on_invalid(self):
        with pytest.raises(ValueError):
            _extract_json("this is not json at all")

    def test_nested_json(self):
        raw = '{"entry_signal": {"type": "ma_cross", "fast_period": 9}, "n": 42}'
        result = _extract_json(raw)
        assert result["entry_signal"]["fast_period"] == 9
