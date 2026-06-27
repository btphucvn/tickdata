// ============================================================================
//  Module 5C — SigScanner tool dòng lệnh
// ----------------------------------------------------------------------------
//  Quét 1 file PE (vd terminal.exe) tìm AOB signature, in ra fileOffset/RVA/VA.
//  Đọc signatures.json (do Ghidra script sinh) để quét hàng loạt.
//
//  Cách dùng:
//    sigscan.exe terminal.exe "48 8B ?? ?? 89 ?? E8"
//    sigscan.exe --json signatures.json terminal.exe
//
//  (Parser JSON ở đây tối giản, chỉ đọc cặp "name"/"pattern" — đủ cho file do
//   ghidra_scripts/find_candidates.py sinh; không phải JSON parser tổng quát.)
// ============================================================================
#include "sigscan.h"

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

// Chuyển UTF-8/ANSI std::string -> std::wstring (đường dẫn file).
static std::wstring Widen(const std::string& s)
{
    return std::wstring(s.begin(), s.end());
}

static void ScanAndPrint(const std::wstring& pe, const std::string& name,
                         const std::string& pattern)
{
    auto results = sigscan::ScanPeFile(pe, pattern);
    printf("== %s ==\n  pattern: %s\n", name.c_str(), pattern.c_str());
    if (results.empty()) {
        printf("  (không match — sai pattern hoặc khác build)\n");
        return;
    }
    if (results.size() > 1) {
        printf("  ⚠️ %zu match (signature chưa đủ duy nhất — nên kéo dài/đặc trưng hơn)\n",
               results.size());
    }
    for (const auto& r : results) {
        printf("  fileOffset=0x%08zX  RVA=0x%08llX  VA=0x%08llX\n",
               r.fileOffset, (unsigned long long)r.rva,
               (unsigned long long)r.preferredVa);
    }
}

// Trích value của key trong 1 dòng JSON kiểu  "key": "value"
static bool ExtractJsonString(const std::string& line, const std::string& key,
                              std::string& out)
{
    std::string needle = "\"" + key + "\"";
    size_t k = line.find(needle);
    if (k == std::string::npos) return false;
    size_t colon = line.find(':', k);
    if (colon == std::string::npos) return false;
    size_t q1 = line.find('"', colon);
    if (q1 == std::string::npos) return false;
    size_t q2 = line.find('"', q1 + 1);
    if (q2 == std::string::npos) return false;
    out = line.substr(q1 + 1, q2 - q1 - 1);
    return true;
}

int main(int argc, char** argv)
{
    if (argc >= 4 && std::string(argv[1]) == "--json") {
        // sigscan --json signatures.json terminal.exe
        const std::string jsonPath = argv[2];
        const std::wstring pe = Widen(argv[3]);

        std::ifstream jf(jsonPath);
        if (!jf) { fprintf(stderr, "Không mở được %s\n", jsonPath.c_str()); return 1; }

        std::string line, curName, curPattern;
        while (std::getline(jf, line)) {
            std::string v;
            if (ExtractJsonString(line, "name", v))    curName = v;
            if (ExtractJsonString(line, "pattern", v)) {
                curPattern = v;
                if (!curName.empty())
                    ScanAndPrint(pe, curName, curPattern);
                curName.clear();
            }
        }
        return 0;
    }

    if (argc == 3) {
        // sigscan terminal.exe "48 8B ?? ..."
        ScanAndPrint(Widen(argv[1]), "(cli)", argv[2]);
        return 0;
    }

    printf("Cách dùng:\n"
           "  sigscan.exe <pe_file> \"48 8B ?? E8\"\n"
           "  sigscan.exe --json signatures.json <pe_file>\n");
    return 2;
}
