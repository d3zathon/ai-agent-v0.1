<#
    run_agent.ps1
    Windows launcher for the local security-agent.

    - Creates a .venv virtual environment next to this script if one
      doesn't already exist.
    - Installs/updates dependencies from requirements.txt.
    - Starts the agent (interactive REPL, or a single task if you pass
      arguments through to this script).

    Usage:
        .\run_agent.ps1
        .\run_agent.ps1 "add a subtract function and test it"

    You may need to allow local script execution once, in an elevated or
    per-user PowerShell session:
        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TaskArgs
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VenvDir = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Get-BasePython {
    foreach ($candidate in @("py", "python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "No Python interpreter found on PATH. Install Python 3.11+ from python.org and try again."
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating virtual environment in .venv ..." -ForegroundColor Cyan
    $basePython = Get-BasePython
    & $basePython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment."
    }
}

Write-Host "Installing dependencies from requirements.txt ..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r (Join-Path $ScriptDir "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install dependencies."
}

Write-Host "Starting security-agent ..." -ForegroundColor Green
if ($TaskArgs -and $TaskArgs.Count -gt 0) {
    & $VenvPython -m agent.main @TaskArgs
} else {
    & $VenvPython -m agent.main
}
