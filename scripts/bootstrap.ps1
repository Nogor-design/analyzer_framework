param(
    [string]$PythonVersion = "3.12",
    [string]$VenvPath = ".venv",
    [switch]$Recreate,
    [switch]$SkipChromium
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )

    Write-Host ">> $($Command -join ' ')"
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($Command -join ' ')"
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $VenvPath "Scripts\python.exe"
$playwrightExe = Join-Path $VenvPath "Scripts\playwright.exe"
$envCheckScript = Join-Path $PSScriptRoot "check_env.py"
$backupName = $null

try {
    $pythonExe = (& py "-$PythonVersion" -c "import sys; print(sys.executable)") | Select-Object -First 1
} catch {
    throw "Python $PythonVersion is not installed or not available via the py launcher."
}

if (-not $pythonExe) {
    throw "Python $PythonVersion is not installed or not available via the py launcher."
}

$shouldRecreate = $Recreate.IsPresent -or -not (Test-Path $venvPython)

if (-not $shouldRecreate -and (Test-Path $venvPython)) {
    & $venvPython $envCheckScript | Out-Host
    if ($LASTEXITCODE -ne 0) {
        $shouldRecreate = $true
    }
}

if ($shouldRecreate -and (Test-Path $VenvPath)) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupName = "$VenvPath.bak_$timestamp"
    Write-Host "Backing up existing virtualenv to $backupName"
    Move-Item -LiteralPath $VenvPath -Destination $backupName
}

if ($shouldRecreate) {
    Invoke-Checked -Command @("py", "-$PythonVersion", "-m", "venv", $VenvPath)
}

Invoke-Checked -Command @($venvPython, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
Invoke-Checked -Command @($venvPython, "-m", "pip", "install", "-e", ".[analysis,reporting,dev]")

if (-not $SkipChromium) {
    Invoke-Checked -Command @($playwrightExe, "install", "chromium")
}

Invoke-Checked -Command @($venvPython, $envCheckScript)

if ($backupName) {
    Write-Host "Previous virtualenv backup: $backupName"
}
