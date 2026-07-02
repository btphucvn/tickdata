// FXT ẢO (virtual) — sinh record FXT on-the-fly TRUC TIEP tu file .tkd (per-ngay,
// nen raw-deflate), giong co che TDS (kho nen, doc theo cua so).
//
// GIAI DOAN B: thay mmap month .bin bang doc file NGAY .tkd + giai nen puff (LRU cache).
//   -> KHONG con scratch .bin; hook doc thang kho nen; RAM chi giu vai ngay giai nen.
//
// Config data\active.fxtv (do python/fxt_virtual.py ghi):
//   'FXTV'(4) | u32 ver=1 | u32 header_len=728 | u8[728] header |
//   i32 period_sec | i32 gmt_offset | u32 dst_count | {i64 s,i64 e}*dst_count |
//   i64 from_ms | i64 to_ms | u64 total | f64 point |
//   u32 phname_len | char[phname_len] placeholder_basename_utf8 |
//   u32 nday | {u32 len, char[len] path_utf8}*nday      <-- path .tkd (moi ngay)
//
// File .tkd (daystore): "TKD1"(4)+u32 ver(=2 deflate)+i64 day_ms+i64 first_ms+
//   i64 last_ms+u32 count = 36B header, roi RAW DEFLATE cua count x record 24B:
//   int64 time_ms | double bid | double ask.
//
// Sinh record 56B: <iiddddqii> = barTime,pad,open,high,low,close(bid),vol,tickTime,flag
// LOGIC SINH RECORD PHAI KHOP build_fxt (python) -> ket qua byte-identical (GIU NGUYEN).
#include "fxt_virtual.h"
extern "C" {
#include "puff.h"      // C linkage — puff.c bien dich dang C
}
#include <vector>
#include <string>
#include <cstring>
#include <cstdio>
#include <cstdint>

namespace {

CRITICAL_SECTION g_cs;
bool g_csInit = false;
bool g_active = false;
FILETIME g_cfgTime{};

// ---- config ----
uint8_t  g_header[728];
int32_t  g_period_sec = 0;
int32_t  g_gmt = 0;
std::vector<std::pair<int64_t,int64_t>> g_dst;   // các khoảng UTC-sec DST (+3600)
int64_t  g_from_ms = 0, g_to_ms = 0;
uint64_t g_total_bars = 0;    // "total" trong config (so record FXT du kien)
double   g_point = 0.0;
std::wstring g_phname;        // basename placeholder (đã lowercase)

// ---- nguon NGAY .tkd (doc theo cua so + LRU) ----
struct Day {
    std::wstring path;
    int64_t  first_ms, last_ms;
    uint32_t count;
    uint64_t start;           // global record-index cua record dau ngay nay
    uint8_t* data;            // records da giai nen (count*24 byte), null neu chua nap
    uint64_t lru;             // dau thoi diem truy cap (de evict)
};
std::vector<Day> g_days;
uint64_t g_total = 0;         // tong record (moi ngay)
uint64_t g_loaded_bytes = 0;  // tong byte da giai nen dang giu
uint64_t g_lru_clock = 0;
const uint64_t LRU_CAP_BYTES = 96ull * 1024 * 1024;   // ~96MB cache giai nen

// ---- cursor + bar state (trạng thái SAU khi phát record g_cur-1) ----
uint64_t g_cur = 0;           // so record FXT DA PHAT (sau loc cuoi tuan)
uint64_t g_recidx = 0;        // con tro record THO ke tiep se xet
int32_t  g_barTime = 0;
double   g_open = 0, g_high = 0, g_low = 0;
int64_t  g_vol = 0;
bool     g_barStarted = false;

// ---- cache record cuối sinh ----
uint64_t g_cachedK = ~0ull;
uint8_t  g_cachedRec[56];

void FreeDay(Day& d) {
    if (d.data) { free(d.data); d.data = nullptr; }
}

void CloseDays() {
    for (auto& d : g_days) FreeDay(d);
    g_days.clear();
    g_total = 0; g_loaded_bytes = 0;
}

// Doc 36B header 1 file .tkd -> first_ms/last_ms/count. Chi nhan v2 (deflate).
bool ReadDayHeader(const std::wstring& path, int64_t& first_ms, int64_t& last_ms, uint32_t& count) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    uint8_t head[36]; DWORD rd = 0;
    BOOL ok = ReadFile(h, head, 36, &rd, nullptr);
    CloseHandle(h);
    if (!ok || rd < 36 || memcmp(head, "TKD1", 4) != 0) return false;
    uint32_t ver; memcpy(&ver, head + 4, 4);
    if (ver != 2) return false;                 // chi doc v2 raw-deflate
    memcpy(&first_ms, head + 16, 8);
    memcpy(&last_ms,  head + 24, 8);
    memcpy(&count,    head + 32, 4);
    return true;
}

void EvictLRU(uint64_t need) {
    while (g_loaded_bytes + need > LRU_CAP_BYTES) {
        Day* victim = nullptr;
        for (auto& d : g_days)
            if (d.data && (!victim || d.lru < victim->lru)) victim = &d;
        if (!victim) break;
        g_loaded_bytes -= (uint64_t)victim->count * 24;
        FreeDay(*victim);
    }
}

// Dam bao ngay `d` da giai nen (doc file + puff). LRU-evict neu vuot tran.
bool EnsureDay(Day& d) {
    if (d.data) { d.lru = ++g_lru_clock; return true; }
    HANDLE h = CreateFileW(d.path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER sz{};
    if (!GetFileSizeEx(h, &sz) || sz.QuadPart < 36) { CloseHandle(h); return false; }
    std::vector<uint8_t> buf((size_t)sz.QuadPart);
    DWORD rd = 0;
    BOOL ok = ReadFile(h, buf.data(), (DWORD)buf.size(), &rd, nullptr);
    CloseHandle(h);
    if (!ok || rd < 36 || memcmp(buf.data(), "TKD1", 4) != 0) return false;
    uint32_t cnt; memcpy(&cnt, buf.data() + 32, 4);
    if (cnt != d.count) return false;
    unsigned long destlen = (unsigned long)cnt * 24;
    uint8_t* out = (uint8_t*)malloc(destlen ? destlen : 1);
    if (!out) return false;
    unsigned long srclen = (unsigned long)(rd - 36);
    int rc = puff(out, &destlen, buf.data() + 36, &srclen);
    if (rc != 0 || destlen != (unsigned long)cnt * 24) { free(out); return false; }
    EvictLRU((uint64_t)cnt * 24);
    d.data = out;
    d.lru = ++g_lru_clock;
    g_loaded_bytes += (uint64_t)cnt * 24;
    return true;
}

// Con tro toi record THO theo global-index (binary search g_days theo start).
const uint8_t* RecordPtr(uint64_t gi) {
    if (g_days.empty() || gi >= g_total) return nullptr;
    int lo = 0, hi = (int)g_days.size() - 1, ans = -1;
    while (lo <= hi) {
        int m = (lo + hi) >> 1;
        if (g_days[m].start <= gi) { ans = m; lo = m + 1; } else hi = m - 1;
    }
    if (ans < 0) return nullptr;
    Day& d = g_days[ans];
    if (gi >= d.start + d.count) return nullptr;
    if (!EnsureDay(d)) return nullptr;
    return d.data + (size_t)(gi - d.start) * 24;
}

std::wstring Utf8ToW(const char* s, int len) {
    int wn = MultiByteToWideChar(CP_UTF8, 0, s, len, nullptr, 0);
    std::wstring w(wn, 0);
    MultiByteToWideChar(CP_UTF8, 0, s, len, &w[0], wn);
    return w;
}

bool CfgPath(HMODULE hMod, wchar_t* out, DWORD n) {
    if (!GetModuleFileNameW(hMod, out, n)) return false;
    for (int i = 0; i < 5; ++i) {
        wchar_t* p = wcsrchr(out, L'\\');
        if (!p) return false;
        *p = 0;
    }
    wcscat_s(out, n, L"\\data\\active.fxtv");
    return true;
}

int32_t TzShift(int64_t tsec) {
    int32_t off = g_gmt * 3600;
    for (auto& iv : g_dst)
        if (tsec >= iv.first && tsec < iv.second) { off += 3600; break; }
    return off;
}

void ResetCursor() {
    g_cur = 0; g_recidx = 0; g_barStarted = false;
    g_cachedK = ~0ull;
}

// Lấy tick kế tiếp trong [from_ms,to_ms). Trả false nếu hết. (LOGIC GIU NGUYEN)
bool NextTick(int64_t& t_ms, double& bid) {
    while (g_recidx < g_total) {
        const uint8_t* p = RecordPtr(g_recidx);
        ++g_recidx;
        if (!p) return false;
        int64_t t = *reinterpret_cast<const int64_t*>(p);
        if (t < g_from_ms) continue;
        if (t >= g_to_ms) return false;
        // Loc cuoi tuan (lich phien broker, giong TDS) — PHAI khop build_fxt python.
        {
            int64_t ss = t / 1000 + TzShift(t / 1000);
            int w = (int)(((ss / 86400) + 4) % 7);   // 0=Sun .. 6=Sat
            if (w == 0 || w == 6) continue;
        }
        t_ms = t;
        bid  = *reinterpret_cast<const double*>(p + 8);
        return true;
    }
    return false;
}

// Phát record g_cur (cập nhật bar state). out=nullptr -> chỉ tua. (GIU NGUYEN)
void Emit(uint8_t* out) {
    int64_t t_ms; double bid;
    if (!NextTick(t_ms, bid)) { if (out) memset(out, 0, 56); return; }
    int64_t tsec_utc = t_ms / 1000;
    int64_t tsec = tsec_utc + TzShift(tsec_utc);
    int32_t bt = (int32_t)((tsec / g_period_sec) * g_period_sec);
    if (!g_barStarted || bt != g_barTime) {
        g_barTime = bt; g_open = bid; g_high = bid; g_low = bid; g_vol = 0;
        g_barStarted = true;
    }
    ++g_vol;
    if (bid > g_high) g_high = bid;
    if (bid < g_low)  g_low  = bid;
    if (out) {
        int32_t z = 0, flag = 1, tsec32 = (int32_t)tsec;
        memcpy(out + 0,  &g_barTime, 4);
        memcpy(out + 4,  &z, 4);
        memcpy(out + 8,  &g_open, 8);
        memcpy(out + 16, &g_high, 8);
        memcpy(out + 24, &g_low, 8);
        memcpy(out + 32, &bid, 8);
        memcpy(out + 40, &g_vol, 8);
        memcpy(out + 48, &tsec32, 4);
        memcpy(out + 52, &flag, 4);
    }
    ++g_cur;
}

void RecordAt(uint64_t k, uint8_t out[56]) {
    if (g_cachedK == k) { memcpy(out, g_cachedRec, 56); return; }
    if (k < g_cur) ResetCursor();
    while (g_cur < k) Emit(nullptr);   // tua tới (cập nhật state)
    Emit(out);                          // phát record k
    g_cachedK = k; memcpy(g_cachedRec, out, 56);
}

} // namespace

namespace vfxt {

bool Active() { return g_active; }

bool LoadPath(const wchar_t* path) {
    if (!g_csInit) { InitializeCriticalSection(&g_cs); g_csInit = true; }
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) { g_active = false; return false; }
    LARGE_INTEGER sz{}; GetFileSizeEx(h, &sz);
    std::vector<uint8_t> buf((size_t)sz.QuadPart);
    DWORD rd = 0; ReadFile(h, buf.data(), (DWORD)buf.size(), &rd, nullptr);
    CloseHandle(h);
    if (rd < 12 + 728 || memcmp(buf.data(), "FXTV", 4) != 0) { g_active = false; return false; }

    EnterCriticalSection(&g_cs);
    CloseDays();
    const uint8_t* p = buf.data();
    size_t off = 4;
    auto rd32  = [&](void){ uint32_t v; memcpy(&v, p+off, 4); off += 4; return v; };
    auto rdi32 = [&](void){ int32_t v; memcpy(&v, p+off, 4); off += 4; return v; };
    auto rd64  = [&](void){ int64_t v; memcpy(&v, p+off, 8); off += 8; return v; };
    auto rdd   = [&](void){ double v; memcpy(&v, p+off, 8); off += 8; return v; };
    (void)rd32();                        // version
    uint32_t hlen = rd32();              // header_len (728)
    memcpy(g_header, p + off, 728); off += hlen;
    g_period_sec = rdi32();
    g_gmt = rdi32();
    uint32_t ndst = rd32();
    g_dst.clear();
    for (uint32_t i = 0; i < ndst; ++i) { int64_t s = rd64(); int64_t e = rd64(); g_dst.emplace_back(s, e); }
    g_from_ms = rd64();
    g_to_ms = rd64();
    g_total_bars = (uint64_t)rd64();
    g_point = rdd();
    uint32_t phlen = rd32();
    g_phname = Utf8ToW((const char*)(p + off), (int)phlen); off += phlen;
    for (auto& c : g_phname) c = (wchar_t)towlower(c);
    uint32_t nday = rd32();
    uint64_t cum = 0;
    bool ok = true;
    for (uint32_t i = 0; i < nday; ++i) {
        uint32_t plen = rd32();
        std::wstring dp = Utf8ToW((const char*)(p + off), (int)plen); off += plen;
        Day d{}; d.path = dp; d.data = nullptr; d.lru = 0;
        if (!ReadDayHeader(dp, d.first_ms, d.last_ms, d.count)) { ok = false; continue; }
        d.start = cum;
        cum += d.count;
        g_days.push_back(d);
    }
    g_total = cum;
    ResetCursor();
    g_active = ok && !g_days.empty();
    LeaveCriticalSection(&g_cs);

    FILE* f = nullptr;
    fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\hook.log", "a");
    if (f) {
        fprintf(f, "[vfxt] Load: active=%d bars_hdr=%llu period=%d gmt=%d dst=%zu days=%zu total_rec=%llu ph=%ls\n",
                (int)g_active, (unsigned long long)g_total_bars, g_period_sec, g_gmt,
                g_dst.size(), g_days.size(), (unsigned long long)g_total, g_phname.c_str());
        fclose(f);
    }
    return g_active;
}

bool Load(HMODULE hMod) {
    if (!g_csInit) { InitializeCriticalSection(&g_cs); g_csInit = true; }
    wchar_t path[MAX_PATH];
    if (!CfgPath(hMod, path, MAX_PATH)) return false;
    WIN32_FILE_ATTRIBUTE_DATA fa{};
    if (!GetFileAttributesExW(path, GetFileExInfoStandard, &fa)) { g_active = false; return false; }
    if (g_active && CompareFileTime(&fa.ftLastWriteTime, &g_cfgTime) == 0) return true;
    bool r = LoadPath(path);
    g_cfgTime = fa.ftLastWriteTime;
    return r;
}

bool MatchName(LPCWSTR path) {
    if (!g_active || !path) return false;
    const wchar_t* base = wcsrchr(path, L'\\');
    base = base ? base + 1 : path;
    std::wstring bn(base);
    for (auto& c : bn) c = (wchar_t)towlower(c);
    return bn == g_phname;
}

void Serve(uint64_t offset, void* buf, uint32_t len) {
    if (!g_active || !buf || !len) return;
    EnterCriticalSection(&g_cs);
    uint8_t* b = (uint8_t*)buf;
    uint64_t end = offset + len;
    // Phần header
    if (offset < 728) {
        uint64_t stop = end < 728 ? end : 728;
        uint32_t n = (uint32_t)(stop - offset);
        memcpy(b, g_header + offset, n);
        b += n; offset += n;
    }
    // Phần record
    while (offset < end) {
        uint64_t k = (offset - 728) / 56;
        uint32_t within = (uint32_t)((offset - 728) % 56);
        uint8_t rec[56];
        if (k < g_total_bars) RecordAt(k, rec);
        else memset(rec, 0, 56);
        uint64_t avail = 56 - within;
        uint64_t need = end - offset;
        uint32_t n = (uint32_t)(avail < need ? avail : need);
        memcpy(b, rec + within, n);
        b += n; offset += n;
    }
    LeaveCriticalSection(&g_cs);
}

// Spread THẬT (ask-bid, GIÁ) của tick khớp (server_sec, bid). Doc tu .tkd (windowed):
// chi giai nen NGAY giao cua so ±5s (dung first_ms/last_ms tu header, khong bung ngoai).
double RealSpreadPrice(int32_t server_sec, double bid) {
    if (!g_active || g_days.empty()) return -1.0;
    int64_t utc0 = (int64_t)server_sec - (int64_t)g_gmt * 3600;
    int64_t utc  = (int64_t)server_sec - TzShift(utc0);
    int64_t utc_ms = utc * 1000;
    const int64_t W = 5000;   // cửa sổ ±5s
    double best_ask = -1.0, best_db = 1e300;
    EnterCriticalSection(&g_cs);
    for (Day& d : g_days) {
        if (d.count == 0) continue;
        if (utc_ms + W < d.first_ms || utc_ms - W > d.last_ms) continue;   // ngoai cua so
        if (!EnsureDay(d)) continue;
        const uint8_t* base = d.data;
        // binary search: tick đầu tiên có time >= utc_ms - W
        uint64_t lo = 0, hi = d.count;
        while (lo < hi) {
            uint64_t m = (lo + hi) >> 1;
            int64_t tm = *reinterpret_cast<const int64_t*>(base + m * 24);
            if (tm < utc_ms - W) lo = m + 1; else hi = m;
        }
        for (uint64_t i = lo; i < d.count; ++i) {
            const uint8_t* q = base + i * 24;
            int64_t tm = *reinterpret_cast<const int64_t*>(q);
            if (tm > utc_ms + W) break;
            double bb = *reinterpret_cast<const double*>(q + 8);
            double db = bb - bid; if (db < 0) db = -db;
            if (db < best_db) {
                best_db = db;
                best_ask = *reinterpret_cast<const double*>(q + 16);
            }
        }
    }
    LeaveCriticalSection(&g_cs);
    if (best_ask < 0.0 || best_db > 1e-6) return -1.0;   // ko khớp bid chính xác
    double sp = best_ask - bid;
    return sp > 0.0 ? sp : 0.0;
}

} // namespace vfxt
