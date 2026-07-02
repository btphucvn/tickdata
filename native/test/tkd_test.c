/* tkd_test.c — CHUNG MINH doc file .tkd (v2 = raw DEFLATE) bang puff.c.
 *
 * Doc 1 file .tkd, parse header, puff-giai nen than -> count*24 byte record,
 * in count + record dau/cuoi. So khop voi Python (daystore) -> xac nhan decoder
 * DUNG truoc khi dua vao fxt_virtual.cpp (hot-path 32-bit).
 *
 * .tkd header 36B: "TKD1"(4) u32 ver, i64 day_ms, i64 first_ms, i64 last_ms, u32 count
 *   -> than: v2 = RAW DEFLATE cua count x (i64 time_ms, f64 bid, f64 ask) = 24B.
 *
 * Build (32-bit, giong hook):
 *   cl /nologo /O2 tkd_test.c ..\third_party\puff\puff.c /I..\third_party\puff
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "puff.h"

int main(int argc, char** argv) {
    if (argc < 2) { printf("usage: tkd_test <file.tkd>\n"); return 2; }
    FILE* f = fopen(argv[1], "rb");
    if (!f) { printf("khong mo duoc %s\n", argv[1]); return 2; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char* buf = (unsigned char*)malloc(sz);
    if (fread(buf, 1, sz, f) != (size_t)sz) { printf("read loi\n"); return 2; }
    fclose(f);

    if (sz < 36 || memcmp(buf, "TKD1", 4) != 0) { printf("magic sai\n"); return 1; }
    uint32_t ver, count;
    memcpy(&ver, buf + 4, 4);
    memcpy(&count, buf + 32, 4);
    printf("version=%u count=%u filesize=%ld\n", ver, count, sz);
    if (ver != 2) { printf("KHONG phai v2(deflate) -> can reencode\n"); return 1; }

    unsigned long destlen = (unsigned long)count * 24;
    unsigned char* out = (unsigned char*)malloc(destlen ? destlen : 1);
    unsigned long srclen = (unsigned long)(sz - 36);
    int rc = puff(out, &destlen, buf + 36, &srclen);
    printf("puff rc=%d  destlen=%lu (mong doi %lu)  srcused=%lu\n",
           rc, destlen, (unsigned long)count * 24, srclen);
    if (rc != 0 || destlen != (unsigned long)count * 24) {
        printf("GIAI NEN LOI\n"); return 1;
    }
    /* in record dau/cuoi */
    int64_t t0, tn; double b0, a0, bn, an;
    memcpy(&t0, out + 0, 8); memcpy(&b0, out + 8, 8); memcpy(&a0, out + 16, 8);
    unsigned long off = (unsigned long)(count - 1) * 24;
    memcpy(&tn, out + off, 8); memcpy(&bn, out + off + 8, 8); memcpy(&an, out + off + 16, 8);
    printf("first: t=%lld bid=%.6f ask=%.6f\n", (long long)t0, b0, a0);
    printf("last : t=%lld bid=%.6f ask=%.6f\n", (long long)tn, bn, an);
    /* checksum don gian de so voi Python: tong t + tong (bid*1e6) */
    double sb = 0; long long st = 0;
    for (uint32_t i = 0; i < count; i++) {
        int64_t t; double b;
        memcpy(&t, out + (size_t)i * 24, 8);
        memcpy(&b, out + (size_t)i * 24 + 8, 8);
        st += t; sb += b;
    }
    printf("checksum: sum_t=%lld sum_bid=%.3f\n", st, sb);
    free(out); free(buf);
    return 0;
}
