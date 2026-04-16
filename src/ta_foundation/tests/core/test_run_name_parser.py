"""
tests/core/test_run_name_parser.py
───────────────────────────────────
Unit tests for core/run_name_parser.py.

Covers:
  - Artifact suffix stripping (old and new naming conventions)
  - Market inference: dash-suffix, legacy token, default
  - strategy_name derivation
  - Run bundling: all artifact types for the same run resolve to the same run_id
  - Backward-compat: TOKEN_TO_SYMBOL / TICK_VALUE_USD still importable from derived_metrics
"""

import pytest
from pathlib import Path

from ta_foundation.core.run_name_parser import (
    strip_artifact_suffix,
    parse_run_name,
    infer_instrument_from_run_id,
    EXPLICIT_MARKET_CODES,
    ARTIFACT_SUFFIXES,
)


# ─────────────────────────────────────────────────────────────────────────────
# strip_artifact_suffix
# ─────────────────────────────────────────────────────────────────────────────

class TestStripArtifactSuffix:
    """All artifact suffixes are stripped; the run_id prefix is returned."""

    @pytest.mark.parametrize("filename, expected", [
        # Legacy names — old suffix variants
        ("GranetPhoenixInferno_Trades.csv",    "GranetPhoenixInferno"),
        ("GranetPhoenixInferno_Analysis.csv",  "GranetPhoenixInferno"),
        ("GranetPhoenixInferno_Summery.csv",   "GranetPhoenixInferno"),  # typo variant
        ("GranetPhoenixInferno_Summary.csv",   "GranetPhoenixInferno"),  # canonical spelling
        ("GranetPhoenixInferno_Settings.csv",  "GranetPhoenixInferno"),
        ("GranetPhoenixInferno_Optimization.csv", "GranetPhoenixInferno"),
        ("GranetPhoenixInferno_Summary.png",   "GranetPhoenixInferno"),
        ("GranetPhoenixInferno_Analysis.png",  "GranetPhoenixInferno"),

        # New dash-suffix names — market code is PART of run_id
        ("CoilingAresFireB-NQ_Summary.csv",    "CoilingAresFireB-NQ"),
        ("CoilingAresFireB-NQ_Trades.csv",     "CoilingAresFireB-NQ"),
        ("CoilingAresFireB-NQ_Analysis.csv",   "CoilingAresFireB-NQ"),
        ("CoilingAresFireB-NQ_Settings.csv",   "CoilingAresFireB-NQ"),
        ("CoilingAresFireB-ES_Summary.csv",    "CoilingAresFireB-ES"),
        ("CoilingAresFireB-YM_Summary.csv",    "CoilingAresFireB-YM"),

        # New dash-suffix names — no market code
        ("CoilingAresFireB_Summary.csv",       "CoilingAresFireB"),
        ("CoilingAresFireB_Trades.csv",        "CoilingAresFireB"),

        # Unknown suffix falls back to stem-only
        ("SomeFile.txt",                        "SomeFile"),
    ])
    def test_strips_suffix(self, filename: str, expected: str):
        assert strip_artifact_suffix(filename) == expected

    def test_all_artifact_suffixes_covered(self):
        """Every entry in ARTIFACT_SUFFIXES is exercised by at least one case."""
        tested = {s for s in ARTIFACT_SUFFIXES
                  if any(f.endswith(s) for f, _ in [
                      ("x_Trades.csv",       "x"),
                      ("x_Analysis.csv",     "x"),
                      ("x_Summary.csv",      "x"),
                      ("x_Summery.csv",      "x"),
                      ("x_Settings.csv",     "x"),
                      ("x_Optimization.csv", "x"),
                      ("x_Summary.png",      "x"),
                      ("x_Analysis.png",     "x"),
                      ("x_Summery.png",      "x"),
                  ])}
        assert tested == set(ARTIFACT_SUFFIXES)


# ─────────────────────────────────────────────────────────────────────────────
# parse_run_name — market inference
# ─────────────────────────────────────────────────────────────────────────────

class TestParseRunNameMarket:
    """Market is inferred in the correct precedence order."""

    # ── Dash-suffix (step A) ──────────────────────────────────────────────
    def test_dash_nq_explicit(self):
        info = parse_run_name("CoilingAresFireB-NQ")
        assert info.market == "NQ"
        assert info.instrument_source == "dash_suffix"

    def test_dash_es_explicit(self):
        info = parse_run_name("CoilingAresFireB-ES")
        assert info.market == "ES"
        assert info.instrument_source == "dash_suffix"

    def test_dash_ym_explicit(self):
        info = parse_run_name("CoilingAresFireB-YM")
        assert info.market == "YM"
        assert info.instrument_source == "dash_suffix"

    def test_dash_mnq_explicit(self):
        info = parse_run_name("CoilingAresFireB-MNQ")
        assert info.market == "MNQ"
        assert info.instrument_source == "dash_suffix"

    @pytest.mark.parametrize("code", sorted(EXPLICIT_MARKET_CODES))
    def test_all_explicit_market_codes(self, code: str):
        """Every code in EXPLICIT_MARKET_CODES is recognized."""
        info = parse_run_name(f"SomeStrategy-{code}")
        assert info.market == code
        assert info.instrument_source == "dash_suffix"

    # ── Legacy token matching (step B) — first CamelCase token only ──────
    def test_legacy_granit_ym(self):
        """GranitPhoenixInferno → YM (first token 'Granit' matches 'granit')."""
        info = parse_run_name("GranitPhoenixInferno")
        assert info.market == "YM"
        assert info.instrument_source == "token:granit"

    def test_legacy_granet_ym(self):
        """GranetPhoenixInferno → YM (alternate 'granet' spelling also recognized)."""
        info = parse_run_name("GranetPhoenixInferno")
        assert info.market == "YM"
        assert info.instrument_source == "token:granet"

    def test_legacy_bronze_nq(self):
        """BronzePhoenixInferno → NQ (first token 'Bronze' matches 'bronze')."""
        info = parse_run_name("BronzePhoenixInferno")
        assert info.market == "NQ"
        assert info.instrument_source == "token:bronze"

    def test_legacy_iron_es(self):
        info = parse_run_name("IronSomething")
        assert info.market == "ES"
        assert "token:" in info.instrument_source

    def test_legacy_no_false_positive_oil_in_coiling(self):
        """'Coiling' must NOT match the 'oil' token — only the first word is checked."""
        info = parse_run_name("CoilingAresFireB")
        assert info.market == "NQ"            # not CL
        assert info.instrument_source == "default"

    # ── Default fallback (step C) ─────────────────────────────────────────
    def test_default_nq_no_suffix_no_token(self):
        """New-style name without market suffix and no legacy token → NQ."""
        info = parse_run_name("CoilingAresFireB")
        assert info.market == "NQ"
        assert info.instrument_source == "default"

    def test_default_nq_unknown_name(self):
        info = parse_run_name("RandomStrategyXyz")
        assert info.market == "NQ"
        assert info.instrument_source == "default"

    # ── Dash suffix takes priority over legacy token ───────────────────────
    def test_dash_suffix_beats_legacy_token(self):
        """A name with both a dash-suffix and a legacy token: dash-suffix wins."""
        # "GranitFoo-ES": first token 'Granit' → YM, but "-ES" suffix → ES
        info = parse_run_name("GranitFoo-ES")
        assert info.market == "ES"
        assert info.instrument_source == "dash_suffix"

    def test_unrecognized_dash_segment_falls_through(self):
        """A trailing -SOMETHING that isn't a market code is NOT stripped."""
        # "CoilingAresFireB-XYZ" — XYZ not in EXPLICIT_MARKET_CODES → default NQ
        info = parse_run_name("CoilingAresFireB-XYZ")
        assert info.market == "NQ"
        assert info.instrument_source == "default"
        assert info.strategy_name == "CoilingAresFireB-XYZ"  # nothing stripped


# ─────────────────────────────────────────────────────────────────────────────
# parse_run_name — strategy_name
# ─────────────────────────────────────────────────────────────────────────────

class TestParseRunNameStrategyName:
    """strategy_name strips the market suffix for new-style names only."""

    def test_new_style_strategy_name_stripped(self):
        info = parse_run_name("CoilingAresFireB-NQ")
        assert info.strategy_name == "CoilingAresFireB"

    def test_new_style_es_strategy_name_stripped(self):
        info = parse_run_name("CoilingAresFireB-ES")
        assert info.strategy_name == "CoilingAresFireB"

    def test_legacy_strategy_name_unchanged(self):
        """For legacy names strategy_name == raw_run_id."""
        info = parse_run_name("GranetPhoenixInferno")
        assert info.strategy_name == "GranetPhoenixInferno"
        assert info.raw_run_id == "GranetPhoenixInferno"

    def test_no_suffix_strategy_name_unchanged(self):
        info = parse_run_name("CoilingAresFireB")
        assert info.strategy_name == "CoilingAresFireB"
        assert info.raw_run_id == "CoilingAresFireB"

    def test_raw_run_id_always_preserved(self):
        """raw_run_id is always the original string, never modified."""
        for run_id in ("CoilingAresFireB-NQ", "GranetPhoenixInferno", "Unknown"):
            info = parse_run_name(run_id)
            assert info.raw_run_id == run_id


# ─────────────────────────────────────────────────────────────────────────────
# parse_run_name — tick values
# ─────────────────────────────────────────────────────────────────────────────

class TestParseRunNameTickValues:
    def test_nq_tick_value(self):
        assert parse_run_name("CoilingAresFireB-NQ").tick_value == 5.0

    def test_es_tick_value(self):
        assert parse_run_name("CoilingAresFireB-ES").tick_value == 12.5

    def test_mnq_tick_value(self):
        assert parse_run_name("CoilingAresFireB-MNQ").tick_value == 0.5

    def test_ym_tick_value(self):
        assert parse_run_name("CoilingAresFireB-YM").tick_value == 5.0

    def test_legacy_ym_tick_value(self):
        assert parse_run_name("GranetPhoenixInferno").tick_value == 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Run bundling — all artifact suffixes for the same run resolve to the same id
# ─────────────────────────────────────────────────────────────────────────────

class TestRunBundling:
    """All artifact files for the same run must resolve to identical run_ids."""

    @pytest.mark.parametrize("base_name", [
        "GranetPhoenixInferno",
        "BronzePhoenixInferno",
        "CoilingAresFireB-NQ",
        "CoilingAresFireB-ES",
        "CoilingAresFireB-YM",
        "CoilingAresFireB",
    ])
    def test_all_artifact_suffixes_same_run_id(self, base_name: str):
        """
        Regardless of which artifact file we encounter first, the run_id
        must be the same for all files belonging to the same run.
        """
        run_ids = {
            strip_artifact_suffix(f"{base_name}{suffix}")
            for suffix in ARTIFACT_SUFFIXES
            # Exclude .png suffixes — those files don't always exist but the
            # logic must handle them the same way
        }
        assert run_ids == {base_name}, (
            f"Files for run '{base_name}' resolved to multiple run_ids: {run_ids}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# infer_instrument_from_run_id — backward-compat shim
# ─────────────────────────────────────────────────────────────────────────────

class TestInferInstrumentShim:
    """The old (symbol, tick, source) tuple API still works correctly."""

    def test_legacy_tuple_granit(self):
        sym, tick, src = infer_instrument_from_run_id("GranitPhoenixInferno")
        assert sym == "YM"
        assert tick == 5.0
        assert src == "token:granit"

    def test_legacy_tuple_granet_alias(self):
        sym, tick, src = infer_instrument_from_run_id("GranetPhoenixInferno")
        assert sym == "YM"
        assert tick == 5.0
        assert src == "token:granet"

    def test_legacy_tuple_bronze(self):
        sym, tick, src = infer_instrument_from_run_id("BronzePhoenixInferno")
        assert sym == "NQ"
        assert tick == 5.0
        assert src == "token:bronze"

    def test_new_dash_nq(self):
        sym, tick, src = infer_instrument_from_run_id("CoilingAresFireB-NQ")
        assert sym == "NQ"
        assert src == "dash_suffix"

    def test_new_dash_es(self):
        sym, tick, src = infer_instrument_from_run_id("CoilingAresFireB-ES")
        assert sym == "ES"
        assert tick == 12.5
        assert src == "dash_suffix"

    def test_default_fallback(self):
        sym, tick, src = infer_instrument_from_run_id("CoilingAresFireB")
        assert sym == "NQ"
        assert src == "default"

    def test_no_false_positive_oil_in_coiling(self):
        """Substring 'oil' in 'Coiling' must NOT fire the CL token."""
        sym, _, src = infer_instrument_from_run_id("CoilingAresFireB")
        assert sym == "NQ"
        assert src == "default"


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat: TOKEN_TO_SYMBOL / TICK_VALUE_USD still importable from
# derived_metrics (they're re-exported there)
# ─────────────────────────────────────────────────────────────────────────────

class TestBackwardCompatImports:
    def test_token_to_symbol_importable_from_derived_metrics(self):
        from ta_foundation.core.derived_metrics import TOKEN_TO_SYMBOL
        assert isinstance(TOKEN_TO_SYMBOL, list)
        assert any(sym == "YM" for _, sym in TOKEN_TO_SYMBOL)

    def test_tick_value_usd_importable_from_derived_metrics(self):
        from ta_foundation.core.derived_metrics import TICK_VALUE_USD
        assert TICK_VALUE_USD["NQ"] == 5.0
        assert TICK_VALUE_USD["ES"] == 12.5

    def test_infer_instrument_importable_from_derived_metrics(self):
        from ta_foundation.core.derived_metrics import infer_instrument_from_run_id
        sym, _, _ = infer_instrument_from_run_id("GranitFoo")
        assert sym == "YM"
