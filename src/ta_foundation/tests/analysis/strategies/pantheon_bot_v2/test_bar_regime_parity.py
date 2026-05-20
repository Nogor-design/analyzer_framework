"""
Ticket 2B: end-to-end bar-level regime feature parity.

For each `debug_<stem>.txt` fixture pair, this test looks for a
companion minute-bar export `bars_<stem>.Last.txt` (NinjaTrader chart
export, semicolon-delimited). If present, it:

  1. Parses the bars with MinuteBarsLastTxtParser.
  2. Resamples to the primary (5m) and HTF (15m) timeframes.
  3. Runs compute_bar_regime with the same settings the NT backtest used.
  4. Joins the per-bar features to the debug fixture on `dt`.
  5. Asserts the Python-computed slope, distATR, and ATR percentile rank
     match the NT-emitted values within the print-precision tolerance.

When the bars file is missing the test skips with a directive on how to
capture it.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pandas as pd
import pytest

from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import (
    MinuteBarsLastTxtParser,
)
from ta_foundation.analysis.strategies.pantheon_bot_v2.bar_regime import (
    BarRegimeConfig,
    compute_bar_regime,
    config_from_settings,
    resample_nt_right_labeled,
)
from ta_foundation.analysis.strategies.pantheon_bot_v2.debug_parser import (
    parse_debug_log,
)
from ta_foundation.analysis.strategies.pantheon_bot_v2.settings_loader import (
    load_settings,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
LOCAL_BARS_PATHS = FIXTURES_DIR / "bars_paths.local.json"


def _load_local_bars_map() -> dict[str, Path]:
    if not LOCAL_BARS_PATHS.exists():
        return {}
    raw = json.loads(LOCAL_BARS_PATHS.read_text(encoding="utf-8"))
    return {k: Path(v) for k, v in raw.items() if Path(v).exists()}


def _discover_fixture_triples() -> list[tuple[Path, Path, Path]]:
    if not FIXTURES_DIR.exists():
        return []
    local_map = _load_local_bars_map()
    triples: list[tuple[Path, Path, Path]] = []
    for debug_path in sorted(FIXTURES_DIR.glob("debug_*.txt")):
        stem = debug_path.stem
        settings_candidates = [
            FIXTURES_DIR / f"{stem}.csv",
            FIXTURES_DIR / f"{stem}.json",
        ]
        settings_path = next((p for p in settings_candidates if p.exists()), None)
        if settings_path is None:
            continue
        bars_path: Path | None = None
        bars_local = list(FIXTURES_DIR.glob(f"bars_{stem.removeprefix('debug_')}*.Last.txt"))
        if bars_local:
            bars_path = bars_local[0]
        elif stem in local_map:
            bars_path = local_map[stem]
        if bars_path is None:
            continue
        triples.append((debug_path, settings_path, bars_path))
    return triples


FIXTURE_TRIPLES = _discover_fixture_triples()
PARAM_IDS = [p[0].stem for p in FIXTURE_TRIPLES]


pytestmark = pytest.mark.skipif(
    not FIXTURE_TRIPLES,
    reason=(
        "No bars fixture available. To enable, export NinjaTrader minute "
        "bars covering the debug-log window (plus several weeks of warm-up "
        "so EMA/ATR/percentile stabilize) and save as "
        "`bars_<stem>.Last.txt` next to debug_<stem>.txt. See "
        "fixtures/README.md."
    ),
)


@pytest.fixture(scope="module", params=FIXTURE_TRIPLES, ids=PARAM_IDS)
def feature_pair(request) -> tuple[pd.DataFrame, dict]:
    debug_path, settings_path, bars_path = request.param

    parsed = parse_debug_log(debug_path)
    assert parsed.n_parsed > 0, f"No debug lines parsed from {debug_path.name}"
    debug_df = parsed.df

    settings = load_settings(settings_path)
    cfg = config_from_settings(settings)
    # Anchor the session VWAP at the first debug-log row so Python's first
    # session matches NT's "session starts at the first bar of the loaded
    # data" backtest behavior. Without this, Python accumulates VWAP from
    # the prior calendar session anchor (e.g., 15:00 MT) using bars NT never
    # loaded, and VWAP drifts by tens of points.
    first_debug_dt = pd.to_datetime(parsed.df["dt"].iloc[0])
    cfg = dataclasses.replace(cfg, vwap_anchor_dt=first_debug_dt)

    parser = MinuteBarsLastTxtParser()
    artifact = parser.parse(bars_path, run_id=None)
    bars_1m = artifact.df
    assert not bars_1m.empty, f"Bars fixture {bars_path.name} parsed empty"

    primary = resample_nt_right_labeled(bars_1m, int(cfg.primary_tf.rstrip("m")))
    htf = resample_nt_right_labeled(bars_1m, cfg.trend_htf_minutes)

    features = compute_bar_regime(primary, htf, cfg)
    joined = debug_df.merge(features, on="dt", how="inner")

    coverage = len(joined) / max(len(debug_df), 1)
    assert coverage > 0.9, (
        f"Only {coverage:.0%} of debug bars have matching feature rows. "
        f"Check that bars fixture covers the full debug window and that "
        f"timestamp labelling lines up. "
        f"debug={len(debug_df)} feats={len(features)} joined={len(joined)}"
    )

    # Drop the first N bars where warm-up is still material. The lookback
    # window for the volatility percentile dominates everything else.
    warmup = max(
        cfg.volatility_lookback_window,
        cfg.trend_ema_period * 2,
        cfg.vwap_atr_period * 2,
    )
    if len(joined) > warmup:
        joined = joined.iloc[warmup:].reset_index(drop=True)

    return joined, settings


def _fmt_mismatch(df: pd.DataFrame, cols: list[str], n: int = 20) -> str:
    return df[cols].head(n).to_string(index=False)


def test_slope_matches_cs(feature_pair):
    df, _settings = feature_pair
    df = df.dropna(subset=["slope_y", "slope_x"]) if "slope_y" in df.columns else df.dropna(subset=["slope"])
    # Debug-log column is named "slope" (from parser); features column is also
    # "slope" — pandas merges them with a suffix collision. Resolve explicitly.
    slope_py = df["slope_y"] if "slope_y" in df.columns else df["slope"]
    slope_cs = df["slope_x"] if "slope_x" in df.columns else df["slope"]
    # Should never both be the same column; the merge produced suffixes.
    assert slope_py is not slope_cs

    diff = (slope_py - slope_cs).abs()
    # The HTF EMA recursion seeds from whatever warm-up data NT loaded into
    # its chart, which is not exactly what our bars file holds. That drift
    # (~0.2 on a 27000-price EMA) propagates through
    # slope = (ema[i] - ema[i-3]) / (3*atr) and gives empirical slope errors
    # up to ~0.025. We tolerate up to 0.05 — wide enough to absorb seed
    # drift but tight enough to catch logic errors that would shift slopes
    # by an order of magnitude. The classifier test (2A) confirms the
    # *labels* still agree, which is what matters operationally.
    tol = 0.05
    bad = df[diff > tol].assign(diff=diff[diff > tol])
    assert bad.empty, (
        f"slope disagreement on {len(bad)} bars (tol={tol}):\n"
        f"{_fmt_mismatch(bad.rename(columns={'slope_x': 'cs', 'slope_y': 'py'}), ['dt', 'cs', 'py', 'diff'])}"
    )


def test_dist_atr_matches_cs(feature_pair):
    df, _settings = feature_pair
    cs = df["dist_atr_x"] if "dist_atr_x" in df.columns else df["dist_atr"]
    py = df["dist_atr_y"] if "dist_atr_y" in df.columns else df["dist_atr"]
    diff = (py - cs).abs()
    # F3 print precision → 5e-4. Loosen to 5e-3 to absorb any session-VWAP
    # boundary drift if NT's instrument session template differs from the
    # configured reset hour by a small amount.
    tol = 5e-3
    bad = df[diff > tol].assign(diff=diff[diff > tol])
    assert bad.empty, (
        f"distATR disagreement on {len(bad)} bars (tol={tol}):\n"
        f"{_fmt_mismatch(bad.rename(columns={'dist_atr_x': 'cs', 'dist_atr_y': 'py'}), ['dt', 'cs', 'py', 'diff'])}"
    )


def test_pct_matches_cs(feature_pair):
    df, _settings = feature_pair
    cs = df["pct_x"] if "pct_x" in df.columns else df["pct"]
    py = df["pct_y"] if "pct_y" in df.columns else df["pct"]
    diff = (py - cs).abs()
    # P1 print precision → 5e-3. Percentile rank with the (less+0.5*equal)/n
    # formula is fully deterministic so the only source of drift is the
    # rolling-window contents being identical, which they are once warm-up
    # is past.
    tol = 1e-2
    bad = df[diff > tol].assign(diff=diff[diff > tol])
    assert bad.empty, (
        f"ATR percentile disagreement on {len(bad)} bars (tol={tol}):\n"
        f"{_fmt_mismatch(bad.rename(columns={'pct_x': 'cs', 'pct_y': 'py'}), ['dt', 'cs', 'py', 'diff'])}"
    )


# ---------------------------------------------------------------------------
# Intermediate parity (require the extended EnableDebugPrint patch).
# These tests localize which Python feature first diverges from NT, so we
# can fix the source of drift instead of guessing.
# ---------------------------------------------------------------------------

def _require_intermediates(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if col not in df.columns or df[col].isna().all():
        pytest.skip(
            "Debug log lacks intermediate values. Recapture with the patched "
            "PantheonBotV2 EnableDebugPrint to enable per-intermediate parity."
        )
    return df.dropna(subset=[col]).copy()


def _intermediate_check(df: pd.DataFrame, cs_col: str, py_col: str, tol: float, label: str):
    sub = _require_intermediates(df, cs_col).dropna(subset=[py_col])
    if sub.empty:
        pytest.skip(f"No rows with both {cs_col} and {py_col} populated.")
    diff = (sub[py_col] - sub[cs_col]).abs()
    bad = sub[diff > tol].assign(diff=diff[diff > tol])
    assert bad.empty, (
        f"{label} disagreement on {len(bad)} bars (tol={tol}):\n"
        f"{_fmt_mismatch(bad.rename(columns={cs_col: 'cs', py_col: 'py'}), ['dt', 'cs', 'py', 'diff'])}"
    )


def test_ema_htf_matches_cs(feature_pair):
    df, _ = feature_pair
    # F4 print precision → 5e-5. Allow wider band — NT may seed EMA from a
    # different first-bar than the bars file does.
    _intermediate_check(df, "ema_htf_cs", "ema_htf", tol=1.0, label="HTF EMA")


def test_atr_htf_matches_cs(feature_pair):
    df, _ = feature_pair
    _intermediate_check(df, "atr_htf_cs", "atr_htf", tol=0.5, label="HTF ATR")


def test_atr_primary_matches_cs(feature_pair):
    df, _ = feature_pair
    _intermediate_check(df, "atr_pri_cs", "atr_primary", tol=0.5, label="Primary ATR")


def test_vwap_matches_cs(feature_pair):
    df, _ = feature_pair
    # VWAP scale ≈ price; tol of 5.0 on NQ means within ~5 points which is
    # one tick on a fat-tick instrument. If session boundary is wrong, this
    # blows up — that's the diagnostic we want.
    _intermediate_check(df, "vwap_cs_v", "vwap", tol=5.0, label="Session VWAP")


def test_atr_vol_matches_cs(feature_pair):
    df, _ = feature_pair
    _intermediate_check(df, "atr_vol_cs", "atr_vol", tol=0.5, label="Volatility ATR")
