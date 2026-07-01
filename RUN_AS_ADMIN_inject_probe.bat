@echo off
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% == 0 (
    powershell -ExecutionPolicy Bypass -File "%~dp0inject_probe.ps1"
) else (
    powershell -Command "Start-Process -FilePath 'powershell' -ArgumentList '-ExecutionPolicy Bypass -File ""%~dp0inject_probe.ps1""' -Verb RunAs -Wait"
)
