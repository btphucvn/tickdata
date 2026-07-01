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
