#Requires -Version 5.1
<#
.SYNOPSIS
Build the locked Windows x64 onedir bundle, collect licenses, and create a ZIP.
.DESCRIPTION
All build output remains beneath the repository's build/ and dist/ directories.
This script does not publish releases or build an installer. Run smoke_windows.ps1
afterwards to verify that the frozen application starts and exits successfully.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows is required to build the Windows executable.'
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$distributionRoot = Join-Path $projectRoot 'dist\windows'
$buildRoot = Join-Path $projectRoot 'build\windows'
$pyinstallerCache = Join-Path $projectRoot 'build\pyinstaller-cache'
$specification = Join-Path $projectRoot 'packaging\pyinstaller\OpenLedger.spec'
$bundleRoot = Join-Path $distributionRoot 'OpenLedger'
$uvPath = (Get-Command uv -CommandType Application -ErrorAction Stop).Source

function Assert-RepositoryOutputPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $absolutePath = [IO.Path]::GetFullPath($Path)
    $repositoryPrefix = $projectRoot.TrimEnd('\') + '\'
    if (-not $absolutePath.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build output must remain inside the repository: $absolutePath"
    }
    $currentPath = $absolutePath
    while ($currentPath -ne $projectRoot) {
        if (Test-Path -LiteralPath $currentPath) {
            $existingItem = Get-Item -LiteralPath $currentPath -Force
            if (($existingItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Build output cannot pass through a junction or symbolic link: $currentPath"
            }
        }
        $currentPath = [IO.Path]::GetDirectoryName($currentPath)
    }
}

function Invoke-LockedUv {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & $uvPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "uv command failed with exit code $LASTEXITCODE."
    }
}

$previousPyinstallerCache = [Environment]::GetEnvironmentVariable('PYINSTALLER_CONFIG_DIR', 'Process')
$previousPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$previousExecutablePath = [Environment]::GetEnvironmentVariable('PATH', 'Process')
Push-Location -LiteralPath $projectRoot
try {
    Assert-RepositoryOutputPath -Path $distributionRoot
    Assert-RepositoryOutputPath -Path $bundleRoot
    Assert-RepositoryOutputPath -Path $buildRoot
    Assert-RepositoryOutputPath -Path $pyinstallerCache
    $env:PYINSTALLER_CONFIG_DIR = $pyinstallerCache
    Remove-Item -LiteralPath 'Env:\PYTHONPATH' -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $distributionRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $pyinstallerCache -Force | Out-Null
    Invoke-LockedUv -Arguments @('sync', '--locked', '--group', 'dev', '--group', 'build')
    $runtimeCheck = "import platform, sys; assert sys.version_info[:2] == (3, 12); assert sys.platform == 'win32'; assert platform.machine().upper() in ('AMD64', 'X86_64'), 'Windows x64 Python is required'"
    Invoke-LockedUv -Arguments @('run', '--locked', '--no-sync', 'python', '-c', $runtimeCheck)
    Invoke-LockedUv -Arguments @(
        'run', '--locked', '--no-sync', 'python', 'scripts/update_translations.py',
        '--check', '--compile'
    )
    Invoke-LockedUv -Arguments @('run', '--locked', '--no-sync', 'python', 'scripts/release_tools.py', 'prepare')
    # Resolve dependencies only from this environment and Windows. Unrelated tools
    # on the caller's PATH can contain DLLs with the same name but a different ABI.
    $prefixCommand = 'import sys; print(sys.base_prefix)'
    $prefixOutput = @(Invoke-LockedUv -Arguments @('run', '--locked', '--no-sync', 'python', '-c', $prefixCommand))
    $interpreterRoot = [IO.Path]::GetFullPath(($prefixOutput -join '').Trim())
    $windowsDirectory = [Environment]::GetEnvironmentVariable('SystemRoot', 'Process')
    if ([string]::IsNullOrWhiteSpace($windowsDirectory)) {
        throw 'Windows system directory could not be resolved.'
    }
    $env:PATH = @(
        (Join-Path $projectRoot '.venv\Scripts'),
        $interpreterRoot,
        (Join-Path $interpreterRoot 'DLLs'),
        (Join-Path $windowsDirectory 'System32'),
        $windowsDirectory
    ) -join [IO.Path]::PathSeparator
    Invoke-LockedUv -Arguments @(
        'run', '--locked', '--group', 'build', 'pyinstaller',
        '--noconfirm', '--clean',
        '--distpath', $distributionRoot,
        '--workpath', $buildRoot,
        $specification
    )

    $executable = Join-Path $bundleRoot 'OpenLedger.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "PyInstaller did not produce $executable."
    }

    Invoke-LockedUv -Arguments @(
        'run', '--locked', '--no-sync', 'python', 'scripts/collect_licenses.py',
        '--output-dir', (Join-Path $bundleRoot 'licenses')
    )
    Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination $bundleRoot -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md') -Destination $bundleRoot -Force
    Invoke-LockedUv -Arguments @('run', '--locked', '--no-sync', 'python', 'scripts/release_tools.py', 'bundle')

    $versionCommand = 'from openledger._version import __version__; print(__version__)'
    $versionOutput = @(Invoke-LockedUv -Arguments @('run', '--locked', '--no-sync', 'python', '-c', $versionCommand))
    $applicationVersion = ($versionOutput -join '').Trim()
    if ($applicationVersion -notmatch '^[A-Za-z0-9][A-Za-z0-9.+-]*$') {
        throw 'Application version cannot be used as a safe archive name.'
    }

    $archivePath = Join-Path $distributionRoot "OpenLedger-$applicationVersion-windows-x64.zip"
    Compress-Archive -LiteralPath $bundleRoot -DestinationPath $archivePath -CompressionLevel Optimal -Force
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $archiveName = [IO.Path]::GetFileName($archivePath)
    [IO.File]::WriteAllText(
        "$archivePath.sha256",
        "$archiveHash  $archiveName`n",
        [Text.UTF8Encoding]::new($false)
    )
    Write-Output "Executable: $executable"
    Write-Output "Archive: $archivePath"
    Write-Output "SHA256: $archiveHash"
}
finally {
    [Environment]::SetEnvironmentVariable('PYINSTALLER_CONFIG_DIR', $previousPyinstallerCache, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $previousPythonPath, 'Process')
    [Environment]::SetEnvironmentVariable('PATH', $previousExecutablePath, 'Process')
    Pop-Location
}
