#Requires -Version 5.1
<## Compile a per-user installer from the already verified, complete bundle. ##>
[CmdletBinding()]
param([string]$CompilerPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ([string]::IsNullOrWhiteSpace($CompilerPath)) { $CompilerPath = Join-Path $projectRoot 'build\tools\inno-6.7.3\ISCC.exe' }
$compiler = (Resolve-Path -LiteralPath $CompilerPath).Path
$bundle = Join-Path $projectRoot 'dist\windows\OpenLedger'
$output = Join-Path $projectRoot 'dist\installer'
foreach ($path in @($bundle, $output)) {
    $ancestor = [IO.Path]::GetFullPath($path)
    if (-not $ancestor.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Installer output escapes repository' }
    while ($ancestor -ne $projectRoot) {
        if ((Test-Path -LiteralPath $ancestor) -and (((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'Installer path passes through a reparse point' }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
}
$toolManifest = Get-Content -LiteralPath (Join-Path $projectRoot 'packaging\inno\compiler-manifest.json') -Raw | ConvertFrom-Json
$compilerDirectory = [IO.Path]::GetDirectoryName($compiler)
foreach ($entry in $toolManifest.files.PSObject.Properties) {
    $path = Join-Path $compilerDirectory $entry.Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) { throw "Pinned compiler file differs: $($entry.Name)" }
}
$manifest = Get-Content -LiteralPath (Join-Path $bundle 'bundle-manifest.json') -Raw | ConvertFrom-Json
$identity = Get-Content -LiteralPath (Join-Path $projectRoot 'build\release\identity.json') -Raw | ConvertFrom-Json
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$sourceVersion = (& $python -c 'from openledger import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0 -or $sourceVersion -ne $identity.version) { throw 'Rebuild the bundle after changing the source version' }
if ($manifest.version -ne $identity.version -or $manifest.app_id -ne $identity.app_id) { throw 'Bundle identity differs from current source' }
foreach ($entry in $manifest.files) {
    $path = [IO.Path]::GetFullPath((Join-Path $bundle $entry.path))
    if (-not $path.StartsWith($bundle + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest file escapes bundle' }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -ne $entry.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw "Bundle file changed: $($entry.path)" }
}
New-Item -ItemType Directory -Path $output -Force | Out-Null
& $compiler ("/DBundleDir=$bundle") ("/DOutputDir=$output") ("/DAppVersion=$($identity.version)") ("/DWindowsVersion=$($identity.windows_version)") (Join-Path $projectRoot 'packaging\inno\OpenLedger.iss')
if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed: $LASTEXITCODE" }
$installer = Join-Path $output "OpenLedger-$($identity.version)-windows-x64-setup.exe"
$digest = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$installer.sha256", "$digest  $([IO.Path]::GetFileName($installer))`n", [Text.UTF8Encoding]::new($false))
Write-Output "Installer: $installer"
Write-Output "SHA256: $digest"
