from __future__ import annotations

from ta_foundation.nt_strategy_loop.nt_ui_monitor import ERROR_PATTERNS, _scan_script


def test_scan_script_contains_error_patterns() -> None:
    script = _scan_script(ERROR_PATTERNS)

    assert "UIAutomationClient" in script
    assert "programming errors" in script
    assert "ConvertTo-Json" in script

