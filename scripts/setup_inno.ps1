#Requires -Version 5.1
<## Download the pinned, signed compiler and extract its supported portable mode. ##>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$toolRoot = Join-Path $projectRoot 'build\tools\inno-6.7.3'
$downloadRoot = Join-Path $projectRoot 'build\tools\downloads'
$installer = Join-Path $downloadRoot 'innosetup-6.7.3.exe'
$expectedHash = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
$downloadUri = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
foreach ($path in @($toolRoot, $downloadRoot)) {
    $resolved = [IO.Path]::GetFullPath($path)
    if (-not $resolved.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Compiler path escapes repository' }
    $ancestor = $resolved
    while ($ancestor -ne $projectRoot) {
        if ((Test-Path -LiteralPath $ancestor) -and (((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'Compiler directory passes through a reparse point' }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $installer)) {
    Invoke-WebRequest -Uri $downloadUri -OutFile $installer -UseBasicParsing
}
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Compiler download digest mismatch' }
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Pyrsys B\.V\.') { throw 'Compiler publisher signature is invalid' }
if (-not (Test-Path -LiteralPath (Join-Path $toolRoot 'ISCC.exe'))) {
    $process = Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/PORTABLE=1','/NOICONS',('/DIR="' + $toolRoot + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Compiler portable extraction failed: $($process.ExitCode)" }
}
Write-Output (Join-Path $toolRoot 'ISCC.exe')
