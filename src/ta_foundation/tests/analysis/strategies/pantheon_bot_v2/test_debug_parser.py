"""
Self-contained tests for the EnableDebugPrint line parser. These do not
require an NT capture and run on every CI invocation.
"""

from __future__ import annotations

from ta_foundation.analysis.strategies.pantheon_bot_v2.debug_parser import (
    parse_debug_log,
)


SAMPLE_LINES = [
    "2026-05-15 09:30:00 | Trend=Up slope=0.0421 | VWAP=Above distATR=0.342 | Vol=Mid pct=54.2%",
    "2026-05-15 09:35:00 | Trend=Down slope=-0.0510 | VWAP=Below distATR=-0.812 | Vol=High pct=78.0%",
    "2026-05-15 09:40:00 | Trend=Flat slope=0.0010 | VWAP=Near distATR=0.050 | Vol=Low pct=12.5%",
]


def test_parses_well_formed_lines():
    result = parse_debug_log(SAMPLE_LINES)
    assert result.n_parsed == 3
    assert result.n_skipped == 0
    df = result.df
    assert list(df["trend_cs"]) == ["up", "down", "flat"]
    assert list(df["vwap_cs"]) == ["above", "below", "near"]
    assert list(df["vol_cs"]) == ["mid", "high", "low"]
    assert df["slope"].tolist() == [0.0421, -0.0510, 0.0010]
    assert df["dist_atr"].tolist() == [0.342, -0.812, 0.050]
    assert df["pct"].tolist() == [0.542, 0.780, 0.125]


def test_tz_localization_is_denver():
    result = parse_debug_log(SAMPLE_LINES)
    assert str(result.df["dt"].dt.tz) == "America/Denver"


def test_skips_malformed_lines_and_keeps_samples():
    lines = SAMPLE_LINES + [
        "not a debug line",
        "2026-05-15 09:45:00 something weird",
        "",
    ]
    result = parse_debug_log(lines)
    assert result.n_parsed == 3
    assert result.n_skipped == 2
    assert len(result.skipped_samples) == 2
    assert "not a debug line" in result.skipped_samples


def test_handles_nan_slope():
    lines = [
        "2026-05-15 09:30:00 | Trend=Flat slope=NaN | VWAP=Near distATR=NaN | Vol=Mid pct=50.0%",
    ]
    result = parse_debug_log(lines)
    assert result.n_parsed == 1
    row = result.df.iloc[0]
    assert row["slope"] != row["slope"]   # NaN
    assert row["dist_atr"] != row["dist_atr"]
    assert row["pct"] == 0.5
