[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Repo,

    [string]$Provider,
    [string]$Model,
    [switch]$ListApis,
    [switch]$SkipCliBootstrap,
    [switch]$SkipVisionMcp,
    [switch]$NoWriteAgents,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

function Get-ZCodeProfiles {
    param([Parameter(Mandatory)] [string]$Controller)

    $json = & node $Controller api-profiles | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read API profiles from ZCode Desktop."
    }
    return ($json | ConvertFrom-Json).profiles
}

function Select-NumberedItem {
    param(
        [Parameter(Mandatory)] [object[]]$Items,
        [Parameter(Mandatory)] [scriptblock]$Label,
        [Parameter(Mandatory)] [string]$Prompt
    )

    for ($index = 0; $index -lt $Items.Count; $index++) {
        Write-Host ("[{0}] {1}" -f ($index + 1), (& $Label $Items[$index]))
    }
    $choice = Read-Host $Prompt
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $Items.Count) {
        throw "Invalid selection: $choice"
    }
    return $Items[$number - 1]
}

foreach ($commandName in @('node', 'python', 'git')) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required command is missing from PATH: $commandName"
    }
}

$toolRoot = Split-Path -Parent $PSScriptRoot
$controllerPath = Join-Path $toolRoot 'tools\zcode_control\zcodectl.mjs'
$supervisorPath = Join-Path $toolRoot 'tools\zcode_supervisor\zcode_supervisor.py'
$resolvedRepo = (Resolve-Path -LiteralPath $Repo).Path
$gitRoot = (& git -C $resolvedRepo rev-parse --show-toplevel 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $gitRoot) {
    throw "Target is not inside a Git repository: $resolvedRepo"
}
$resolvedRepo = (Resolve-Path -LiteralPath $gitRoot).Path

$profiles = @(Get-ZCodeProfiles -Controller $controllerPath)
if ($profiles.Count -eq 0) {
    throw "ZCode Desktop has no API profile with both a credential and at least one model."
}

if ($ListApis) {
    $profiles |
        Select-Object id, name, kind, @{Name = 'models'; Expression = { $_.models -join ', ' } } |
        Format-Table -Wrap -AutoSize
    return
}

if (-not $SkipCliBootstrap) {
    $selectedProfile = $null
    if ($Provider) {
        $selectedProfile = $profiles | Where-Object id -EQ $Provider | Select-Object -First 1
        if (-not $selectedProfile) {
            throw "Provider '$Provider' is not a usable ZCode API profile. Run with -ListApis to inspect choices."
        }
    }
    else {
        $selectedProfile = Select-NumberedItem -Items $profiles `
            -Label { param($item) "$($item.name) [$($item.id)]" } `
            -Prompt 'Select a ZCode API provider number'
        $Provider = $selectedProfile.id
    }

    $models = @($selectedProfile.models)
    if ($Model) {
        $configuredModel = $models | Where-Object { $_ -ieq $Model } | Select-Object -First 1
        if (-not $configuredModel) {
            throw "Model '$Model' is not configured for provider '$Provider'."
        }
        $Model = $configuredModel
    }
    else {
        $Model = Select-NumberedItem -Items $models `
            -Label { param($item) $item } `
            -Prompt 'Select a model number'
    }

    Invoke-Checked -FilePath node -ArgumentList @(
        $controllerPath,
        'bootstrap-cli-config',
        '--provider', $Provider,
        '--model', $Model
    )
}

Invoke-Checked -FilePath node -ArgumentList @($controllerPath, 'cli-preflight')

$installArguments = @($supervisorPath, 'install-repo', '--repo', $resolvedRepo, '--python-command', 'python')
if (-not $NoWriteAgents) { $installArguments += '--write-agents' }
if ($SkipVisionMcp) { $installArguments += '--skip-vision-mcp' }
if ($Force) { $installArguments += '--force' }
Invoke-Checked -FilePath python -ArgumentList $installArguments

Write-Host "ZCode supervisor routing is ready in $resolvedRepo"
if (-not $SkipCliBootstrap) {
    Write-Host "Selected ZCode API: $Provider / $Model"
}
