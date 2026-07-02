// FXT ẢO (virtual) — provider sinh record FXT on-the-fly từ tick .bin (giống TDS).
// Xem fxt_virtual.cpp. Config do python/fxt_virtual.py ghi (data\active.fxtv).
#pragma once
#include <windows.h>
#include <cstdint>

namespace vfxt {

// Nạp/refresh config data\active.fxtv (tính đường dẫn từ module DLL). Trả true nếu active.
bool Load(HMODULE hMod);

// Đang ở chế độ FXT ảo?
bool Active();

// basename của `path` có khớp placeholder FXT ảo đang active không? (case-insensitive)
bool MatchName(LPCWSTR path);

// Ghi byte [offset, offset+len) của FXT ảo vào buf (header 728B + record sinh động).
void Serve(uint64_t offset, void* buf, uint32_t len);

// Spread THẬT (ask-bid, đơn vị GIÁ) của tick khớp (server_sec, bid) đọc thẳng từ .bin.
// Match theo bid CHÍNH XÁC trong cửa sổ thời gian -> đúng tick kể cả nhiều tick/giây.
// Trả -1 nếu không tìm thấy tick khớp bid. Dùng cho "use my spread" per-tick chuẩn.
double RealSpreadPrice(int32_t server_sec, double bid);

} // namespace vfxt
