[CmdletBinding()]
param(
    [string]$Python = '',
    [string]$SdkRoot = $env:ANDROID_HOME,
    [string]$JavaHome = $env:JAVA_HOME
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $projectRoot '.venv/Scripts/python.exe'
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Python 3.12 is required; specify -Python.' }
if ([string]::IsNullOrWhiteSpace($JavaHome) -or -not (Test-Path -LiteralPath (Join-Path $JavaHome 'bin/java.exe'))) { throw 'JDK 17 is required; specify -JavaHome.' }
if ([string]::IsNullOrWhiteSpace($SdkRoot) -or -not (Test-Path -LiteralPath (Join-Path $SdkRoot 'platforms/android-36/android.jar'))) { throw 'Android SDK Platform 36 is required; specify -SdkRoot.' }
$javaTemporary = Join-Path $projectRoot 'build/android-java-tmp'
New-Item -ItemType Directory -Path $javaTemporary -Force | Out-Null
$savedEnvironment = @{}
foreach ($name in @('JAVA_HOME', 'ANDROID_HOME', 'OPENLEDGER_BUILD_PYTHON', 'PYTHONUTF8', 'TEMP', 'TMP', 'JDK_JAVA_OPTIONS')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:JAVA_HOME = [IO.Path]::GetFullPath($JavaHome)
    $env:ANDROID_HOME = [IO.Path]::GetFullPath($SdkRoot)
    $env:OPENLEDGER_BUILD_PYTHON = [IO.Path]::GetFullPath($Python)
    $env:PYTHONUTF8 = '1'
    & $Python (Join-Path $projectRoot 'scripts/build_help.py') --check
    if ($LASTEXITCODE -ne 0) { throw 'The bundled offline manual differs from its reviewed sources.' }
    # A full workspace temporary path avoids Windows AF_UNIX failures with 8.3 aliases.
    $env:TEMP = $javaTemporary
    $env:TMP = $javaTemporary
    $env:JDK_JAVA_OPTIONS = "-Djdk.net.unixdomain.tmpdir=`"$javaTemporary`" -Djava.io.tmpdir=`"$javaTemporary`""
    $outputRoot = Join-Path $projectRoot 'dist/android'
    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
    Push-Location (Join-Path $projectRoot 'android')
    try {
        & ./gradlew.bat --no-daemon --console=plain assembleDebug assembleDebugAndroidTest lintDebug
        if ($LASTEXITCODE -ne 0) { throw 'Android build or lint failed.' }
        $apk = Join-Path $projectRoot 'android/app/build/outputs/apk/debug/app-debug.apk'
        $report = Join-Path $projectRoot 'build/validation/android/apk-check.json'
        & $Python (Join-Path $projectRoot 'scripts/verify_android_apk.py') --apk $apk --sdk-root $SdkRoot --output $report
        if ($LASTEXITCODE -ne 0) { throw 'APK verification failed.' }
        $verified = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
        $mobileVersion = $verified.version -replace '-preview$', ''
        if ($mobileVersion -notmatch '^[0-9][0-9A-Za-z.+-]{0,39}$') { throw 'Invalid verified mobile version.' }
        $target = Join-Path $outputRoot "OpenLedger-$mobileVersion-android-preview.apk"
        Copy-Item -LiteralPath $apk -Destination $target -Force
        $digest = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText("$target.sha256", "$digest  $([IO.Path]::GetFileName($target))`n", $utf8)
        Write-Output "Verified preview APK: $target"
    } finally {
        Pop-Location
    }
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
}
