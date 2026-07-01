@echo off
title TDS Clone - Cleanup (tat TDS that + clone cu)

rem Tu xin quyen Administrator
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    exit /b
)

echo ============================================================
echo  DON DEP: tat TDS that + tien trinh clone cu de test sach
echo ============================================================
echo.

echo [1] Tat MT4 (terminal.exe)...
taskkill /F /IM terminal.exe >nul 2>&1

echo [2] Tat TDS that (loader/support/manager/helper)...
taskkill /F /IM TDSLoader.exe >nul 2>&1
taskkill /F /IM TDSSupport.exe >nul 2>&1
taskkill /F /IM "Tick Data Manager.exe" >nul 2>&1
taskkill /F /IM "TDS privileged helper.exe" >nul 2>&1

echo [3] Stop service TDS that (TDSService)...
sc stop "TDSService.exe" >nul 2>&1
taskkill /F /IM TDSService.exe >nul 2>&1
rem Tam thoi khong cho service tu chay lai (de test clone). Bat lai sau bang:
rem    sc config "TDSService.exe" start= auto
sc config "TDSService.exe" start= demand >nul 2>&1

echo [4] Tat GUI/service clone cu (pythonw)...
taskkill /F /IM pythonw3.11.exe >nul 2>&1

echo.
echo [XONG] Da don dep. Bay gio:
echo   1. Mo MT4 TRUC TIEP tu:
echo      C:\Program Files (x86)\Dukascopy MetaTrader 4\terminal.exe
echo      (KHONG mo qua shortcut TDS)
echo   2. Chay INJECT.bat
echo   3. Backtest BTCUSD M1
echo.
echo (Muon bat lai TDS that sau nay: sc config "TDSService.exe" start= auto, roi khoi dong lai may)
echo.
pause
