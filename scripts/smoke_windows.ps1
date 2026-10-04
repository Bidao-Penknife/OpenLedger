#Requires -Version 5.1
<#
.SYNOPSIS
Verify a frozen OpenLedger executable through the shared process smoke harness.
.PARAMETER Executable
Absolute or caller-relative path to the executable to verify.
.PARAMETER OutputDirectory
Directory for the application report, harness report, screenshot, and diagnostics.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,
    [string]$OutputDirectory,
    [ValidateRange(5, 120)]
    [int]$Timeout = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows is required to verify the Windows executable.'
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$executablePath = (Resolve-Path -LiteralPath $Executable -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
    throw "Executable does not exist: $executablePath"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $reportDirectory = Join-Path $projectRoot 'build\validation\frozen'
}
else {
    $reportDirectory = [IO.Path]::GetFullPath($OutputDirectory)
}
$uvPath = (Get-Command uv -CommandType Application -ErrorAction Stop).Source

Push-Location -LiteralPath $projectRoot
try {
    & $uvPath run --locked --no-sync python scripts/smoke_app.py `
        --executable $executablePath --output-dir $reportDirectory --timeout $Timeout
    if ($LASTEXITCODE -ne 0) {
        throw "Frozen application smoke test failed with exit code $LASTEXITCODE."
    }
    Write-Output "Smoke evidence: $reportDirectory"
}
finally {
    Pop-Location
}
