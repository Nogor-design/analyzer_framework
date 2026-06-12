"""CLI entry-loader tests for scripts/stop_audit_parity.py (NT Trades.csv parsing)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import ta_foundation


def _load_cli():
    root = Path(ta_foundation.__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("sap_cli", root / "scripts" / "stop_audit_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _load_cli()

TRADES_CSV = (
    "Trade number,Instrument,Market pos.,Entry price,Exit price,Entry time,Exit name\n"
    "1,NQ 06-26,Long,30125.25,30124.75,5/27/2026 1:29:01 AM,PantheonLongStop\n"
    "2,NQ 06-26,Short,30200.00,30180.00,5/27/2026 2:05:00 PM,Stop loss\n"
    ",,,,,,\n"  # trailing blank row NT sometimes appends
)


def test_load_nt_trades_entries(tmp_path):
    p = tmp_path / "Trades.csv"
    p.write_text(TRADES_CSV, encoding="utf-8")
    out = CLI.load_nt_trades_entries(str(p))
    assert list(out.columns) == ["entry_dt", "entry_price", "direction"]
    assert len(out) == 2                       # blank row dropped
    assert list(out["direction"]) == [1, -1]   # Long -> +1, Short -> -1
    assert out["entry_price"].iloc[0] == 30125.25
    # 12-hr clock parsed correctly: 2:05:00 PM -> 14:05
    assert out["entry_dt"].iloc[1].hour == 14 and out["entry_dt"].iloc[1].minute == 5
    # time-sorted
    assert out["entry_dt"].is_monotonic_increasing


def test_num_parses_money_and_parens():
    assert CLI._num("$1,234.50") == 1234.5
    assert CLI._num("($10.00)") == -10.0     # NT parenthesized loss
    assert CLI._num("") is None
