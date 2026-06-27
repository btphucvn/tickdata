// ============================================================================
//  Module 5B — Hook Engine DLL  (Cách C3 "y hệt TDS", phần override giá)
//  TDS_CLONE_ARCHITECTURE.md — mục 5.2.
// ----------------------------------------------------------------------------
//  DLL này được injector (5A) nạp vào terminal.exe. Trong DllMain(PROCESS_ATTACH)
//  nó:
//    1. Mở shared memory chứa cấu hình spread (do Module 6 / orchestrator ghi).
//    2. Dùng SigScanner (5C) tìm địa chỉ hàm "trả giá trong tester" từ signature.
//    3. Đặt inline hook bằng MinHook -> mỗi tick override spread theo bảng tra.
//
//  ⚠️ CHỮ KÝ HÀM Ở ĐÂY LÀ GIẢ ĐỊNH. Hàm nội bộ của MetaQuotes là undocumented;
//     bạn PHẢI tự xác định đúng hàm + chữ ký bằng Ghidra/x64dbg trên BẢN MT4 của
//     mình (mục 5.3). Khi MT4 update, signature có thể gãy -> chạy lại 5C.
//  ⚠️ x86 (32-bit), calling convention nội bộ MT4 thường __thiscall/__fastcall.
//
//  NẾU CHƯA CÓ MINHOOK: target này không build (xem native/CMakeLists.txt). Bạn
//  vẫn có Module 4 (#import) là đường sạch không cần hook.
// ============================================================================

#include <windows.h>
#include <psapi.h>       // GetModuleInformation, MODULEINFO
#pragma comment(lib, "psapi.lib")
#include <cstdint>
#include <cstdio>

#include "MinHook.h"     // third_party/minhook
#include "sigscan.h"     // Module 5C

// ----------------------------------------------------------------------------
//  Cấu trúc shared memory orchestrator <-> hook (mục 5.2 + 6.3).
//  Module 6 (Python) tạo file-mapping "TDSClone_SpreadShm" và ghi bảng spread;
//  hook đọc read-only. Layout phải KHỚP với phía Python.
// ----------------------------------------------------------------------------
#pragma pack(push, 1)
struct SpreadShmHeader {
    uint32_t magic;        // 'TDSS' = 0x53534454
    uint32_t count;        // số bản ghi
    // theo sau: count * { int32 unixTime; float points }
};
struct SpreadShmRecord {
    int32_t unixTime;
    float   points;
};
#pragma pack(pop)

namespace {
    const wchar_t* SHM_NAME = L"Local\\TDSClone_SpreadShm";
    HANDLE                 g_shmHandle = nullptr;
    const SpreadShmHeader* g_shm = nullptr;   // map read-only
    double                 g_point = 0.00001; // point của symbol (orchestrator set)

    // --- Con trỏ tới hàm gốc (trampoline do MinHook tạo) -------------------
    // Chữ ký GIẢ ĐỊNH: hàm thiscall trả giá double theo tickTime.
    typedef double(__thiscall* GetQuote_t)(void* self, int tickTime);
    GetQuote_t orig_GetQuote = nullptr;

    // Signature GIẢ ĐỊNH của hàm mục tiêu — THAY bằng signature thật từ Ghidra.
    const char* TARGET_SIGNATURE = "55 8B EC ?? ?? ?? ?? 8B ?? ?? E8 ?? ?? ?? ??";
}

// ----------------------------------------------------------------------------
//  Tra spread (points) tại unixTime từ shared memory (binary search tay).
// ----------------------------------------------------------------------------
static float LookupSpread(int unixTime)
{
    if (!g_shm || g_shm->magic != 0x53534454u || g_shm->count == 0)
        return 0.0f;

    auto recs = (const SpreadShmRecord*)((const uint8_t*)g_shm + sizeof(SpreadShmHeader));
    int lo = 0, hi = (int)g_shm->count - 1, ans = -1;
    while (lo <= hi) {                       // tìm bản ghi gần nhất <= unixTime
        int mid = (lo + hi) / 2;
        if (recs[mid].unixTime <= unixTime) { ans = mid; lo = mid + 1; }
        else hi = mid - 1;
    }
    if (ans < 0) ans = 0;
    return recs[ans].points;
}

// ----------------------------------------------------------------------------
//  Hàm hook: gọi hàm gốc lấy giá thật rồi áp spread động lên kết quả.
//  Ở đây minh hoạ "giá trả về là BID, ta cộng spread để dựng ASK". Logic THẬT
//  phụ thuộc hàm bạn hook trả gì (bid? ask? mid?) — điều chỉnh sau khi RE.
// ----------------------------------------------------------------------------
static double __thiscall Hook_GetQuote(void* self, int tickTime)
{
    double real = orig_GetQuote(self, tickTime);          // giá gốc của tester
    float  spPts = LookupSpread(tickTime);                 // spread points động
    double ask = real + spPts * g_point;                   // dựng ask = bid + spread
    return ask;
}

// ----------------------------------------------------------------------------
//  Mở shared memory do orchestrator tạo (read-only).
// ----------------------------------------------------------------------------
static bool OpenSharedMemory()
{
    g_shmHandle = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_NAME);
    if (!g_shmHandle) return false;
    g_shm = (const SpreadShmHeader*)MapViewOfFile(
        g_shmHandle, FILE_MAP_READ, 0, 0, 0);
    return g_shm != nullptr;
}

// ----------------------------------------------------------------------------
//  Khởi tạo hook: tìm địa chỉ hàm bằng signature, tạo + bật hook.
//  Trả true nếu thành công.
// ----------------------------------------------------------------------------
static bool InstallHook()
{
    // Lấy module base của terminal.exe (chính tiến trình hiện tại).
    HMODULE base = GetModuleHandleW(nullptr);
    MODULEINFO mi{};
    if (!GetModuleInformation(GetCurrentProcess(), base, &mi, sizeof(mi)))
        return false;

    // Quét signature trong vùng code của terminal.exe (đang map ở memory).
    auto pat = sigscan::ParsePattern(TARGET_SIGNATURE);
    const uint8_t* hit = sigscan::FindFirst(
        (const uint8_t*)mi.lpBaseOfDll, mi.SizeOfImage, pat);
    if (!hit) {
        OutputDebugStringW(L"[tdshook] Không tìm thấy hàm mục tiêu qua signature. "
                           L"Cần cập nhật signature (5C).");
        return false;
    }

    if (MH_Initialize() != MH_OK) return false;
    if (MH_CreateHook((void*)hit, (void*)&Hook_GetQuote,
                      (void**)&orig_GetQuote) != MH_OK) return false;
    if (MH_EnableHook((void*)hit) != MH_OK) return false;

    OutputDebugStringW(L"[tdshook] Hook cài đặt thành công.");
    return true;
}

// ----------------------------------------------------------------------------
//  DllMain — điểm vào khi injector nạp DLL.
// ----------------------------------------------------------------------------
BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID)
{
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hModule);
        // Không làm việc nặng trong DllMain (loader lock). Spawn thread riêng.
        CreateThread(nullptr, 0, [](LPVOID) -> DWORD {
            OpenSharedMemory();    // cấu hình spread (có thể chưa sẵn -> hook trả 0)
            InstallHook();
            return 0;
        }, nullptr, 0, nullptr);
        break;

    case DLL_PROCESS_DETACH:
        MH_DisableHook(MH_ALL_HOOKS);
        MH_Uninitialize();
        if (g_shm)       UnmapViewOfFile(g_shm);
        if (g_shmHandle) CloseHandle(g_shmHandle);
        break;
    }
    return TRUE;
}
