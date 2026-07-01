@echo off
:: Khoi dong TDS Clone GUI (Tick Data Manager) - pipeline moi
setlocal
cd /d "%~dp0"
python python\tds_gui.py 2>"%TEMP%\tdsclone_gui.log"
if %errorlevel% neq 0 (
    echo [ERROR] GUI khong khoi dong duoc. Log:
    type "%TEMP%\tdsclone_gui.log"
    pause
)
endlocal
