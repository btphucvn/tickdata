@echo off
title TDS Clone
setlocal enabledelayedexpansion

rem ===========================================================================
rem  TDS Clone - khoi dong 1 phat (GUI + auto-inject service)
rem  Double-click file nay. Tu xin quyen Administrator de inject duoc vao MT4.
rem ===========================================================================

rem --- 1. Tu nang quyen Administrator (can de OpenProcess inject terminal.exe) ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Dang xin quyen Administrator de co the inject vao MT4...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    exit /b
)

rem --- 2. Ve dung thu muc du an (sau khi nang quyen, cwd bi reset) ---
cd /d "%~dp0"

rem --- 3. Tim Python: console (PY) + windowless (PYW) ---
set "PY="
set "PYW="
where python >nul 2>&1 && set "PY=python"
where pythonw >nul 2>&1 && set "PYW=pythonw"
if not defined PY (
    where py >nul 2>&1 && set "PY=py -3"
)
if not defined PYW if defined PY set "PYW=%PY%"

if not defined PY (
    echo [LOI] Khong tim thay Python tren may.
    echo Cai Python 3.11+ tu https://python.org roi chay lai file nay.
    echo Nho tick "Add Python to PATH" khi cai.
    pause
    exit /b 1
)

rem --- 4. Kiem tra PySide6 (thu vien GUI) ---
%PY% -c "import PySide6" >nul 2>&1
if %errorlevel% neq 0 (
    echo Lan dau chay: dang cai thu vien giao dien PySide6...
    %PY% -m pip install PySide6
    if !errorlevel! neq 0 (
        echo [LOI] Cai PySide6 that bai. Kiem tra ket noi mang.
        pause
        exit /b 1
    )
)

rem --- 5. Mo GUI (windowless, khong hien cua so cmd den) ---
echo Dang mo TDS Clone...
start "" %PYW% "%~dp0python\tds_gui.py"

rem Dong cua so .bat ngay (GUI da chay rieng)
exit /b 0
