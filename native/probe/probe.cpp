// probe.cpp - Sampling profiler
// Every 10ms: read EIP of all MT4 threads → histogram
// After 30s backtest: top addresses = tick processing hot path
#include <windows.h>
#include <tlhelp32.h>
#include <io.h>
#include <fcntl.h>
#include <cstdint>
#include <cstdio>
#include <algorithm>

static FILE*            g_log    = nullptr;
static CRITICAL_SECTION g_cs     = {};
static volatile bool    g_running = true;

struct EipBin { uint32_t eip; int count; };
static const int MAX_BINS = 8192;
static EipBin    g_bins[MAX_BINS];
static int       g_nBins   = 0;
static int       g_samples = 0;

static void AddSample(uint32_t eip) {
    for (int i = 0; i < g_nBins; i++) {
        if (g_bins[i].eip == eip) { g_bins[i].count++; return; }
    }
    if (g_nBins < MAX_BINS) g_bins[g_nBins++] = {eip, 1};
}

static DWORD WINAPI SamplerThread(LPVOID)
{
    Sleep(500); // wait for MT4 to settle

    DWORD pid   = GetCurrentProcessId();
    DWORD myTid = GetCurrentThreadId();

    // Sample for 60 seconds (user runs backtest during this window)
    for (int s = 0; s < 6000 && g_running; s++) {
        Sleep(10);

        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        if (snap == INVALID_HANDLE_VALUE) continue;

        THREADENTRY32 te = {sizeof(te)};
        if (Thread32First(snap, &te)) do {
            if (te.th32OwnerProcessID != pid) continue;
            if (te.th32ThreadID == myTid)     continue;

            HANDLE h = OpenThread(THREAD_GET_CONTEXT | THREAD_SUSPEND_RESUME,
                                  FALSE, te.th32ThreadID);
            if (!h) continue;

            if (SuspendThread(h) != (DWORD)-1) {
                CONTEXT ctx = {}; ctx.ContextFlags = CONTEXT_CONTROL;
                if (GetThreadContext(h, &ctx))
                    AddSample(ctx.Eip);
                ResumeThread(h);
            }
            CloseHandle(h);
        } while (Thread32Next(snap, &te));

        CloseHandle(snap);
        g_samples++;
    }

    // Sort descending by count
    std::sort(g_bins, g_bins + g_nBins,
        [](const EipBin& a, const EipBin& b){ return a.count > b.count; });

    uintptr_t base = (uintptr_t)GetModuleHandleW(nullptr);
    // Correct declared ImageBase (from PE header, verified = 0x00400000).
    static const uintptr_t STATIC_BASE = 0x00400000u;

    EnterCriticalSection(&g_cs);
    if (g_log) {
        fprintf(g_log, "=== Sampling done: %d samples, %d unique addresses ===\n\n",
                g_samples, g_nBins);
        fprintf(g_log, "  actualBase=0x%08X  declaredBase=0x%08X  aslrDelta=%+d\n\n",
                (uint32_t)base, (uint32_t)STATIC_BASE, (int)(base - STATIC_BASE));

        fprintf(g_log, "Rank  EIP(runtime)  EIP(static)   Count  Module\n");
        fprintf(g_log, "----  -----------  -----------   -----  ------\n");
        int top = g_nBins < 60 ? g_nBins : 60;
        for (int i = 0; i < top; i++) {
            uint32_t eip = g_bins[i].eip;
            bool inExe = (eip >= (uint32_t)base && eip < (uint32_t)base + 0x3000000u);
            // Static VA (matches Ghidra address): runtime - actualBase + declaredBase
            uint32_t staticEip = inExe ? (uint32_t)(eip - base + declaredBase) : 0;
            fprintf(g_log, "[%3d]  0x%08X   %s   %5d  %s\n",
                    i+1, eip,
                    inExe ? (char*)"" : "            ",
                    g_bins[i].count,
                    inExe ? "terminal.exe" : "ntdll/kernel32/...");
            if (inExe)
                fprintf(g_log, "       static=0x%08X\n", staticEip);
        }
        fflush(g_log);
    }
    LeaveCriticalSection(&g_cs);
    return 0;
}

static DWORD WINAPI InitThread(LPVOID)
{
    Sleep(300);
    InitializeCriticalSection(&g_cs);

    char logPath[MAX_PATH];
    GetTempPathA(MAX_PATH, logPath); strcat_s(logPath, "tds_probe.log");
    HANDLE hFile = CreateFileA(logPath, GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                               CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (hFile != INVALID_HANDLE_VALUE) {
        int fd = _open_osfhandle((intptr_t)hFile, 0);
        if (fd >= 0) g_log = _fdopen(fd, "w");
    }
    if (g_log) {
        fprintf(g_log, "=== TDS Sampling Profiler ===\n");
        fprintf(g_log, "Sampling 60s @ 10ms interval. Start backtest NOW.\n\n");
        fflush(g_log);
    }

    CreateThread(nullptr, 0, SamplerThread, nullptr, 0, nullptr);
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hMod, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hMod);
        CreateThread(nullptr, 0, InitThread, nullptr, 0, nullptr);
    }
    else if (reason == DLL_PROCESS_DETACH) {
        g_running = false;
        if (g_log) { fprintf(g_log, "\n(unloaded early)\n"); fclose(g_log); }
        DeleteCriticalSection(&g_cs);
    }
    return TRUE;
}
