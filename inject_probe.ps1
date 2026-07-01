# Self-elevating PowerShell script - double-click INJECT.bat to run
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$Host.UI.RawUI.WindowTitle = "TDS Probe Injector [ADMIN]"

$inj    = "$PSScriptRoot\native\build\injector\Release\injector.exe"
$dll    = "$PSScriptRoot\native\build\probe\Release\tds_probe2.dll"
$mt4exe = "C:\Program Files (x86)\Dukascopy MetaTrader 4\terminal.exe"
$log    = "$env:TEMP\tds_probe.log"

Write-Host "=== TDS Probe Injector ===" -ForegroundColor Cyan

if (-not (Test-Path $inj)) {
    Write-Host "ERROR: Missing $inj" -ForegroundColor Red
    Read-Host "Press Enter to exit"; exit 1
}

# Add Windows Defender exclusions so it stops locking our DLL
Write-Host "Adding Defender exclusions..." -ForegroundColor Yellow
try {
    Add-MpPreference -ExclusionPath "$PSScriptRoot\native\build" -EA Stop
    Add-MpPreference -ExclusionPath $dll -EA Stop
    Write-Host "Defender exclusions OK" -ForegroundColor Green
} catch {
    Write-Host "WARNING: Could not set Defender exclusion: $_" -ForegroundColor Yellow
}

# Kill ALL MT4 instances (aggressive)
Write-Host "Killing all MT4 instances..." -ForegroundColor Yellow
taskkill /F /IM terminal.exe /T 2>$null
Start-Sleep -Seconds 2

# Force-delete old DLL
if (Test-Path $dll) {
    Remove-Item $dll -Force -EA 0
    Start-Sleep -Milliseconds 500
}

# Rebuild probe DLL
Write-Host "Building tds_probe.dll..." -ForegroundColor Yellow
$buildDir = "$PSScriptRoot\native\build"
$buildOut = cmake --build $buildDir --config Release --target probe 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "BUILD FAILED:" -ForegroundColor Red
    Write-Host ($buildOut -join "`n")
    Read-Host "Press Enter to exit"; exit 1
}
Write-Host "Build OK" -ForegroundColor Green
Start-Sleep -Seconds 2

Write-Host "Starting MT4..." -ForegroundColor Yellow
Start-Process $mt4exe

Write-Host "Injecting (retry every 0.5s, max 15s)..." -ForegroundColor Yellow
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    $procPid = (Get-Process terminal -EA 0 | Select-Object -First 1).Id
    if (-not $procPid) { continue }
    $result = & $inj --pid $procPid $dll 2>&1
    $txt = "$result"
    Write-Host "  [$i] PID=$procPid -> $txt"
    if ($txt -match "thanh cong|HMODULE") {
        $ok = $true
        break
    }
}

if (-not $ok) {
    Write-Host "INJECTION FAILED" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "=== INJECTION OK ===" -ForegroundColor Green
Write-Host "1. Switch to MT4"
Write-Host "2. Press Ctrl+R to open Strategy Tester"
Write-Host "3. Pick any EA + EURUSD + M1 -> click Start"
Write-Host "4. Let backtest run 20 seconds"
Write-Host "5. Close MT4 (File -> Exit)"
Write-Host "6. Come back here and press Enter"
Write-Host ""
Read-Host "Press Enter AFTER MT4 is closed"

if (Test-Path $log) {
    Write-Host ""
    Write-Host "=== PROBE LOG ===" -ForegroundColor Cyan
    Get-Content $log
} else {
    Write-Host "Log not found at $log" -ForegroundColor Red
}

Write-Host ""
Read-Host "Done. Press Enter to exit"
