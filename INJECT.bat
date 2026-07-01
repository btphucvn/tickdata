@echo off
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/k cd /d \"%CD%\" && \"%~f0\"' -Verb RunAs"
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
    goto :END
)

if not exist "%INJ%" (
    echo [ERROR] Khong tim thay injector:
    echo   %INJ%
    goto :END
)

tasklist /fi "IMAGENAME eq terminal.exe" /fo csv /nh 2>nul | findstr /i "terminal" >nul
if %errorlevel% neq 0 (
    echo [ERROR] terminal.exe chua chay - Mo MT4 truoc roi chay lai!
    goto :END
)

echo [*] terminal.exe dang chay
echo [*] Injecting: %DLL%
echo.

"%INJ%" --name terminal.exe "%DLL%"
set /a RET=%ERRORLEVEL%

echo.
if %RET% == 0 (
    echo [SUCCESS] Hook da inject thanh cong!
    echo           Mo DebugView de xem: [tdshook] Hook OK
) else (
    echo [FAILED] Exit code = %RET%
    echo Cac nguyen nhan co the:
    echo  1. Antivirus chan - them folder build vao Defender exclusion
    echo  2. MT4 dang khong chay
    echo  3. Thieu quyen Admin
)

:END
echo.
echo Bam phim bat ky de dong...
pause >nul
endlocal
