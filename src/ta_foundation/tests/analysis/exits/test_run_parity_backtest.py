"""Template-builder test for scripts/run_parity_backtest.py (generated from the real
PantheonMaster.cs; no NinjaTrader). Guards that the parity backtest pins the exact
managed bar-close-trail configuration the Python replica models."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import ta_foundation
from ta_foundation.analysis.exits.nt_atr_trail_parity import NtAtrTrailConfig


def _load_rpb():
    root = Path(ta_foundation.__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("rpb", root / "scripts" / "run_parity_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RPB = _load_rpb()


@pytest.mark.skipif(not RPB.DEFAULT_STRATEGY_SOURCE.is_file(),
                    reason="PantheonMaster.cs not present")
def test_build_parity_template_pins_managed_bar_close_trail(tmp_path):
    out = tmp_path / "parity.xml"
    audit = r"C:\temp\parity_audit.csv"
    RPB.build_parity_backtest_template(
        instrument="NQ 06-26", from_date="2026-03-12", to_date="2026-03-20",
        audit_csv_path=audit, output_path=out, cfg=NtAtrTrailConfig(),
    )
    text = out.read_text(encoding="utf-8")
    # managed bar-close path (the leg the replica models), trail under test, trading on
    assert "<Category>Backtest</Category>" in text
    assert "<UseLiveStopManagement>false</UseLiveStopManagement>" in text
    assert "<UseDiscoveryExitPolicy>true</UseDiscoveryExitPolicy>" in text
    assert "<EnableDiscoveryFilters>false</EnableDiscoveryFilters>" in text
    assert "<DiscoveryExitPolicy>AtrTrail</DiscoveryExitPolicy>" in text
    assert "<StopTicks>60</StopTicks>" in text
    assert "<AtrPeriod>14</AtrPeriod>" in text
    assert audit in text                                   # StopAuditCsvPath pinned
    assert "<OrderFillResolution>Standard</OrderFillResolution>" in text
    # a fixed backtest has NO optimizer sections (enum params can't crash the optimizer)
    assert "OptimizationParameters" not in text
    # the intermediate seed is cleaned up
    assert not out.with_name(out.stem + "__seed.xml").exists()
