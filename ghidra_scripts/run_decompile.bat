@echo off
:: Ghidra headless decompiler - no-analysis mode (fast, low RAM)
:: Decompiles 43 candidates in terminal.exe without full binary analysis

set GHIDRA=%~dp0..\ghidra
set PROJ=%~dp0..\ghidra_proj_decomp
set BINARY=C:\Program Files (x86)\Dukascopy MetaTrader 4\terminal.exe
set SCRIPT_DIR=%~dp0

echo ========================================
echo  Ghidra Minimal Decompile (noanalysis)
echo ========================================
echo.
echo Ghidra: %GHIDRA%
echo Binary: %BINARY%
echo Script: %SCRIPT_DIR%decompile_candidates.py
echo Output: %SCRIPT_DIR%decompiled_output.txt
echo.

:: Create project dir if not exists
if not exist "%PROJ%" mkdir "%PROJ%"

:: Delete old project so import is fresh
if exist "%PROJ%\MT4Decomp.rep" rmdir /s /q "%PROJ%\MT4Decomp.rep"
if exist "%PROJ%\MT4Decomp.gpr" del /q "%PROJ%\MT4Decomp.gpr"

echo Running Ghidra headless (this should finish in 3-10 minutes)...
echo.

:: Set JVM heap - 2G is enough for no-analysis + 43 function decompile
set GHIDRA_HEADLESS_MAXMEM=2G

"%GHIDRA%\support\analyzeHeadless.bat" ^
    "%PROJ%" MT4Decomp ^
    -import "%BINARY%" ^
    -noanalysis ^
    -postScript decompile_candidates.py ^
    -scriptPath "%SCRIPT_DIR%" ^
    -log "%SCRIPT_DIR%ghidra_headless.log"

echo.
if exist "%SCRIPT_DIR%decompiled_output.txt" (
    echo SUCCESS - output saved to decompiled_output.txt
    echo Opening in Notepad...
    notepad "%SCRIPT_DIR%decompiled_output.txt"
) else (
    echo FAILED - check ghidra_headless.log for errors
    if exist "%SCRIPT_DIR%ghidra_headless.log" (
        echo --- Last 30 lines of log ---
        powershell -Command "Get-Content '%SCRIPT_DIR%ghidra_headless.log' | Select-Object -Last 30"
    )
)

pause
