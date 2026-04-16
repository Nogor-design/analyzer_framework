from __future__ import annotations

"""
Tests for NinjaTraderOptimizationCsvParser and its Parameters-column parser.

Covers:
 - parse_parameters: clean parse, inner parens in names, mismatch, empty, missing outer parens
 - Type coercion: bool, int, float, str/filename/bot-name
 - Full CSV parse: metric columns, forward-fill of Instrument,
   unnamed-column stripping, param_* expansion, parse_ok flags
 - Multiple file ingestion via pipeline.ingest_folder
 - Report section renders without crashing (smoke test)
"""

import io
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from ta_foundation.parsers.ninjatrader.optimization_csv import (
    NinjaTraderOptimizationCsvParser,
    parse_parameters,
    _coerce_scalar,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Canonical sample Parameters string taken from the real optimization file.
_SAMPLE_PARAMS = (
    "True/0/0/1/0/5/400/300/False/False/10000/10000/10000/1/50/True/0.4/True/False"
    "/True/True/False/800/400/False/False/0.3/panthionIcon4.png/Oden2.png"
    "/Iron Aphrodite Hunter "
    "(Use_Time_Filter Start_Time_(HH) Start_Time_(mm) Duration_Time_(HH) "
    "Duration_Time_(mm) averageFast averageSlow averageTrend UseTrend "
    "UseTrendReverse ProfitStop LossStop MaxTrades Contracts MaxStop "
    "Use_MaxStop MaxTPRatio Use_MaxTP Long Short Reverse Use_Kill "
    "Kill_Profit_Stop Kill_Loss_Stop Show_Current_PNL Show_Stats_Box "
    "Image_Opacity Corner_Image Background_Image Bot_Name )"
)

_EXPECTED_PARAM_NAMES = [
    "Use_Time_Filter", "Start_Time_(HH)", "Start_Time_(mm)", "Duration_Time_(HH)",
    "Duration_Time_(mm)", "averageFast", "averageSlow", "averageTrend",
    "UseTrend", "UseTrendReverse", "ProfitStop", "LossStop", "MaxTrades",
    "Contracts", "MaxStop", "Use_MaxStop", "MaxTPRatio", "Use_MaxTP",
    "Long", "Short", "Reverse", "Use_Kill", "Kill_Profit_Stop", "Kill_Loss_Stop",
    "Show_Current_PNL", "Show_Stats_Box", "Image_Opacity", "Corner_Image",
    "Background_Image", "Bot_Name",
]

_EXPECTED_VALUES = [
    True, 0, 0, 1, 0, 5, 400, 300, False, False, 10000, 10000, 10000, 1, 50,
    True, 0.4, True, False, True, True, False, 800, 400, False, False, 0.3,
    "panthionIcon4.png", "Oden2.png", "Iron Aphrodite Hunter",
]


def _make_csv(rows: list[dict]) -> str:
    """Build a CSV string from a list of row dicts using the optimization export layout."""
    columns = [
        "Instrument", "Performance", "Parameters",
        "Total net profit", "Gross profit", "Gross loss",
        "Profit factor", "Max. drawdown", "Total # of trades",
        "Percent profitable",
    ]
    lines = [",".join(columns)]
    for i, row in enumerate(rows):
        # NinjaTrader only writes Instrument on the first row
        instrument = row.get("Instrument", "") if i == 0 else ""
        cells = [
            instrument,
            str(row.get("Performance", "99")),
            row.get("Parameters", _SAMPLE_PARAMS),
            str(row.get("Total net profit", "200")),
            str(row.get("Gross profit", "200")),
            str(row.get("Gross loss", "0")),
            str(row.get("Profit factor", "99")),
            str(row.get("Max. drawdown", "0")),
            str(row.get("Total # of trades", "2")),
            str(row.get("Percent profitable", "1")),
        ]
        lines.append(",".join(cells))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tests: parse_parameters
# ---------------------------------------------------------------------------

class TestParseParameters:

    def test_clean_parse_count(self):
        param_map, warns = parse_parameters(_SAMPLE_PARAMS)
        assert warns == [], f"Expected no warnings, got: {warns}"
        assert len(param_map) == len(_EXPECTED_PARAM_NAMES)

    def test_clean_parse_names(self):
        param_map, _ = parse_parameters(_SAMPLE_PARAMS)
        assert list(param_map.keys()) == _EXPECTED_PARAM_NAMES

    def test_clean_parse_values(self):
        param_map, _ = parse_parameters(_SAMPLE_PARAMS)
        for name, expected in zip(_EXPECTED_PARAM_NAMES, _EXPECTED_VALUES):
            actual = param_map[name]
            assert actual == expected, (
                f"Mismatch for {name!r}: expected {expected!r}, got {actual!r}"
            )

    def test_inner_parens_in_names_preserved(self):
        """Parameter names like Start_Time_(HH) must be treated as single tokens."""
        param_map, _ = parse_parameters(_SAMPLE_PARAMS)
        assert "Start_Time_(HH)" in param_map
        assert "Start_Time_(mm)" in param_map
        assert "Duration_Time_(HH)" in param_map
        assert "Duration_Time_(mm)" in param_map

    def test_bot_name_with_spaces(self):
        """Multi-word bot name must survive as a single value string."""
        param_map, _ = parse_parameters(_SAMPLE_PARAMS)
        assert param_map["Bot_Name"] == "Iron Aphrodite Hunter"

    def test_filename_values_unchanged(self):
        """Filenames like 'panthionIcon4.png' must come through as strings."""
        param_map, _ = parse_parameters(_SAMPLE_PARAMS)
        assert param_map["Corner_Image"]     == "panthionIcon4.png"
        assert param_map["Background_Image"] == "Oden2.png"

    def test_boolean_coercion(self):
        param_map, _ = parse_parameters(_SAMPLE_PARAMS)
        assert param_map["Use_Time_Filter"] is True   # position 0 → True
        assert param_map["UseTrend"]        is False  # position 8 → False
        assert param_map["UseTrendReverse"] is False  # position 9 → False
        assert param_map["Use_MaxStop"]     is True   # position 15 → True

    def test_integer_coercion(self):
        param_map, _ = parse_parameters(_SAMPLE_PARAMS)
        assert param_map["averageFast"]  == 5
        assert param_map["averageSlow"]  == 400
        assert param_map["ProfitStop"]   == 10000
        assert param_map["Contracts"]    == 1

    def test_float_coercion(self):
        param_map, _ = parse_parameters(_SAMPLE_PARAMS)
        assert param_map["MaxTPRatio"]   == pytest.approx(0.4)
        assert param_map["Image_Opacity"] == pytest.approx(0.3)

    def test_empty_string(self):
        param_map, warns = parse_parameters("")
        assert param_map == {}
        assert warns  # should have at least one warning

    def test_missing_closing_paren(self):
        broken = "True/0 (Use_Time_Filter Start_Time_(HH)"
        param_map, warns = parse_parameters(broken)
        # Should not raise; warnings expected
        assert isinstance(warns, list)

    def test_missing_opening_paren(self):
        broken = "True/0 Use_Time_Filter Start_Time_HH)"
        param_map, warns = parse_parameters(broken)
        assert isinstance(warns, list)
        assert len(warns) > 0

    def test_count_mismatch_produces_warning(self):
        """Extra value with no matching name → warning, partial result."""
        extra_val = "True/0/EXTRA (A B )"
        param_map, warns = parse_parameters(extra_val)
        assert any("mismatch" in w.lower() for w in warns)
        # Should still parse up to min(values, names)
        assert len(param_map) == 2

    def test_duplicate_param_names_handled(self):
        """Duplicate names should be de-duplicated with _2 suffix."""
        dup = "1/2/3 (A A A )"
        param_map, warns = parse_parameters(dup)
        assert "A"   in param_map
        assert "A_2" in param_map
        assert "A_3" in param_map
        assert len(param_map) == 3


# ---------------------------------------------------------------------------
# Tests: _coerce_scalar
# ---------------------------------------------------------------------------

class TestCoerceScalar:

    def test_true(self):  assert _coerce_scalar("True")  is True
    def test_false(self): assert _coerce_scalar("False") is False
    def test_int(self):   assert _coerce_scalar("400")   == 400
    def test_float(self): assert _coerce_scalar("0.4")   == pytest.approx(0.4)
    def test_string(self): assert _coerce_scalar("Iron Aphrodite Hunter") == "Iron Aphrodite Hunter"
    def test_filename(self): assert _coerce_scalar("panthionIcon4.png")   == "panthionIcon4.png"
    def test_zero(self):  assert _coerce_scalar("0")    == 0
    def test_negative(self): assert _coerce_scalar("-5") == -5


# ---------------------------------------------------------------------------
# Tests: full CSV parse
# ---------------------------------------------------------------------------

class TestNinjaTraderOptimizationCsvParser:

    def _write_and_parse(self, tmp_path: Path, csv_content: str, filename: str = "Bot_Optimization.csv"):
        p = tmp_path / filename
        p.write_text(csv_content, encoding="utf-8")
        parser = NinjaTraderOptimizationCsvParser()
        return parser.parse(p, run_id="Bot")

    # --- can_parse ---

    def test_can_parse_valid_file(self, tmp_path):
        p = tmp_path / "Bot_Optimization.csv"
        p.write_text("Instrument,Performance,Parameters,Total net profit\n", encoding="utf-8")
        parser = NinjaTraderOptimizationCsvParser()
        header = p.read_text()[:200]
        assert parser.can_parse(p, header)

    def test_cannot_parse_wrong_suffix(self, tmp_path):
        p = tmp_path / "Bot_Trades.csv"
        p.write_text("Instrument,Performance,Parameters\n", encoding="utf-8")
        parser = NinjaTraderOptimizationCsvParser()
        assert not parser.can_parse(p, p.read_text()[:200])

    def test_cannot_parse_missing_params_col(self, tmp_path):
        p = tmp_path / "Bot_Optimization.csv"
        p.write_text("Instrument,Performance,Foo\n", encoding="utf-8")
        parser = NinjaTraderOptimizationCsvParser()
        assert not parser.can_parse(p, p.read_text()[:200])

    # --- basic parse ---

    def test_parse_returns_optimization_kind(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        assert art.kind == "optimization"

    def test_parse_df_not_empty(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}, {}, {}])
        art = self._write_and_parse(tmp_path, csv)
        assert art.df is not None
        assert not art.df.empty
        assert len(art.df) == 3

    def test_unnamed_columns_dropped(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}]) + ","  # trailing comma → Unnamed column
        art = self._write_and_parse(tmp_path, csv)
        assert not any(c.startswith("Unnamed") for c in art.df.columns)

    def test_instrument_forward_filled(self, tmp_path):
        """Instrument should be filled from row 0 into all subsequent rows."""
        csv = _make_csv([{"Instrument": "NQ 06-26"}, {}, {}])
        art = self._write_and_parse(tmp_path, csv)
        assert list(art.df["Instrument"].dropna()) == ["NQ 06-26", "NQ 06-26", "NQ 06-26"]

    def test_metric_columns_renamed(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        assert "total_net_profit" in art.df.columns
        assert "profit_factor"    in art.df.columns
        assert "total_trades"     in art.df.columns

    def test_param_columns_created(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        param_cols = [c for c in art.df.columns if c.startswith("param_")]
        assert len(param_cols) == len(_EXPECTED_PARAM_NAMES)

    def test_param_values_correct(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        row = art.df.iloc[0]
        # pandas stores booleans as numpy.bool_ — use == rather than `is`
        assert row["param_Use_Time_Filter"] == True   # noqa: E712
        assert row["param_UseTrend"]        == False  # noqa: E712  (position 8 → False)
        assert row["param_averageFast"]     == 5
        assert row["param_Bot_Name"]        == "Iron Aphrodite Hunter"

    def test_parse_ok_flag_set(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        assert art.df["parse_ok"].all()

    def test_raw_parameters_preserved(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        assert "raw_parameters" in art.df.columns
        # The raw string should start with the first value
        assert str(art.df.iloc[0]["raw_parameters"]).startswith("True/")

    def test_source_file_column(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv, filename="MyStrat_Optimization.csv")
        assert art.df.iloc[0]["source_file"] == "MyStrat_Optimization.csv"

    def test_summary_contains_expected_keys(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        for key in ("batch_id", "parameter_names", "metric_columns", "instrument",
                    "row_count", "successfully_parsed_rows"):
            assert key in art.summary, f"Missing key: {key!r}"

    def test_summary_parameter_names(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        assert art.summary["parameter_names"] == _EXPECTED_PARAM_NAMES

    def test_summary_instrument(self, tmp_path):
        csv = _make_csv([{"Instrument": "NQ 06-26"}])
        art = self._write_and_parse(tmp_path, csv)
        assert art.summary["instrument"] == "NQ 06-26"

    # --- malformed rows ---

    def test_blank_parameters_row_flagged(self, tmp_path):
        rows = [
            {"Instrument": "NQ 06-26"},   # row 0 — good params
            {"Parameters": ""},           # row 1 — blank params
        ]
        csv = _make_csv(rows)
        art = self._write_and_parse(tmp_path, csv)
        assert not art.df.iloc[1]["parse_ok"]
        assert "blank" in str(art.df.iloc[1]["parse_warnings"]).lower()

    def test_bad_parameters_row_flagged(self, tmp_path):
        rows = [
            {"Instrument": "NQ 06-26"},
            {"Parameters": "totally_malformed_no_parens_at_all"},
        ]
        csv = _make_csv(rows)
        art = self._write_and_parse(tmp_path, csv)
        assert not art.df.iloc[1]["parse_ok"]

    # --- multiple rows ---

    def test_multiple_rows_all_parsed(self, tmp_path):
        rows = [
            {"Instrument": "NQ 06-26", "Parameters": _SAMPLE_PARAMS.replace("0.4", "0.4")},
            {"Parameters": _SAMPLE_PARAMS.replace("0.4", "0.8")},
            {"Parameters": _SAMPLE_PARAMS.replace("0.4", "1.2")},
        ]
        csv = _make_csv(rows)
        art = self._write_and_parse(tmp_path, csv)
        assert art.summary["row_count"] == 3
        assert art.summary["successfully_parsed_rows"] == 3
        # Check the varying parameter value parsed correctly
        assert art.df.iloc[0]["param_MaxTPRatio"] == pytest.approx(0.4)
        assert art.df.iloc[1]["param_MaxTPRatio"] == pytest.approx(0.8)
        assert art.df.iloc[2]["param_MaxTPRatio"] == pytest.approx(1.2)

    # --- CSV read error ---

    def test_nonexistent_file_returns_empty_artifact(self, tmp_path):
        p = tmp_path / "Ghost_Optimization.csv"
        parser = NinjaTraderOptimizationCsvParser()
        art = parser.parse(p, run_id="Ghost")
        assert art.kind == "optimization"
        assert art.df is not None
        assert art.df.empty
        assert any(w.get("code") == "CSV_READ_ERROR" for w in art.warnings)


# ---------------------------------------------------------------------------
# Tests: multiple file ingestion via pipeline
# ---------------------------------------------------------------------------

class TestMultiFileIngestion:

    def test_ingest_two_optimization_files(self, tmp_path):
        """Both files should be in OptimizationStore; no AnalysisPackage created."""
        from ta_foundation.core.pipeline import ingest_folder
        from ta_foundation.core.registry import ParserRegistry

        parser = NinjaTraderOptimizationCsvParser()
        registry = ParserRegistry(parsers=[parser])

        for name in ("BotA_Optimization.csv", "BotB_Optimization.csv"):
            csv = _make_csv([{"Instrument": "NQ 06-26"}])
            (tmp_path / name).write_text(csv, encoding="utf-8")

        result = ingest_folder(tmp_path, registry=registry, load_tick_data=False)

        # No AnalysisPackage should be created for optimization files
        assert result.packages == {}

        # Both batches in OptimizationStore
        assert result.optimization_store is not None
        assert "BotA" in result.optimization_store.batches
        assert "BotB" in result.optimization_store.batches

    def test_optimization_does_not_pollute_packages(self, tmp_path):
        """An optimization file alongside a trades file must not mix the two."""
        from ta_foundation.core.pipeline import ingest_folder
        from ta_foundation.core.registry import ParserRegistry
        from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser

        # Minimal trades CSV — must have "Trade number", "Entry time", "Exit time"
        # to satisfy NinjaTraderTradesCsvParser.can_parse()
        trades_csv = (
            "Trade number,Instrument,Account,Strategy,Market pos.,Qty,"
            "Entry price,Exit price,Entry time,Exit time,Entry name,Exit name,"
            "Profit,Cum. profit,Commission,MAE,MFE,ETD\n"
            "1,NQ 06-26,Sim101,BotA,Long,1,18000,18050,"
            "4/1/2025 9:00:00 AM,4/1/2025 9:30:00 AM,"
            "Long Entry,Long Exit,250,250,0,0,300,50\n"
        )
        opt_csv = _make_csv([{"Instrument": "NQ 06-26"}])

        (tmp_path / "BotA_Trades.csv").write_text(trades_csv, encoding="utf-8")
        (tmp_path / "BotA_Optimization.csv").write_text(opt_csv, encoding="utf-8")

        registry = ParserRegistry(parsers=[
            NinjaTraderTradesCsvParser(),
            NinjaTraderOptimizationCsvParser(),
        ])
        result = ingest_folder(tmp_path, registry=registry, load_tick_data=False)

        # AnalysisPackage exists for the trades file
        assert "BotA" in result.packages
        # Optimization data is NOT attached to the package
        pkg = result.packages["BotA"]
        assert not hasattr(pkg, "optimization") or getattr(pkg, "optimization", None) is None

        # Optimization data is in the store
        assert result.optimization_store is not None
        assert "BotA" in result.optimization_store.batches

    def test_combined_results(self, tmp_path):
        from ta_foundation.core.pipeline import ingest_folder
        from ta_foundation.core.registry import ParserRegistry

        parser = NinjaTraderOptimizationCsvParser()
        registry = ParserRegistry(parsers=[parser])

        for name in ("BotA_Optimization.csv", "BotB_Optimization.csv"):
            csv = _make_csv([{"Instrument": "NQ 06-26"}, {}])
            (tmp_path / name).write_text(csv, encoding="utf-8")

        result = ingest_folder(tmp_path, registry=registry, load_tick_data=False)
        combined = result.optimization_store.combined_results()
        assert combined is not None
        assert len(combined) == 4  # 2 rows × 2 batches
        assert "batch_id" in combined.columns


# ---------------------------------------------------------------------------
# Tests: report section smoke test
# ---------------------------------------------------------------------------

class TestRenderOptimizationOverview:

    def test_renders_without_crash_when_empty(self):
        from ta_foundation.reports.html.sections.optimization_overview import render_optimization_overview
        html = render_optimization_overview({"optimization_store": None})
        assert "No optimization data" in html

    def test_renders_with_data(self, tmp_path):
        from ta_foundation.reports.html.sections.optimization_overview import render_optimization_overview
        from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser
        from ta_foundation.optimization.model import OptimizationStore
        from datetime import datetime, timezone

        p = tmp_path / "Bot_Optimization.csv"
        csv = _make_csv([
            {"Instrument": "NQ 06-26"},
            {},
            {},
        ])
        p.write_text(csv, encoding="utf-8")

        parser = NinjaTraderOptimizationCsvParser()
        art = parser.parse(p, run_id="Bot")

        from ta_foundation.core.pipeline import _attach_optimization_artifact
        store = OptimizationStore()
        _attach_optimization_artifact(store, art, warnings_sink=[])

        html = render_optimization_overview({"optimization_store": store, "options": {}})
        assert "<table" in html
        assert "NQ 06-26" in html
        assert "Bot" in html

    def test_renders_multi_file_aggregate(self, tmp_path):
        from ta_foundation.reports.html.sections.optimization_overview import render_optimization_overview
        from ta_foundation.core.pipeline import ingest_folder
        from ta_foundation.core.registry import ParserRegistry

        parser = NinjaTraderOptimizationCsvParser()
        registry = ParserRegistry(parsers=[parser])

        for name in ("BotA_Optimization.csv", "BotB_Optimization.csv"):
            csv = _make_csv([{"Instrument": "NQ 06-26"}, {}])
            (tmp_path / name).write_text(csv, encoding="utf-8")

        result = ingest_folder(tmp_path, registry=registry, load_tick_data=False)
        html = render_optimization_overview({
            "optimization_store": result.optimization_store,
            "options": {"top_n": 5},
        })
        assert "Multi-File Aggregate" in html
        assert "BotA" in html
        assert "BotB" in html
