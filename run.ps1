<#
.SYNOPSIS
    One-command launcher for the Reddit signal pipeline (no virtual environment).

.DESCRIPTION
    Finds a system/user Python interpreter, installs dependencies from
    requirements.txt if they aren't already importable, runs run_weekly.py,
    and exits. Nothing is activated or left behind in the current shell.

.PARAMETER Stage
    Which pipeline stage to run: fetch, extract, match, report, materiality, or
    all (default).

.PARAMETER Config
    Path to the config file (default: config.yaml).

.EXAMPLE
    .\run.ps1
    Runs the full pipeline using config.yaml.

.EXAMPLE
    .\run.ps1 -Stage match
    Re-runs only the match stage.
#>

param(
    [ValidateSet("fetch", "extract", "match", "report", "materiality", "all")]
    [string]$Stage = "all",

    [string]$Config = "config.yaml"
)

$ErrorActionPreference = "Stop"

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "No Python interpreter found. Install Python 3.11+ and make sure 'python' or the 'py' launcher is on PATH."
}

$python = Find-Python

$versionOutput = & $python[0] $python[1..($python.Length - 1)] --version
Write-Host "Using $versionOutput"

Write-Host "Checking dependencies..."
& $python[0] @($python[1..($python.Length - 1)]) -c "import praw, prawcore, anthropic, yaml, keyring" 1> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies from requirements.txt..."
    & $python[0] @($python[1..($python.Length - 1)]) -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed. See output above."
    }
}

Write-Host "Running pipeline (stage: $Stage, config: $Config)..."
& $python[0] @($python[1..($python.Length - 1)]) run_weekly.py --stage $Stage --config $Config
exit $LASTEXITCODE
