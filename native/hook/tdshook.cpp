// TDS Clone Hook DLL
// Hook FetchNextTick (VA=0xF84FB0, RVA=0xB84FB0) trong terminal.exe.
//
// RE (2026-07): ham nay la iterator per-tick cua Strategy Tester.
//   int __thiscall FetchNextTick(this=model, out_rec**, out_tick*);   // ret 8
//   Tra 1 = co tick, 0 = het. Caller = tester main loop @0xF5EF44.
//   Ben trong tinh: ask = bid + [model+0x328], voi [model+0x328] =
//   spread_setting(points) * point (tinh 1 lan trong tickgen 0xF84D90).
//   -> Voi Spread="Current(0)" thi offset=0 nen ask=bid. Ta override ask
//      = bid + spread_bien_dong(tick_time) -> giong TDS "Variable".
//
// Layout out_tick (arg2, tren stack caller):
//   +0x00: double  bid
//   +0x08: double  ask   <-- override o day
//   +0x10: int32   time_sec (record.tickTime, DA shift GMT+2/DST)
//
// 2 che do (theo magic file data\active.spread):
//   Magic='TDSS': spread-only -> ask = bid + spread*point
//   Magic='TDST': tick mode   -> bid+ask thay bang gia that tu Dukascopy

#include <windows.h>
#include <psapi.h>
#pragma comment(lib, "psapi.lib")
#include <cstdint>
#include <cstdio>
#include "MinHook.h"
#include "sigscan.h"
#include "gui_inject.h"
#include "slippage.h"
#include "fxt_patch.h"

// ---------------------------------------------------------------------------
// Shared memory layouts (phai khop voi orchestrator.py)
// ---------------------------------------------------------------------------
#pragma pack(push, 1)

// Mode 0 — spread-only  (magic='TDSS')
struct SpreadHeader {
    uint32_t magic;   // 0x53534454
    uint32_t count;
    double   point;
    // followed by count x { int32 time; float spread_pts }
};
struct SpreadRec {
    int32_t  unixTime;
    float    spreadPts;
};

// Mode 1 — tick mode  (magic='TDST')
struct TickHeader {
    uint32_t magic;   // 0x54534454
    uint32_t mode;    // =1
    double   point;
    uint64_t count;
    // followed by count x TDSTick
};
struct TDSTick {
    int64_t time_ms;
    double  bid;
    double  ask;
};

#pragma pack(pop)

// Output tick cua FetchNextTick (arg2). Layout xac nhan qua RE @0xF8511D-0xF85147.
struct FetchTick {
    double   bid;       // +0x00
    double   ask;       // +0x08  <-- override
    int32_t  time_sec;  // +0x10  (da shift GMT+2/DST khop spread file)
};

// ---------------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------------
namespace {
    HANDLE   g_shmHandle = nullptr;   // file mapping handle
    void*    g_shm       = nullptr;   // mapped view cua data\active.spread
    uint32_t g_shmMagic  = 0;
    HMODULE  g_hMod      = nullptr;   // module DLL (de tinh duong dan spread file)

    // Cursor tien theo thoi gian (chi dung mode 1)
    volatile uint32_t g_tickCursor = 0;

    const uint32_t FETCHTICK_RVA = 0x00B84FB0u;   // FetchNextTick per-tick

    // __thiscall(this, out_rec**, out_tick*) -> int. Dung __fastcall + edx dummy.
    typedef int (__fastcall* FetchTick_t)(void*, void*, void*, void*);
    FetchTick_t g_orig = nullptr;
}

static void OpenShm();   // forward decl (lazy open trong hook)
static void HookLog(const char* fmt, ...);  // log chan doan -> hook.log
void HookReopenSpread(); // goi tu gui_inject khi tick "Use my tick data" (file moi)

// ---------------------------------------------------------------------------
// Tra spread (mode 0)
// ---------------------------------------------------------------------------
static float LookupSpread(uint32_t ts)
{
    auto* h = static_cast<const SpreadHeader*>(g_shm);
    if (!h || h->count == 0) return 0.0f;
    auto* r = reinterpret_cast<const SpreadRec*>(h + 1);
    int lo = 0, hi = (int)h->count - 1, ans = 0;
    while (lo <= hi) {
        int mid = (lo + hi) >> 1;
        if (r[mid].unixTime <= (int32_t)ts) { ans = mid; lo = mid + 1; }
        else hi = mid - 1;
    }
    return r[ans].spreadPts;
}

// ---------------------------------------------------------------------------
// Tim tick that gan nhat theo thoi gian (mode 1)
// ---------------------------------------------------------------------------
static const TDSTick* FindTick(uint32_t time_sec)
{
    auto* h = static_cast<const TickHeader*>(g_shm);
    if (!h || h->count == 0) return nullptr;
    auto* ticks = reinterpret_cast<const TDSTick*>(h + 1);
    uint64_t count = h->count;

    // Tien cursor tu vi tri hien tai den tick co time_ms/1000 >= time_sec
    uint32_t idx = g_tickCursor;
    while (idx + 1 < count &&
           (uint32_t)(ticks[idx].time_ms / 1000) < time_sec)
        ++idx;

    // Ghi lai cursor (khong can atomic — backtest MT4 single-thread)
    g_tickCursor = idx;

    return (idx < count) ? &ticks[idx] : nullptr;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------
static int __fastcall Hook_FetchTick(void* self,
                                     void* edx_unused,
                                     void* outRec,
                                     void* outTick)
{
    int result = g_orig(self, edx_unused, outRec, outTick);

    static long long lastTime = 0;   // theo doi tick cuoi cung xu ly (chan doan crash)

    // result==0: het tick -> ket thuc xu ly tick (crash sau day = o report, khong phai hook)
    if (result != 1) {
        static bool doneLogged = false;
        if (!doneLogged && GuiUseMyTickData()) {
            doneLogged = true;
            HookLog("[done] Xu ly tick XONG (result=%d), tick cuoi t=%lld\n", result, lastTime);
        }
        return result;
    }

    // Chi can thiep khi user da tick "Use my tick data"
    if (outTick && GuiUseMyTickData()) {
        if (!g_shm) OpenShm();   // lazy: file spread co the ghi SAU khi inject
        if (!g_shm) return result;
        auto* tk = static_cast<FetchTick*>(outTick);

        // Bao ve: 1 tick loi khong duoc lam sap MT4 (log tick + bo qua)
        __try {
            lastTime = tk->time_sec;
            if (g_shmMagic == 0x54534454u) {
                // ---- Mode tick that tu Dukascopy ----
                const TDSTick* rt = FindTick((uint32_t)tk->time_sec);
                if (rt) { tk->bid = rt->bid; tk->ask = rt->ask; }
            } else if (g_shmMagic == 0x53534454u) {
                // ---- Mode spread bien dong: chi ap khi chon "use my spread" ----
                if (!GuiUseMySpread()) return result;
                auto* h = static_cast<const SpreadHeader*>(g_shm);
                float sp = LookupSpread((uint32_t)tk->time_sec);
                if (sp > 0.0f) {
                    double off = sp * h->point;   // ask offset (gia)
                    tk->ask = tk->bid + off;      // exact cho tick nay
                    // DONG BO offset noi bo MT4 (self+0x328) -> report/finalization
                    // nhat quan (giong TDS: spread offset bien dong, khong phai const 0).
                    *reinterpret_cast<double*>(
                        reinterpret_cast<uint8_t*>(self) + 0x328) = off;
                }

                // Log mau: 5 tick dau + moi 2 trieu tick
                static long long dbg = 0;
                ++dbg;
                if (dbg <= 5 || (dbg % 2000000) == 0)
                    HookLog("[spread #%lld] t=%d bid=%.1f sp=%.0f -> ask=%.1f off328=%.1f\n",
                            dbg, tk->time_sec, tk->bid, sp, tk->ask,
                            *reinterpret_cast<double*>(reinterpret_cast<uint8_t*>(self)+0x328));
            }
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            static long long nEx = 0;
            if (++nEx <= 20)
                HookLog("[EXCEPTION] code=0x%08lX tick t=%lld outTick=%p (bo qua tick nay)\n",
                        GetExceptionCode(), lastTime, outTick);
        }
    }
    return result;
}

// ---------------------------------------------------------------------------
// SLIPPAGE — ap slippage vao gia fill cua 1 lenh.
//
// TDS ap slippage luc KHOP LENH (khong phai luc sinh tick). De "trong suot" hoan
// toan, can hook ham khop lenh trong tester (OrderSend path) — ham nay CHUA RE
// xong. Khi xac dinh duoc, dat hook tro toi Hook_OrderExecute va dien chu ky.
//
// Engine tinh slippage (slip::SlippagePoints) DA SAN SANG, doc tham so tu
// Local\TDSClone_SlippageShm (block 'TSLP' do python/shm_writer.py ghi).
// ---------------------------------------------------------------------------

// Ap slippage (points, co dau: + bat loi) vao gia fill theo huong lenh.
//   isBuy: true = lenh mua (fill o Ask), false = ban (fill o Bid).
//   point: gia tri 1 point cua symbol.
static double ApplySlippageToFill(double fillPrice, int orderType,
                                  double point, double recentVolPts, int isSlTp)
{
    if (!slip::Enabled()) return fillPrice;
    double sp = slip::SlippagePoints(orderType, recentVolPts, isSlTp);
    if (sp == 0.0) return fillPrice;

    bool isBuy = (orderType == slip::OP_BUY ||
                  orderType == slip::OP_BUYLIMIT ||
                  orderType == slip::OP_BUYSTOP);
    // + slippage (bat loi): mua fill cao hon, ban fill thap hon.
    double delta = sp * point;
    return isBuy ? (fillPrice + delta) : (fillPrice - delta);
}

// Scaffold hook ham khop lenh — chu ky GIA DINH, can RE xac nhan.
// Hien CHUA gan (target ham fill chua biet). Giu day de wire nhanh khi co dia chi.
//
// typedef double (__thiscall* OrderFill_t)(void* self, int orderType, double price);
// static OrderFill_t g_origFill = nullptr;
// static double __fastcall Hook_OrderExecute(void* self, void* edx,
//                                            int orderType, double price) {
//     double real = g_origFill(self, orderType, price);   // qua edx? tuy ABI
//     return ApplySlippageToFill(real, orderType, g_point, g_recentVol, 0);
// }

// ---------------------------------------------------------------------------
// Mo shared memory
// ---------------------------------------------------------------------------
// Mo FILE spread that: <project_root>\data\active.spread (do prepare ghi).
// File-based (khong SHM-process) -> on dinh. Format == SpreadHeader.
static void OpenShm()
{
    if (g_shm) return;   // da mo
    wchar_t path[MAX_PATH];
    if (!GetModuleFileNameW(g_hMod, path, MAX_PATH)) return;
    // DLL o <root>\native\build\hook\Release\tdshook.dll -> len 5 cap = <root>
    for (int i = 0; i < 5; ++i) {
        wchar_t* p = wcsrchr(path, L'\\');
        if (!p) return;
        *p = 0;
    }
    wcscat_s(path, MAX_PATH, L"\\data\\active.spread");

    HANDLE hFile = CreateFileW(path, GENERIC_READ,
                               FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                               nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (hFile == INVALID_HANDLE_VALUE) return;
    g_shmHandle = CreateFileMappingW(hFile, nullptr, PAGE_READONLY, 0, 0, nullptr);
    CloseHandle(hFile);
    if (!g_shmHandle) return;
    g_shm = MapViewOfFile(g_shmHandle, FILE_MAP_READ, 0, 0, 0);
    if (g_shm) {
        g_shmMagic = *static_cast<const uint32_t*>(g_shm);
        auto* h = static_cast<const SpreadHeader*>(g_shm);
        FILE* f = nullptr;
        fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\hook.log", "a");
        if (f) {
            fprintf(f, "[open] Spread file MAPPED: magic=0x%08X count=%u point=%.4f path=%ls\n",
                    g_shmMagic, h->count, h->point, path);
            fclose(f);
        }
    } else {
        FILE* f = nullptr;
        fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\hook.log", "a");
        if (f) { fprintf(f, "[open] MapViewOfFile FAILED\n"); fclose(f); }
    }
}

// Dong mapping hien tai -> tick sau se mo lai file MOI (khi prepare ghi lai).
void HookReopenSpread()
{
    if (g_shm)       { UnmapViewOfFile(g_shm); g_shm = nullptr; }
    if (g_shmHandle) { CloseHandle(g_shmHandle); g_shmHandle = nullptr; }
    g_shmMagic = 0;
    g_tickCursor = 0;
}

// ---------------------------------------------------------------------------
// Cai hook
// ---------------------------------------------------------------------------
static void HookLog(const char* fmt, ...)
{
    FILE* f = nullptr;
    fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\hook.log", "a");
    if (!f) return;
    va_list ap; va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fflush(f);
    fclose(f);
}

static bool InstallHook()
{
    auto* base   = static_cast<uint8_t*>(static_cast<void*>(GetModuleHandleW(nullptr)));
    void* target = base + FETCHTICK_RVA;

    // Sanity: entry FetchNextTick = 55 8B EC 83 E4 C0 83 EC 34 53 56 33 C0 8B F1 57
    auto* p = static_cast<const uint8_t*>(target);
    HookLog("[install] base=%p target=%p bytes=%02X %02X %02X %02X %02X %02X %02X %02X %02X\n",
            base, target, p[0],p[1],p[2],p[3],p[4],p[5],p[6],p[7],p[8]);
    if (!(p[0]==0x55 && p[1]==0x8B && p[2]==0xEC && p[3]==0x83 &&
          p[4]==0xE4 && p[5]==0xC0 && p[6]==0x83 && p[7]==0xEC && p[8]==0x34)) {
        // Fallback: signature scan (prologue day du, rat dac trung)
        MODULEINFO mi{};
        GetModuleInformation(GetCurrentProcess(),
                             GetModuleHandleW(nullptr), &mi, sizeof(mi));
        auto pat = sigscan::ParsePattern(
            "55 8B EC 83 E4 C0 83 EC 34 53 56 33 C0 8B F1 57 89 44 24 3C");
        auto* hit = sigscan::FindFirst(
            static_cast<const uint8_t*>(mi.lpBaseOfDll), mi.SizeOfImage, pat);
        if (!hit) {
            OutputDebugStringW(L"[tdshook] Khong tim thay FetchNextTick");
            return false;
        }
        target = const_cast<uint8_t*>(hit);
    }

    MH_STATUS st = MH_Initialize();
    if (st != MH_OK && st != MH_ERROR_ALREADY_INITIALIZED) return false;

    if (MH_CreateHook(target,
                      reinterpret_cast<void*>(&Hook_FetchTick),
                      reinterpret_cast<void**>(&g_orig)) != MH_OK) {
        HookLog("[install] MH_CreateHook FAILED\n");
        return false;
    }
    HookLog("[install] CreateHook OK, g_orig=%p -> EnableHook...\n", (void*)g_orig);
    if (MH_EnableHook(target) != MH_OK) {
        HookLog("[install] MH_EnableHook FAILED\n");
        return false;
    }
    HookLog("[install] Hook @ %p ENABLED OK\n", target);
    return true;
}

// ---------------------------------------------------------------------------
// CHAN DOAN: bat dia chi CHINH XAC MT4 crash (RVA trong terminal.exe)
// ---------------------------------------------------------------------------
static LONG CALLBACK CrashLogger(EXCEPTION_POINTERS* ep)
{
    DWORD code = ep->ExceptionRecord->ExceptionCode;
    bool severe = (code == EXCEPTION_ACCESS_VIOLATION ||
                   code == EXCEPTION_INT_DIVIDE_BY_ZERO ||
                   code == EXCEPTION_FLT_DIVIDE_BY_ZERO ||
                   code == EXCEPTION_ILLEGAL_INSTRUCTION ||
                   code == EXCEPTION_STACK_OVERFLOW ||
                   code == EXCEPTION_INT_OVERFLOW ||
                   code == 0xC0000409u);
    if (!severe) return EXCEPTION_CONTINUE_SEARCH;
    static long n = 0;
    if (++n > 3) return EXCEPTION_CONTINUE_SEARCH;

    void* addr = ep->ExceptionRecord->ExceptionAddress;
    HMODULE mod = nullptr;
    wchar_t modname[MAX_PATH]; modname[0] = 0;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCWSTR)addr, &mod) && mod)
        GetModuleFileNameW(mod, modname, MAX_PATH);

    HookLog("[CRASH] code=0x%08lX addr=%p mod=%ls base=%p rva=0x%tX\n",
            code, addr, modname[0] ? modname : L"???", (void*)mod,
            mod ? (uint8_t*)addr - (uint8_t*)mod : (ptrdiff_t)0);

    // Quet stack (huong dia chi tang = frame cha) tim return-addr lap lai = chu ky de quy
    if (mod) {
        DWORD* sp = (DWORD*)ep->ContextRecord->Esp;
        uintptr_t lo = (uintptr_t)mod, hi = lo + 0x03000000;
        DWORD cand[6] = {0}; int cnt[6] = {0};
        for (int i = 0; i < 4000; ++i) {
            DWORD v = sp[i];
            if (v < lo || v >= hi) continue;
            int slot = -1;
            for (int k = 0; k < 6; ++k) if (cand[k] == v) { slot = k; break; }
            if (slot < 0) for (int k = 0; k < 6; ++k) if (cnt[k] == 0) { cand[k] = v; slot = k; break; }
            if (slot >= 0) ++cnt[slot];
        }
        int bi = 0; for (int k = 1; k < 6; ++k) if (cnt[k] > cnt[bi]) bi = k;
        HookLog("[CRASH]   recurse_ret=%p rva=0x%tX  xuat_hien=%d lan (Eip=%p Esp=%p)\n",
                (void*)cand[bi], mod ? (uintptr_t)cand[bi] - (uintptr_t)mod : (uintptr_t)0,
                cnt[bi], (void*)ep->ContextRecord->Eip, (void*)ep->ContextRecord->Esp);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

// ---------------------------------------------------------------------------
// DllMain
// ---------------------------------------------------------------------------
BOOL APIENTRY DllMain(HMODULE hMod, DWORD reason, LPVOID)
{
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hMod);
        g_hMod = hMod;   // de OpenShm tinh duong dan data\active.spread
        AddVectoredExceptionHandler(1, CrashLogger);   // bat dia chi crash
        CreateThread(nullptr, 0, [](LPVOID) -> DWORD {
            OpenShm();
            if (slip::Open())
                OutputDebugStringA("[tdshook] Slippage params loaded (TSLP)\n");
            // Cai guard SOM (doc lap voi tickloop hook): chan MT4 ghi de FXT.
            MH_Initialize();
            InstallFxtGuard();
            InstallHook();
            return 0;
        }, nullptr, 0, nullptr);
        StartGuiInjection(hMod);   // chen control vao panel tester
        break;

    case DLL_PROCESS_DETACH:
        MH_DisableHook(MH_ALL_HOOKS);
        MH_Uninitialize();
        slip::Close();
        if (g_shm)       UnmapViewOfFile(g_shm);
        if (g_shmHandle) CloseHandle(g_shmHandle);
        break;
    }
    return TRUE;
}
