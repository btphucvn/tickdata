//+------------------------------------------------------------------+
//|  TDSCloneTemplate.mq4                                            |
//|  Module 4 (phía EA) — minh hoạ dùng tdsclone.dll qua #import.    |
//|  TDS_CLONE_ARCHITECTURE.md — mục 4.3.                           |
//|                                                                  |
//|  MỤC ĐÍCH: trong backtest, MT4 tester thường chỉ có 1 spread cố |
//|  định. EA này hỏi DLL spread ĐỘNG theo thời gian tick rồi tự     |
//|  dựng Ask = Bid + spread*Point, mô phỏng variable spread mà      |
//|  KHÔNG cần inject/hook (đường sạch C2).                          |
//|                                                                  |
//|  CHUẨN BỊ:                                                       |
//|   1. Build tdsclone.dll (native/ea_helper_dll) bản x86.         |
//|   2. Copy vào <terminal>\MQL4\Libraries\tdsclone.dll            |
//|   3. Sinh file .tdspread bằng Python:                            |
//|        tdsclone build EURUSD --period 1 --from ... --to ...      |
//|      rồi copy file EURUSD1.tdspread vào <terminal>\MQL4\Files\   |
//|   4. Bật "Allow DLL imports" trong Strategy Tester.             |
//+------------------------------------------------------------------+
#property strict
#property copyright "TDS-Clone"

//--- Khai báo hàm import từ DLL. Tên KHỚP bảng export (tdsclone.def).
//    Lưu ý: MQL4 truyền `string` cho DLL như con trỏ char* ANSI -> DLL nhận
//    const char* (xem TDS_Load trong tdsclone.cpp).
#import "tdsclone.dll"
   int    TDS_Load(string spreadFile);   // nạp file .tdspread, trả số bản ghi (-1 lỗi)
   double TDS_SpreadAt(int unixTime);     // spread (points) tại unixTime
   double TDS_SlippageAt(int unixTime);   // slippage (points)
   int    TDS_Count();                    // số bản ghi đã nạp (debug)
#import

//--- Tham số EA
input string SpreadFile = "EURUSD1.tdspread"; // nằm trong MQL4\Files
input bool   UseDynamicSpread = true;          // bật/tắt variable spread

//--- Trạng thái
bool g_loaded = false;

//+------------------------------------------------------------------+
//| Khởi tạo: nạp file spread.                                       |
//+------------------------------------------------------------------+
int OnInit()
{
   // TerminalInfoString(TERMINAL_DATA_PATH) + \MQL4\Files\ là nơi DLL nên đọc.
   // Ở đây truyền tên tương đối; nếu DLL không tìm thấy, dùng đường dẫn tuyệt đối.
   int n = TDS_Load(SpreadFile);
   g_loaded = (n >= 0);
   if(!g_loaded)
      Print("TDS_Load THẤT BẠI cho ", SpreadFile,
            " — kiểm tra DLL imports & đường dẫn file.");
   else
      Print("TDS_Load OK: ", n, " bản ghi spread.");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Mỗi tick: dựng Ask động từ Bid + spread của DLL.                |
//+------------------------------------------------------------------+
void OnTick()
{
   double bid = Bid;
   double ask = Ask;   // ask gốc của tester (spread cố định)

   if(UseDynamicSpread && g_loaded)
   {
      int    t        = (int)TimeCurrent();          // thời gian tick (giây)
      double spreadPt = TDS_SpreadAt(t);             // spread points động
      double dynAsk   = bid + spreadPt * Point;       // tái dựng Ask
      double slipPt   = TDS_SlippageAt(t);            // (tuỳ chọn) slippage points

      // ====== DÙNG dynAsk / spreadPt / slipPt trong logic giao dịch của bạn ======
      // Vì MT4 không cho ghi đè Ask của tester, ở C2 ta TỰ tính giá vào lệnh dựa
      // trên dynAsk thay vì biến Ask dựng sẵn. Ví dụ minh hoạ:
      //    - khi BUY:  giá vào = dynAsk (+ slippage)
      //    - khi SELL: giá ra  = dynAsk
      // (Phần đặt lệnh thực tế tuỳ chiến lược của bạn — đây chỉ là khung.)
      Comment("Bid=", DoubleToString(bid, Digits),
              "  dynAsk=", DoubleToString(dynAsk, Digits),
              "  spread(pts)=", DoubleToString(spreadPt, 1));
   }
}

//+------------------------------------------------------------------+
//| Dọn dẹp.                                                        |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Comment("");
}
//+------------------------------------------------------------------+
