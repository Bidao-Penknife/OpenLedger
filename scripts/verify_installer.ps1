#Requires -Version 5.1
<## Verify the real installer with only PowerShell and the shipped runtime. ##>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$OutputDirectory,
    [string]$PreviousInstaller,
    [string]$SeedDirectory,
    [switch]$RequireCleanSystem,
    [switch]$AllowElevatedRunner
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Join-Path $projectRoot 'build\validation\installer' }
$output = [IO.Path]::GetFullPath($OutputDirectory)
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$keyName = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{2EA4E61C-9182-4B6C-BFF8-995365131679}_is1'
$registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryView]::Registry64)
$existing = $registry.OpenSubKey($keyName)
if ($null -ne $existing) { $existing.Dispose(); throw 'An existing OpenLedger installation is registered. Verification must use a disposable user account.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isElevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isElevated -and -not $AllowElevatedRunner) { throw 'Run this check as an ordinary user, without elevation.' }
if ($RequireCleanSystem -and $AllowElevatedRunner) { throw 'Clean-system certification requires an ordinary user.' }
$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
$pythonRegistered = $false
foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser,[Microsoft.Win32.RegistryHive]::LocalMachine)) {
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64,[Microsoft.Win32.RegistryView]::Registry32)) {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,$view)
        $pythonKey = $base.OpenSubKey('Software\Python\PythonCore')
        if ($null -ne $pythonKey) { $pythonRegistered = $pythonRegistered -or ($pythonKey.SubKeyCount -gt 0); $pythonKey.Dispose() }
        $base.Dispose()
    }
}
$pythonOnPath = ($null -ne $pythonCommand -and $pythonCommand.Source -notlike '*\WindowsApps\*')
if ($RequireCleanSystem -and ($pythonRegistered -or $pythonOnPath)) { throw 'Clean-system gate failed: a Python installation was found. Use a fresh Windows VM.' }
New-Item -ItemType Directory -Path $output -Force | Out-Null
$run = Join-Path $output ('安装验收 space-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
$program = Join-Path $run '程序目录'
$profile = Join-Path $run '独立LocalAppData'
$data = Join-Path $profile 'OpenLedger'
$database = Join-Path $data 'database\openledger.sqlite3'
$reportPath = Join-Path $output 'installer-check.json'
$oldLocal = [Environment]::GetEnvironmentVariable('LOCALAPPDATA', 'Process')
$oldPath = [Environment]::GetEnvironmentVariable('PATH', 'Process')
$oldPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$oldPythonHome = [Environment]::GetEnvironmentVariable('PYTHONHOME', 'Process')
$oldQtPlatform = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM', 'Process')
$proof = [ordered]@{ status='running'; ordinary_user=(-not $isElevated); elevated_runner_allowed=[bool]$AllowElevatedRunner; clean_system_requested=[bool]$RequireCleanSystem; clean_windows_certified=$false; python_found_on_host=($pythonOnPath -or $pythonRegistered); installer=$installerPath; installer_sha256=(Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant(); run_directory=$run; cases=@() }

function Invoke-VerifiedProcess {
    param([string]$File,[string[]]$Arguments,[int]$Seconds=180,[switch]$AllowFailure)
    $processInfo = New-Object Diagnostics.ProcessStartInfo
    $processInfo.FileName = $File
    $processInfo.WorkingDirectory = $run
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $quoted = foreach ($argument in $Arguments) {
        if ($argument.Contains('"')) { throw 'Unexpected quote in verification argument' }
        '"' + $argument + '"'
    }
    $processInfo.Arguments = $quoted -join ' '
    $process = [Diagnostics.Process]::Start($processInfo)
    try {
        if (-not $process.WaitForExit($Seconds * 1000)) { $process.Kill(); throw 'Owned verification process timed out' }
        $code = $process.ExitCode
        if (-not $AllowFailure -and $code -ne 0) { throw "Process failed ($code): $File" }
        return $code
    }
    finally { $process.Dispose() }
}

function Install-Package {
    param([string]$File,[string]$Target,[string]$Name,[switch]$Reject)
    $args = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/NOICONS','/LANG=chinesesimplified','/TASKS=!desktopicon',('/DIR=' + $Target),('/LOG=' + (Join-Path $run ($Name + '.log'))))
    $code = Invoke-VerifiedProcess -File $File -Arguments $args -AllowFailure
    if ($Reject -and $code -eq 0) { throw "Installer unexpectedly accepted $Name" }
    if (-not $Reject -and $code -ne 0) { throw "Installer failed $Name ($code)" }
    $proof.cases += @{name=$Name; exit_code=$code; expected_rejection=[bool]$Reject; status='passed'}
}

function Test-InstalledFiles {
    $manifest = Get-Content -LiteralPath (Join-Path $program 'bundle-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($item in $manifest.files) {
        $path = [IO.Path]::GetFullPath((Join-Path $program $item.path))
        if (-not $path.StartsWith($program + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Installed manifest path escapes directory' }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -ne $item.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) { throw "Installed byte mismatch: $($item.path)" }
    }
    $key = $registry.OpenSubKey($keyName)
    if ($null -eq $key) { throw 'Current-user uninstall registration missing' }
    try {
        if ($key.GetValue('DisplayVersion') -ne $manifest.version -or $key.GetValue('InstallLocation').TrimEnd('\') -ne $program) { throw 'Registration differs from installed bundle' }
    }
    finally { $key.Dispose() }
    $proof.version = $manifest.version
    $proof.installed_file_count = $manifest.files.Count
    $proof.installed_files_match_manifest = $true
    return $manifest
}

function Test-InstalledStartup {
    param([string]$Name,[string]$DataRoot)
    $startup = Join-Path $run ($Name + '.json')
    $picture = Join-Path $run ($Name + '.png')
    $exe = Join-Path $program 'OpenLedger.exe'
    $args = @('--smoke-test','--smoke-features','--smoke-report',$startup,'--screenshot',$picture)
    if (-not [string]::IsNullOrWhiteSpace($DataRoot)) { $args += @('--data-dir',$DataRoot) }
    $null = Invoke-VerifiedProcess -File $exe -Arguments $args -Seconds 45
    $result = Get-Content -LiteralPath $startup -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($result.runtime.app_version -ne $proof.version) { throw 'Installed application differs from installer version' }
    if ($result.status -ne 'passed' -or $result.qt_platform -ne 'windows' -or -not $result.frozen -or -not $result.installation_guard_held -or -not $result.native_handle_created -or -not $result.close_accepted -or $result.exit_code -ne 0 -or $result.system_features.network_requests -ne 0 -or $result.system_features.financial_writes -ne 0 -or -not $result.features.financial_counts_unchanged) { throw 'Installed runtime proof incomplete' }
    if (-not (Test-Path -LiteralPath $picture)) { throw 'Installed screenshot missing' }
    $proof.cases += @{name=$Name; status='passed'; report=$startup; screenshot=$picture; elapsed_seconds=$result.elapsed_seconds}
    return $result
}

try {
    [Environment]::SetEnvironmentVariable('LOCALAPPDATA',$profile,'Process')
    [Environment]::SetEnvironmentVariable('PATH',(Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot,'Process')
    foreach ($name in @('PYTHONPATH','PYTHONHOME','QT_QPA_PLATFORM')) { [Environment]::SetEnvironmentVariable($name,$null,'Process') }
    if (-not [string]::IsNullOrWhiteSpace($SeedDirectory)) {
        $seed = (Resolve-Path -LiteralPath $SeedDirectory).Path
        if (-not $seed.StartsWith((Join-Path $projectRoot 'build\validation\') ,[StringComparison]::OrdinalIgnoreCase)) { throw 'Seed must be an explicitly synthetic validation directory' }
        New-Item -ItemType Directory -Path $data -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $seed 'database') -Destination $data -Recurse
        foreach ($name in @('settings.json','ai-settings.json','update-settings.json')) {
            if (Test-Path -LiteralPath (Join-Path $seed $name)) { Copy-Item -LiteralPath (Join-Path $seed $name) -Destination $data }
        }
    }
    $foreign = Join-Path $run '其他软件目录'
    New-Item -ItemType Directory -Path $foreign | Out-Null
    [IO.File]::WriteAllText((Join-Path $foreign 'keep.txt'),'synthetic unrelated file')
    Install-Package -File $installerPath -Target $foreign -Name 'reject-foreign-directory' -Reject
    if ((Get-Content -LiteralPath (Join-Path $foreign 'keep.txt') -Raw) -ne 'synthetic unrelated file') { throw 'Foreign directory changed' }
    if (-not [string]::IsNullOrWhiteSpace($PreviousInstaller)) {
        Install-Package -File (Resolve-Path -LiteralPath $PreviousInstaller).Path -Target $program -Name 'install-previous-baseline'
    }
    $before = if (Test-Path -LiteralPath $database) { (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash } else { $null }
    Install-Package -File $installerPath -Target $program -Name 'install-or-upgrade-current'
    $manifest = Test-InstalledFiles
    $startup = Test-InstalledStartup -Name 'installed-default-data-root' -DataRoot ''
    if ([IO.Path]::GetFullPath($startup.runtime.data_directory) -ne $data) { throw 'Default data written outside independent LocalAppData' }
    if ($null -ne $before -and (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -ne $before) { throw 'Existing financial database changed' }
    $before = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
    $privateSettings = @{}
    foreach ($name in @('settings.json','ai-settings.json','update-settings.json')) {
        $path = Join-Path $data $name
        if (Test-Path -LiteralPath $path) { $privateSettings[$name] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
    }
    Install-Package -File $installerPath -Target $program -Name 'same-version-repair'
    $null = Test-InstalledFiles
    if (-not [string]::IsNullOrWhiteSpace($PreviousInstaller)) {
        Install-Package -File (Resolve-Path -LiteralPath $PreviousInstaller).Path -Target $program -Name 'reject-downgrade' -Reject
        $null = Test-InstalledFiles
    }
    if (-not ('OpenLedgerInstallerGateNative' -as [type])) {
        Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class OpenLedgerInstallerGateNative { [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr CreateMutex(IntPtr a,bool b,string c); [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h); }'
    }
    $gate = [OpenLedgerInstallerGateNative]::CreateMutex([IntPtr]::Zero,$false,'Local\OpenLedger.InstallationGate')
    if ($gate -eq [IntPtr]::Zero) { throw 'Could not create native active-process gate' }
    try {
        Install-Package -File $installerPath -Target $program -Name 'reject-active-application' -Reject
        $code = Invoke-VerifiedProcess -File (Join-Path $program 'unins000.exe') -Arguments @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -AllowFailure
        if ($code -eq 0 -or -not (Test-Path -LiteralPath (Join-Path $program 'OpenLedger.exe'))) { throw 'Uninstaller accepted active application' }
        $proof.cases += @{name='reject-active-uninstall'; status='passed'; exit_code=$code}
    }
    finally { $null = [OpenLedgerInstallerGateNative]::CloseHandle($gate) }
    $progressGate = [OpenLedgerInstallerGateNative]::CreateMutex([IntPtr]::Zero,$false,'Local\OpenLedger.InstallationInProgress')
    if ($progressGate -eq [IntPtr]::Zero) { throw 'Could not create installation-in-progress gate' }
    try {
        $blockedData = Join-Path $run 'must-not-start-during-install'
        $blockedReport = Join-Path $run 'must-not-produce-startup.json'
        $code = Invoke-VerifiedProcess -File (Join-Path $program 'OpenLedger.exe') -Arguments @('--data-dir',$blockedData,'--smoke-test','--smoke-report',$blockedReport) -AllowFailure
        if ($code -eq 0 -or (Test-Path -LiteralPath $blockedData)) { throw 'New application started while installation was active' }
        $proof.cases += @{name='reject-new-application-during-install'; status='passed'; exit_code=$code}
    }
    finally { $null = [OpenLedgerInstallerGateNative]::CloseHandle($progressGate) }
    $null = Test-InstalledFiles
    $backup = Join-Path $run 'verified.olbackup'
    $exe = Join-Path $program 'OpenLedger.exe'
    $null = Invoke-VerifiedProcess -File $exe -Arguments @('--backup',$backup)
    if (-not (Test-Path -LiteralPath $backup)) { throw 'Installed backup missing' }
    $backupHash = (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash
    $restore = Join-Path $run '恢复后的账本 space'
    $null = Invoke-VerifiedProcess -File $exe -Arguments @('--restore',$backup,'--restore-to',$restore)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($backup)
    try {
        $reader = New-Object IO.StreamReader($archive.GetEntry('manifest.json').Open())
        try { $backupManifest = $reader.ReadToEnd() | ConvertFrom-Json }
        finally { $reader.Dispose() }
    }
    finally { $archive.Dispose() }
    if ((Get-FileHash -LiteralPath (Join-Path $restore 'database\openledger.sqlite3') -Algorithm SHA256).Hash.ToLowerInvariant() -ne $backupManifest.database_sha256) { throw 'Restored file differs from verified backup snapshot' }
    $restoredStartup = Test-InstalledStartup -Name 'installed-restored-startup' -DataRoot $restore
    if ($restoredStartup.features.financial_fingerprint -ne $startup.features.financial_fingerprint -or -not $restoredStartup.features.all_financial_rows_unchanged) { throw 'Restored financial rows or schema differ' }
    $proof.all_financial_rows_and_schema_preserved = $true
    $code = Invoke-VerifiedProcess -File $exe -Arguments @('--backup',$backup) -AllowFailure
    if ($code -eq 0 -or (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne $backupHash) { throw 'Existing backup was overwritten' }
    $damaged = Join-Path $run 'damaged.olbackup'
    [IO.File]::WriteAllText($damaged,'synthetic invalid backup')
    $badTarget = Join-Path $run 'must-not-exist'
    $code = Invoke-VerifiedProcess -File $exe -Arguments @('--restore',$damaged,'--restore-to',$badTarget) -AllowFailure
    if ($code -eq 0 -or (Test-Path -LiteralPath $badTarget)) { throw 'Invalid backup accepted' }
    $userFile = Join-Path $program 'user-kept-note.txt'
    [IO.File]::WriteAllText($userFile,'synthetic user-owned file')
    $null = Invoke-VerifiedProcess -File (Join-Path $program 'unins000.exe') -Arguments @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG=' + (Join-Path $run 'uninstall.log')))
    if (Test-Path -LiteralPath (Join-Path $program 'OpenLedger.exe')) { throw 'Uninstall left application executable' }
    if ((Get-Content -LiteralPath $userFile -Raw) -ne 'synthetic user-owned file') { throw 'Uninstall removed unrelated user file' }
    $key = $registry.OpenSubKey($keyName)
    if ($null -ne $key) { $key.Dispose(); throw 'Uninstall registration remains' }
    if ((Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -ne $before -or (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne $backupHash) { throw 'Uninstall changed financial records or backup' }
    foreach ($name in $privateSettings.Keys) {
        if ((Get-FileHash -LiteralPath (Join-Path $data $name) -Algorithm SHA256).Hash -ne $privateSettings[$name]) { throw 'Uninstall changed private preferences' }
    }
    $proof.cases += @{name='backup-restore-rejections-and-uninstall'; status='passed'}
    $proof.financial_data_preserved = $true
    $proof.preferences_preserved = $true
    $proof.unrelated_files_preserved = $true
    $proof.registration_removed = $true
    $proof.system_path_only = $true
    $proof.backup_sha256 = $backupHash.ToLowerInvariant()
    $proof.database_sha256 = $before.ToLowerInvariant()
    $proof.clean_windows_certified = [bool]$RequireCleanSystem
    $proof.status = 'passed'
}
catch { $proof.status='failed'; $proof.error=$_.Exception.Message; throw }
finally {
    if ($proof.status -ne 'passed' -and (Test-Path -LiteralPath (Join-Path $program 'unins000.exe'))) {
        $cleanupKey = $registry.OpenSubKey($keyName)
        if ($null -ne $cleanupKey) {
            try {
                if ($cleanupKey.GetValue('InstallLocation').TrimEnd('\') -eq $program) {
                    try { $proof.failed_run_cleanup_exit_code = Invoke-VerifiedProcess -File (Join-Path $program 'unins000.exe') -Arguments @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -AllowFailure }
                    catch { $proof.failed_run_cleanup_error = $_.Exception.Message }
                }
            }
            finally { $cleanupKey.Dispose() }
        }
    }
    foreach ($pair in @(@('LOCALAPPDATA',$oldLocal),@('PATH',$oldPath),@('PYTHONPATH',$oldPythonPath),@('PYTHONHOME',$oldPythonHome),@('QT_QPA_PLATFORM',$oldQtPlatform))) { [Environment]::SetEnvironmentVariable($pair[0],$pair[1],'Process') }
    $registry.Dispose()
    $proof | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8
}
Write-Output "Installer verification: $reportPath"
