// vfxt_gen_test.cpp — sinh FXT qua vfxt::Serve (CODE NATIVE THAT: fxt_virtual.cpp doc
// .tkd + puff) roi ghi ra file, de so BYTE-BYTE voi build_fxt cua Python (reference da
// khop TDS). Neu trung khit -> native giai doan B sinh record DUNG.
//
//   vfxt_gen_test <active.fxtv> <out.fxt> <total_size_bytes>
#include "fxt_virtual.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <vector>

int wmain(int argc, wchar_t** argv) {
    if (argc < 4) { printf("usage: vfxt_gen_test <cfg> <out> <total_size>\n"); return 2; }
    if (!vfxt::LoadPath(argv[1])) { printf("LoadPath FAIL: %ls\n", argv[1]); return 1; }
    uint64_t total = (uint64_t)_wcstoui64(argv[3], nullptr, 10);
    std::vector<uint8_t> buf(total);
    const uint32_t CH = 1u << 20;   // 1MB moi lan (giong MT4 doc tung khuc)
    uint64_t off = 0;
    while (off < total) {
        uint32_t n = (uint32_t)((total - off < CH) ? (total - off) : CH);
        vfxt::Serve(off, buf.data() + off, n);
        off += n;
    }
    FILE* f = _wfopen(argv[2], L"wb");
    if (!f) { printf("mo out FAIL\n"); return 1; }
    fwrite(buf.data(), 1, (size_t)total, f);
    fclose(f);
    printf("da ghi %llu byte -> %ls\n", (unsigned long long)total, argv[2]);
    return 0;
}
