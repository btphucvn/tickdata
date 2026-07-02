// GUI injection — chen control vao panel Strategy Tester cua MT4.
//
// Co che (giong TDS):
//   1. Luong nen poll tim panel tester (parent cua nut "Start" id=1034).
//   2. Subclass panel (SetWindowLongPtr GWLP_WNDPROC).
//   3. PostMessage(WM_APP) -> control duoc tao TREN UI THREAD (an toan).
//   4. Checkbox "Use my tick data" -> bat/tat g_useMyTickData.
//   5. Button "Tick data settings" -> mo dialog cau hinh.
//
// Vi tri chen: hang "Visual mode" (y=129), ben phai (x tu ~1300) dang trong.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>
#include <shlwapi.h>
#include <shellapi.h>
#include <cstdio>
#include <cstring>
#include "gui_inject.h"

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "user32.lib")

// ---------------------------------------------------------------------------
// IDs cho control moi (tranh trung voi MT4: dung >= 0x9C40)
// ---------------------------------------------------------------------------
#define ID_USE_MYTICK    40001
#define ID_MYTICK_SETUP  40002
#define WM_APP_CREATE    (WM_APP + 7)
#define WM_APP_AUGMENT   (WM_APP + 8)   // re-them item "use my spread" + refresh flag

// ID control native cua MT4 tester (tu win_spy.py)
#define MT4_ID_START     1034
#define MT4_ID_SYMBOL    1347   // ComboBox symbol
#define MT4_ID_PERIOD    1228   // ComboBox period
#define MT4_ID_USEDATE   1023

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
namespace {
    HMODULE   g_self      = nullptr;
    HWND      g_panel     = nullptr;
    HWND      g_chk       = nullptr;
    HWND      g_btn       = nullptr;
    WNDPROC   g_origProc  = nullptr;
    bool      g_useMyTick = false;
    HWND      g_dlg       = nullptr;   // settings dialog (modeless)

    // Combobox Spread cua tester (native MT4) + item "use my spread"
    HWND         g_cbSpreadTester = nullptr;   // HWND combobox Spread
    int          g_cbSpreadId     = 0;         // control id cua no
    volatile bool g_useMySpread   = false;     // dang chon "use my spread"?

    wchar_t   g_projectRoot[MAX_PATH] = L"";
}

const wchar_t* const MYSPREAD_ITEM = L"use my spread";

bool GuiUseMyTickData() { return g_useMyTick; }
bool GuiUseMySpread()   { return g_useMySpread; }   // hook doc (bool cache, nhanh)

// ---------------------------------------------------------------------------
// Tinh project root tu duong dan DLL:
//   <root>\native\build\hook\Release\tdshook.dll  -> len 5 cap = <root>
// ---------------------------------------------------------------------------
static void ComputeProjectRoot()
{
    if (!GetModuleFileNameW(g_self, g_projectRoot, MAX_PATH)) {
        g_projectRoot[0] = 0;
        return;
    }
    for (int i = 0; i < 5; ++i)
        PathRemoveFileSpecW(g_projectRoot);   // bo filename + 4 thu muc
}

// ---------------------------------------------------------------------------
// Tim panel tester: nut "Start" (id 1034) -> parent
// ---------------------------------------------------------------------------
static HWND g_foundStart = nullptr;

static BOOL CALLBACK FindStartProc(HWND hwnd, LPARAM)
{
    if (GetDlgCtrlID(hwnd) == MT4_ID_START) {
        wchar_t txt[64] = {0};
        GetWindowTextW(hwnd, txt, 64);
        if (wcscmp(txt, L"Start") == 0) {
            g_foundStart = hwnd;
            return FALSE;   // dung
        }
    }
    return TRUE;
}

static HWND FindTesterPanel()
{
    g_foundStart = nullptr;
    EnumChildWindows(GetDesktopWindow(), FindStartProc, 0);
    if (!g_foundStart) {
        // Thu tim qua tat ca top-level cua process nay
        EnumWindows([](HWND top, LPARAM) -> BOOL {
            DWORD pid = 0;
            GetWindowThreadProcessId(top, &pid);
            if (pid == GetCurrentProcessId())
                EnumChildWindows(top, FindStartProc, 0);
            return g_foundStart ? FALSE : TRUE;
        }, 0);
    }
    return g_foundStart ? GetParent(g_foundStart) : nullptr;
}

// ---------------------------------------------------------------------------
// Doc symbol hien tai tu combo tester (vd "XAUUSD, US Dollar..." -> "XAUUSD")
// ---------------------------------------------------------------------------
static void ReadTesterSymbol(wchar_t* out, int cch)
{
    out[0] = 0;
    HWND combo = GetDlgItem(g_panel, MT4_ID_SYMBOL);
    if (!combo) return;
    wchar_t buf[128] = {0};
    GetWindowTextW(combo, buf, 128);
    // Cat tai dau cach hoac dau phay
    int i = 0;
    for (; buf[i] && i < cch - 1; ++i) {
        if (buf[i] == L',' || buf[i] == L' ') break;
        out[i] = buf[i];
    }
    out[i] = 0;
}

// ---------------------------------------------------------------------------
// Doc period tu combo tester -> so phut (M1=1, H4=240, D1=1440...)
// ---------------------------------------------------------------------------
static int ReadTesterPeriod()
{
    HWND combo = GetDlgItem(g_panel, MT4_ID_PERIOD);
    if (!combo) return 1;
    wchar_t buf[32] = {0};
    GetWindowTextW(combo, buf, 32);

    struct { const wchar_t* name; int min; } M[] = {
        {L"MN1",43200}, {L"MN",43200}, {L"W1",10080}, {L"D1",1440},
        {L"H4",240}, {L"H1",60}, {L"M30",30}, {L"M15",15},
        {L"M5",5}, {L"M1",1},
    };
    // So sanh prefix (mang da xep tu dai -> ngan de tranh M1 khop M15)
    for (auto& m : M)
        if (wcsncmp(buf, m.name, wcslen(m.name)) == 0)
            return m.min;
    return 1;   // mac dinh M1
}

// ---------------------------------------------------------------------------
// Doc 2 date-picker (From/To) tren hang "Use date"
// ---------------------------------------------------------------------------
struct DateScan { HWND from; HWND to; int fromX; int toX; };

static BOOL CALLBACK ScanDatesProc(HWND hwnd, LPARAM lp)
{
    wchar_t cls[64] = {0};
    GetClassNameW(hwnd, cls, 64);
    if (wcscmp(cls, L"SysDateTimePick32") == 0) {
        RECT r; GetWindowRect(hwnd, &r);
        POINT p{ r.left, r.top };
        ScreenToClient(g_panel, &p);
        // Chi lay 2 picker tren cao (y < 115) = From/To, bo qua "Skip to"
        if (p.y < 115) {
            auto* d = reinterpret_cast<DateScan*>(lp);
            if (!d->from || p.x < d->fromX) {
                d->to = d->from; d->toX = d->fromX;
                d->from = hwnd;  d->fromX = p.x;
            } else if (!d->to || p.x < d->toX) {
                d->to = hwnd; d->toX = p.x;
            }
        }
    }
    return TRUE;
}

static void ReadTesterDates(wchar_t* fromStr, wchar_t* toStr)
{
    fromStr[0] = toStr[0] = 0;
    DateScan d{ nullptr, nullptr, 99999, 99999 };
    EnumChildWindows(g_panel, ScanDatesProc, reinterpret_cast<LPARAM>(&d));

    SYSTEMTIME st;
    if (d.from && DateTime_GetSystemtime(d.from, &st) == GDT_VALID)
        swprintf(fromStr, 16, L"%04d-%02d-%02d", st.wYear, st.wMonth, st.wDay);
    if (d.to && DateTime_GetSystemtime(d.to, &st) == GDT_VALID)
        swprintf(toStr, 16, L"%04d-%02d-%02d", st.wYear, st.wMonth, st.wDay);
}

// ===========================================================================
// SETTINGS DIALOG (modeless popup)
// ===========================================================================
namespace {
    HWND g_edSymbol = nullptr;
    HWND g_edFrom   = nullptr;
    HWND g_edTo     = nullptr;
    HWND g_cbSpread = nullptr;
    HWND g_lblStat  = nullptr;
    HFONT g_dlgFont = nullptr;
}

#define ID_DL_SYMBOL  100
#define ID_DL_FROM    101
#define ID_DL_TO      102
#define ID_DL_SPREAD  103
#define ID_DL_RUN     104
#define ID_DL_CLOSE   105
#define ID_DL_STATUS  106
#define ID_DL_LIST    107
#define ID_DL_DELETE  108
#define ID_DL_VERIFY  109
#define ID_DL_REDL    110   // Xoa + tai lai fresh (delete + force download)

// Chay python tds_clone.py voi sub-command cho truoc, hien cua so cmd.
// keep_open: true = cmd /k (giu mo de xem ket qua), false = cmd /c
static bool RunPython(const wchar_t* subcmd_with_args, bool keep_open)
{
    wchar_t cmd[1280];
    swprintf(cmd, 1280,
        L"%s cd /d \"%s\" && python tds_clone.py %s",
        keep_open ? L"/k" : L"/c",
        g_projectRoot, subcmd_with_args);

    SHELLEXECUTEINFOW sei = { sizeof(sei) };
    sei.lpVerb = L"open";
    sei.lpFile = L"cmd.exe";
    sei.lpParameters = cmd;
    sei.nShow = SW_SHOWNORMAL;
    return ShellExecuteExW(&sei) != FALSE;
}

// Hook (tdshook.cpp) doc spread tu FILE data\active.spread. Khi prepare ghi file
// moi -> goi ham nay de hook map lai file moi (variable spread file-based).
extern void HookReopenSpread();

// Nut "Download + Build" trong dialog settings -> tai data vao store + build
static void RunPipeline()
{
    wchar_t sym[64], from[32], to[32];
    GetWindowTextW(g_edSymbol, sym, 64);
    GetWindowTextW(g_edFrom,   from, 32);
    GetWindowTextW(g_edTo,     to,   32);

    if (!sym[0] || !from[0] || !to[0]) {
        SetWindowTextW(g_lblStat, L"Thieu symbol/ngay!");
        return;
    }

    wchar_t args[512];
    swprintf(args, 512,
        L"run --symbol %s --from %s --to %s "
        L"& echo. & echo === XONG: dong cua so nay roi tick \"Use my tick data\" ===",
        sym, from, to);

    if (RunPython(args, true))
        SetWindowTextW(g_lblStat, L"Dang tai... xem cua so cmd.");
    else
        SetWindowTextW(g_lblStat, L"Loi: khong chay duoc python.");
}

// Nut "Xoa + Tai lai fresh": xoa sach data cu roi tai lai day du tu Dukascopy.
// Dung khi muon data moi nhat (Dukascopy da backfill) — GMT+2/DST=US nhu TDS.
static void RedownloadFresh()
{
    wchar_t sym[64], from[32], to[32];
    GetWindowTextW(g_edSymbol, sym, 64);
    GetWindowTextW(g_edFrom,   from, 32);
    GetWindowTextW(g_edTo,     to,   32);
    if (!sym[0] || !from[0] || !to[0]) {
        SetWindowTextW(g_lblStat, L"Thieu symbol/ngay!"); return;
    }
    wchar_t msg[256];
    swprintf(msg, 256,
        L"XOA toan bo data %s roi TAI LAI moi tu Dukascopy?\n(%s -> %s)\n\n"
        L"Data se day du + moi nhat. Timezone GMT+2/DST=US (giong TDS).",
        sym, from, to);
    if (MessageBoxW(g_dlg, msg, L"Xoa + Tai lai fresh",
                    MB_YESNO | MB_ICONQUESTION) != IDYES) return;

    // Chain: delete -> run (download+build+deploy). Sau delete store rong -> tai het.
    wchar_t args[768];
    swprintf(args, 768,
        L"delete --symbol %s "
        L"& python tds_clone.py run --symbol %s --from %s --to %s "
        L"& echo. & echo === XONG: dong cua so nay, roi tick \"Use my tick data\" ===",
        sym, sym, from, to);
    if (RunPython(args, true))
        SetWindowTextW(g_lblStat, L"Dang xoa + tai lai fresh... xem cua so cmd.");
    else
        SetWindowTextW(g_lblStat, L"Loi: khong chay duoc python.");
}

// Khi tick "Use my tick data": build+deploy FXT cho dung symbol/period/ngay tester.
static void PrepareFromTester()
{
    wchar_t sym[64] = {0}, from[32] = {0}, to[32] = {0};
    ReadTesterSymbol(sym, 64);
    ReadTesterDates(from, to);
    int period = ReadTesterPeriod();

    if (!sym[0] || !from[0] || !to[0]) {
        MessageBoxW(g_panel,
            L"Khong doc duoc symbol/ngay tu tester.\n"
            L"Hay bat 'Use date' va chon khoang ngay.",
            L"TDS Clone", MB_OK | MB_ICONWARNING);
        return;
    }

    // --- TDS-FLOW: KHONG build o day. Chi ghi MARKER -> hook (fxt_patch) se tu chay
    //     `prepare --virtual` LUC START (khi MT4 mo FXT), giong TDS. Tuc thi, khong cmd. ---
    char csym[64] = {0}, cfrom[32] = {0}, cto[32] = {0};
    WideCharToMultiByte(CP_ACP, 0, sym, -1, csym, 64, nullptr, nullptr);
    WideCharToMultiByte(CP_ACP, 0, from, -1, cfrom, 32, nullptr, nullptr);
    WideCharToMultiByte(CP_ACP, 0, to, -1, cto, 32, nullptr, nullptr);
    wchar_t mpath[MAX_PATH];
    swprintf(mpath, MAX_PATH, L"%s\\data\\pending.prep", g_projectRoot);
    FILE* f = nullptr;
    if (_wfopen_s(&f, mpath, L"wb") == 0 && f) {
        fprintf(f, "%s %s %s %d\n", csym, cfrom, cto, period);
        fclose(f);
        SetWindowTextW(g_lblStat, L"San sang. Nhan Start -> tu build data (giong TDS).");
    }
    HookReopenSpread();
}

// Khi BO TICK "Use my tick data": go FXT/HST cua ta -> MT4 dung data broker.
static void RestoreFromTester()
{
    HookReopenSpread();   // tat variable spread (hook khong con file)
    wchar_t sym[64] = {0};
    ReadTesterSymbol(sym, 64);
    int period = ReadTesterPeriod();
    if (!sym[0]) return;
    wchar_t args[256];
    swprintf(args, 256, L"restore --symbol %s --period %d", sym, period);
    RunPython(args, false);   // im lang, khong pause
}

// Xem danh sach symbol + ngay da tai
static void ShowDataList()
{
    RunPython(L"list & echo. & pause", false);
}

// Kiem tra do phu / model quality cho symbol+ngay trong dialog
static void VerifyData()
{
    wchar_t sym[64], from[32], to[32];
    GetWindowTextW(g_edSymbol, sym, 64);
    GetWindowTextW(g_edFrom,   from, 32);
    GetWindowTextW(g_edTo,     to,   32);
    if (!sym[0]) { SetWindowTextW(g_lblStat, L"Thieu symbol!"); return; }
    wchar_t args[400];
    swprintf(args, 400, L"verify --symbol %s --from %s --to %s & echo. & pause",
             sym, from, to);
    RunPython(args, false);
}

// Xoa data cua symbol trong o (hoi xac nhan truoc)
static void DeleteData()
{
    wchar_t sym[64];
    GetWindowTextW(g_edSymbol, sym, 64);
    if (!sym[0]) { SetWindowTextW(g_lblStat, L"Thieu symbol!"); return; }

    wchar_t msg[160];
    swprintf(msg, 160, L"Xoa toan bo data da tai cua %s?", sym);
    if (MessageBoxW(g_dlg, msg, L"Xac nhan xoa", MB_YESNO | MB_ICONQUESTION) != IDYES)
        return;

    wchar_t args[200];
    swprintf(args, 200, L"delete --symbol %s & echo. & pause", sym);
    RunPython(args, false);
    SetWindowTextW(g_lblStat, L"Da gui lenh xoa.");
}

static LRESULT CALLBACK DlgProc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case ID_DL_RUN:    RunPipeline();    return 0;
        case ID_DL_REDL:   RedownloadFresh(); return 0;
        case ID_DL_LIST:   ShowDataList();   return 0;
        case ID_DL_VERIFY: VerifyData();     return 0;
        case ID_DL_DELETE: DeleteData();     return 0;
        case ID_DL_CLOSE:  DestroyWindow(h); return 0;
        }
        break;
    case WM_DESTROY:
        g_dlg = nullptr;
        return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

static HWND MakeLabel(HWND parent, const wchar_t* txt, int x, int y, int w)
{
    HWND s = CreateWindowExW(0, L"STATIC", txt, WS_CHILD | WS_VISIBLE,
        x, y, w, 18, parent, nullptr, g_self, nullptr);
    SendMessageW(s, WM_SETFONT, (WPARAM)g_dlgFont, TRUE);
    return s;
}

static HWND MakeEdit(HWND parent, int id, const wchar_t* txt, int x, int y, int w)
{
    HWND e = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", txt,
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL,
        x, y, w, 22, parent, (HMENU)(INT_PTR)id, g_self, nullptr);
    SendMessageW(e, WM_SETFONT, (WPARAM)g_dlgFont, TRUE);
    return e;
}

static HWND MakeButton(HWND parent, int id, const wchar_t* txt, int x, int y, int w)
{
    HWND b = CreateWindowExW(0, L"BUTTON", txt,
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        x, y, w, 26, parent, (HMENU)(INT_PTR)id, g_self, nullptr);
    SendMessageW(b, WM_SETFONT, (WPARAM)g_dlgFont, TRUE);
    return b;
}

static void OpenSettingsDialog()
{
    if (g_dlg) { SetForegroundWindow(g_dlg); return; }

    static bool registered = false;
    const wchar_t* CLS = L"TDSCloneSettingsDlg";
    if (!registered) {
        WNDCLASSEXW wc = { sizeof(wc) };
        wc.lpfnWndProc   = DlgProc;
        wc.hInstance     = g_self;
        wc.hCursor       = LoadCursor(nullptr, IDC_ARROW);
        wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
        wc.lpszClassName = CLS;
        RegisterClassExW(&wc);
        registered = true;
    }

    if (!g_dlgFont)
        g_dlgFont = CreateFontW(-12, 0, 0, 0, FW_NORMAL, 0, 0, 0,
            DEFAULT_CHARSET, 0, 0, 0, 0, L"Segoe UI");

    RECT wa; SystemParametersInfo(SPI_GETWORKAREA, 0, &wa, 0);
    int W = 400, H = 390;
    int x = (wa.right - W) / 2, y = (wa.bottom - H) / 2;

    g_dlg = CreateWindowExW(WS_EX_TOOLWINDOW, CLS,
        L"TDS Clone - Tick Data Settings",
        WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
        x, y, W, H, g_panel, nullptr, g_self, nullptr);

    // Auto-doc symbol + ngay tu tester
    wchar_t sym[64] = L"EURUSD", from[32] = L"2024-01-01", to[32] = L"2024-01-31";
    ReadTesterSymbol(sym, 64);
    if (!sym[0]) wcscpy(sym, L"EURUSD");
    ReadTesterDates(from, to);
    if (!from[0]) wcscpy(from, L"2024-01-01");
    if (!to[0])   wcscpy(to,   L"2024-01-31");

    int lx = 16, ex = 130, ew = 238, yy = 14;
    MakeLabel(g_dlg, L"Du lieu tick that tu Dukascopy (giong TDS)", lx, yy, 360); yy += 26;

    MakeLabel(g_dlg, L"Symbol:", lx, yy + 3, 100);
    g_edSymbol = MakeEdit(g_dlg, ID_DL_SYMBOL, sym, ex, yy, ew); yy += 30;

    MakeLabel(g_dlg, L"Tu ngay:", lx, yy + 3, 100);
    g_edFrom = MakeEdit(g_dlg, ID_DL_FROM, from, ex, yy, ew); yy += 30;

    MakeLabel(g_dlg, L"Den ngay:", lx, yy + 3, 100);
    g_edTo = MakeEdit(g_dlg, ID_DL_TO, to, ex, yy, ew); yy += 26;

    MakeLabel(g_dlg, L"(YYYY-MM-DD. Ho tro FX, vang, BTC, index... moi thu Dukascopy co)",
              lx, yy, 370); yy += 22;
    MakeLabel(g_dlg, L"Timezone: GMT+2 / DST=US (co dinh, giong TDS)", lx, yy, 370); yy += 28;

    // Nut CHINH: Xoa + Tai lai fresh (lay data day du/moi nhat tu Dukascopy)
    MakeButton(g_dlg, ID_DL_REDL, L"XOA + TAI LAI FRESH (data moi nhat)", lx, yy, 368); yy += 36;

    // Hang: Tai them (giu cu) + Verify
    MakeButton(g_dlg, ID_DL_RUN,    L"Tai them (giu cu)", lx, yy, 180);
    MakeButton(g_dlg, ID_DL_VERIFY, L"Kiem tra quality",  lx + 188, yy, 180); yy += 32;

    // Hang: Xem list + Xoa
    MakeButton(g_dlg, ID_DL_LIST,   L"Xem data da tai",     lx, yy, 180);
    MakeButton(g_dlg, ID_DL_DELETE, L"Xoa data symbol nay", lx + 188, yy, 180); yy += 32;

    // Hang: Dong
    MakeButton(g_dlg, ID_DL_CLOSE,  L"Dong", lx + 278, yy, 90); yy += 30;

    g_lblStat = MakeLabel(g_dlg, L"Xoa + Tai lai fresh -> tick 'Use my tick data' de chay.", lx, yy, 370);
}

// ===========================================================================
// COMBOBOX SPREAD: them item "use my spread" (giong TDS them "Variable")
// ===========================================================================
// Tim combobox Spread cua tester: la ComboBox co item[0] == "Current".
// Lay text item AN TOAN: kiem tra do dai truoc (CB_GETLBTEXT KHONG gioi han buffer!)
// Tra false neu item qua dai (bo qua) -> tranh tran stack.
static bool SafeGetItem(HWND cb, int idx, wchar_t* buf, int cch)
{
    int len = (int)SendMessageW(cb, CB_GETLBTEXTLEN, idx, 0);
    if (len < 0 || len >= cch) { buf[0] = 0; return false; }
    if (SendMessageW(cb, CB_GETLBTEXT, idx, (LPARAM)buf) == CB_ERR) { buf[0] = 0; return false; }
    return true;
}

static BOOL CALLBACK FindSpreadProc(HWND hwnd, LPARAM)
{
    wchar_t cls[32] = {0};
    GetClassNameW(hwnd, cls, 32);
    if (!wcsstr(cls, L"Combo") && !wcsstr(cls, L"COMBO")) return TRUE;
    int n = (int)SendMessageW(hwnd, CB_GETCOUNT, 0, 0);
    if (n < 2) return TRUE;
    wchar_t item[64] = {0};
    if (!SafeGetItem(hwnd, 0, item, 64)) return TRUE;   // item dai (vd Model) -> bo qua
    if (wcscmp(item, L"Current") == 0) {
        g_cbSpreadTester = hwnd;
        g_cbSpreadId     = GetDlgCtrlID(hwnd);
        return FALSE;   // dung
    }
    return TRUE;
}

// Cap nhat g_useMySpread tu selection hien tai cua combobox (chay tren UI thread).
static void UpdateMySpreadFlag()
{
    g_useMySpread = false;
    if (!g_cbSpreadTester || !IsWindow(g_cbSpreadTester)) return;
    int sel = (int)SendMessageW(g_cbSpreadTester, CB_GETCURSEL, 0, 0);
    if (sel < 0) return;
    wchar_t item[64] = {0};
    if (!SafeGetItem(g_cbSpreadTester, sel, item, 64)) return;
    g_useMySpread = (wcscmp(item, MYSPREAD_ITEM) == 0);
}

// Log chan doan (1 lan): dump toan bo item + selection cua combobox Spread
static void DumpSpreadCombo()
{
    static bool done = false;
    if (done || !g_cbSpreadTester) return;
    done = true;
    FILE* f = nullptr;
    fopen_s(&f, "C:\\Users\\Phuc\\Desktop\\tickdata\\hook.log", "a");
    if (!f) return;
    int n = (int)SendMessageW(g_cbSpreadTester, CB_GETCOUNT, 0, 0);
    int sel = (int)SendMessageW(g_cbSpreadTester, CB_GETCURSEL, 0, 0);
    fprintf(f, "[combo] Spread combobox hwnd=%p id=%d count=%d cursel=%d\n",
            (void*)g_cbSpreadTester, g_cbSpreadId, n, sel);
    for (int i = 0; i < n && i < 20; ++i) {
        wchar_t it[64] = {0};
        SafeGetItem(g_cbSpreadTester, i, it, 64);
        fprintf(f, "[combo]   item[%d]=%ls%s\n", i, it, (i == sel) ? "  <== SELECTED" : "");
    }
    fclose(f);
}

// Tim combobox Spread va them item "use my spread" (neu chua co).
static void AugmentSpreadCombo(HWND panel)
{
    if (!g_cbSpreadTester || !IsWindow(g_cbSpreadTester)) {
        g_cbSpreadTester = nullptr;
        EnumChildWindows(panel, FindSpreadProc, 0);
    }
    if (!g_cbSpreadTester) return;
    DumpSpreadCombo();

    // Da co item chua?
    int n = (int)SendMessageW(g_cbSpreadTester, CB_GETCOUNT, 0, 0);
    bool has = false;
    for (int i = 0; i < n; ++i) {
        wchar_t it[64] = {0};
        if (SafeGetItem(g_cbSpreadTester, i, it, 64) &&
            wcscmp(it, MYSPREAD_ITEM) == 0) { has = true; break; }
    }
    if (!has)
        SendMessageW(g_cbSpreadTester, CB_ADDSTRING, 0, (LPARAM)MYSPREAD_ITEM);
}

// ===========================================================================
// SUBCLASSED PANEL PROC
// ===========================================================================
static LRESULT CALLBACK PanelProc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_APP_CREATE: {
        // Chay TREN UI THREAD -> an toan tao child control
        HFONT font = nullptr;
        HWND start = GetDlgItem(h, MT4_ID_START);
        if (start) font = (HFONT)SendMessageW(start, WM_GETFONT, 0, 0);

        g_chk = CreateWindowExW(0, L"BUTTON", L"Use my tick data",
            WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
            1300, 130, 150, 20, h, (HMENU)ID_USE_MYTICK, g_self, nullptr);

        g_btn = CreateWindowExW(0, L"BUTTON", L"Tick data settings",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            1455, 127, 150, 24, h, (HMENU)ID_MYTICK_SETUP, g_self, nullptr);

        if (font) {
            SendMessageW(g_chk, WM_SETFONT, (WPARAM)font, TRUE);
            SendMessageW(g_btn, WM_SETFONT, (WPARAM)font, TRUE);
        }

        // Them "use my spread" vao combobox Spread cua tester (giong TDS "Variable")
        AugmentSpreadCombo(h);
        UpdateMySpreadFlag();
        return 0;
    }
    case WM_APP_AUGMENT:
        // Dinh ky: dam bao item con do + cap nhat co (phong MT4 repopulate)
        AugmentSpreadCombo(h);
        UpdateMySpreadFlag();
        return 0;
    case WM_COMMAND: {
        WORD id   = LOWORD(wp);
        WORD code = HIWORD(wp);
        if (id == ID_USE_MYTICK) {
            g_useMyTick = (SendMessageW(g_chk, BM_GETCHECK, 0, 0) == BST_CHECKED);
            if (g_useMyTick) {
                PrepareFromTester();      // BAT -> build+deploy FXT read-only (tick that)
                // Auto-chon "use my spread" trong combobox (giong TDS auto Variable)
                AugmentSpreadCombo(h);
                if (g_cbSpreadTester && IsWindow(g_cbSpreadTester)) {
                    int idx = (int)SendMessageW(g_cbSpreadTester,
                        CB_FINDSTRINGEXACT, (WPARAM)-1, (LPARAM)MYSPREAD_ITEM);
                    if (idx >= 0)
                        SendMessageW(g_cbSpreadTester, CB_SETCURSEL, idx, 0);
                }
            } else {
                RestoreFromTester();      // TAT -> go FXT/HST cua ta (ve data broker)
                // Tra combobox ve "Current" (item 0)
                if (g_cbSpreadTester && IsWindow(g_cbSpreadTester))
                    SendMessageW(g_cbSpreadTester, CB_SETCURSEL, 0, 0);
            }
            UpdateMySpreadFlag();         // cap nhat co (CB_SETCURSEL khong gui CBN_SELCHANGE)
            return 0;
        }
        if (id == ID_MYTICK_SETUP) {
            OpenSettingsDialog();
            return 0;
        }
        // Combobox Spread doi selection -> cap nhat co "use my spread"
        if (g_cbSpreadId && id == (WORD)g_cbSpreadId && code == CBN_SELCHANGE) {
            UpdateMySpreadFlag();
            // fall-through: de MT4 tu xu ly selection (spread base = 0/Current, hook override)
        }
        break;
    }
    case WM_NCDESTROY:
        // Panel bi huy -> go subclass (chi khi g_origProc hop le, khong phai chinh minh)
        if (g_origProc && g_origProc != (WNDPROC)PanelProc)
            SetWindowLongPtrW(h, GWLP_WNDPROC, (LONG_PTR)g_origProc);
        g_panel = nullptr;
        break;
    }
    // PHONG THU: tuyet doi khong goi chinh minh (tranh de quy vo han -> stack overflow)
    if (g_origProc && g_origProc != (WNDPROC)PanelProc)
        return CallWindowProcW(g_origProc, h, msg, wp, lp);
    return DefWindowProcW(h, msg, wp, lp);
}

// ===========================================================================
// Worker thread: poll panel -> subclass -> tao control
// ===========================================================================
static DWORD WINAPI GuiThread(LPVOID)
{
    // doi MT4 dung UI, roi giu chay de re-augment combobox dinh ky.
    // QUAN TRONG: TUYET DOI khong subclass 2 lan cung 1 panel -> neu subclass lai,
    // SetWindowLongPtr tra ve chinh PanelProc -> g_origProc = PanelProc ->
    // CallWindowProc goi chinh no -> DE QUY VO HAN -> stack overflow.
    for (;;) {
        HWND panel = FindTesterPanel();
        if (panel && IsWindow(panel)) {
            if (panel == g_panel) {
                // Da subclass panel nay roi -> chi re-augment combobox
                PostMessageW(panel, WM_APP_AUGMENT, 0, 0);
            } else {
                // Panel "moi": CHI subclass neu wndproc HIEN TAI chua phai PanelProc.
                WNDPROC cur = (WNDPROC)GetWindowLongPtrW(panel, GWLP_WNDPROC);
                if (cur != (WNDPROC)PanelProc) {
                    g_origProc = (WNDPROC)SetWindowLongPtrW(
                        panel, GWLP_WNDPROC, (LONG_PTR)PanelProc);
                    g_panel = panel;
                    SendMessageW(panel, WM_APP_CREATE, 0, 0);
                    OutputDebugStringA("[tdshook] GUI injected\n");
                } else {
                    // Da la PanelProc (g_panel bi mat dong bo) -> chi cap nhat, KHONG subclass lai
                    g_panel = panel;
                    PostMessageW(panel, WM_APP_AUGMENT, 0, 0);
                }
            }
        }
        Sleep(1500);
    }
}

void StartGuiInjection(HMODULE self)
{
    g_self = self;
    ComputeProjectRoot();

    INITCOMMONCONTROLSEX icc = { sizeof(icc), ICC_DATE_CLASSES | ICC_STANDARD_CLASSES };
    InitCommonControlsEx(&icc);

    CreateThread(nullptr, 0, GuiThread, nullptr, 0, nullptr);
}
