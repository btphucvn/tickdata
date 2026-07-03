// FXT ẢO (virtual) — sinh record FXT on-the-fly từ tick .bin, giống cơ chế TDS.
//
// Config data\active.fxtv (do python/fxt_virtual.py ghi):
//   'FXTV'(4) | u32 ver=1 | u32 header_len=728 | u8[728] header |
//   i32 period_sec | i32 gmt_offset | u32 dst_count | {i64 s,i64 e}*dst_count |
//   i64 from_ms | i64 to_ms | u64 total | f64 point |
//   u32 phname_len | char[phname_len] placeholder_basename_utf8 |
//   u32 nbin | {u32 len, char[len] path_utf8}*nbin
//
// Tick .bin (tick_store): 'TDST'(4)+u32 ver+u64 count = 16B header, rồi record 24B:
//   int64 time_ms | double bid | double ask
//
// Sinh record 56B: <iiddddqii> = barTime,pad,open,high,low,close(bid),vol,tickTime,flag
// LOGIC PHẢI KHỚP build_fxt (python) -> kết quả byte-identical.
#include "fxt_virtual.h"
#include <vector>
#include <string>
#include <cstring>
#include <cstdio>

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
uint64_t g_total = 0;
double   g_point = 0.0;
std::wstring g_phname;   // basename placeholder (đã lowercase)

// ---- bin mmap ----
struct Bin { HANDLE hf; HANDLE hm; const uint8_t* base; uint64_t nrec; };
std::vector<Bin> g_bins;

// ---- cursor + bar state (trạng thái SAU khi phát record g_cur-1) ----
uint64_t g_cur = 0;          // index record kế tiếp sẽ phát
size_t   g_bi = 0;           // bin đang đọc
uint64_t g_ri = 0;           // record-index trong bin g_bi
int32_t  g_barTime = 0;
double   g_open = 0, g_high = 0, g_low = 0;
int64_t  g_vol = 0;
bool     g_barStarted = false;

// ---- cache record cuối sinh ----
uint64_t g_cachedK = ~0ull;
uint8_t  g_cachedRec[56];

void CloseBins() {
    for (auto& b : g_bins) {
        if (b.base) UnmapViewOfFile(b.base);
        if (b.hm)   CloseHandle(b.hm);
        if (b.hf && b.hf != INVALID_HANDLE_VALUE) CloseHandle(b.hf);
    }
    g_bins.clear();
}

bool MapBin(const std::wstring& path) {
    Bin b{};
    b.hf = CreateFileW(path.c_str(), GENERIC_READ,
                       FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                       OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (b.hf == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER sz{};
    if (!GetFileSizeEx(b.hf, &sz) || sz.QuadPart < 16) { CloseHandle(b.hf); return false; }
    b.hm = CreateFileMappingW(b.hf, nullptr, PAGE_READONLY, 0, 0, nullptr);
    if (!b.hm) { CloseHandle(b.hf); return false; }
    b.base = (const uint8_t*)MapViewOfFile(b.hm, FILE_MAP_READ, 0, 0, 0);
    if (!b.base) { CloseHandle(b.hm); CloseHandle(b.hf); return false; }
    b.nrec = (uint64_t)((sz.QuadPart - 16) / 24);   // trừ header 16B, record 24B
    g_bins.push_back(b);
    return true;
}

std::wstring Utf8ToW(const char* s, int len) {
    int wn = MultiByteToWideChar(CP_UTF8, 0, s, len, nullptr, 0);
    std::wstring w(wn, 0);
    MultiByteToWideChar(CP_UTF8, 0, s, len, &w[0], wn);
    return w;
}

// Đường dẫn config: <root>\data\active.fxtv  (từ module DLL, lùi 5 cấp — giống OpenShm)
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
    g_cur = 0; g_bi = 0; g_ri = 0; g_barStarted = false;
    g_cachedK = ~0ull;
}

// Lấy tick kế tiếp trong [from_ms,to_ms). Trả false nếu hết.
bool NextTick(int64_t& t_ms, double& bid) {
    while (g_bi < g_bins.size()) {
        Bin& b = g_bins[g_bi];
        if (g_ri >= b.nrec) { ++g_bi; g_ri = 0; continue; }
        const uint8_t* p = b.base + 16 + g_ri * 24;
        int64_t t = *reinterpret_cast<const int64_t*>(p);
        ++g_ri;
        if (t < g_from_ms) continue;
        if (t >= g_to_ms) return false;
        // Loc cuoi tuan (lich phien broker: BTCUSD dong Sat+Sun theo GIO SERVER) —
        // giong TDS. Data Dukascopy 2019 co tick 24/7, nhung tester chi dung bar
        // trong phien -> phai bo tick Sat/Sun de khong sinh bar/lenh cuoi tuan.
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

// Phát record g_cur (cập nhật bar state). out=nullptr -> chỉ tua (không xuất byte).
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

bool Load(HMODULE hMod) {
    if (!g_csInit) { InitializeCriticalSection(&g_cs); g_csInit = true; }
    wchar_t path[MAX_PATH];
    if (!CfgPath(hMod, path, MAX_PATH)) return false;

    // check mtime -> chỉ reload khi đổi
    WIN32_FILE_ATTRIBUTE_DATA fa{};
    if (!GetFileAttributesExW(path, GetFileExInfoStandard, &fa)) { g_active = false; return false; }
    if (g_active && CompareFileTime(&fa.ftLastWriteTime, &g_cfgTime) == 0) return true;

    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) { g_active = false; return false; }
    LARGE_INTEGER sz{}; GetFileSizeEx(h, &sz);
    std::vector<uint8_t> buf((size_t)sz.QuadPart);
    DWORD rd = 0; ReadFile(h, buf.data(), (DWORD)buf.size(), &rd, nullptr);
    CloseHandle(h);
    if (rd < 12 + 728 || memcmp(buf.data(), "FXTV", 4) != 0) { g_active = false; return false; }

    EnterCriticalSection(&g_cs);
    CloseBins();
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
    g_total = (uint64_t)rd64();
    g_point = rdd();
    uint32_t phlen = rd32();
    g_phname = Utf8ToW((const char*)(p + off), (int)phlen); off += phlen;
    for (auto& c : g_phname) c = (wchar_t)towlower(c);
    uint32_t nbin = rd32();
    bool ok = true;
    for (uint32_t i = 0; i < nbin; ++i) {
        uint32_t plen = rd32();
        std::wstring bp = Utf8ToW((const char*)(p + off), (int)plen); off += plen;
        if (!MapBin(bp)) ok = false;
    }
    ResetCursor();
    g_active = ok && !g_bins.empty();
    g_cfgTime = fa.ftLastWriteTime;
    LeaveCriticalSection(&g_cs);

    FILE* f = nullptr;
    fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\hook.log", "a");
    if (f) {
        fprintf(f, "[vfxt] Load: active=%d total=%llu bars_hdr period=%d gmt=%d dst=%zu bins=%zu ph=%ls\n",
                (int)g_active, (unsigned long long)g_total, g_period_sec, g_gmt,
                g_dst.size(), g_bins.size(), g_phname.c_str());
        fclose(f);
    }
    return g_active;
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
        if (k < g_total) RecordAt(k, rec);
        else memset(rec, 0, 56);
        uint64_t avail = 56 - within;
        uint64_t need = end - offset;
        uint32_t n = (uint32_t)(avail < need ? avail : need);
        memcpy(b, rec + within, n);
        b += n; offset += n;
    }
    LeaveCriticalSection(&g_cs);
}

// Spread THẬT (ask-bid, GIÁ) của tick khớp (server_sec, bid) — đọc thẳng .bin.
// server_sec -> utc (trừ TzShift), tìm tick có bid TRÙNG KHÍT trong cửa sổ ±5s.
// Match bằng bid chính xác => đúng tick kể cả nhiều tick/giây (điểm gap). -1 nếu ko thấy.
double RealSpreadPrice(int32_t server_sec, double bid) {
    if (!g_active || g_bins.empty()) return -1.0;
    // Đảo tz: utc = server - TzShift(utc). Dùng ước lượng utc0 để tra khoảng DST.
    int64_t utc0 = (int64_t)server_sec - (int64_t)g_gmt * 3600;
    int64_t utc  = (int64_t)server_sec - TzShift(utc0);
    int64_t utc_ms = utc * 1000;
    const int64_t W = 5000;   // cửa sổ ±5s
    double best_ask = -1.0, best_db = 1e300;
    EnterCriticalSection(&g_cs);
    for (const Bin& bn : g_bins) {
        if (bn.nrec == 0) continue;
        int64_t t0 = *reinterpret_cast<const int64_t*>(bn.base + 16);
        int64_t tN = *reinterpret_cast<const int64_t*>(bn.base + 16 + (bn.nrec - 1) * 24);
        if (utc_ms + W < t0 || utc_ms - W > tN) continue;
        // binary search: tick đầu tiên có time >= utc_ms - W
        uint64_t lo = 0, hi = bn.nrec;
        while (lo < hi) {
            uint64_t m = (lo + hi) >> 1;
            int64_t tm = *reinterpret_cast<const int64_t*>(bn.base + 16 + m * 24);
            if (tm < utc_ms - W) lo = m + 1; else hi = m;
        }
        for (uint64_t i = lo; i < bn.nrec; ++i) {
            const uint8_t* p = bn.base + 16 + i * 24;
            int64_t tm = *reinterpret_cast<const int64_t*>(p);
            if (tm > utc_ms + W) break;
            double bb = *reinterpret_cast<const double*>(p + 8);
            double db = bb - bid; if (db < 0) db = -db;
            if (db < best_db) {
                best_db = db;
                best_ask = *reinterpret_cast<const double*>(p + 16);
            }
        }
    }
    LeaveCriticalSection(&g_cs);
    if (best_ask < 0.0 || best_db > 1e-6) return -1.0;   // ko khớp bid chính xác
    double sp = best_ask - bid;
    return sp > 0.0 ? sp : 0.0;
}

} // namespace vfxt
