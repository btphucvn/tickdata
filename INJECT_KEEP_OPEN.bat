@echo off
:: Cua so nay KHONG bao gio tu dong -- chi dong khi ban go "exit"

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/k \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

setlocal
set "ROOT=%~dp0"
set "DLL=%ROOT%native\build\hook\Release\tdshook.dll"
set "INJ=%ROOT%native\build\injector\Release\injector.exe"

echo ============================================
echo  TDS Clone -- DLL Injector
echo ============================================
echo.

if not exist "%DLL%" (
    echo [ERROR] Khong tim thay DLL:
    echo   %DLL%
    echo.
    goto :SHELL
)

if not exist "%INJ%" (
    echo [ERROR] Khong tim thay injector:
    echo   %INJ%
    echo.
    goto :SHELL
)

tasklist /fi "IMAGENAME eq terminal.exe" /fo csv /nh 2>nul | findstr /i "terminal" >nul
if %errorlevel% neq 0 (
    echo [ERROR] terminal.exe chua chay!
    echo   -^> Mo MT4, vao Strategy Tester, roi go lenh: inject
    echo.
    goto :SHELL
)

echo [*] terminal.exe dang chay
echo [*] Injecting: %DLL%
echo.

"%INJ%" --name terminal.exe "%DLL%"
set /a RET=%ERRORLEVEL%

echo.
if %RET% == 0 (
    echo [SUCCESS] Hook da inject thanh cong!
    echo           Mo DebugView ^(as Admin^) de xem log:
    echo           [tdshook] Hook @ 0x... OK
) else (
    echo [FAILED] Exit code = %RET%
    echo  1. Thu them Defender exclusion roi chay lai lenh: inject
    echo  2. Hoac chay lai bat ky lenh nao duoi day
)

:SHELL
echo.
echo ---- CMD van dang mo. Cac lenh co san: ----
echo   inject      ^<-- chay lai injection
echo   exit        ^<-- dong cua so
echo -------------------------------------------
echo.

:: Tao alias lenh ngan "inject" trong session nay
doskey inject="%INJ%" --name terminal.exe "%DLL%"

cmd /k "echo [Shell san sang] Go 'inject' de inject lai, 'exit' de dong."
endlocal
