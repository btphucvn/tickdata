// ============================================================================
// Module 4 — EA Helper DLL  (khong can inject, khong can quyen Admin)
// ----------------------------------------------------------------------------
// MT4 EA goi qua #import de lay spread dong tu Dukascopy (qua shared memory).
// Shared memory duoc tao boi orchestrator.py.
//
// MT4 #import:
//   #import "tdsclone.dll"
//     int    TDS_Load(string dummy);       // ket noi shared mem (goi 1 lan OnInit)
//     double TDS_SpreadAt(int time_sec);   // spread (points) tai thoi diem nay
//     double TDS_Point();                  // pip size
//     double TDS_SlippageAt(int time_sec); // slippage (0 neu khong co data)
//   #import
//
// Shared memory layout (khop orchestrator.py Mode 0):
//   uint32 magic='TDSS', uint32 count, double point,
//   count x { int32 unixTime, float spread_pts }
// ============================================================================

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdint>

// ---------------------------------------------------------------------------
// Layout shared memory
// ---------------------------------------------------------------------------
#pragma pack(push, 1)
struct ShmHeader {
    uint32_t magic;
    uint32_t count;
    double   point;
};
struct ShmRec {
    int32_t  time;
    float    spread;
};
#pragma pack(pop)

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
static HANDLE         g_hMap = nullptr;
static const void*    g_view = nullptr;
static double         g_slippage = 0.0;

static const ShmHeader* Hdr()
{
    return static_cast<const ShmHeader*>(g_view);
}
static const ShmRec* Recs()
{
    return reinterpret_cast<const ShmRec*>(Hdr() + 1);
}

static double LookupSpread(int32_t ts)
{
    const ShmHeader* h = Hdr();
    if (!h || h->count == 0) return 0.0;
    const ShmRec*    r = Recs();
    uint32_t lo = 0, hi = h->count - 1, ans = 0;
    while (lo <= hi) {
        uint32_t mid = (lo + hi) >> 1;
        if (r[mid].time <= ts) { ans = mid; lo = mid + 1; }
        else hi = mid - 1;
    }
    return static_cast<double>(r[ans].spread);
}

// ---------------------------------------------------------------------------
// Exports  (stdcall = bat buoc cho MT4 #import)
// ---------------------------------------------------------------------------
extern "C" {

// Ket noi shared memory do orchestrator.py tao.
// Tra so ban ghi (>0) = thanh cong; am = loi.
// dummy: tham so placeholder de MQL4 khong bao loi khi goi TDS_Load("").
__declspec(dllexport) int __stdcall TDS_Load(const char* /*dummy*/)
{
    if (g_view) return (int)Hdr()->count;   // da ket noi roi

    const wchar_t* SHM_NAME = L"Local\\TDSClone_SpreadShm";
    g_hMap = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_NAME);
    if (!g_hMap) return -1;

    g_view = MapViewOfFile(g_hMap, FILE_MAP_READ, 0, 0, 0);
    if (!g_view) {
        CloseHandle(g_hMap); g_hMap = nullptr;
        return -2;
    }
    if (Hdr()->magic != 0x53534454u) {
        UnmapViewOfFile(g_view); g_view = nullptr;
        CloseHandle(g_hMap);     g_hMap = nullptr;
        return -3;
    }
    return (int)Hdr()->count;
}

// Spread (points) tai thoi diem nay.
__declspec(dllexport) double __stdcall TDS_SpreadAt(int time_sec)
{
    return LookupSpread((int32_t)time_sec);
}

// Pip size.
__declspec(dllexport) double __stdcall TDS_Point()
{
    return g_view ? Hdr()->point : 0.00001;
}

// Slippage (mac dinh 0, co the set boi broker sim).
__declspec(dllexport) double __stdcall TDS_SlippageAt(int /*time_sec*/)
{
    return g_slippage;
}

__declspec(dllexport) void __stdcall TDS_SetSlippage(double pts)
{
    g_slippage = pts;
}

// So ban ghi hien tai (debug/check).
__declspec(dllexport) int __stdcall TDS_Count()
{
    return g_view ? (int)Hdr()->count : 0;
}

} // extern "C"

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_DETACH) {
        if (g_view)  UnmapViewOfFile(g_view);
        if (g_hMap)  CloseHandle(g_hMap);
    }
    return TRUE;
}
