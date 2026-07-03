@echo off
cd /d "C:\Users\Phuc\Desktop\tickdata\native\test"
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul
cl /nologo /O2 /EHsc /Fe:vfxt_gen_test.exe vfxt_gen_test.cpp ..\hook\fxt_virtual.cpp ..\third_party\puff\puff.c /I..\hook /I..\third_party\puff
