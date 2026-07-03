// GUI injection — chen control "Use my tick data" + "Tick data settings"
// vao panel Strategy Tester cua MT4 (giong TDS).
#pragma once
#include <windows.h>

// Bat dau luong cho panel tester xuat hien roi chen control.
void StartGuiInjection(HMODULE self);

// Hook doc co nay: true = nguoi dung da tick "Use my tick data".
bool GuiUseMyTickData();

// Hook doc co nay: true = combobox Spread cua tester dang chon "use my spread"
// (giong TDS chon "Variable") -> ap spread bien dong per-tick.
bool GuiUseMySpread();

// Doc TRUC TIEP tham so tester HIEN TAI tu GUI (symbol/from/to dang YYYY-MM-DD/period-phut).
// Hook goi LUC START de prepare FXT ao dung khoang ngay dang chon -> range "dong" theo MT4.
// Tra true neu doc du symbol+from+to. Buffer: sym>=64, from/to>=32.
bool GuiReadTesterParams(char* sym, int symN, char* from, int fromN,
                         char* to, int toN, int* period);
