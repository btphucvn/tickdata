@echo off
cd /d "C:\Users\Phuc\Desktop\tickdata\native\test"
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul
cl /nologo /O2 /Fe:tkd_test.exe tkd_test.c ..\third_party\puff\puff.c /I..\third_party\puff
