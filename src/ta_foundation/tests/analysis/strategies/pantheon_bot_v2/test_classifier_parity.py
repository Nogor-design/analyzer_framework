"""
Ticket 2A: classifier parity.

Given the slope / distATR / percentile values that PantheonBotV2 emits via
EnableDebugPrint, do the Python classifiers in
ta_foundation.analysis.strategies.pantheon_bot_v2.classifiers produce the
same enum labels as the C# RegimeClassifier?

This isolates the classification step from feature computation. If a bar
disagrees here, the bug is in the if/else logic — not in EMA, ATR, or
VWAP math.

Ticket 2B (bar_regime_parity) covers the feature side.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ta_foundation.analysis.strategies.pantheon_bot_v2.classifiers import (
    classify_trend,
    classify_vwap,
    classify_volatility_from_percentile,
)
from ta_foundation.analysis.strategies.pantheon_bot_v2.debug_parser import (
    parse_debug_log,
)
from ta_foundation.analysis.strategies.pantheon_bot_v2.settings_loader import (
    load_settings,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _discover_fixture_pairs() -> list[tuple[Path, Path]]:
    if not FIXTURES_DIR.exists():
        return []
    pairs: list[tuple[Path, Path]] = []
    for debug_path in sorted(FIXTURES_DIR.glob("debug_*.txt")):
        stem = debug_path.stem
        candidates = [
            FIXTURES_DIR / f"{stem}.csv",
            FIXTURES_DIR / f"{stem}.json",
            FIXTURES_DIR / f"bot_settings_{stem.removeprefix('debug_')}.csv",
            FIXTURES_DIR / f"bot_settings_{stem.removeprefix('debug_')}.json",
        ]
        for settings_path in candidates:
            if settings_path.exists():
                pairs.append((debug_path, settings_path))
                break
    return pairs


FIXTURE_PAIRS = _discover_fixture_pairs()
PARAM_IDS = [p[0].stem for p in FIXTURE_PAIRS]


pytestmark = pytest.mark.skipif(
    not FIXTURE_PAIRS,
    reason="No PantheonBotV2 debug fixtures present. See fixtures/README.md.",
)


@pytest.fixture(scope="module", params=FIXTURE_PAIRS, ids=PARAM_IDS)
def fixture_pair(request) -> tuple[pd.DataFrame, dict]:
    debug_path, settings_path = request.param
    parsed = parse_debug_log(debug_path)
    assert parsed.n_parsed > 0, (
        f"No lines parsed from {debug_path.name}. "
        f"Skipped {parsed.n_skipped} lines; sample: {parsed.skipped_samples[:2]}"
    )
    settings = load_settings(settings_path)
    return parsed.df, settings


def _format_mismatch_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    return df.head(max_rows).to_string(index=False)


def test_debug_log_parser_self_check(fixture_pair):
    df, _ = fixture_pair
    assert {"dt", "trend_cs", "slope", "vwap_cs", "dist_atr", "vol_cs", "pct"} <= set(df.columns)
    assert df["pct"].between(0.0, 1.0).all(), "pct must be a fraction in [0,1]"
    assert df["trend_cs"].isin({"up", "down", "flat"}).all()
    assert df["vwap_cs"].isin({"above", "below", "near"}).all()
    assert df["vol_cs"].isin({"low", "mid", "high"}).all()


def test_trend_classifier_matches_cs(fixture_pair):
    df, settings = fixture_pair
    flat_threshold = float(settings["TrendFlatThreshold"])

    py = df["slope"].apply(lambda s: classify_trend(s, flat_threshold))
    cs = df["trend_cs"]

    # The C# code uses strict > / < against the threshold. Floats arriving via
    # the Output window are formatted with 4 decimals (F4), so the displayed
    # slope and the slope used for classification can disagree by up to 5e-5
    # exactly at the boundary. We treat near-boundary disagreements as
    # tolerated rather than failures.
    boundary_band = 1e-4
    near_boundary = (df["slope"].abs() - flat_threshold).abs() <= boundary_band
    mismatches = df.assign(py=py, cs=cs).loc[(py != cs) & ~near_boundary,
                                             ["dt", "slope", "cs", "py"]]
    assert mismatches.empty, (
        f"trend label disagreement on {len(mismatches)} bars:\n"
        f"{_format_mismatch_table(mismatches)}"
    )


def test_vwap_classifier_matches_cs(fixture_pair):
    df, settings = fixture_pair
    near_threshold = float(settings["VwapNearThresholdAtr"])

    py = df["dist_atr"].apply(lambda d: classify_vwap(d, near_threshold))
    cs = df["vwap_cs"]

    boundary_band = 1e-3  # distATR is printed at F3 precision
    near_boundary = (df["dist_atr"].abs() - near_threshold).abs() <= boundary_band
    mismatches = df.assign(py=py, cs=cs).loc[(py != cs) & ~near_boundary,
                                             ["dt", "dist_atr", "cs", "py"]]
    assert mismatches.empty, (
        f"vwap label disagreement on {len(mismatches)} bars:\n"
        f"{_format_mismatch_table(mismatches)}"
    )


def test_volatility_classifier_matches_cs(fixture_pair):
    df, settings = fixture_pair
    low = float(settings["VolatilityLowPercentile"])
    high = float(settings["VolatilityHighPercentile"])

    py = df["pct"].apply(lambda p: classify_volatility_from_percentile(p, low, high))
    cs = df["vol_cs"]

    # pct is printed at P1 (one decimal of percent), so worst-case rounding is
    # 5e-3 on either side of a threshold.
    boundary_band = 5e-3
    near_low = (df["pct"] - low).abs() <= boundary_band
    near_high = (df["pct"] - high).abs() <= boundary_band
    near_boundary = near_low | near_high

    mismatches = df.assign(py=py, cs=cs).loc[(py != cs) & ~near_boundary,
                                             ["dt", "pct", "cs", "py"]]
    assert mismatches.empty, (
        f"volatility label disagreement on {len(mismatches)} bars:\n"
        f"{_format_mismatch_table(mismatches)}"
    )
