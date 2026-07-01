// Slippage engine (native) — doc block tham so 'TSLP' tu shared memory
// (Local\TDSClone_SlippageShm) va tinh slippage points cho moi lenh khop.
//
// Khop dung mo hinh Python: python/spread_slippage.py (SlippageModel).
// Quy uoc: slippage points DUONG = bat loi, AM = co loi.
#pragma once
#include <cstdint>

namespace slip {

// Order type giong MT4
enum { OP_BUY = 0, OP_SELL = 1, OP_BUYLIMIT = 2, OP_SELLLIMIT = 3,
       OP_BUYSTOP = 4, OP_SELLSTOP = 5 };

// Mo shared memory slippage. Tra true neu co block hop le (magic TSLP).
bool Open();

// Dong shared memory.
void Close();

// Da bat slippage chua?
bool Enabled();

// Tinh slippage (points, co dau) cho 1 lenh.
//   orderType: OP_* (xem tren)
//   recentVolatilityPts: bien dong gia gan day (points) — cho latency mode.
//   isSlTp: 0=thuong, 1=SL, 2=TP.
double SlippagePoints(int orderType, double recentVolatilityPts, int isSlTp);

} // namespace slip
