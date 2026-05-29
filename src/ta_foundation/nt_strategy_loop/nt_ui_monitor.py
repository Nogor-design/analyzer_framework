from __future__ import annotations

"""UI Automation helpers for detecting NinjaTrader modal/error text."""

import json
import subprocess
import textwrap
from dataclasses import asdict, dataclass
from typing import Sequence


ERROR_PATTERNS = (
    "must be",
    "programming errors",
    "must be resolved before compiling",
    "warnings have been detected",
    "error",
    "failed",
    "invalid",
)


@dataclass(frozen=True)
class NinjaTraderUiFinding:
    window_name: str
    automation_id: str
    class_name: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def scan_ninjatrader_error_text(patterns: Sequence[str] = ERROR_PATTERNS) -> list[NinjaTraderUiFinding]:
    script = _scan_script(patterns)
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return []
    findings: list[NinjaTraderUiFinding] = []
    for item in payload if isinstance(payload, list) else []:
        findings.append(
            NinjaTraderUiFinding(
                window_name=str(item.get("window_name") or ""),
                automation_id=str(item.get("automation_id") or ""),
                class_name=str(item.get("class_name") or ""),
                text=str(item.get("text") or ""),
            )
        )
    return findings


def dismiss_ninjatrader_error_dialogs() -> bool:
    """Best-effort click on standard NinjaTrader error/warning dialogs.

    This intentionally avoids main tool windows such as Strategy Analyzer,
    Control Center, and NinjaScript Editor. It is for modal-style popups that
    expose OK/Close buttons.
    """
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _dismiss_script()],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0 and "clicked=True" in completed.stdout


def abort_strategy_analyzer_run() -> bool:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _abort_script()],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0 and "clicked=True" in completed.stdout


def _scan_script(patterns: Sequence[str]) -> str:
    pattern_array = "@(" + ",".join(repr(str(pattern)) for pattern in patterns) + ")"
    return textwrap.dedent(
        f"""
        $ErrorActionPreference = 'SilentlyContinue'
        Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
        $patterns = {pattern_array}
        $nt = Get-Process NinjaTrader -ErrorAction SilentlyContinue | Select-Object -First 1
        $rows = New-Object System.Collections.ArrayList
        $seen = New-Object 'System.Collections.Generic.HashSet[string]'
        if ($nt) {{
            $desktop = [System.Windows.Automation.AutomationElement]::RootElement
            $wins = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
            for ($i = 0; $i -lt $wins.Count; $i++) {{
                $w = $wins.Item($i)
                try {{
                    if ($w.Current.ProcessId -ne $nt.Id) {{ continue }}
                    $candidates = New-Object System.Collections.ArrayList
                    [void]$candidates.Add($w)
                    $desc = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
                    for ($j = 0; $j -lt $desc.Count; $j++) {{
                        [void]$candidates.Add($desc.Item($j))
                    }}
                    for ($j = 0; $j -lt $candidates.Count; $j++) {{
                        $el = $candidates.Item($j)
                        $txt = [string]$el.Current.Name
                        if ([string]::IsNullOrWhiteSpace($txt)) {{ continue }}
                        foreach ($p in $patterns) {{
                            if ($txt.IndexOf($p, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {{
                                $key = ([string]$w.Current.Name) + '|' + $txt
                                if ($seen.Add($key)) {{
                                    [void]$rows.Add([pscustomobject]@{{
                                        window_name = [string]$w.Current.Name
                                        automation_id = [string]$el.Current.AutomationId
                                        class_name = [string]$el.Current.ClassName
                                        text = $txt
                                    }})
                                }}
                                break
                            }}
                        }}
                    }}
                }} catch {{ }}
            }}
        }}
        $rows | ConvertTo-Json -Depth 4
        """
    )


def _dismiss_script() -> str:
    return textwrap.dedent(
        """
        $ErrorActionPreference = 'SilentlyContinue'
        Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
        $nt = Get-Process NinjaTrader -ErrorAction SilentlyContinue | Select-Object -First 1
        $clicked = $false
        if ($nt) {
            $desktop = [System.Windows.Automation.AutomationElement]::RootElement
            $wins = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
            for ($i = 0; $i -lt $wins.Count -and -not $clicked; $i++) {
                $w = $wins.Item($i)
                try {
                    if ($w.Current.ProcessId -ne $nt.Id) { continue }
                    $name = [string]$w.Current.Name
                    $class = [string]$w.Current.ClassName
                    if ($class -eq 'ControlCenter' -or $name -match 'Strategy Analyzer|NinjaScript Editor') { continue }
                    $buttonCond = New-Object System.Windows.Automation.PropertyCondition(
                        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                        [System.Windows.Automation.ControlType]::Button)
                    $buttons = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCond)
                    for ($j = 0; $j -lt $buttons.Count -and -not $clicked; $j++) {
                        $button = $buttons.Item($j)
                        $buttonName = [string]$button.Current.Name
                        if (-not $button.Current.IsEnabled) { continue }
                        if ($buttonName -match '^(OK|Ok|Close|Dismiss|Yes)$') {
                            $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                            $clicked = $true
                        }
                    }
                } catch { }
            }
        }
        Write-Output "clicked=$clicked"
        exit 0
        """
    )


def _abort_script() -> str:
    return textwrap.dedent(
        """
        $ErrorActionPreference = 'SilentlyContinue'
        Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
        $nt = Get-Process NinjaTrader -ErrorAction SilentlyContinue | Select-Object -First 1
        $clicked = $false
        if ($nt) {
            $desktop = [System.Windows.Automation.AutomationElement]::RootElement
            $wins = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
            for ($i = 0; $i -lt $wins.Count -and -not $clicked; $i++) {
                $w = $wins.Item($i)
                try {
                    if ($w.Current.ProcessId -ne $nt.Id -or $w.Current.Name -notmatch 'Strategy Analyzer') { continue }
                    $cond = New-Object System.Windows.Automation.PropertyCondition(
                        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
                        'btnCancel')
                    $abort = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
                    if ($abort -and $abort.Current.IsEnabled) {
                        $abort.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                        $clicked = $true
                    }
                } catch { }
            }
        }
        Write-Output "clicked=$clicked"
        exit 0
        """
    )
