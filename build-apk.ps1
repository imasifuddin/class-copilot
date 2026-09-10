# Builds the Android APK.
#   .\build-apk.ps1            build it
#   .\build-apk.ps1 -Install   build it and push it to a USB-connected phone
#   .\build-apk.ps1 -Clean     rebuild from scratch
param(
    [switch]$Install,
    [switch]$Clean
)

Set-Location $PSScriptRoot

# --- Android SDK ---
$sdk = $env:ANDROID_HOME
if (-not $sdk -or -not (Test-Path "$sdk\platforms")) { $sdk = "$env:LOCALAPPDATA\Android\Sdk" }
if (-not (Test-Path "$sdk\platforms")) {
    Write-Host "Android SDK not found at $sdk" -ForegroundColor Red
    Write-Host "Get the command-line tools from https://developer.android.com/studio#command-tools"
    Write-Host "unzip to $env:LOCALAPPDATA\Android\Sdk\cmdline-tools\latest, then run:"
    Write-Host "  sdkmanager `"platform-tools`" `"platforms;android-34`" `"build-tools;34.0.0`""
    exit 1
}

# --- Gradle ---
$gradle = "$env:LOCALAPPDATA\gradle-dist\gradle-8.7\bin\gradle.bat"
if (-not (Test-Path $gradle)) {
    $found = Get-Command gradle -ErrorAction SilentlyContinue
    if ($found) {
        $gradle = $found.Source
    } else {
        Write-Host "Gradle not found." -ForegroundColor Red
        Write-Host "Download the binary zip from https://gradle.org/releases/ and unzip it to"
        Write-Host "  $env:LOCALAPPDATA\gradle-dist\gradle-8.7"
        exit 1
    }
}

# --- JDK 17+ ---
if (-not $env:JAVA_HOME -and (Test-Path "C:\Program Files\Java\jdk-19")) {
    $env:JAVA_HOME = "C:\Program Files\Java\jdk-19"
}

$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
Set-Content -Path "android\local.properties" `
            -Value ("sdk.dir=" + $sdk.Replace("\", "\\")) -Encoding ascii

$tasks = @()
if ($Clean) { $tasks += "clean" }
$tasks += "assembleDebug"

Push-Location android
try {
    # javac writes deprecation notes to stderr and PowerShell 5.1 turns those
    # into ErrorRecords, so trust the exit code rather than the error stream.
    $ErrorActionPreference = "Continue"
    & $gradle --no-daemon @tasks
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($code -ne 0) {
    Write-Host ""
    Write-Host "Gradle exited with code $code." -ForegroundColor Red
    exit $code
}

$apk = "android\app\build\outputs\apk\debug\app-debug.apk"
if (-not (Test-Path $apk)) {
    Write-Host "Gradle reported success but no APK was produced." -ForegroundColor Red
    exit 1
}

$full = (Resolve-Path $apk).Path
$kb = [math]::Round((Get-Item $apk).Length / 1KB)
Write-Host ""
Write-Host "APK built: $full  ($kb KB)" -ForegroundColor Green

if ($Install) {
    $adb = "$sdk\platform-tools\adb.exe"
    Write-Host "Installing to the connected phone..." -ForegroundColor Cyan
    & $adb install -r $full
} else {
    Write-Host "Copy it to your phone and tap it to install."
    Write-Host "Or, with the phone plugged in and USB debugging on:"
    Write-Host "  .\build-apk.ps1 -Install"
}
