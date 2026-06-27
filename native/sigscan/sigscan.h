// ============================================================================
//  Module 5C — SigScanner (header)
//  TDS_CLONE_ARCHITECTURE.md — mục 5.3.
// ----------------------------------------------------------------------------
//  AOB (Array-Of-Bytes) signature scanner: tìm một mẫu byte có wildcard trong
//  một vùng nhớ (file PE trên đĩa HOẶC memory của tiến trình đang chạy).
//
//  SỰ THẬT cần nắm (mục 5.3): KHÔNG có cách auto tìm hàm "chưa biết". Quy trình:
//    Neo vào thứ ĐÃ BIẾT (string/import/hằng) -> quét signature -> ra địa chỉ.
//  Tool này tự động hoá BƯỚC quét signature; việc xác định ĐÚNG hàm là phán đoán
//  RE của bạn (Ghidra/x64dbg).
// ============================================================================
#pragma once
#include <cstdint>
#include <cstddef>
#include <vector>
#include <string>

namespace sigscan {

// Một byte trong pattern: hoặc giá trị cụ thể, hoặc wildcard (??).
struct PatByte {
    uint8_t value;
    bool    wildcard;
};

// Parse chuỗi pattern dạng "48 8B ?? ?? 89 ?? E8" -> vector<PatByte>.
//  - byte hex 2 ký tự  -> value cụ thể
//  - "??" hoặc "?"     -> wildcard
std::vector<PatByte> ParsePattern(const std::string& pattern);

// Tìm MỌI vị trí khớp pattern trong [base, base+size). Trả về danh sách offset
// (tính từ base). Khớp khi mọi byte không-wildcard trùng.
std::vector<size_t> FindAll(const uint8_t* base, size_t size,
                            const std::vector<PatByte>& pattern);

// Tiện ích: tìm match đầu tiên, trả con trỏ (hoặc nullptr).
const uint8_t* FindFirst(const uint8_t* base, size_t size,
                         const std::vector<PatByte>& pattern);

// --- Quét trên FILE PE (tính RVA) -------------------------------------------
struct PeScanResult {
    size_t   fileOffset;   // offset trong file
    uint64_t rva;          // RVA tương ứng (dựa trên section)
    uint64_t preferredVa;  // ImageBase + rva (địa chỉ ưa thích)
};

// Đọc file PE, quét pattern trong các section thực thi, trả mọi match kèm RVA.
std::vector<PeScanResult> ScanPeFile(const std::wstring& path,
                                     const std::string& pattern);

} // namespace sigscan
