// ============================================================================
//  Module 4 — EA Helper DLL  (Cách C2: sạch & dễ, KHÔNG inject)
//  TDS_CLONE_ARCHITECTURE.md — mục 4.
// ----------------------------------------------------------------------------
//  DLL export vài hàm cho EA gọi qua `#import`, trả spread/slippage ĐỘNG theo
//  thời gian tick. Vì MT4 chính thức hỗ trợ #import nên cách này KHÔNG gãy khi
//  MT4 update (khác hẳn Module 5 hook runtime).
//
//  Calling convention: __stdcall  (BẮT BUỘC cho #import của MQL4).
//  Bitness:            x86 (32-bit) — cùng MT4.  String MQL4 -> truyền dạng
//                      char* ANSI khi #import "string"? MQL4 truyền `string` như
//                      `const char*` (ANSI). Ở đây nhận const char* cho path.
//
//  Luồng dùng:
//    1. Python sinh file .tdspread (xem python/tdsclone/convert/spread_file.py):
//         magic "TDSSPRD1" | uint32 count | count*{ int32 unixTime; float points }
//       SẮP XẾP tăng theo unixTime.
//    2. EA gọi TDS_Load("EURUSD1.tdspread") trong OnInit.
//    3. Mỗi OnTick gọi TDS_SpreadAt(TimeCurrent()) -> số points -> tự dựng Ask.
// ============================================================================

#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <vector>
#include <algorithm>
#include <mutex>

// ---- Bản ghi spread trong bộ nhớ -------------------------------------------
struct SpreadRecord {
    int32_t unixTime;     // epoch giây (UTC)
    float   points;       // spread tính bằng points
};

namespace {
    // Bảng tra đã nạp, sắp xếp tăng theo unixTime để binary-search O(log n).
    std::vector<SpreadRecord> g_table;
    std::mutex                g_mutex;     // bảo vệ g_table (EA đơn luồng nhưng cho chắc)
    float                     g_slippage = 0.0f;  // slippage points mặc định
    const char                MAGIC[8] = {'T','D','S','S','P','R','D','1'};
}

// ----------------------------------------------------------------------------
//  TDS_Load — nạp file .tdspread vào bộ nhớ. Trả số bản ghi (>=0), -1 nếu lỗi.
// ----------------------------------------------------------------------------
extern "C" __declspec(dllexport) int __stdcall TDS_Load(const char* spreadFile)
{
    if (!spreadFile) return -1;

    FILE* f = nullptr;
    // fopen_s an toàn hơn fopen trên MSVC.
    if (fopen_s(&f, spreadFile, "rb") != 0 || !f) return -1;

    char magic[8] = {0};
    if (fread(magic, 1, 8, f) != 8 || memcmp(magic, MAGIC, 8) != 0) {
        fclose(f);
        return -1;  // không đúng định dạng
    }

    uint32_t count = 0;
    if (fread(&count, sizeof(count), 1, f) != 1) { fclose(f); return -1; }

    std::vector<SpreadRecord> table;
    table.resize(count);
    // Mỗi record trên đĩa = int32 + float = 8 byte, packed little-endian.
    // Đọc theo từng record để không phụ thuộc padding của struct.
    for (uint32_t i = 0; i < count; ++i) {
        int32_t t; float p;
        if (fread(&t, sizeof(t), 1, f) != 1 ||
            fread(&p, sizeof(p), 1, f) != 1) {
            fclose(f);
            return -1;
        }
        table[i].unixTime = t;
        table[i].points   = p;
    }
    fclose(f);

    // Đảm bảo đã sort (file Python vốn sort, nhưng phòng thủ).
    std::sort(table.begin(), table.end(),
              [](const SpreadRecord& a, const SpreadRecord& b){
                  return a.unixTime < b.unixTime;
              });

    std::lock_guard<std::mutex> lk(g_mutex);
    g_table.swap(table);
    return (int)g_table.size();
}

// ----------------------------------------------------------------------------
//  TDS_SpreadAt — trả spread (points) tại unixTime: bản ghi gần nhất <= unixTime.
//  Nếu chưa nạp/đứng trước bản ghi đầu -> trả 0.
// ----------------------------------------------------------------------------
extern "C" __declspec(dllexport) double __stdcall TDS_SpreadAt(int unixTime)
{
    std::lock_guard<std::mutex> lk(g_mutex);
    if (g_table.empty()) return 0.0;

    // upper_bound tìm phần tử ĐẦU TIÊN > unixTime; lùi 1 = gần nhất <= unixTime.
    SpreadRecord key{ (int32_t)unixTime, 0.0f };
    auto it = std::upper_bound(
        g_table.begin(), g_table.end(), key,
        [](const SpreadRecord& a, const SpreadRecord& b){
            return a.unixTime < b.unixTime;
        });
    if (it == g_table.begin()) return (double)g_table.front().points;
    --it;
    return (double)it->points;
}

// ----------------------------------------------------------------------------
//  TDS_SlippageAt — slippage points (đơn giản: hằng số set qua TDS_SetSlippage).
//  Có thể mở rộng thành mô hình theo thời gian giống spread.
// ----------------------------------------------------------------------------
extern "C" __declspec(dllexport) double __stdcall TDS_SlippageAt(int /*unixTime*/)
{
    return (double)g_slippage;
}

extern "C" __declspec(dllexport) void __stdcall TDS_SetSlippage(double points)
{
    g_slippage = (float)points;
}

// ----------------------------------------------------------------------------
//  TDS_Count — số bản ghi đang nạp (debug).
// ----------------------------------------------------------------------------
extern "C" __declspec(dllexport) int __stdcall TDS_Count()
{
    std::lock_guard<std::mutex> lk(g_mutex);
    return (int)g_table.size();
}

// DllMain tối giản — không cần khởi tạo gì đặc biệt.
BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_DETACH) {
        std::lock_guard<std::mutex> lk(g_mutex);
        g_table.clear();
    }
    return TRUE;
}
