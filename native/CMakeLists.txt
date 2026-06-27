# =============================================================================
#  TDS-Clone — native (Module 4 & 5) — build TRÊN WINDOWS bằng MSVC.
# -----------------------------------------------------------------------------
#  ⚠️ BITNESS: MT4 terminal.exe là 32-bit => MỌI thứ cắm vào MT4 phải x86 (Win32).
#     Cấu hình:   cmake -S native -B native/build -A Win32
#                 cmake --build native/build --config Release
#
#  KHÔNG build được trên WSL/Linux (WinAPI + DLL injection cần Windows thật).
#  Xem mục 0.5 của TDS_CLONE_ARCHITECTURE.md.
# =============================================================================
cmake_minimum_required(VERSION 3.20)
project(tdsclone_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Cảnh báo nếu cấu hình không phải 32-bit (chỉ hợp lệ khi target MT4).
if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(WARNING
        "Đang build 64-bit. MT4 là 32-bit; injector/hook 64-bit KHÔNG inject được "
        "vào terminal.exe. Hãy cấu hình lại với '-A Win32'.")
endif()

# MinHook (Module 5B) — thêm như submodule vào third_party/minhook.
# git submodule add https://github.com/TsudaKageyu/minhook native/third_party/minhook
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/third_party/minhook/CMakeLists.txt")
    add_subdirectory(third_party/minhook)
    set(HAVE_MINHOOK ON)
else()
    message(STATUS "Chưa có MinHook -> bỏ qua target 'tdshook'. "
                   "Thêm submodule vào native/third_party/minhook để build hook.")
    set(HAVE_MINHOOK OFF)
endif()

add_subdirectory(ea_helper_dll)   # Module 4 — tdsclone.dll (#import)
add_subdirectory(injector)        # Module 5A — injector.exe
add_subdirectory(sigscan)         # Module 5C — sigscan.exe + lib

if(HAVE_MINHOOK)
    add_subdirectory(hook)        # Module 5B — tdshook.dll
endif()
