# Tiến độ Clone TDS — LC2 (inject + hook usermode)

Tài liệu này tổng kết phần **mới thêm** để tiến tới "giống 100% cơ chế TDS", và cách
chạy end-to-end. Kiến trúc tổng thể: [`TDS_CLONE_ARCHITECTURE.md`](TDS_CLONE_ARCHITECTURE.md).

## Đối chiếu với TDS thật

| Thành phần TDS | File tương ứng trong clone | Trạng thái |
|---|---|---|
| `Tick Data Manager.exe` (GUI) | [python/tds_gui.py](python/tds_gui.py) | ✅ 5 tab: Download / Quản lý / **Settings** / **Service** / Log |
| `TDSService.exe` (service nền) | [python/tds_service.py](python/tds_service.py) | ✅ watcher auto-inject + giữ SHM + tray |
| `TDSLoader` (inject MT4) | [native/injector/injector.cpp](native/injector/injector.cpp) | ✅ usermode injector |
| Hook tick tester (`tdslib.dll`) | [native/hook/tdshook.cpp](native/hook/tdshook.cpp) | ✅ hook `Parent_tickgen_loop` (cần verify build) |
| GUI nhúng trong MT4 | [native/hook/gui_inject.cpp](native/hook/gui_inject.cpp) | ✅ checkbox + dialog |
| `ITickDataSettings` | [python/settings_store.py](python/settings_store.py) | ✅ đủ field, lưu `data/settings.db` |
| Variable spread formula | [python/settings_store.py](python/settings_store.py) · [shm_writer.py](python/shm_writer.py) | ✅ `clamp(real*mult+add,min,max)` |
| **Slippage model** | [python/spread_slippage.py](python/spread_slippage.py) · [native/hook/slippage.cpp](native/hook/slippage.cpp) | ⚠️ engine xong; **hook order-fill chưa RE** |
| `tdsstor64.dll` (storage 17MB) | [python/tick_store.py](python/tick_store.py) | ✅ `.bin` per-month (đơn giản hơn, đủ dùng) |
| `tdsdrv64.sys` (kernel driver) | — | ❌ cố tình bỏ (xem lý do bên dưới) |

## Các file mới thêm trong đợt này

1. **[python/settings_store.py](python/settings_store.py)** — settings per-symbol đúng theo
   `ITickDataSettings` của TDS (spread mult/add/min/max, ~20 tham số slippage, override
   digits/lot/stops/commission, GMT/DST). Lưu SQLite `data/settings.db`. Có sẵn hàm
   `spread_points_for()` áp đúng công thức spread của TDS.

2. **[python/spread_slippage.py](python/spread_slippage.py)** — mô hình slippage đầy đủ
   (latency / dealer-style / standard-deviation / custom-chance / favorable-chance), và
   `pack_slippage_params()` đóng gói tham số thành block nhị phân `TSLP` cho native.

3. **[python/shm_writer.py](python/shm_writer.py)** — đọc tick store + settings, áp công thức
   spread TDS, ghi 2 vùng shared memory:
   - `Local\TDSClone_SpreadShm` (spread biến động `TDSS`, hoặc tick `TDST`)
   - `Local\TDSClone_SlippageShm` (tham số slippage `TSLP`)
   Lớp `ShmHolder` giữ vùng nhớ sống.

4. **[python/tds_service.py](python/tds_service.py)** — service nền (vai trò TDSLoader+TDSService):
   tự phát hiện `terminal.exe` → inject `tdshook.dll`; giữ SHM cho symbol active; tự
   republish khi đổi symbol; tray icon (PySide6, không cần pystray).

5. **[native/hook/slippage.cpp](native/hook/slippage.cpp) + .h** — engine slippage C++ đọc
   block `TSLP`, RNG có seed (reproducible), khớp đúng mô hình Python. Đã wire vào
   `tdshook.cpp` (mở SHM lúc attach) và build thành công.

6. **GUI** — 2 tab mới: **Settings** (chỉnh toàn bộ field TDS per-symbol) và **Service**
   (bật/tắt auto-inject, chọn symbol/mode active, publish SHM).

## Luồng chạy end-to-end (giống TDS)

```
1. python python/tds_gui.py                 # GUI: Download data + chỉnh Settings
2. Tab "Service" -> Bật service             # (chạy GUI as Administrator để inject)
   (hoặc: python python/tds_service.py)
3. Mở MT4 -> service tự inject tdshook.dll
4. Strategy Tester: tick "Use my tick data" -> Start
   -> tick thật + variable spread (công thức TDS) áp trong suốt với EA
```

Build lại native khi sửa: `cmake --build native\build --config Release`.

## Còn lại để đạt "100% trong suốt" hoàn toàn

- **Hook order-fill cho slippage**: engine slippage đã sẵn sàng, nhưng để slippage áp
  *trong suốt* lúc khớp lệnh, cần RE thêm hàm khớp lệnh trong `terminal.exe` rồi gắn vào
  `Hook_OrderExecute` (scaffold + `ApplySlippageToFill` đã có trong
  [tdshook.cpp](native/hook/tdshook.cpp)). Trước khi RE xong, slippage có thể dùng qua
  đường `#import` EA ([mql4/TDSCloneTemplate.mq4](mql4/TDSCloneTemplate.mq4)).
- **Verify tick-hook trên đúng build MT4 của bạn**: xác nhận RVA `0xB84D90`
  (`Parent_tickgen_loop`) và offset tick struct `self+0x304` bằng x64dbg (đặt breakpoint,
  chạy backtest, xem bắn mỗi tick). Có sẵn 40+ signature ứng viên trong
  [ghidra_scripts/signatures.json](ghidra_scripts/signatures.json).

## Đã cố tình bỏ (so với TDS thật)

- **Kernel driver `tdsdrv64.sys`**: cần EV certificate (~$300-600/năm + pháp nhân doanh
  nghiệp), rủi ro BSOD, và **không cải thiện kết quả backtest** — chỉ phục vụ chống-AV và
  chống-crack của TDS. Usermode injector đã đủ chức năng.
- **Storage engine 17MB riêng**: `.bin` per-month đủ dùng cho mục tiêu backtest.
