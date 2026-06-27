// ============================================================================
//  Module 5A — DLL Injector  (Cách C3 "y hệt TDS", phần nạp DLL)
//  TDS_CLONE_ARCHITECTURE.md — mục 5.1.
// ----------------------------------------------------------------------------
//  Nạp tdshook.dll vào tiến trình terminal.exe bằng kỹ thuật chuẩn:
//      OpenProcess -> VirtualAllocEx -> WriteProcessMemory(path) ->
//      CreateRemoteThread(LoadLibraryW, path).
//
//  ⚠️ BITNESS: injector PHẢI cùng bitness với target. MT4 = 32-bit => build x86.
//     Inject DLL 64-bit vào tiến trình 32-bit là BẤT KHẢ THI.
//  ⚠️ Chạy AS ADMINISTRATOR (PROCESS_ALL_ACCESS).  Dùng UTF-16 (LoadLibraryW).
//
//  Cách dùng:
//      injector.exe --pid 1234   C:\path\tdshook.dll
//      injector.exe --name terminal.exe   C:\path\tdshook.dll
// ============================================================================

#include <windows.h>
#include <tlhelp32.h>
#include <cstdio>
#include <cwchar>
#include <string>

// ----------------------------------------------------------------------------
//  Tìm PID theo tên tiến trình (vd "terminal.exe"). Trả 0 nếu không thấy.
// ----------------------------------------------------------------------------
static DWORD FindPidByName(const wchar_t* exeName)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;

    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    DWORD pid = 0;
    if (Process32FirstW(snap, &entry)) {
        do {
            if (_wcsicmp(entry.szExeFile, exeName) == 0) {
                pid = entry.th32ProcessID;
                break;
            }
        } while (Process32NextW(snap, &entry));
    }
    CloseHandle(snap);
    return pid;
}

// ----------------------------------------------------------------------------
//  Inject DLL (đường dẫn UTF-16) vào tiến trình pid. Trả true nếu thành công.
// ----------------------------------------------------------------------------
static bool InjectDll(DWORD pid, const std::wstring& dllPath)
{
    // 1) Mở tiến trình với quyền đủ để cấp phát + ghi + tạo thread.
    HANDLE proc = OpenProcess(PROCESS_ALL_ACCESS, FALSE, pid);
    if (!proc) {
        fwprintf(stderr, L"OpenProcess thất bại (err=%lu). Chạy as Administrator?\n",
                 GetLastError());
        return false;
    }

    // 2) Cấp phát vùng nhớ trong tiến trình đích để chứa chuỗi đường dẫn DLL.
    const SIZE_T bytes = (dllPath.size() + 1) * sizeof(wchar_t);
    void* remoteMem = VirtualAllocEx(proc, nullptr, bytes,
                                     MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remoteMem) {
        fwprintf(stderr, L"VirtualAllocEx thất bại (err=%lu)\n", GetLastError());
        CloseHandle(proc);
        return false;
    }

    // 3) Ghi đường dẫn DLL vào vùng nhớ vừa cấp.
    if (!WriteProcessMemory(proc, remoteMem, dllPath.c_str(), bytes, nullptr)) {
        fwprintf(stderr, L"WriteProcessMemory thất bại (err=%lu)\n", GetLastError());
        VirtualFreeEx(proc, remoteMem, 0, MEM_RELEASE);
        CloseHandle(proc);
        return false;
    }

    // 4) Lấy địa chỉ LoadLibraryW trong kernel32. Vì kernel32 nạp cùng địa chỉ
    //    base ở mọi tiến trình (ASLR per-boot, không per-process cho system DLL),
    //    địa chỉ này dùng được cho tiến trình đích.
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    auto loadLib = (LPTHREAD_START_ROUTINE)GetProcAddress(k32, "LoadLibraryW");
    if (!loadLib) {
        fwprintf(stderr, L"Không lấy được LoadLibraryW\n");
        VirtualFreeEx(proc, remoteMem, 0, MEM_RELEASE);
        CloseHandle(proc);
        return false;
    }

    // 5) Tạo thread từ xa chạy LoadLibraryW(remoteMem) -> DLL được nạp & DllMain chạy.
    HANDLE thread = CreateRemoteThread(proc, nullptr, 0, loadLib, remoteMem, 0, nullptr);
    if (!thread) {
        fwprintf(stderr, L"CreateRemoteThread thất bại (err=%lu)\n", GetLastError());
        VirtualFreeEx(proc, remoteMem, 0, MEM_RELEASE);
        CloseHandle(proc);
        return false;
    }

    // 6) Đợi LoadLibraryW xong. Giá trị trả (HMODULE) cắt còn 32-bit trên x86.
    WaitForSingleObject(thread, INFINITE);
    DWORD remoteModule = 0;
    GetExitCodeThread(thread, &remoteModule);

    CloseHandle(thread);
    VirtualFreeEx(proc, remoteMem, 0, MEM_RELEASE);
    CloseHandle(proc);

    if (remoteModule == 0) {
        fwprintf(stderr, L"LoadLibraryW trả 0 — DLL không nạp được trong tiến trình đích.\n");
        return false;
    }
    wprintf(L"Inject thành công. HMODULE từ xa = 0x%08X\n", remoteModule);
    return true;
}

// ----------------------------------------------------------------------------
//  Chuyển đường dẫn DLL tương đối -> tuyệt đối (LoadLibraryW cần path đầy đủ thì chắc).
// ----------------------------------------------------------------------------
static std::wstring ToAbsolute(const wchar_t* path)
{
    wchar_t buf[MAX_PATH] = {0};
    DWORD n = GetFullPathNameW(path, MAX_PATH, buf, nullptr);
    if (n == 0 || n >= MAX_PATH) return path;  // fallback giữ nguyên
    return buf;
}

int wmain(int argc, wchar_t** argv)
{
    if (argc < 4) {
        wprintf(L"Cách dùng:\n"
                L"  injector.exe --pid  <PID>          <duong_dan_dll>\n"
                L"  injector.exe --name <ten_exe>      <duong_dan_dll>\n"
                L"Ví dụ:\n"
                L"  injector.exe --name terminal.exe   C:\\dev\\tds-clone\\tdshook.dll\n");
        return 2;
    }

    DWORD pid = 0;
    if (_wcsicmp(argv[1], L"--pid") == 0) {
        pid = (DWORD)_wtoi(argv[2]);
    } else if (_wcsicmp(argv[1], L"--name") == 0) {
        pid = FindPidByName(argv[2]);
        if (pid == 0) {
            fwprintf(stderr, L"Không tìm thấy tiến trình '%ls'.\n", argv[2]);
            return 1;
        }
    } else {
        fwprintf(stderr, L"Tham số đầu phải là --pid hoặc --name.\n");
        return 2;
    }

    std::wstring dll = ToAbsolute(argv[3]);
    wprintf(L"Target PID=%lu, DLL=%ls\n", pid, dll.c_str());
    return InjectDll(pid, dll) ? 0 : 1;
}
