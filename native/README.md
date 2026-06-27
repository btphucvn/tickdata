# Native modules (4 & 5) — build trên Windows

> ⚠️ **Không build được trên WSL/Linux.** WinAPI + DLL injection cần Windows thật.
> Xem mục 0.5 của [`../TDS_CLONE_ARCHITECTURE.md`](../TDS_CLONE_ARCHITECTURE.md).
> **Bitness bắt buộc: x86 (32-bit)** vì MT4 `terminal.exe` là 32-bit.

## Thành phần

| Thư mục | Module | Output | Vai trò |
|---------|--------|--------|---------|
| `ea_helper_dll/` | 4 (C2) | `tdsclone.dll` | Export `__stdcall` cho EA `#import` — variable spread **sạch, không inject** |
| `injector/` | 5A (C3) | `injector.exe` | Nạp `tdshook.dll` vào `terminal.exe` |
| `hook/` | 5B (C3) | `tdshook.dll` | Inline-hook hàm giá của tester, override spread theo shared memory |
| `sigscan/` | 5C | `sigscan.exe` + lib | Quét AOB signature tìm địa chỉ hàm trong PE |

## Build (PowerShell, Visual Studio 2022 + CMake)

```powershell
# 1. (cho hook) thêm MinHook
git submodule add https://github.com/TsudaKageyu/minhook native/third_party/minhook

# 2. Cấu hình & build — ÉP 32-bit cho khớp terminal.exe
cmake -S native -B native\build -A Win32
cmake --build native\build --config Release
```

Output `.dll`/`.exe` nằm trong `native\build\<module>\Release\`.

## Triển khai

* **Module 4 (C2 — khuyến nghị):**
  1. Copy `tdsclone.dll` -> `<terminal>\MQL4\Libraries\`
  2. Sinh `.tdspread` bằng `tdsclone build ...`, copy vào `<terminal>\MQL4\Files\`
  3. Dùng EA mẫu [`../mql4/TDSCloneTemplate.mq4`](../mql4/TDSCloneTemplate.mq4),
     bật **Allow DLL imports** trong Strategy Tester.

* **Module 5 (C3 — "trong suốt" nhưng fragile):**
  1. Python ghi shared memory: `tdsclone.ipc.publish_spread_shm(...)` (Windows).
  2. Tìm hàm mục tiêu: chạy [`../ghidra_scripts/find_candidates.py`](../ghidra_scripts/find_candidates.py)
     trong Ghidra -> `signatures.json`; verify bằng x64dbg.
  3. `sigscan.exe --json signatures.json terminal.exe` để xác nhận địa chỉ.
  4. Cập nhật `TARGET_SIGNATURE` trong `hook/tdshook.cpp`, build lại.
  5. Mở MT4, `injector.exe --name terminal.exe <path>\tdshook.dll` (**as Administrator**).

> **Khuyến nghị:** đi Module 4 trước (đạt ~90% giá trị, 100% sạch). Chỉ làm Module 5
> nếu bắt buộc variable spread trong suốt tuyệt đối với EA.
