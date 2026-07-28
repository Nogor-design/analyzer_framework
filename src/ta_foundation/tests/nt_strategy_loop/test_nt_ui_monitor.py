from __future__ import annotations

from ta_foundation.nt_strategy_loop.nt_ui_monitor import (
    ERROR_PATTERNS,
    _is_ambient_editor_chrome,
    _scan_script,
)


def test_scan_script_contains_error_patterns() -> None:
    script = _scan_script(ERROR_PATTERNS)

    assert "UIAutomationClient" in script
    assert "programming errors" in script
    assert "TrueCondition" in script
    assert "Current.Name" in script
    assert "ConvertTo-Json" in script


def test_empty_editor_error_grid_chrome_is_not_a_finding() -> None:
    window = "NinjaScript Editor - New tab"

    assert _is_ambient_editor_chrome(
        window, "Header", "EditorErrorGridItem - 5 Fields - index = 0"
    )
    assert _is_ambient_editor_chrome(window, "Label", "Error")
    assert _is_ambient_editor_chrome(
        window, "TextBlock", "The above warnings have been detected:"
    )
    assert _is_ambient_editor_chrome(
        window,
        "TextBlock",
        "The above programming errors must be resolved before compiling",
    )


def test_real_editor_error_and_other_window_error_are_preserved() -> None:
    assert not _is_ambient_editor_chrome(
        "NinjaScript Editor - New tab",
        "DataGridCell",
        "CS0103 The name 'missingValue' does not exist",
    )
    assert not _is_ambient_editor_chrome(
        "Strategy Analyzer",
        "TextBlock",
        "Error",
    )
