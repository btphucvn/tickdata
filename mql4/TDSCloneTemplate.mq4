//+------------------------------------------------------------------+
//|  TDSCloneTemplate.mq4                                            |
//|  Module 4 (phia EA) — dung tdsclone.dll qua #import.            |
//|                                                                  |
//|  2 che do hoat dong (chon 1):                                    |
//|    A. Shared memory (khuyen dung): orchestrator.py dang chay     |
//|       -> EA tu dong lay spread that Dukascopy theo tung tick.    |
//|    B. Khong can orchestrator: EA dung spread co dinh hoac tu EA. |
//|                                                                  |
//|  CHUAN BI:                                                        |
//|   1. Build tdsclone.dll (cmake --build native\build --target tdsclone)
//|   2. Copy tdsclone.dll vao <terminal>\MQL4\Libraries\            |
//|   3. Chay: python orchestrator.py --spread-from-ticks ticks.bin  |
//|   4. Bat "Allow DLL imports" trong Strategy Tester.              |
//|   5. Chon Model=Every Tick, dung FXT da deploy.                  |
//+------------------------------------------------------------------+
#property strict
#property copyright "TDS Clone"
#property version   "1.0"

#import "tdsclone.dll"
   int    TDS_Load(string dummy);         // ket noi shared memory (goi 1 lan OnInit)
   double TDS_SpreadAt(int time_sec);     // spread (points) tai thoi diem nay
   double TDS_Point();                    // pip size
   double TDS_SlippageAt(int time_sec);   // slippage (points)
   int    TDS_Count();                    // so ban ghi (debug)
#import

input bool   UseDynamicSpread = true;   // dung spread dong tu Dukascopy
input double FallbackSpread   = 2.0;    // spread du phong (pts) neu DLL chua ket noi

bool   g_loaded = false;

int OnInit()
{
   int n = TDS_Load("");
   g_loaded = (n > 0);
   if (!g_loaded)
      Print("[TDSClone] Chua ket noi shared memory. Chay orchestrator.py truoc! n=", n);
   else
      Print("[TDSClone] OK: ", n, " records spread, point=", TDS_Point());
   return INIT_SUCCEEDED;
}

void OnTick()
{
   if (!UseDynamicSpread) return;

   int    t  = (int)TimeCurrent();
   double sp = g_loaded ? TDS_SpreadAt(t) : FallbackSpread;
   double pt = g_loaded ? TDS_Point()     : Point;

   double dynBid = Bid;
   double dynAsk = dynBid + sp * pt;

   // --- Su dung dynBid / dynAsk thay vi Bid/Ask trong logic cua EA ---
   //   Ex: khi BUY  -> gia vao = dynAsk
   //       khi SELL -> gia vao = dynBid (khong can spread)
   //
   // (Tich hop vao EA cua ban: thay the Bid/Ask bang dynBid/dynAsk)

   Comment(
      "TDS Clone | Bid=", DoubleToStr(dynBid, Digits),
      "  Ask=",           DoubleToStr(dynAsk, Digits),
      "  Spread=",        DoubleToStr(sp, 1), " pts",
      "  [", TimeToStr(TimeCurrent(), TIME_DATE|TIME_SECONDS), "]"
   );
}

void OnDeinit(const int reason)
{
   Comment("");
}
//+------------------------------------------------------------------+
