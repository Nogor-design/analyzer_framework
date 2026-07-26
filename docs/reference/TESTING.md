# Testing Patterns & Procedures

**Purpose:** Guide for writing and running tests in ta_foundation.  
**Audience:** Contributors adding features, maintaining test suite.  
**Test framework:** pytest  
**Coverage target:** 75%+ (focus on critical paths).

---

## Quick Start

### Run All Tests
```bash
python -m pytest src/ta_foundation/tests/ -v
```

### Run a Single Test File
```bash
python -m pytest src/ta_foundation/tests/analysis/ma_structure/test_orchestrator.py -v
```

### Run a Single Test
```bash
python -m pytest src/ta_foundation/tests/analysis/ma_structure/test_orchestrator.py::test_ma_anchor_detection -v
```

### Run with Coverage Report
```bash
python -m pytest src/ta_foundation/tests/ --cov=src/ta_foundation --cov-report=html
```

---

## Test Structure

```
src/ta_foundation/tests/
├── __init__.py
├── conftest.py                          # Shared fixtures
├── fixtures/                            # Test data
│   ├── backtest_exports/                # Sample NinjaTrader CSVs
│   ├── market_data/                     # Sample OHLCV bars
│   └── strategy_configs/                # Sample YAML configs
├── core/                                # Core module tests
│   ├── test_model.py                    # AnalysisPackage, SummaryBlock tests
│   ├── test_pipeline.py                 # Pipeline orchestration tests
│   └── test_registry.py                 # Parser registry tests
├── analysis/
│   ├── ma_structure/
│   │   ├── test_orchestrator.py         # MA anchor analysis flow
│   │   ├── test_anchors.py              # Anchor detection
│   │   ├── test_segment_detection.py    # Segment detection
│   │   └── test_tp_sl_engine.py         # TP/SL scoring
│   ├── pattern_engine/
│   │   ├── test_engine.py
│   │   ├── test_cluster.py
│   │   └── test_monte_carlo.py
│   ├── entry_strategies/
│   │   ├── test_candle_sweep.py
│   │   ├── test_ma_crossover.py
│   │   └── test_validation.py
│   └── regime_recommender/
│       ├── test_classifier.py
│       ├── test_recommender.py
│       └── test_storage.py
├── parsers/
│   ├── test_trades_parser.py
│   ├── test_daily_parser.py
│   └── test_minute_bars_parser.py
├── reports/
│   └── html/sections/
│       ├── test_run_kpi_cards.py
│       ├── test_daily_scoreboard.py
│       └── test_equity_curve.py
└── integration/                         # End-to-end tests
    ├── test_cli_full_pipeline.py        # Full CLI run
    ├── test_report_rendering.py         # Report generation
    └── test_discovery_pipeline.py       # Discovery workflow
```

---

## Writing Tests: Core Patterns

### Pattern 1: Test a Parser

```python
# File: src/ta_foundation/tests/parsers/test_trades_parser.py
import pytest
import pandas as pd
from pathlib import Path
from ta_foundation.parsers.ninjatrader.trades_csv import TradesParser

@pytest.fixture
def sample_trades_csv(tmp_path):
    """Create a minimal NinjaTrader trades CSV."""
    csv_content = """EntryTime,ExitTime,EntryPrice,ExitPrice,PnL,Quantity
2024-01-15 10:30:00,2024-01-15 10:45:00,5000.50,5010.25,1000.00,1
2024-01-15 11:00:00,2024-01-15 11:15:00,5010.00,5005.75,-500.00,1"""
    
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(csv_content)
    return csv_file

def test_trades_parser_can_parse(sample_trades_csv):
    """Test parser recognizes trades CSV format."""
    parser = TradesParser()
    with open(sample_trades_csv, "r") as f:
        header = f.readline()
    
    assert parser.can_parse(sample_trades_csv, header)

def test_trades_parser_parses_data(sample_trades_csv):
    """Test parser extracts trade data correctly."""
    parser = TradesParser()
    artifact = parser.parse(sample_trades_csv, run_id="test_001")
    
    assert artifact.kind == "trades"
    assert artifact.run_id == "test_001"
    
    trades_df = artifact.data["trades"]
    assert len(trades_df) == 2
    assert trades_df.iloc[0]["pnl"] == 1000.00
    assert trades_df.iloc[1]["pnl"] == -500.00

def test_trades_parser_timestamps_are_tz_aware(sample_trades_csv):
    """Test timestamps are localized to America/Denver."""
    parser = TradesParser()
    artifact = parser.parse(sample_trades_csv, run_id="test_001")
    trades_df = artifact.data["trades"]
    
    entry_time = trades_df.iloc[0]["entry_time"]
    assert entry_time.tz is not None
    assert str(entry_time.tz) == "America/Denver"
```

### Pattern 2: Test a Core Module (AnalysisPackage)

```python
# File: src/ta_foundation/tests/core/test_model.py
import pytest
import pandas as pd
from ta_foundation.core.model import AnalysisPackage, SummaryBlock

@pytest.fixture
def sample_package():
    """Create a minimal AnalysisPackage."""
    trades = pd.DataFrame({
        "entry_time": pd.date_range("2024-01-15", periods=5, freq="h", tz="America/Denver"),
        "exit_time": pd.date_range("2024-01-15", periods=5, freq="h", tz="America/Denver") + pd.Timedelta("30m"),
        "pnl": [100, -50, 75, -25, 150],
    })
    
    summary = SummaryBlock(
        start_dt=trades["entry_time"].min(),
        end_dt=trades["exit_time"].max(),
        kpis_all={"total net profit": 250.0, "win rate": 0.6},
        kpis_long={},
        kpis_short={},
    )
    
    return AnalysisPackage(
        run_id="test_001",
        trades=trades,
        daily=pd.DataFrame(),
        summary=summary,
        settings=None,
        assets={},
        metadata={},
        warnings=[],
    )

def test_analysis_package_creation(sample_package):
    """Test AnalysisPackage creation."""
    assert sample_package.run_id == "test_001"
    assert len(sample_package.trades) == 5
    assert sample_package.summary.kpis_all["total net profit"] == 250.0

def test_metadata_derived_storage(sample_package):
    """Test derived data storage under metadata."""
    sample_package.metadata["derived"] = {
        "anchor_interaction": {"ma_count": 3},
        "pattern_engine": {"clusters": 10},
    }
    
    assert sample_package.metadata["derived"]["anchor_interaction"]["ma_count"] == 3
    assert sample_package.metadata["derived"]["pattern_engine"]["clusters"] == 10

def test_metadata_json_safe(sample_package):
    """Test metadata is JSON-safe."""
    import json
    
    sample_package.metadata["derived"] = {
        "result": {"count": 42, "value": 123.45},
        "list": [1, 2, 3],
    }
    
    # Should not raise
    json_str = json.dumps(sample_package.metadata)
    assert len(json_str) > 0
```

### Pattern 3: Test an Analysis Module

```python
# File: src/ta_foundation/tests/analysis/regime_recommender/test_classifier.py
import pytest
import pandas as pd
from ta_foundation.analysis.regime_recommender.classifier import RegimeClassifier

@pytest.fixture
def trending_bars():
    """Create bars in a trending regime."""
    dates = pd.date_range("2024-01-01", periods=50, freq="h", tz="America/Denver")
    return pd.DataFrame({
        "timestamp": dates,
        "open": range(5000, 5050),
        "high": range(5002, 5052),
        "low": range(4998, 5048),
        "close": range(5001, 5051),  # Consistently rising
        "volume": [1000] * 50,
    })

def test_regime_classifier_identifies_trending(trending_bars):
    """Test classifier identifies trending regime."""
    classifier = RegimeClassifier()
    regime = classifier.classify(trending_bars.iloc[-1], lookback=20)
    
    assert regime.regime == "trending"
    assert regime.strength > 0.7
    assert regime.adx > 25

def test_regime_features_computed(trending_bars):
    """Test classifier computes all required features."""
    classifier = RegimeClassifier()
    regime = classifier.classify(trending_bars.iloc[-1], lookback=20)
    
    assert hasattr(regime, "adx")
    assert hasattr(regime, "atr")
    assert hasattr(regime, "bb_width")
    assert regime.adx is not None
```

### Pattern 4: Test a Report Section

```python
# File: src/ta_foundation/tests/reports/html/sections/test_run_kpi_cards.py
import pytest
from ta_foundation.reports.html.sections.core_run_kpi_cards import render_run_kpi_cards
from ta_foundation.core.model import AnalysisPackage, SummaryBlock
import pandas as pd

@pytest.fixture
def sample_context():
    """Create a minimal report context."""
    pkg = AnalysisPackage(
        run_id="test_001",
        trades=pd.DataFrame({"pnl": [100, -50, 75]}),
        daily=pd.DataFrame(),
        summary=SummaryBlock(
            start_dt=pd.Timestamp("2024-01-01", tz="America/Denver"),
            end_dt=pd.Timestamp("2024-01-31", tz="America/Denver"),
            kpis_all={"total net profit": 125.0, "win rate": 0.67},
            kpis_long={},
            kpis_short={},
        ),
        settings=None,
        assets={},
        metadata={},
        warnings=[],
    )
    
    return {
        "packages": {"test_001": pkg},
        "options": {},
        "all_options": {},
        "market": None,
        "report_config": None,
    }

def test_run_kpi_cards_renders(sample_context):
    """Test section renders without error."""
    html = render_run_kpi_cards(sample_context)
    
    assert html is not None
    assert len(html) > 0
    assert "<table>" in html.lower() or "<div>" in html.lower()

def test_run_kpi_cards_includes_metrics(sample_context):
    """Test section includes KPI values."""
    html = render_run_kpi_cards(sample_context)
    
    assert "125" in html  # Total net profit
    assert "0.67" in html or "67" in html  # Win rate
```

### Pattern 5: Integration Test (Full Pipeline)

```python
# File: src/ta_foundation/tests/integration/test_cli_full_pipeline.py
import pytest
import tempfile
from pathlib import Path
from ta_foundation.cli.main import main as cli_main

@pytest.fixture
def sample_backtest_files(tmp_path):
    """Create sample NinjaTrader export files."""
    # Create trades CSV
    trades_csv = tmp_path / "Strategy_Trades.csv"
    trades_csv.write_text("""EntryTime,ExitTime,EntryPrice,ExitPrice,PnL,Quantity
2024-01-15 10:30:00,2024-01-15 10:45:00,5000.50,5010.25,1000.00,1
2024-01-15 11:00:00,2024-01-15 11:15:00,5010.00,5005.75,-500.00,1""")
    
    # Create summary CSV
    summary_csv = tmp_path / "Strategy_Summary.csv"
    summary_csv.write_text("""Metric,Value
Total Net Profit,500.00
Percent Profitable,50.00
Win Rate,0.5""")
    
    return tmp_path

def test_cli_full_pipeline(sample_backtest_files):
    """Test CLI pipeline from ingest to report."""
    output_dir = Path(tempfile.mkdtemp())
    report_yaml = sample_backtest_files / "report.yaml"
    report_yaml.write_text("""
report:
  title: "Test Report"
  output_filename: "test.html"

sections:
  - id: run_kpi_cards
""")
    
    # Run CLI
    cli_main(
        input_folder=str(sample_backtest_files),
        output_folder=str(output_dir),
        report_config=str(report_yaml),
    )
    
    # Verify output
    report_file = output_dir / "test.html"
    assert report_file.exists()
    
    html_content = report_file.read_text()
    assert "Test Report" in html_content
    assert "500" in html_content  # Total net profit
```

---

## Fixtures: Shared Test Data

### conftest.py: Centralized Fixtures

```python
# File: src/ta_foundation/tests/conftest.py
import pytest
import pandas as pd
import tempfile
from pathlib import Path

@pytest.fixture
def sample_market_bars():
    """5-day sample OHLCV bars (1h resolution)."""
    dates = pd.date_range("2024-01-15", periods=120, freq="h", tz="America/Denver")
    return pd.DataFrame({
        "timestamp": dates,
        "open": range(5000, 5120),
        "high": range(5002, 5122),
        "low": range(4998, 5118),
        "close": range(5001, 5121),
        "volume": [10000] * 120,
    })

@pytest.fixture
def sample_trades_df():
    """Sample trades DataFrame."""
    return pd.DataFrame({
        "entry_time": pd.date_range("2024-01-15", periods=10, freq="h", tz="America/Denver"),
        "exit_time": pd.date_range("2024-01-15", periods=10, freq="h", tz="America/Denver") + pd.Timedelta("30m"),
        "entry_price": [5000 + i*5 for i in range(10)],
        "exit_price": [5005 + i*5 for i in range(10)],
        "pnl": [100, -50, 75, -25, 150, 50, -75, 125, -25, 200],
        "duration_bars": [30] * 10,
    })

@pytest.fixture
def sample_analysis_package(sample_trades_df):
    """Minimal AnalysisPackage for testing."""
    from ta_foundation.core.model import AnalysisPackage, SummaryBlock
    
    return AnalysisPackage(
        run_id="test_001",
        trades=sample_trades_df,
        daily=pd.DataFrame(),
        summary=SummaryBlock(
            start_dt=sample_trades_df["entry_time"].min(),
            end_dt=sample_trades_df["exit_time"].max(),
            kpis_all={"total net profit": 500.0, "win rate": 0.6},
            kpis_long={},
            kpis_short={},
        ),
        settings=None,
        assets={},
        metadata={"derived": {}},
        warnings=[],
    )

@pytest.fixture
def tmp_backtest_folder():
    """Temporary folder with sample backtest files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create sample trades CSV
        trades_csv = tmp_path / "Strategy_Trades.csv"
        trades_csv.write_text("""EntryTime,ExitTime,EntryPrice,ExitPrice,PnL
2024-01-15 10:30:00,2024-01-15 10:45:00,5000.50,5010.25,1000.00
2024-01-15 11:00:00,2024-01-15 11:15:00,5010.00,5005.75,-500.00""")
        
        # Create sample summary CSV
        summary_csv = tmp_path / "Strategy_Summary.csv"
        summary_csv.write_text("""Metric,Value
Total Net Profit,500.00
Total Trades,2
Win Rate,0.5""")
        
        yield tmp_path
```

---

## Test Categories & Checklist

### Unit Tests (Isolated Components)

Tests for individual functions/classes. Should:
- Test one thing per test
- Use fixtures for inputs
- Verify outputs and side effects
- Run in < 100ms

**Example:**
```python
def test_ma_calculation_basic(sample_market_bars):
    """Test SMA calculation."""
    from ta_foundation.analysis.indicators import simple_moving_average
    
    result = simple_moving_average(sample_market_bars["close"], period=20)
    
    assert len(result) == len(sample_market_bars)
    assert result.iloc[0:19].isna().all()  # First 19 are NaN
    assert not result.iloc[20:].isna().any()  # Rest are computed
```

### Integration Tests (Multi-Component)

Tests that combine multiple modules (parser → pipeline → analysis). Should:
- Test realistic workflows
- Use real data (or realistic fixtures)
- Verify component interaction
- Can take seconds

**Example:**
```python
def test_parser_to_analysis_package(tmp_backtest_folder):
    """Test CSV parsing → AnalysisPackage construction."""
    from ta_foundation.cli.main import main
    
    main(input_folder=str(tmp_backtest_folder), ...)
    # Verify output AnalysisPackage
```

### End-to-End Tests (Full Pipeline)

Tests that run the complete CLI/workflow. Should:
- Test from user input to final output
- Can take 10+ seconds
- Run less frequently (e.g., pre-release)

**Example:**
```python
def test_cli_backtest_to_html_report(tmp_backtest_folder):
    """Test complete CLI: backtest files → HTML report."""
    # Run full CLI pipeline
    # Verify HTML file created, contains expected content
```

---

## Common Testing Patterns

### Pattern: Parametrized Tests

```python
import pytest

@pytest.mark.parametrize("family,expected_regime", [
    ("trending", "trending"),
    ("choppy", "choppy"),
    ("volatile", "volatile"),
])
def test_regime_classification(family, expected_regime, regime_bars_fixture):
    """Test regime classification for multiple families."""
    classifier = RegimeClassifier()
    regime = classifier.classify(regime_bars_fixture[family].iloc[-1])
    
    assert regime.regime == expected_regime
```

### Pattern: Fixture Parameterization

```python
@pytest.fixture(params=["SMA", "EMA", "TEMA"])
def ma_family(request):
    """Test across all MA families."""
    return request.param

def test_ma_calculation(sample_bars, ma_family):
    """Test MA calculation for all families."""
    result = compute_ma(sample_bars["close"], family=ma_family, period=20)
    assert len(result) == len(sample_bars)
```

### Pattern: Mocking External Dependencies

```python
from unittest.mock import patch, MagicMock

def test_sqlite_storage_fallback():
    """Test SQLite storage fallback if database unavailable."""
    with patch("sqlite3.connect") as mock_connect:
        mock_connect.side_effect = Exception("DB unavailable")
        
        store = RegimeStore()
        # Should use in-memory fallback
        assert store.is_fallback_mode
```

---

## Test Execution

### Run Tests Locally (Before Commit)

```bash
# Unit tests only (fast)
python -m pytest src/ta_foundation/tests/ -m "not integration" -v

# All tests
python -m pytest src/ta_foundation/tests/ -v

# With coverage
python -m pytest src/ta_foundation/tests/ --cov=src/ta_foundation --cov-report=term-missing
```

### Debug a Failing Test

```bash
# Verbose output + pdb on failure
python -m pytest src/ta_foundation/tests/test_file.py::test_name -vv --pdb

# Show stdout/print statements
python -m pytest src/ta_foundation/tests/test_file.py::test_name -s

# Last N lines of output on failure
python -m pytest src/ta_foundation/tests/test_file.py::test_name --tb=short
```

### Continuous Integration (CI)

```bash
# Run full suite with coverage
python -m pytest src/ta_foundation/tests/ \
  --cov=src/ta_foundation \
  --cov-report=xml \
  --junit-xml=test_results.xml
```

---

## Checklist: Before Committing Code

- [ ] All unit tests pass: `pytest -m "not integration" -v`
- [ ] New feature has test coverage (unit + integration)
- [ ] No test regressions (all existing tests still pass)
- [ ] Coverage >= 75% for new/modified files
- [ ] Test names are descriptive (`test_...` format)
- [ ] Fixtures are reused (don't duplicate setup)
- [ ] Timestamps are tz-aware in test data
- [ ] Metadata used in tests is JSON-safe

---

## Sign-Off

**Test framework:** pytest  
**Coverage target:** 75%  
**Last updated:** May 24, 2026  
**Maintainer:** Development team

---

Last updated: May 24, 2026
