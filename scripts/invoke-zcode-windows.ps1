[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Repo,

    [Parameter(Mandatory, Position = 1)]
    [string]$Objective,

    [Parameter(Mandatory)]
    [string[]]$Allowed,

    [Parameter(Mandatory)]
    [string]$Validation,

    [string[]]$Forbidden = @(),
    [int]$MaxChangedFiles = 0,
    [ValidateSet('small-fix', 'long-horizon', 'architecture', 'root-cause', 'production-gate', 'mobile-debug', 'research')]
    [string]$TaskClass = 'root-cause',
    [ValidateSet('low', 'medium', 'high')]
    [string]$RiskBudget = 'low',
    [ValidateRange(1, 3)]
    [int]$MaxAttempts = 1,
    [ValidateRange(0, 7200000)]
    [int]$AcceptValidatedArtifactAfterMs = 60000,
    [ValidateRange(1000, 7200000)]
    [int]$TimeoutMs = 600000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.11 or newer is required on PATH as python.'
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw 'Node.js 22 or newer is required on PATH as node.'
}

$toolRoot = Split-Path -Parent $PSScriptRoot
$supervisorPath = Join-Path $toolRoot 'tools\zcode_supervisor\zcode_supervisor.py'
$resolvedRepo = (Resolve-Path -LiteralPath $Repo).Path
$gitRoot = (& git -C $resolvedRepo rev-parse --show-toplevel 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $gitRoot) {
    throw "Target is not inside a Git repository: $resolvedRepo"
}
$resolvedRepo = (Resolve-Path -LiteralPath $gitRoot).Path

if ($Validation -match '(^|[;&|]\s*)python3(?=\s|$)') {
    throw "Use 'python' rather than 'python3' in Windows validation commands."
}

if ($MaxChangedFiles -eq 0) {
    $MaxChangedFiles = $Allowed.Count
}

$routeArguments = @(
    $supervisorPath,
    'auto-route',
    '--workspace', $resolvedRepo,
    '--objective', $Objective,
    '--task-kind', 'implementation',
    '--validation', $Validation,
    '--run-mode', 'edit',
    '--task-class', $TaskClass,
    '--risk-budget', $RiskBudget,
    '--workspace-kind', 'regular',
    '--max-changed-files', $MaxChangedFiles.ToString(),
    '--max-attempts', $MaxAttempts.ToString(),
    '--timeout-ms', $TimeoutMs.ToString(),
    '--usage-snapshot-source', 'none',
    '--no-repair-validation',
    '--result-verbosity', 'compact',
    '--execute'
)

if ($AcceptValidatedArtifactAfterMs -gt 0) {
    $routeArguments += @(
        '--accept-validated-artifact-after-ms',
        $AcceptValidatedArtifactAfterMs.ToString()
    )
}

foreach ($path in $Allowed) {
    $routeArguments += @('--allowed', $path)
}
foreach ($path in $Forbidden) {
    $routeArguments += @('--forbidden', $path)
}

& python @routeArguments
if ($LASTEXITCODE -ne 0) {
    throw "ZCode delegated run failed with code $LASTEXITCODE. Inspect .codex\zcode\runs in the target repository."
}
