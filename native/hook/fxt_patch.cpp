// Xem fxt_patch.h. Hook CreateFileW de bao ve FXT khoi bi MT4 ghi de (sinh lai).
#include "fxt_patch.h"
#include "MinHook.h"
#include <string>
#include <cwctype>
#include <cstdio>
#include <ctime>

namespace {
    typedef HANDLE (WINAPI *CreateFileW_t)(LPCWSTR, DWORD, DWORD,
        LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
    CreateFileW_t g_orig = nullptr;

    // Ghi log ra file de chan doan (user khong can DebugView).
    void GuardLog(const char* msg, LPCWSTR detail = nullptr) {
        FILE* f = nullptr;
        fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\guard.log", "a");
        if (!f) return;
        time_t t = time(nullptr);
        char ts[32]; ctime_s(ts, sizeof(ts), &t);
        ts[strlen(ts) > 0 ? strlen(ts) - 1 : 0] = 0;
        if (detail) fwprintf(f, L"[%hs] %hs : %ls\n", ts, msg, detail);
        else        fprintf(f, "[%s] %s\n", ts, msg);
        fclose(f);
    }

    bool IsTesterFxt(LPCWSTR name) {
        if (!name) return false;
        std::wstring s(name);
        for (auto& c : s) c = (wchar_t)towlower(c);
        if (s.size() < 4 || s.compare(s.size() - 4, 4, L".fxt") != 0)
            return false;
        return s.find(L"\\tester\\history\\") != std::wstring::npos;
    }

    HANDLE WINAPI Hook_CreateFileW(LPCWSTR name, DWORD access, DWORD share,
        LPSECURITY_ATTRIBUTES sa, DWORD disp, DWORD flags, HANDLE tmpl)
    {
        if (IsTesterFxt(name)) {
            bool writeIntent =
                (access & (GENERIC_WRITE | FILE_WRITE_DATA | GENERIC_ALL)) != 0 ||
                disp == CREATE_ALWAYS || disp == CREATE_NEW ||
                disp == TRUNCATE_EXISTING;
            if (writeIntent) {
                // MT4 dinh GHI/sinh lai FXT -> chuyen huong sang dummy.
                std::wstring dummy(name);
                dummy += L".regen";   // luon cung file -> ghi de dummy, FXT that an toan
                GuardLog("REDIRECT WRITE (chan sinh lai)", name);
                return g_orig(dummy.c_str(), access, share, sa, disp, flags, tmpl);
            }
            GuardLog("READ FXT that cua ta", name);
        }
        return g_orig(name, access, share, sa, disp, flags, tmpl);
    }
}

bool InstallFxtGuard()
{
    // Win10: MT4 goi kernelbase!CreateFileW truc tiep (apiset) -> hook kernelbase.
    HMODULE h = GetModuleHandleW(L"kernelbase.dll");
    if (!h) h = GetModuleHandleW(L"kernel32.dll");
    if (!h) { GuardLog("=== GUARD FAIL: khong tim thay module ==="); return false; }

    void* target = (void*)GetProcAddress(h, "CreateFileW");
    if (!target) { GuardLog("=== GUARD FAIL: khong tim CreateFileW ==="); return false; }

    // QUAN TRONG: MH_CreateHook gan g_orig (trampoline) NGAY -> phai gan TRUOC
    // khi MH_EnableHook (tranh race: hook ban khi g_orig con null -> crash).
    if (MH_CreateHook(target, (void*)&Hook_CreateFileW,
                      reinterpret_cast<void**>(&g_orig)) != MH_OK) {
        GuardLog("=== GUARD FAIL: MH_CreateHook ==="); return false;
    }
    if (!g_orig) { GuardLog("=== GUARD FAIL: g_orig null ==="); return false; }
    if (MH_EnableHook(target) != MH_OK) {
        GuardLog("=== GUARD FAIL: MH_EnableHook ==="); return false;
    }
    GuardLog("=== GUARD LOADED OK (kernelbase CreateFileW) ===");
    return true;
}
