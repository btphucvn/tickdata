// FXT ẢO (virtual) — sinh record FXT on-the-fly TRỰC TIẾP từ file .tkd (per-ngày,
// nén raw-deflate), ĐÚNG cơ chế TDS (kho nén, đọc theo cửa sổ / windowed).
//
// TDS: lưu .bfc mỗi-ngày (~1.7GB/9 năm nén); DLL 32-bit inject vào MT4 chỉ giải nén
// NGÀY đang test (working-set vài MB) khi MT4 chạy tuần tự -> KHÔNG giữ toàn bộ trong
// RAM -> chạy 10+ năm trong MT4 32-bit. Bản này làm y hệt với .tkd:
//   - Load: chỉ đọc DANH SÁCH ngày + bounds (first_ms/last_ms/count) từ config (không mở file).
//   - Serve/spread: giải nén NGÀY cần (puff) theo yêu cầu, cache LRU (cap 256MB), evict ngày cũ.
//
// AN TOÀN (Stage B cũ crash 0xC0000005 vì thiếu): (1) EvictLRU KHÔNG free ngày đang
// dùng (keep + g_pinned); (2) bounds-check puff nghiêm ngặt; (3) __try/__except (SEH)
// bọc Serve + RealSpreadPrice -> lỗi bất kỳ trả record 0 / -1 thay vì SẬP MT4;
// (4) buffer đọc/giải nén bằng malloc thô (không object C++) -> SEH-clean.
//
// Config data\active.fxtv (ver 2, do python/fxt_virtual.py ghi):
//   'FXTV'(4) | u32 ver=2 | u32 hlen=728 | u8[728] header |
//   i32 period_sec | i32 gmt | u32 ndst | {i64 s,i64 e}*ndst |
//   i64 from_ms | i64 to_ms | u64 total_filtered | f64 point |
//   u32 phlen | char[phlen] phname |
//   u32 nday | { u32 pathlen, char[pathlen] path, i64 first_ms, i64 last_ms, u32 count }*nday
//
// File .tkd (daystore v2): "TKD1"(4)+u32 ver(=2)+i64 day_ms+i64 first_ms+i64 last_ms+
//   u32 count = 36B header, rồi RAW DEFLATE của count x record 24B (i64 time_ms, f64 bid, f64 ask).
//
// Sinh record 56B: <iiddddqii> = barTime,pad,open,high,low,close(bid),vol,tickTime,flag.
// LOGIC SINH RECORD PHẢI KHỚP build_fxt/range_meta (python) -> byte-identical.
#include "fxt_virtual.h"
extern "C" {
#include "puff.h"          // C linkage — puff.c biên dịch dạng C
}
#include <vector>
#include <string>
#include <cstring>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cwctype>

namespace {

CRITICAL_SECTION g_cs;
bool g_csInit = false;
bool g_active = false;
FILETIME g_cfgTime{};

// ---- config ----
uint8_t  g_header[728];
int32_t  g_period_sec = 0;
int32_t  g_gmt = 0;
std::vector<std::pair<int64_t,int64_t>> g_dst;   // khoảng UTC-sec DST (+3600)
int64_t  g_from_ms = 0, g_to_ms = 0;
uint64_t g_total_bars = 0;     // total FILTERED (từ python range_meta) = số record FXT phục vụ
double   g_point = 0.0;
std::wstring g_phname;

// ---- nguồn NGÀY .tkd (windowed + LRU) ----
struct Day {
    std::wstring path;
    int64_t  first_ms, last_ms;
    uint32_t count;
    uint64_t start;            // global RAW record-index của record đầu ngày
    uint8_t* data;             // count*24 byte đã giải nén, null nếu chưa nạp
    uint64_t lru;
};
std::vector<Day> g_days;
uint64_t g_total = 0;          // tổng record RAW (sum count)
uint64_t g_loaded_bytes = 0;   // tổng byte giải nén đang giữ
uint64_t g_lru_clock = 0;
size_t   g_pinned = (size_t)-1;                 // ngày đang đọc (KHÔNG evict)
const uint64_t LRU_CAP_BYTES = 256ull * 1024 * 1024;   // 256MB — an toàn trong 32-bit

// ---- cursor + bar state (SAU khi phát record g_cur-1) ----
uint64_t g_cur = 0;            // số record FXT ĐÃ PHÁT (sau lọc cuối tuần)
uint64_t g_recidx = 0;         // con trỏ record RAW kế tiếp
int32_t  g_barTime = 0;
double   g_open = 0, g_high = 0, g_low = 0;
int64_t  g_vol = 0;
bool     g_barStarted = false;

// ---- cache record cuối sinh ----
uint64_t g_cachedK = ~0ull;
uint8_t  g_cachedRec[56];

void FreeDay(Day& d) { if (d.data) { free(d.data); d.data = nullptr; } }

void CloseDays() {
    for (auto& d : g_days) FreeDay(d);
    g_days.clear();
    g_total = 0; g_loaded_bytes = 0; g_pinned = (size_t)-1;
}

// Evict ngày LRU cho tới khi + need <= cap. KHÔNG đụng `keep` (đang nạp) và `g_pinned`
// (đang đọc) -> không bao giờ free con trỏ đang sống.
void EvictLRU(size_t keep, uint64_t need) {
    while (g_loaded_bytes + need > LRU_CAP_BYTES) {
        size_t vic = (size_t)-1; uint64_t oldest = ~0ull;
        for (size_t i = 0; i < g_days.size(); ++i) {
            if (i == keep || i == g_pinned) continue;
            if (g_days[i].data && g_days[i].lru < oldest) { oldest = g_days[i].lru; vic = i; }
        }
        if (vic == (size_t)-1) break;            // không còn gì để evict
        g_loaded_bytes -= (uint64_t)g_days[vic].count * 24;
        FreeDay(g_days[vic]);
    }
}

// Giải nén 1 ngày .tkd -> d.data (malloc thô, không object C++ -> SEH-clean). false nếu lỗi.
bool DecompressDay(Day& d) {
    HANDLE h = CreateFileW(d.path.c_str(), GENERIC_READ,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER sz{};
    if (!GetFileSizeEx(h, &sz) || sz.QuadPart < 36 || sz.QuadPart > (int64_t)0x7fffffff) {
        CloseHandle(h); return false;
    }
    DWORD fsz = (DWORD)sz.QuadPart;
    uint8_t* fbuf = (uint8_t*)malloc(fsz);
    if (!fbuf) { CloseHandle(h); return false; }
    DWORD rd = 0;
    BOOL ok = ReadFile(h, fbuf, fsz, &rd, nullptr);
    CloseHandle(h);
    if (!ok || rd < 36 || memcmp(fbuf, "TKD1", 4) != 0) { free(fbuf); return false; }
    uint32_t ver, cnt;
    memcpy(&ver, fbuf + 4, 4);
    memcpy(&cnt, fbuf + 32, 4);
    if (ver != 2 || cnt != d.count) { free(fbuf); return false; }   // chỉ v2; count phải khớp cfg
    unsigned long destlen = (unsigned long)cnt * 24;
    uint8_t* out = (uint8_t*)malloc(destlen ? destlen : 1);
    if (!out) { free(fbuf); return false; }
    unsigned long srclen = (unsigned long)(rd - 36);
    int rc = puff(out, &destlen, fbuf + 36, &srclen);
    free(fbuf);
    if (rc != 0 || destlen != (unsigned long)cnt * 24) { free(out); return false; }   // bounds-check nghiêm
    d.data = out;
    return true;
}

// Đảm bảo ngày index `di` đã giải nén (LRU-evict nếu tràn). `di` được bảo vệ khỏi evict.
bool EnsureDay(size_t di) {
    Day& d = g_days[di];
    if (d.data) { d.lru = ++g_lru_clock; return true; }
    if (!DecompressDay(d)) return false;         // d.data đã set nếu true
    EvictLRU(di, (uint64_t)d.count * 24);        // evict SAU khi đã có data (giữ di)
    d.lru = ++g_lru_clock;
    g_loaded_bytes += (uint64_t)d.count * 24;
    return true;
}

// Con trỏ tới record RAW theo global-index. Nạp ngày (lazy) + PIN ngày đó (an toàn evict).
const uint8_t* RecordPtr(uint64_t gi) {
    if (g_days.empty() || gi >= g_total) return nullptr;
    int lo = 0, hi = (int)g_days.size() - 1, ans = -1;
    while (lo <= hi) {
        int m = (lo + hi) >> 1;
        if (g_days[(size_t)m].start <= gi) { ans = m; lo = m + 1; } else hi = m - 1;
    }
    if (ans < 0) return nullptr;
    size_t di = (size_t)ans;
    Day& d = g_days[di];
    if (gi >= d.start + d.count) return nullptr;
    if (!EnsureDay(di)) return nullptr;
    g_pinned = di;                                // ngày đang đọc -> không evict
    return d.data + (size_t)(gi - d.start) * 24;
}

std::wstring Utf8ToW(const char* s, int len) {
    int wn = MultiByteToWideChar(CP_UTF8, 0, s, len, nullptr, 0);
    std::wstring w(wn, 0);
    MultiByteToWideChar(CP_UTF8, 0, s, len, &w[0], wn);
    return w;
}

// Đường dẫn config: <root>\data\active.fxtv (từ module DLL, lùi 5 cấp).
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

// Tick RAW kế tiếp trong [from_ms,to_ms), bỏ cuối tuần (giờ server) — KHỚP python.
bool NextTick(int64_t& t_ms, double& bid) {
    while (g_recidx < g_total) {
        const uint8_t* p = RecordPtr(g_recidx);
        ++g_recidx;
        if (!p) return false;
        int64_t t = *reinterpret_cast<const int64_t*>(p);
        if (t < g_from_ms) continue;
        if (t >= g_to_ms) return false;
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

// Phát record g_cur (cập nhật bar). out=nullptr -> chỉ tua. (LOGIC GIỮ NGUYÊN production)
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
    const size_t bufsz = buf.size();
    size_t off = 4;
    bool bad = false;
    // Doc CO BOUNDS-CHECK: thieu byte -> bad=true, tra 0 (KHONG doc tran -> khong crash).
    auto need  = [&](size_t n)->bool{ if (off + n > bufsz) { bad = true; return false; } return true; };
    auto rd32  = [&](void)->uint32_t{ if(!need(4)) return 0; uint32_t v; memcpy(&v,p+off,4); off+=4; return v; };
    auto rdi32 = [&](void)->int32_t { if(!need(4)) return 0; int32_t  v; memcpy(&v,p+off,4); off+=4; return v; };
    auto rd64  = [&](void)->int64_t { if(!need(8)) return 0; int64_t  v; memcpy(&v,p+off,8); off+=8; return v; };
    auto rdd   = [&](void)->double  { if(!need(8)) return 0; double   v; memcpy(&v,p+off,8); off+=8; return v; };

    uint32_t version = rd32();           // version (PHAI ==2)
    uint32_t hlen    = rd32();           // header_len (728)
    // *** BAT BUOC: chi parse cfg VER2. Ban cu (ver1, liet ke .bin) format KHAC ->
    // parse ver2 se doc pathlen rac -> OOB/crash. Bo qua an toan, cho prepare ghi ver2. ***
    if (version != 2 || hlen != 728 || !need(728)) {
        g_active = false;
        LeaveCriticalSection(&g_cs);
        FILE* f = nullptr; fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\hook.log", "a");
        if (f) { fprintf(f, "[vfxt] Load(win): bo qua cfg ver=%u (can ver2, se cho prepare)\n", version); fclose(f); }
        return false;
    }
    memcpy(g_header, p + off, 728); off += hlen;
    g_period_sec = rdi32();
    g_gmt = rdi32();
    uint32_t ndst = rd32();
    g_dst.clear();
    for (uint32_t i = 0; i < ndst && !bad; ++i) { int64_t s = rd64(); int64_t e = rd64(); if(!bad) g_dst.emplace_back(s, e); }
    g_from_ms = rd64();
    g_to_ms = rd64();
    g_total_bars = (uint64_t)rd64();
    g_point = rdd();
    uint32_t phlen = rd32();
    if (bad || !need(phlen) || phlen > 4096) {
        g_active = false; LeaveCriticalSection(&g_cs); return false;
    }
    g_phname = Utf8ToW((const char*)(p + off), (int)phlen); off += phlen;
    for (auto& c : g_phname) c = (wchar_t)towlower(c);

    uint32_t nday = rd32();
    uint64_t cum = 0;
    for (uint32_t i = 0; i < nday && !bad; ++i) {
        uint32_t plen = rd32();
        if (bad || plen > 4096 || !need(plen)) { bad = true; break; }
        std::wstring dp = Utf8ToW((const char*)(p + off), (int)plen); off += plen;
        int64_t fms = rd64(); int64_t lms = rd64(); uint32_t cnt = rd32();
        if (bad) break;
        Day d{}; d.path = dp; d.first_ms = fms; d.last_ms = lms; d.count = cnt;
        d.start = cum; d.data = nullptr; d.lru = 0;
        cum += cnt;
        g_days.push_back(std::move(d));
    }
    g_total = cum;
    ResetCursor();
    g_active = !bad && !g_days.empty();
    LeaveCriticalSection(&g_cs);

    FILE* f = nullptr;
    fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\hook.log", "a");
    if (f) {
        fprintf(f, "[vfxt] Load(win): active=%d bars=%llu raw=%llu period=%d gmt=%d dst=%zu days=%zu ph=%ls\n",
                (int)g_active, (unsigned long long)g_total_bars, (unsigned long long)g_total,
                g_period_sec, g_gmt, g_dst.size(), g_days.size(), g_phname.c_str());
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
    __try {
        uint8_t* b = (uint8_t*)buf;
        uint64_t end = offset + len;
        // Header
        if (offset < 728) {
            uint64_t stop = end < 728 ? end : 728;
            uint32_t n = (uint32_t)(stop - offset);
            memcpy(b, g_header + offset, n);
            b += n; offset += n;
        }
        // Record
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
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        // lỗi bất ngờ -> KHÔNG làm sập MT4 (bỏ qua; MT4 đọc phần còn lại có thể rác nhưng an toàn).
    }
    g_pinned = (size_t)-1;   // hết thao tác -> bỏ pin
    LeaveCriticalSection(&g_cs);
}

// Spread THẬT (ask-bid, GIÁ) của tick khớp (server_sec, bid) — đọc .tkd windowed:
// route theo first_ms/last_ms (không mở file), chỉ giải nén NGÀY giao cửa sổ ±5s.
double RealSpreadPrice(int32_t server_sec, double bid) {
    if (!g_active || g_days.empty()) return -1.0;
    int64_t utc0 = (int64_t)server_sec - (int64_t)g_gmt * 3600;
    int64_t utc  = (int64_t)server_sec - TzShift(utc0);
    int64_t utc_ms = utc * 1000;
    const int64_t W = 5000;   // ±5s
    double best_ask = -1.0, best_db = 1e300;
    EnterCriticalSection(&g_cs);
    __try {
        for (size_t di = 0; di < g_days.size(); ++di) {
            Day& d = g_days[di];
            if (d.count == 0) continue;
            if (utc_ms + W < d.first_ms || utc_ms - W > d.last_ms) continue;   // ngoài cửa sổ (dùng bounds cfg)
            if (!EnsureDay(di)) continue;
            g_pinned = di;                          // đang đọc ngày này -> không evict
            const uint8_t* base = d.data;
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
                if (db < best_db) { best_db = db; best_ask = *reinterpret_cast<const double*>(q + 16); }
            }
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        best_ask = -1.0;
    }
    g_pinned = (size_t)-1;
    LeaveCriticalSection(&g_cs);
    if (best_ask < 0.0 || best_db > 1e-6) return -1.0;
    double sp = best_ask - bid;
    return sp > 0.0 ? sp : 0.0;
}

} // namespace vfxt
