// ============================================================================
//  Module 5C — SigScanner (cài đặt)
//  TDS_CLONE_ARCHITECTURE.md — mục 5.3.
// ----------------------------------------------------------------------------
//  Phần lõi (ParsePattern/FindAll) thuần C++ chuẩn, test được mọi nền tảng.
//  Phần ScanPeFile dùng cấu trúc PE của Windows (winnt.h) — chỉ build trên Windows.
// ============================================================================
#include "sigscan.h"

#include <cctype>
#include <cstring>
#include <fstream>
#include <sstream>

#ifdef _WIN32
#  include <windows.h>   // IMAGE_DOS_HEADER, IMAGE_NT_HEADERS...
#endif

namespace sigscan {

// ----------------------------------------------------------------------------
//  Parse "48 8B ?? E8" -> [{0x48,no},{0x8B,no},{0,yes},{0xE8,no}]
// ----------------------------------------------------------------------------
std::vector<PatByte> ParsePattern(const std::string& pattern)
{
    std::vector<PatByte> out;
    std::istringstream iss(pattern);
    std::string token;
    while (iss >> token) {
        if (token == "??" || token == "?") {
            out.push_back({0, true});
        } else {
            // Chấp nhận 1-2 ký tự hex.
            unsigned int v = 0;
            std::istringstream hs(token);
            hs >> std::hex >> v;
            out.push_back({(uint8_t)(v & 0xFF), false});
        }
    }
    return out;
}

// ----------------------------------------------------------------------------
//  So khớp pattern tại vị trí p.
// ----------------------------------------------------------------------------
static inline bool MatchAt(const uint8_t* p, const std::vector<PatByte>& pat)
{
    for (size_t i = 0; i < pat.size(); ++i) {
        if (!pat[i].wildcard && p[i] != pat[i].value)
            return false;
    }
    return true;
}

std::vector<size_t> FindAll(const uint8_t* base, size_t size,
                            const std::vector<PatByte>& pattern)
{
    std::vector<size_t> hits;
    if (pattern.empty() || size < pattern.size()) return hits;
    const size_t last = size - pattern.size();
    for (size_t i = 0; i <= last; ++i) {
        if (MatchAt(base + i, pattern))
            hits.push_back(i);
    }
    return hits;
}

const uint8_t* FindFirst(const uint8_t* base, size_t size,
                         const std::vector<PatByte>& pattern)
{
    auto hits = FindAll(base, size, pattern);
    return hits.empty() ? nullptr : base + hits.front();
}

// ----------------------------------------------------------------------------
//  Quét file PE: ánh xạ section, quét pattern, quy đổi fileOffset -> RVA.
// ----------------------------------------------------------------------------
std::vector<PeScanResult> ScanPeFile(const std::wstring& path,
                                     const std::string& pattern)
{
    std::vector<PeScanResult> results;
#ifdef _WIN32
    // Đọc toàn bộ file vào bộ nhớ.
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) return results;
    std::streamsize sz = f.tellg();
    f.seekg(0, std::ios::beg);
    std::vector<uint8_t> buf((size_t)sz);
    if (!f.read((char*)buf.data(), sz)) return results;

    auto dos = (IMAGE_DOS_HEADER*)buf.data();
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return results;       // "MZ"
    auto nt = (IMAGE_NT_HEADERS*)(buf.data() + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return results;       // "PE\0\0"

    const uint64_t imageBase = nt->OptionalHeader.ImageBase;
    auto pat = ParsePattern(pattern);

    // Duyệt từng section, chỉ quét section có cờ EXECUTE (chứa code).
    auto sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++sec) {
        if (!(sec->Characteristics & IMAGE_SCN_MEM_EXECUTE)) continue;

        const size_t rawOff = sec->PointerToRawData;
        const size_t rawSize = sec->SizeOfRawData;
        if (rawOff + rawSize > buf.size()) continue;

        auto hits = FindAll(buf.data() + rawOff, rawSize, pat);
        for (size_t h : hits) {
            PeScanResult r{};
            r.fileOffset  = rawOff + h;
            r.rva         = sec->VirtualAddress + h;  // map raw->RVA trong cùng section
            r.preferredVa = imageBase + r.rva;
            results.push_back(r);
        }
    }
#else
    (void)path; (void)pattern;   // ScanPeFile chỉ có nghĩa trên Windows.
#endif
    return results;
}

} // namespace sigscan
