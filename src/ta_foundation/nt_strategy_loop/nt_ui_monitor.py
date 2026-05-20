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
        if ($nt) {{
            $desktop = [System.Windows.Automation.AutomationElement]::RootElement
            $wins = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
            $textCond = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Text)
            for ($i = 0; $i -lt $wins.Count; $i++) {{
                $w = $wins.Item($i)
                try {{
                    if ($w.Current.ProcessId -ne $nt.Id) {{ continue }}
                    $texts = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $textCond)
                    for ($j = 0; $j -lt $texts.Count; $j++) {{
                        $txt = [string]$texts.Item($j).Current.Name
                        if ([string]::IsNullOrWhiteSpace($txt)) {{ continue }}
                        foreach ($p in $patterns) {{
                            if ($txt.IndexOf($p, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {{
                                [void]$rows.Add([pscustomobject]@{{
                                    window_name = [string]$w.Current.Name
                                    automation_id = [string]$w.Current.AutomationId
                                    class_name = [string]$w.Current.ClassName
                                    text = $txt
                                }})
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

