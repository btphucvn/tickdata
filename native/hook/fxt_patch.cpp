// Xem fxt_patch.h. Hook CreateFileW:
//   (1) BAO VE: chan MT4 ghi de/sinh lai FXT (redirect write -> .regen).
//   (2) FXT AO (giong TDS): khi MT4 MO doc FXT-ao (sparse placeholder), track handle;
//       hook ReadFile phuc vu record THAT sinh on-the-fly tu tick_store (vfxt::Serve).
#include "fxt_patch.h"
#include "fxt_virtual.h"
#include "MinHook.h"
#include <string>
#include <cwctype>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <ctime>

namespace {
    typedef HANDLE (WINAPI *CreateFileW_t)(LPCWSTR, DWORD, DWORD,
        LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
    typedef BOOL (WINAPI *ReadFile_t)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
    typedef BOOL (WINAPI *CloseHandle_t)(HANDLE);
    CreateFileW_t g_orig = nullptr;
    ReadFile_t    g_origReadFile = nullptr;
    CloseHandle_t g_origCloseHandle = nullptr;

    // --- Tap handle FXT-ao dang mo (thuong chi 1) ---
    HANDLE g_vh[16]; volatile int g_vhN = 0;
    CRITICAL_SECTION g_vcs; bool g_vcsInit = false;
    bool VhFind(HANDLE h) { for (int i = 0; i < g_vhN; ++i) if (g_vh[i] == h) return true; return false; }
    void VhAdd(HANDLE h) {
        EnterCriticalSection(&g_vcs);
        if (g_vhN < 16 && !VhFind(h)) g_vh[g_vhN++] = h;
        LeaveCriticalSection(&g_vcs);
    }
    void VhDel(HANDLE h) {
        EnterCriticalSection(&g_vcs);
        for (int i = 0; i < g_vhN; ++i) if (g_vh[i] == h) { g_vh[i] = g_vh[--g_vhN]; break; }
        LeaveCriticalSection(&g_vcs);
    }
    bool VhIs(HANDLE h) {
        if (g_vhN == 0) return false;              // fast path: khong co handle ao
        EnterCriticalSection(&g_vcs);
        bool r = VhFind(h);
        LeaveCriticalSection(&g_vcs);
        return r;
    }

    // HMODULE cua chinh DLL nay (de vfxt tinh duong dan config).
    HMODULE SelfModule() {
        HMODULE h = nullptr;
        GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCWSTR)&SelfModule, &h);
        return h;
    }

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

    // <root> = thu muc du an (tu DLL, lui 5 cap).
    bool ProjectRoot(wchar_t* out, DWORD n) {
        if (!GetModuleFileNameW(SelfModule(), out, n)) return false;
        for (int i = 0; i < 5; ++i) {
            wchar_t* p = wcsrchr(out, L'\\');
            if (!p) return false;
            *p = 0;
        }
        return true;
    }

    volatile bool g_preparing = false;

    // TDS-FLOW: neu co marker data\pending.prep -> chay `prepare --virtual` NGAY LUC
    // START (khi MT4 mo FXT), roi moi cho MT4 doc. Giong TDS: build tai Start, khong
    // build o luc tick checkbox. Chay 1 lan cho moi lan tick (xong thi xoa marker).
    void MaybePrepareAtStart() {
        if (g_preparing) return;
        wchar_t root[MAX_PATH];
        if (!ProjectRoot(root, MAX_PATH)) return;
        wchar_t mpath[MAX_PATH];
        swprintf(mpath, MAX_PATH, L"%s\\data\\pending.prep", root);
        FILE* f = nullptr;
        if (_wfopen_s(&f, mpath, L"rb") != 0 || !f) return;      // khong co marker -> thoi
        char buf[512]; size_t nn = fread(buf, 1, sizeof(buf) - 1, f); fclose(f); buf[nn] = 0;
        char sym[64] = {0}, from[32] = {0}, to[32] = {0}; int period = 0;
        if (sscanf_s(buf, "%63s %31s %31s %d",
                     sym, (unsigned)sizeof(sym), from, (unsigned)sizeof(from),
                     to, (unsigned)sizeof(to), &period) < 4) { DeleteFileW(mpath); return; }

        g_preparing = true;
        GuardLog("VIRTUAL FXT: BUILD tai Start (prepare --virtual)...");
        wchar_t wsym[64], wfrom[32], wto[32], pb[16];
        MultiByteToWideChar(CP_ACP, 0, sym, -1, wsym, 64);
        MultiByteToWideChar(CP_ACP, 0, from, -1, wfrom, 32);
        MultiByteToWideChar(CP_ACP, 0, to, -1, wto, 32);
        _itow_s(period, pb, 16, 10);
        std::wstring cmd = L"cmd.exe /c cd /d \"";
        cmd += root;
        cmd += L"\" && python tds_clone.py prepare --virtual --symbol ";
        cmd += wsym; cmd += L" --from "; cmd += wfrom; cmd += L" --to "; cmd += wto;
        cmd += L" --period "; cmd += pb;
        STARTUPINFOW si = { sizeof(si) }; PROCESS_INFORMATION pi = {};
        std::wstring mut = cmd;   // CreateProcessW can buffer ghi duoc
        if (CreateProcessW(nullptr, &mut[0], nullptr, nullptr, FALSE,
                           CREATE_NEW_CONSOLE, nullptr, nullptr, &si, &pi)) {
            WaitForSingleObject(pi.hProcess, 300000);   // cho toi 5 phut build
            CloseHandle(pi.hProcess); CloseHandle(pi.hThread);
            GuardLog("VIRTUAL FXT: BUILD xong.");
        } else {
            GuardLog("VIRTUAL FXT: LOI spawn python prepare");
        }
        DeleteFileW(mpath);          // build 1 lan / moi lan tick checkbox
        vfxt::Load(SelfModule());    // nap config vua build
        g_preparing = false;
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
                std::wstring dummy(name);
                dummy += L".regen";
                GuardLog("REDIRECT WRITE (chan sinh lai)", name);
                return g_orig(dummy.c_str(), access, share, sa, disp, flags, tmpl);
            }
            GuardLog("READ FXT that cua ta", name);
            // --- TDS-FLOW: build config+sparse NGAY LUC START (neu co marker) ---
            MaybePrepareAtStart();
            // --- FXT AO: refresh config + track handle neu khop placeholder ---
            vfxt::Load(SelfModule());
            HANDLE hh = g_orig(name, access, share, sa, disp, flags, tmpl);
            if (hh != INVALID_HANDLE_VALUE && vfxt::MatchName(name)) {
                VhAdd(hh);
                GuardLog("VIRTUAL FXT: track handle -> serve records tu tick_store", name);
            }
            return hh;
        }
        return g_orig(name, access, share, sa, disp, flags, tmpl);
    }

    // Hook ReadFile: chi can thiep khi doc handle FXT-ao (overlapped=NULL).
    BOOL WINAPI Hook_ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD pRead, LPOVERLAPPED ovl)
    {
        if (ovl == nullptr && VhIs(h)) {
            LARGE_INTEGER pos{}, zero{};
            SetFilePointerEx(h, zero, &pos, FILE_CURRENT);          // vi tri truoc khi doc
            BOOL ok = g_origReadFile(h, buf, n, pRead, ovl);        // doc sparse (0), advance pos
            if (ok && pRead && *pRead)
                vfxt::Serve((uint64_t)pos.QuadPart, buf, *pRead);   // ghi de = record THAT
            return ok;
        }
        return g_origReadFile(h, buf, n, pRead, ovl);
    }

    BOOL WINAPI Hook_CloseHandle(HANDLE h)
    {
        if (VhIs(h)) VhDel(h);
        return g_origCloseHandle(h);
    }
}

bool InstallFxtGuard()
{
    if (!g_vcsInit) { InitializeCriticalSection(&g_vcs); g_vcsInit = true; }

    HMODULE h = GetModuleHandleW(L"kernelbase.dll");
    if (!h) h = GetModuleHandleW(L"kernel32.dll");
    if (!h) { GuardLog("=== GUARD FAIL: khong tim thay module ==="); return false; }

    // (1) CreateFileW
    void* target = (void*)GetProcAddress(h, "CreateFileW");
    if (!target) { GuardLog("=== GUARD FAIL: khong tim CreateFileW ==="); return false; }
    if (MH_CreateHook(target, (void*)&Hook_CreateFileW,
                      reinterpret_cast<void**>(&g_orig)) != MH_OK) {
        GuardLog("=== GUARD FAIL: MH_CreateHook CreateFileW ==="); return false;
    }
    if (!g_orig) { GuardLog("=== GUARD FAIL: g_orig null ==="); return false; }
    if (MH_EnableHook(target) != MH_OK) {
        GuardLog("=== GUARD FAIL: MH_EnableHook CreateFileW ==="); return false;
    }

    // (2) ReadFile (phuc vu FXT ao)
    void* pRead = (void*)GetProcAddress(h, "ReadFile");
    if (pRead && MH_CreateHook(pRead, (void*)&Hook_ReadFile,
                               reinterpret_cast<void**>(&g_origReadFile)) == MH_OK
              && g_origReadFile && MH_EnableHook(pRead) == MH_OK) {
        GuardLog("=== HOOK ReadFile OK (FXT ao) ===");
    } else {
        GuardLog("=== WARN: hook ReadFile that bai (FXT ao se khong hoat dong) ===");
    }

    // (3) CloseHandle (untrack handle FXT ao)
    void* pClose = (void*)GetProcAddress(h, "CloseHandle");
    if (pClose && MH_CreateHook(pClose, (void*)&Hook_CloseHandle,
                                reinterpret_cast<void**>(&g_origCloseHandle)) == MH_OK
               && g_origCloseHandle && MH_EnableHook(pClose) == MH_OK) {
        GuardLog("=== HOOK CloseHandle OK ===");
    }

    // Nap config FXT ao neu da co (prepare co the ghi truoc/sau).
    vfxt::Load(SelfModule());
    GuardLog("=== GUARD LOADED OK (CreateFileW + ReadFile + CloseHandle) ===");
    return true;
}
