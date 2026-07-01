// FXT guard — chan MT4 ghi de (sinh lai) file .fxt cua ta (giong co che TDS).
//
// Cach: hook CreateFileW. Khi MT4 mo *.fxt trong \tester\history\ voi y dinh GHI
// (GENERIC_WRITE / CREATE_ALWAYS / TRUNCATE), chuyen huong sang file dummy ".regen"
// -> FXT tick that cua ta KHONG bi ghi de. Khi MT4 mo de DOC (chay test) -> tra
// dung file that -> MT4 dung tick that cua ta -> 99.9%.
//
// Khong phu thuoc offset (dung WinAPI) -> ben qua cac build MT4.
#pragma once
#include <windows.h>

// Cai hook CreateFileW (can goi SAU MH_Initialize). Tra true neu OK.
bool InstallFxtGuard();
