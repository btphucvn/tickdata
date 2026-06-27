# Module 4 — EA Helper DLL (tdsclone.dll), export __stdcall cho #import MQL4.
add_library(tdsclone SHARED tdsclone.cpp tdsclone.def)
set_target_properties(tdsclone PROPERTIES PREFIX "" OUTPUT_NAME "tdsclone")
# MQL4 #import nạp DLL từ <terminal>\MQL4\Libraries\ — copy thủ công sau khi build.
