@echo off
:: SETUP_MT4.bat — Copy tdsclone.dll + EA template vao thu muc MT4 dung.
:: Chay voi quyen Admin (chi can chay 1 lan).

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/k \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

setlocal
set "ROOT=%~dp0"
set "DLL_SRC=%ROOT%native\build\ea_helper_dll\Release\tdsclone.dll"

:: Tim thu muc MT4 AppData
set "MT4_ROOT=%APPDATA%\MetaQuotes\Terminal"
if not exist "%MT4_ROOT%" (
    echo [ERROR] Khong tim thay MT4 tai: %MT4_ROOT%
    goto :END
)

:: Lay thu muc terminal dau tien
for /d %%d in ("%MT4_ROOT%\*") do (
    if exist "%%d\MQL4\Libraries" (
        set "MT4_DIR=%%d"
        goto :FOUND_MT4
    )
)
echo [ERROR] Khong tim thay thu muc MT4 nao co MQL4\Libraries
goto :END

:FOUND_MT4
echo [*] MT4 terminal: %MT4_DIR%

:: 1. Copy tdsclone.dll -> MQL4\Libraries
if not exist "%DLL_SRC%" (
    echo [!] Chua build tdsclone.dll. Chay truoc:
    echo     cd native\build
    echo     cmake --build . --config Release --target tdsclone
    goto :END
)
echo [*] Copy tdsclone.dll -> MQL4\Libraries
copy /y "%DLL_SRC%" "%MT4_DIR%\MQL4\Libraries\tdsclone.dll" >nul
echo [OK] %MT4_DIR%\MQL4\Libraries\tdsclone.dll

:: 2. Copy EA template -> MQL4\Experts
echo [*] Copy TDSCloneTemplate.mq4 -> MQL4\Experts
copy /y "%ROOT%mql4\TDSCloneTemplate.mq4" "%MT4_DIR%\MQL4\Experts\TDSCloneTemplate.mq4" >nul
echo [OK] %MT4_DIR%\MQL4\Experts\TDSCloneTemplate.mq4

echo.
echo ============================================================
echo  SETUP HOAN TAT
echo ============================================================
echo.
echo  Tiep theo — backtest voi tick data that:
echo.
echo  BUOC 1: Download tick data (1 lan, co the mat nhieu phut)
echo    python tds_clone.py download --symbol EURUSD --from 2024-01-01 --to 2024-01-31
echo.
echo  BUOC 2: Build + deploy FXT vao MT4
echo    python tds_clone.py build --symbol EURUSD --from 2024-01-01 --to 2024-01-31
echo.
echo  BUOC 3: Chay spread service (de cua so mo trong khi backtest)
echo    python orchestrator.py --spread-from-ticks data\ticks\EURUSD_2024-01-01_2024-01-31.bin --point 0.00001
echo.
echo  BUOC 4: (Tuy chon) Inject hook DLL de spread trong suot voi moi EA:
echo    Chay INJECT_KEEP_OPEN.bat AS ADMIN -> go lenh: inject
echo.
echo  BUOC 5: Mo MT4 Strategy Tester
echo    Symbol=EURUSD  Period=M1  Model=Every Tick
echo    Date: 2024.01.01 - 2024.01.31
echo    Nhan Start
echo.
echo  KET QUA: backtest voi ~2 trieu tick that, bid+ask that tu Dukascopy.
echo.

:END
pause
endlocal
