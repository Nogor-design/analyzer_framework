from __future__ import annotations

"""NinjaTrader startup helpers for unattended compile/optimizer runs."""

import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path


DEFAULT_NT_EXE = Path(r"C:\Program Files\NinjaTrader 8\bin\NinjaTrader.exe")
DEFAULT_PASSWORD_FILE = Path(r"C:\Users\Owner\Downloads\P.txt")
DEFAULT_USERNAME = "eirwin"


@dataclass(frozen=True)
class NinjaTraderStartupResult:
    state: str
    message: str
    prompt_clicked: bool
    login_submitted: bool
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.state == "ready"


class NinjaTraderStartupError(RuntimeError):
    pass


def ensure_ninjatrader_ready(
    *,
    nt_exe: str | Path = DEFAULT_NT_EXE,
    username: str = DEFAULT_USERNAME,
    password_file: str | Path = DEFAULT_PASSWORD_FILE,
    restart: bool = False,
    startup_wait_seconds: int = 150,
) -> NinjaTraderStartupResult:
    """Start/login NinjaTrader and authorize the rebuilt AddOn prompt if needed.

    The password is read by PowerShell and never printed. The returned stdout is
    intentionally limited to operational state and prompt/login booleans.
    """

    script = _startup_script(
        nt_exe=Path(nt_exe),
        username=username,
        password_file=Path(password_file),
        restart=restart,
        startup_wait_seconds=startup_wait_seconds,
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        text=True,
        capture_output=True,
        check=False,
    )
    state = "error"
    message = ""
    prompt_clicked = False
    login_submitted = False
    for line in completed.stdout.splitlines():
        if line.startswith("state="):
            state = line.split("=", 1)[1].strip()
        elif line.startswith("message="):
            message = line.split("=", 1)[1].strip()
        elif line.startswith("prompt_clicked="):
            prompt_clicked = line.split("=", 1)[1].strip().lower() == "true"
        elif line.startswith("login_submitted="):
            login_submitted = line.split("=", 1)[1].strip().lower() == "true"

    result = NinjaTraderStartupResult(
        state=state,
        message=message,
        prompt_clicked=prompt_clicked,
        login_submitted=login_submitted,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if completed.returncode != 0 and not result.ok:
        raise NinjaTraderStartupError(result.message or result.stderr or result.stdout)
    return result


def _startup_script(
    *,
    nt_exe: Path,
    username: str,
    password_file: Path,
    restart: bool,
    startup_wait_seconds: int,
) -> str:
    restart_literal = "$true" if restart else "$false"
    return textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        $ntPath = {str(nt_exe)!r}
        $username = {username!r}
        $passwordPath = {str(password_file)!r}
        $restart = {restart_literal}
        $startupWaitSeconds = {int(startup_wait_seconds)}

        Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
        Add-Type -AssemblyName System.Windows.Forms

        function Write-State($state, $message, $promptClicked, $loginSubmitted, $exitCode) {{
            Write-Output "state=$state"
            Write-Output "message=$message"
            Write-Output "prompt_clicked=$promptClicked"
            Write-Output "login_submitted=$loginSubmitted"
            exit $exitCode
        }}

        function Find-ChildByAutomationId($root, $automationId) {{
            $cond = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
                $automationId)
            return $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
        }}

        function Get-NtWindows($ntProcess) {{
            $desktop = [System.Windows.Automation.AutomationElement]::RootElement
            $wins = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
            $items = New-Object System.Collections.ArrayList
            for ($i = 0; $i -lt $wins.Count; $i++) {{
                $w = $wins.Item($i)
                try {{
                    if ($w.Current.ProcessId -eq $ntProcess.Id) {{ [void]$items.Add($w) }}
                }} catch {{ }}
            }}
            return $items
        }}

        function Click-AddOnPrompt($ntProcess) {{
            foreach ($w in (Get-NtWindows $ntProcess)) {{
                try {{
                    $windowName = $w.Current.Name
                    $windowId = $w.Current.AutomationId
                    if ($windowId -ne 'NTMessageBox' -and $windowName -notmatch 'Update to Server Definitions|Definitions|Ninja|Add') {{
                        continue
                    }}
                    $yes = Find-ChildByAutomationId $w 'NTMessageBoxYesButton'
                    if (-not $yes) {{
                        $buttonCond = New-Object System.Windows.Automation.PropertyCondition(
                            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                            [System.Windows.Automation.ControlType]::Button)
                        $buttons = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCond)
                        for ($j = 0; $j -lt $buttons.Count; $j++) {{
                            if ($buttons.Item($j).Current.Name -eq 'Yes') {{
                                $yes = $buttons.Item($j)
                                break
                            }}
                        }}
                    }}
                    if ($yes) {{
                        $yes.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                        return $true
                    }}
                }} catch {{ }}
            }}
            return $false
        }}

        function Submit-LoginIfPresent($ntProcess) {{
            foreach ($w in (Get-NtWindows $ntProcess)) {{
                try {{
                    if ($w.Current.Name -ne 'Welcome') {{ continue }}
                    if (-not (Test-Path -LiteralPath $passwordPath)) {{
                        Write-State 'error' 'Password file is missing.' $false $false 2
                    }}
                    $password = (Get-Content -LiteralPath $passwordPath -Raw).Trim()
                    $user = Find-ChildByAutomationId $w 'tbUserName'
                    $passwordContainer = Find-ChildByAutomationId $w 'tbPassword'
                    $passwordBox = Find-ChildByAutomationId $passwordContainer 'passwordBox'
                    $loginButton = Find-ChildByAutomationId $w 'btnLogin'
                    if (-not $user -or -not $passwordBox -or -not $loginButton) {{
                        Write-State 'error' 'Login controls were not found.' $false $false 3
                    }}
                    try {{
                        $valuePattern = $user.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                        $valuePattern.SetValue($username)
                    }} catch {{
                        $user.SetFocus()
                        [System.Windows.Forms.SendKeys]::SendWait('^a')
                        [System.Windows.Forms.SendKeys]::SendWait($username)
                    }}
                    Start-Sleep -Milliseconds 250
                    $passwordBox.SetFocus()
                    [System.Windows.Forms.SendKeys]::SendWait('^a')
                    [System.Windows.Forms.SendKeys]::SendWait($password)
                    Start-Sleep -Milliseconds 250
                    $loginButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                    return $true
                }} catch {{
                    Write-State 'error' ('Login automation failed: ' + $_.Exception.Message) $false $false 4
                }}
            }}
            return $false
        }}

        if ($restart) {{
            $existing = Get-Process NinjaTrader -ErrorAction SilentlyContinue
            if ($existing) {{
                foreach ($p in $existing) {{ try {{ [void]$p.CloseMainWindow() }} catch {{ }} }}
                Start-Sleep -Seconds 10
                $remaining = Get-Process NinjaTrader -ErrorAction SilentlyContinue
                if ($remaining) {{ $remaining | Stop-Process -Force }}
            }}
        }}

        $nt = Get-Process NinjaTrader -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $nt) {{
            Start-Process -FilePath $ntPath
            Start-Sleep -Seconds 3
        }}

        $deadline = (Get-Date).AddSeconds($startupWaitSeconds + 180)
        $promptClicked = $false
        $loginSubmitted = $false
        while ((Get-Date) -lt $deadline) {{
            $nt = Get-Process NinjaTrader -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($nt) {{
                if (-not $loginSubmitted) {{
                    $loginSubmitted = Submit-LoginIfPresent $nt
                }}
                if (Click-AddOnPrompt $nt) {{
                    $promptClicked = $true
                    Start-Sleep -Seconds $startupWaitSeconds
                }}
                foreach ($w in (Get-NtWindows $nt)) {{
                    try {{
                        if ($w.Current.ClassName -eq 'ControlCenter') {{
                            Write-State 'ready' 'NinjaTrader Control Center is ready.' $promptClicked $loginSubmitted 0
                        }}
                    }} catch {{ }}
                }}
            }}
            Start-Sleep -Seconds 2
        }}
        Write-State 'timeout' 'Timed out waiting for NinjaTrader Control Center.' $promptClicked $loginSubmitted 1
        """
    )
