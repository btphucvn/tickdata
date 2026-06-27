# TDS-Clone — Kiến trúc chi tiết (Tick Data Suite functional clone)

> **Mục tiêu:** Tự xây một bộ công cụ đạt **chức năng tương đương Tick Data Suite**: tải tick
> data chất lượng cao, convert sang định dạng MT4 Strategy Tester đọc được, và mô phỏng
> **variable spread / slippage trong suốt với EA** đúng theo cách TDS làm (DLL injection +
> runtime hooking).
>
> **Phạm vi pháp lý (đọc trước khi code):**
> - ✅ Toàn bộ code trong dự án này là **code mới của bạn**, không copy/dịch ngược binary của TDS.
> - ✅ Inject/hook chạy trên **MT4 của chính bạn**, để backtest **EA của bạn** → dual-use hợp pháp.
> - ❌ KHÔNG crack/patch license TDS. KHÔNG sao chép asset/code TDS.
> - ⚠️ Lớp hook runtime (Module 5) đụng chạm nội bộ `terminal.exe` của MetaQuotes → có thể
>   vướng EULA của họ và **gãy mỗi khi MT4 update**. Cân nhắc dùng Module 4 (EA `#import`)
>   nếu chấp nhận sửa EA vài dòng.

---

## 0. TL;DR kiến trúc

```
┌──────────────────────────────────────────────────────────────────────┐
│                          TDS-Clone Suite                               │
│                                                                        │
│  [1] Downloader        [2] Tick Store        [3] FXT/HST Builder       │
│  Dukascopy/HistData →  Parquet/SQLite    →   .fxt + .hst (+spread)     │
│        │                     │                      │                  │
│        └─────────────────────┴──────────────────────┘                 │
│                              │                                         │
│                  ┌───────────┴───────────┐                            │
│                  ▼                       ▼                            │
│        [4] EA Helper DLL          [5] Injector + Hook Engine          │
│        (#import, sửa EA)          (trong suốt, kiểu TDS)              │
│        - getSpread(t)             - DLL injection                      │
│        - getSlippage(t)           - MinHook inline hook                │
│                                   - SigScanner (auto offset)           │
│                                                                        │
│  [6] GUI / Orchestrator (quản lý symbol, job, cấu hình spread model)  │
└──────────────────────────────────────────────────────────────────────┘
```

| Module | Vai trò | Ngôn ngữ đề xuất | Độ khó | Hợp pháp |
|--------|---------|------------------|--------|----------|
| 1. Downloader | Tải tick data | Python | ★☆☆ | Sạch |
| 2. Tick Store | Lưu/quản lý tick | Python | ★☆☆ | Sạch |
| 3. FXT/HST Builder | Convert → MT4 | Python (hoặc C++ cho tốc độ) | ★★☆ | Sạch |
| 4. EA Helper DLL | Variable spread qua `#import` | C++ | ★★☆ | Sạch |
| 5. Injector + Hook | Variable spread trong suốt | C++ | ★★★ | Dual-use, fragile |
| 6. GUI/Orchestrator | Điều phối, cấu hình | Python (PySide6) | ★★☆ | Sạch |

---

## 0.5 Target platform: WINDOWS (bắt buộc)

> Toàn bộ suite chạy trên **Windows** (vì MT4 `terminal.exe` là Windows). Đây không phải
> tuỳ chọn — injector/hook chỉ tồn tại trên Windows.

### Bitness — quyết định kiến trúc số 1
- MT4 build cổ điển (build < 600) thường là **x86 (32-bit)**; build 600+ vẫn là **32-bit**.
  → MetaTrader **4** về cơ bản là tiến trình **32-bit**.
- ⚠️ **Injector + hook DLL PHẢI cùng bitness với `terminal.exe` = x86 (32-bit).**
  Inject DLL 64-bit vào tiến trình 32-bit là **bất khả thi**. Kiểm tra bằng Task Manager
  (cột "Platform") hoặc xem có hậu tố `*32` không.
- Python + GUI có thể 64-bit thoải mái (chạy tiến trình riêng), chỉ phần native cắm vào
  MT4 mới buộc 32-bit.

### Convention Windows cần tuân theo trong code
| Hạng mục | Yêu cầu |
|----------|---------|
| Đường dẫn | Dùng `pathlib.Path`, không hardcode `/`. MT4 data ở `%APPDATA%\MetaQuotes\Terminal\<hash>\` |
| Thư mục FXT | Strategy Tester đọc từ `<terminal>\tester\history\*.fxt` |
| Thư mục HST | `<terminal>\history\<server>\*.hst` |
| Encoding | MQL4/đường dẫn dùng UTF-16 (`wchar_t`, `const wchar_t*`) trong DLL |
| Quyền | Injector cần chạy **as Administrator** (OpenProcess PROCESS_ALL_ACCESS) |
| Calling convention | DLL export cho `#import` MQL4 = `__stdcall`; hook nội bộ MT4 thường `__thiscall`/`__fastcall` |

### Toolchain build (target Windows)
- **Native (Module 4/5):** Visual Studio 2022 + MSVC, toolset **x86**. CMake với
  `-A Win32`. MinHook build kèm x86.
- **Python (Module 1/2/3/6):** Python 3.11+ **bản Windows**. Đóng gói bằng PyInstaller →
  `.exe`. GUI PySide6 chạy native Windows.
- **Installer:** Inno Setup hoặc WiX → 1 file `setup.exe` (giống trải nghiệm cài TDS).

### ⚠️ Bạn đang dev trong WSL2 (Linux) — chiến lược build
Môi trường hiện tại là WSL2, **không build/test được phần Windows-native ở đây**. Tách rõ:
| Phần | Dev ở WSL được? | Build/Test thật ở đâu |
|------|-----------------|------------------------|
| Module 1/2/3 (Python thuần) | ✅ logic + unit test chạy được ở WSL | Vẫn nên verify FXT trên MT4 Windows |
| Module 4/5 (C++ WinAPI) | ✍️ viết code được, **không compile/inject ở WSL** | MSVC trên Windows (hoặc VM Windows) |
| Module 6 (PySide6) | ✅ code được | Chạy GUI trên Windows |
| Verify FXT mở trong MT4 | ❌ | Bắt buộc MT4 trên Windows |

**Khuyến nghị workflow:**
1. Code toàn bộ trong repo chung (WSL ok để viết).
2. Phần native: build trên Windows bằng MSVC — KHÔNG cross-compile từ WSL (WinAPI + injection cần môi trường Windows thật).
3. Đặt repo trên ổ Windows (vd `C:\dev\tds-clone`) rồi mở từ cả WSL lẫn Windows, hoặc clone 2 nơi.
4. Test injection/hook **chỉ trên Windows có MT4 cài thật**.

---

## 1. Module 1 — Tick Data Downloader

### 1.1 Mục tiêu
Tải tick data lịch sử (bid/ask/volume) cho nhiều symbol, nhiều năm, có resume + cache.

### 1.2 Nguồn dữ liệu
| Nguồn | Định dạng | Ghi chú |
|-------|-----------|---------|
| **Dukascopy** | `.bi5` (LZMA-nén) theo giờ | Miễn phí, chất lượng cao, chuẩn de-facto. Ưu tiên. |
| HistData.com | CSV M1/tick | Miễn phí, đăng ký, tải thủ công theo tháng |
| Broker MT5 | real ticks | Nếu có tài khoản, qua MT5 API |

### 1.3 Dukascopy — chi tiết kỹ thuật
- URL pattern (mỗi file = 1 giờ):
  ```
  https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YYYY}/{MM-1:02d}/{DD:02d}/{HH:02d}h_ticks.bi5
  ```
  ⚠️ **Tháng 0-indexed** (Jan = 00). Đây là lỗi phổ biến nhất.
- File `.bi5` = LZMA stream. Giải nén → mảng record 20 byte:
  ```
  struct DukascopyTick {   // big-endian
      uint32 ms_offset;    // mili-giây kể từ đầu giờ
      uint32 ask;          // *10^point (vd EURUSD point=5 → chia 100000)
      uint32 bid;
      float  ask_volume;
      float  bid_volume;
  };
  ```
- Giá thật = `raw / 10^digits` (mỗi symbol có point factor riêng, hardcode bảng tra).

### 1.4 API thiết kế
```python
class TickDownloader:
    def download(self, symbol: str, start: datetime, end: datetime,
                 out_dir: Path, max_workers: int = 8) -> DownloadReport: ...
    # - tải song song theo giờ (asyncio/httpx hoặc ThreadPool)
    # - resume: bỏ qua .bi5 đã có trên đĩa
    # - retry + backoff cho HTTP 503
    # - log giờ bị thiếu (cuối tuần/holiday → rỗng là bình thường)
```

### 1.5 Output
Lưu thô `.bi5` theo cây `raw/{symbol}/{year}/{month}/{day}/{hour}.bi5` để cache, rồi
decode → đẩy sang Module 2.

---

## 2. Module 2 — Tick Store

### 2.1 Mục tiêu
Chuẩn hoá tick từ mọi nguồn về một schema, lưu hiệu quả, query nhanh theo khoảng thời gian.

### 2.2 Schema chuẩn hoá (canonical tick)
```
timestamp_utc : int64   # epoch micro/milli giây
bid           : float64
ask           : float64
bid_volume    : float64
ask_volume    : float64
```
`spread = ask - bid` (tính khi cần, không lưu thừa).

### 2.3 Lưu trữ
- **Parquet** (mỗi symbol-tháng 1 file) — nén tốt, đọc cột nhanh, hợp pandas/polars.
- Index nhẹ bằng **SQLite** (manifest): symbol, khoảng thời gian, đường dẫn file, số tick,
  trạng thái (downloaded/converted), checksum.
- Polars khuyến nghị cho tốc độ khi data lớn (nhiều năm × nhiều symbol).

### 2.4 API
```python
class TickStore:
    def ingest(self, symbol: str, ticks: pl.DataFrame): ...
    def query(self, symbol, start, end) -> pl.DataFrame: ...
    def coverage(self, symbol) -> list[tuple[datetime, datetime]]: ...  # khoảng đã có
    def gaps(self, symbol, start, end) -> list[...]: ...                # khoảng thiếu
```

---

## 3. Module 3 — FXT / HST Builder

> Đây là trái tim convert. MT4 Strategy Tester đọc `.fxt`; biểu đồ/EA đọc `.hst`.

### 3.1 Khái niệm
- **`.hst`** (history): dữ liệu giá theo timeframe, dùng cho chart + `iClose/iHigh...`.
- **`.fxt`** (Forex Tester data): dữ liệu tester dùng để "phát lại" trong backtest.
  Tên file: `{SYMBOL}{PERIOD}_{MODEL}.fxt`, ví dụ `EURUSD1_0.fxt`.
  - MODEL: `0`=Every tick, `1`=Control points, `2`=Open prices.
  - Để tick-accurate → luôn dùng **model 0** + period nhỏ nhất.

### 3.2 Cấu trúc FXT (version 405 / build 600+)

**Header (728 bytes)** — các field quan trọng (đọc kỹ alignment, struct packed 1 byte):
```c
#pragma pack(push, 1)
struct FxtHeader {
    int   version;            // 405
    char  copyright[64];
    char  description[128];
    char  serverName[128];    // (tuỳ build) 
    char  symbol[12];
    int   period;             // phút: 1, 5, 15...
    int   model;              // 0/1/2
    int   bars;               // số bar
    int   fromDate;
    int   toDate;
    int   totalTicks;         // ⚠️ một số build bỏ qua, vẫn nên set đúng
    int   modelQuality;       // *10000 (99.9% → 99900) — TDS thường ghi 99900
    char  baseCurrency[12];
    int   spread;             // ⚠️ spread CỐ ĐỊNH ở đây. 0 = current. Điểm mấu chốt!
    int   digits;
    // ... point, profit/swap mode, lot, margin, stops level, freeze level ...
    int   timezone;           // 0 = giờ chuẩn của data
    // ... (đệm cho đủ 728 byte)
};
#pragma pack(pop)
```
> **Lưu ý sống còn:** byte-count chính xác đổi theo build MT4. **Phải verify** bằng cách
> dump một `.fxt` do MT4 tự tạo rồi so sánh, đừng tin con số tuyệt đối ở đây 100%.

**Tick record (lặp lại sau header) — 56 bytes (build 600+) / 52 bytes (build cũ):**
```c
#pragma pack(push, 1)
struct FxtTick {
    int32  barTime;     // thời điểm mở bar (giây epoch)
    int32  _pad;        // build 600+ thêm 4 byte đệm (alignment double)
    double open;
    double high;
    double low;
    double close;
    int64  volume;
    int32  tickTime;    // thời điểm tick thực
    int32  flag;        // 0 = sinh từ bar, 4 = từ tick thật
};
#pragma pack(pop)
```

### 3.3 Thuật toán build
```
1. Đọc tick canonical (Module 2) cho [symbol, range].
2. Gộp tick thành bar theo PERIOD (mở/cao/thấp/đóng theo BID, volume = số tick).
3. Với mỗi tick: phát ra 1 FxtTick:
     - barTime = mốc bar chứa tick
     - open/high/low/close = giá BID đang chạy của tick đó (tester dùng bid làm giá)
     - tickTime = timestamp tick
     - flag = 4
4. Ghi header + toàn bộ record.
5. Sinh kèm .hst (model giá cho chart).
```

### 3.4 ⭐ Variable spread — 3 chiến lược (đây là điểm "ăn tiền")

MT4 tester mặc định lấy **1 spread cố định** (header `spread` hoặc current). Để có spread
biến động, chọn 1 trong 3:

| Chiến lược | Cách làm | Cần inject? | Trong suốt EA? |
|-----------|----------|-------------|----------------|
| **C1. Bake spread vào FXT** | Lợi dụng field/bit chưa dùng trong record để nhét spread tick, rồi hook đọc lại | Có (nhẹ) | ✅ |
| **C2. EA `#import`** | EA hỏi DLL `getSpread(tickTime)` → tự set Ask | Không | ❌ (sửa EA) |
| **C3. Hook runtime (kiểu TDS)** | Inject DLL, hook hàm trả Ask/Bid của tester, override theo tick | Có | ✅ |

> **Cách TDS thật:** C3. Mục tiêu "y hệt TDS" → đi C3 (xem Module 5).
> **Nhanh & sạch:** C2. **Cân bằng:** C1.

Thiết kế **spread model** dùng chung cho cả 3:
```python
class SpreadModel:
    """Trả spread (points) cho mỗi tick."""
    # nguồn: spread thật từ data (ask-bid), hoặc fixed, hoặc random theo phiên,
    # hoặc widen theo giờ tin tức / rollover 22:00-23:00 GMT.
    def spread_at(self, ts, real_bid, real_ask) -> float: ...
```

### 3.5 API
```python
class FxtBuilder:
    def build(self, symbol, period, ticks, spread_model, model=0) -> Path: ...
class HstBuilder:
    def build(self, symbol, period, bars) -> Path: ...
```

---

## 4. Module 4 — EA Helper DLL (Cách C2, sạch & dễ)

### 4.1 Mục tiêu
DLL export các hàm để EA gọi qua `#import`, trả spread/slippage động theo thời gian tick.
Không inject, không gãy khi update MT4.

### 4.2 Interface DLL (C ABI, `__stdcall`)
```c
// tdsclone.dll
extern "C" __declspec(dllexport) double __stdcall TDS_SpreadAt(int unixTime);
extern "C" __declspec(dllexport) double __stdcall TDS_SlippageAt(int unixTime);
extern "C" __declspec(dllexport) int    __stdcall TDS_Load(const wchar_t* spreadFile);
```
- Nạp 1 file spread đã precompute (timestamp→spread points) lúc init, tra cứu O(log n).

### 4.3 Phía EA (MQL4)
```cpp
#import "tdsclone.dll"
   double TDS_SpreadAt(int unixTime);
   int    TDS_Load(string spreadFile);
#import

// trong OnTick:
double sp = TDS_SpreadAt((int)TimeCurrent());
double ask = Bid + sp * Point;   // tự dựng Ask theo spread động
```
> Cần bật "Allow DLL imports" trong tester. Đây là cơ chế MT4 **chính thức hỗ trợ**.

---

## 5. Module 5 — Injector + Hook Engine (Cách C3, "y hệt TDS")

> ⚠️ Lớp này là phần TDS dùng để đạt variable spread **trong suốt**. Gồm 3 phần:
> (5A) Injector, (5B) Hook engine, (5C) SigScanner tìm hàm mục tiêu trong `terminal.exe`.
> Phần khó/fragile là 5C vì phải xác định hàm nội bộ undocumented của MetaQuotes.

### 5.1 (5A) DLL Injector
**Mục tiêu:** nạp `tdshook.dll` vào tiến trình `terminal.exe`.

Kỹ thuật (chọn 1, tăng dần độ "ẩn"):
1. **`CreateRemoteThread` + `LoadLibraryW`** — chuẩn, đơn giản, đủ dùng cho mục đích này.
2. Manual mapping — phức tạp hơn, không cần thiết cho backtest.

```cpp
// pseudo
HANDLE proc = OpenProcess(PROCESS_ALL_ACCESS, FALSE, pid);
void* mem  = VirtualAllocEx(proc, 0, dllPathLen, MEM_COMMIT, PAGE_READWRITE);
WriteProcessMemory(proc, mem, dllPath, dllPathLen, 0);
auto load = GetProcAddress(GetModuleHandle("kernel32"), "LoadLibraryW");
CreateRemoteThread(proc, 0, 0, (LPTHREAD_START_ROUTINE)load, mem, 0, 0);
```
> Kiến trúc phải khớp: MT4 build cũ = **x86 (32-bit)**. Injector phải cùng bitness.

### 5.2 (5B) Hook Engine
**Mục tiêu:** đặt inline hook lên hàm mục tiêu để override Ask/Bid/spread.

- Dùng **MinHook** (gọn, MIT) hoặc Microsoft Detours.
- Trong `DllMain` (PROCESS_ATTACH) của `tdshook.dll`:
  ```cpp
  MH_Initialize();
  MH_CreateHook(targetAddr, &Hook_GetQuote, (void**)&orig_GetQuote);
  MH_EnableHook(targetAddr);
  ```
- Hook function override spread:
  ```cpp
  // chữ ký GIẢ ĐỊNH — phải xác định bằng RE (5C)
  double __thiscall Hook_GetQuote(void* self, int tickTime) {
      double real = orig_GetQuote(self, tickTime);
      double sp   = SpreadStore::lookup(tickTime);  // shared mem từ orchestrator
      // áp spread động lên giá trả về
      return apply_spread(real, sp);
  }
  ```
- Cấu hình spread truyền vào hook qua **shared memory / named pipe** từ Module 6.

### 5.3 ⭐ (5C) SigScanner — tool tìm offset (cái bạn hỏi)

**Sự thật cần nắm:** không có cách "auto tìm hàm chưa biết". Quy trình thực tế:
> **Neo vào thứ đã biết (string/import/hằng số) → quét AOB signature → ra địa chỉ.**
> Signature bền hơn offset thô nên tự nhận diện qua nhiều build.

**Thành phần tool:**

**(i) AOB Signature Scanner (C++)** — tự build:
```cpp
// pattern dạng "48 8B ?? ?? 89 ?? E8" (?? = wildcard)
uintptr_t FindPattern(uintptr_t base, size_t size,
                      const char* pattern);   // trả RVA/địa chỉ
// chế độ: quét file PE trên đĩa (tính RVA) HOẶC quét memory process đang chạy
```
Output: in ra RVA + offset file + địa chỉ runtime của mọi match.

**(ii) Ghidra helper script (Python/Jython)** — khoanh vùng ứng viên:
```python
# find_candidates.py — chạy trong Ghidra
# 1. liệt kê hàm tham chiếu string liên quan: "Bid","Ask","spread","TestGenerator"
# 2. liệt kê xref tới import nghi vấn
# 3. với mỗi ứng viên: trích 32-48 byte đầu hàm → sinh AOB signature (mask wildcards
#    các byte là địa chỉ tương đối/relocation)
# 4. xuất signatures.json để SigScanner dùng
```

**Quy trình end-to-end:**
```
1. Mở terminal.exe trong Ghidra → chạy find_candidates.py
2. Khoanh hàm "trả giá trong tester" (anchor: string/log/xref import time)
3. Verify thủ công bằng x64dbg: đặt breakpoint, chạy backtest, xem nó có bắn mỗi tick
4. Sinh signature cho hàm đó → lưu signatures.json
5. SigScanner tự tìm địa chỉ hàm này ở mọi máy/build có cùng signature
6. Hook engine (5B) đặt hook lên địa chỉ đó
7. Khi MT4 update gãy → chạy lại bước 1-5 (đây là chi phí maintenance của hướng C3)
```

> **Giới hạn trung thực:** SigScanner mình build sẽ **tự động hoá bước 5** (tìm địa chỉ từ
> signature) và **hỗ trợ bước 4** (sinh signature từ Ghidra). Bước 2-3 (xác định ĐÚNG hàm
> nào) là phán đoán RE bạn tự làm trên bản MT4 của mình — không tool nào auto 100% được,
> vì đó là hàm undocumented. Đây cũng chính là lý do TDS phải bán kèm maintenance.

---

## 6. Module 6 — GUI / Orchestrator

### 6.1 Mục tiêu
Bản sao trải nghiệm "Tick Data Manager" của TDS: quản lý symbol, tải data, build FXT,
cấu hình spread model, bật/tắt hook, chạy backtest.

### 6.2 Tính năng
- Danh sách symbol + coverage (đã tải bao nhiêu, thiếu khoảng nào).
- Nút: Download → Convert → Inject & Run.
- Cấu hình **Spread Model**: real / fixed / random / session-based / news-widen.
- Cấu hình slippage, commission.
- Trạng thái job (progress, log).
- Quản lý profile cho từng broker/symbol.

### 6.3 Stack
- **PySide6 (Qt)** — desktop, gần "cảm giác" TDS nhất.
- Gọi Module 1-3 trực tiếp (Python). Gọi Module 5 injector qua subprocess (exe C++).
- IPC với hook qua named pipe / shared memory để đẩy cấu hình spread runtime.

---

## 7. Cấu trúc thư mục dự án

```
tds-clone/
├── TDS_CLONE_ARCHITECTURE.md      # file này
├── pyproject.toml
├── python/
│   ├── tdsclone/
│   │   ├── download/              # Module 1
│   │   │   ├── dukascopy.py
│   │   │   └── histdata.py
│   │   ├── store/                 # Module 2
│   │   │   └── tickstore.py
│   │   ├── convert/               # Module 3
│   │   │   ├── fxt.py
│   │   │   ├── hst.py
│   │   │   └── spread_model.py
│   │   └── gui/                   # Module 6
│   │       └── app.py
│   └── tests/
├── native/
│   ├── ea_helper_dll/             # Module 4 (C++)
│   │   └── tdsclone.cpp
│   ├── injector/                  # Module 5A (C++)
│   │   └── injector.cpp
│   ├── hook/                      # Module 5B (C++)
│   │   └── tdshook.cpp
│   ├── sigscan/                   # Module 5C (C++)
│   │   └── sigscan.cpp
│   └── third_party/minhook/
├── ghidra_scripts/                # Module 5C
│   └── find_candidates.py
└── mql4/
    └── TDSCloneTemplate.mq4       # Module 4 phía EA
```

---

## 8. Lộ trình vibe coding (ưu tiên cái chạy được sớm)

### Phase 1 — Pipeline data sạch (1-2 buổi) ✅ chạy được ngay, không cần MT4
- [ ] M1: Dukascopy downloader (1 symbol, 1 ngày) → verify decode đúng giá.
- [ ] M2: TickStore Parquet + coverage.
- [ ] M3: FxtBuilder model 0, spread cố định → **mở được trong MT4 tester**.
- [ ] Mốc nghiệm thu: backtest 1 EA đơn giản bằng FXT tự build, kết quả khớp data thật.

### Phase 2 — Variable spread không-inject (1 buổi)
- [ ] M3: SpreadModel (real spread từ data).
- [ ] M4: EA helper DLL + `#import` → spread động trong tester (chấp nhận sửa EA).
- [ ] Mốc: thấy spread thay đổi theo phiên trong report.

### Phase 3 — Lớp inject/hook "kiểu TDS" (nhiều buổi, fragile)
- [ ] M5A: Injector nạp DLL test (hook `MessageBoxW` để chứng minh pipeline).
- [ ] M5C: SigScanner + Ghidra script → tìm hàm giá trong terminal.exe.
- [ ] M5B: Hook hàm đó, override spread theo shared memory.
- [ ] Mốc: spread động **trong suốt**, EA không sửa gì.

### Phase 4 — GUI & hoàn thiện
- [ ] M6: PySide6 quản lý toàn bộ.
- [ ] Đóng gói installer.

---

## 9. Rủi ro & quyết định kiến trúc

| Rủi ro | Ảnh hưởng | Giảm thiểu |
|--------|-----------|-----------|
| FXT byte-layout sai theo build | Tester từ chối/crash | Dump FXT do MT4 sinh, so byte; unit test round-trip |
| Hook gãy khi MT4 update | Mất variable spread trong suốt | Dùng signature thay offset; fallback sang M4 (#import) |
| EULA MetaQuotes | Pháp lý vùng xám hướng C3 | Ưu tiên C1/C2; C3 chỉ trên máy cá nhân |
| Tháng 0-index Dukascopy | Tải sai data | Test mốc ngày đã biết |
| Antivirus cờ injector | Injector bị chặn | Self-sign, whitelist; injector là tiến trình riêng |

> **Khuyến nghị cuối:** Làm Phase 1-2 trước — đạt ~90% giá trị TDS, 100% sạch. Chỉ đầu tư
> Phase 3 nếu bắt buộc "trong suốt tuyệt đối với EA". Nếu chỉ cần tick-accurate backtest,
> cân nhắc **MT5 real ticks** (miễn phí, không cần module nào).
```
```

---

### Phụ lục A — Bảng tra point/digits (cần cho Dukascopy decode)
| Symbol | digits | point factor |
|--------|--------|--------------|
| EURUSD | 5 | 100000 |
| GBPUSD | 5 | 100000 |
| USDJPY | 3 | 1000 |
| XAUUSD | 3 | 1000 |
| (bổ sung theo symbol bạn dùng) | | |

### Phụ lục B — Lệnh khởi tạo nhanh (Windows target)

**Python (chạy được cả WSL lẫn Windows):**
```bash
mkdir -p tds-clone/{python/tdsclone/{download,store,convert,gui},native/{ea_helper_dll,injector,hook,sigscan},ghidra_scripts,mql4}
# Python deps: httpx, polars, pyarrow, pyside6, pytest
python -m pip install httpx polars pyarrow pyside6 pytest
```

**Native — build TRÊN WINDOWS (PowerShell, không phải WSL):**
```powershell
# Yêu cầu: Visual Studio 2022 (Desktop C++), CMake. Target x86 vì MT4 là 32-bit.
cd tds-clone\native
cmake -B build -A Win32                 # -A Win32 = ép 32-bit, KHỚP terminal.exe
cmake --build build --config Release
# MinHook: thêm như submodule -> native/third_party/minhook
```

**Đóng gói (Windows):**
```powershell
pyinstaller --onefile python\tdsclone\gui\app.py   # GUI -> exe
# rồi gom exe + các .dll native + Inno Setup -> setup.exe
```

> Nhắc lại: **injector chạy as Administrator**, và mọi test inject/hook làm trên Windows có
> MT4 thật — WSL chỉ để viết code + unit test phần Python.
