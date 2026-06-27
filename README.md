# TDS-Clone — Tick Data Suite (functional clone)

Bộ công cụ **tải tick-data chất lượng cao → lưu trữ → convert sang định dạng MT4
Strategy Tester (`.fxt`/`.hst`) → mô phỏng variable spread**, đạt chức năng tương
đương [Tick Data Suite](https://eareview.net/tick-data-suite). Kiến trúc chi tiết:
[`TDS_CLONE_ARCHITECTURE.md`](TDS_CLONE_ARCHITECTURE.md).

> ⚖️ **Pháp lý:** toàn bộ là code mới, không reverse binary của TDS. Lớp inject/hook
> (Module 5) chỉ chạy trên MT4 của chính bạn để backtest EA của bạn (dual-use).

## Điểm thiết kế quan trọng

- **Phần lõi (Module 1/2/3) chỉ dùng standard library** → chạy & test ngay trên
  WSL/Linux/Windows, **không cần `pip install` gì cả**.
- Phụ thuộc nặng là **tuỳ chọn**: `PySide6` (GUI), `pyarrow` (Parquet), `polars` (tốc độ).
- Phần native (Module 4/5) là C++/Windows-only — viết được ở WSL nhưng **build trên Windows**.

## Bản đồ module

| Module | Ngôn ngữ | Vị trí | Trạng thái |
|--------|----------|--------|-----------|
| 1. Downloader (Dukascopy/HistData) | Python | [`python/tdsclone/download/`](python/tdsclone/download/) | ✅ chạy được |
| 2. Tick Store (SQLite + columnar/Parquet) | Python | [`python/tdsclone/store/`](python/tdsclone/store/) | ✅ chạy được |
| 3. FXT/HST Builder + SpreadModel | Python | [`python/tdsclone/convert/`](python/tdsclone/convert/) | ✅ chạy được |
| 4. EA Helper DLL (`#import`, C2) | C++ | [`native/ea_helper_dll/`](native/ea_helper_dll/) | ✍️ build trên Windows |
| 5A. Injector | C++ | [`native/injector/`](native/injector/) | ✍️ build trên Windows |
| 5B. Hook engine (MinHook) | C++ | [`native/hook/`](native/hook/) | ✍️ build trên Windows |
| 5C. SigScanner + Ghidra script | C++ / Jython | [`native/sigscan/`](native/sigscan/), [`ghidra_scripts/`](ghidra_scripts/) | ✍️ build trên Windows |
| 6. GUI / Orchestrator | Python (PySide6) | [`python/tdsclone/gui/`](python/tdsclone/gui/) | ✅ (cần PySide6) |
| 6'. CLI orchestrator | Python | [`python/tdsclone/cli.py`](python/tdsclone/cli.py) | ✅ chạy được |

## Cài đặt

```bash
# Lõi (không bắt buộc cài gì — chạy trực tiếp được). Để có lệnh CLI + GUI:
pip install -e .            # chỉ lõi
pip install -e ".[gui]"     # + giao diện PySide6
pip install -e ".[all]"     # + Parquet + polars + pytest
```

## Dùng nhanh (CLI)

```bash
# 1) Tải tick Dukascopy (tháng 0-index đã xử lý sẵn)
python -m tdsclone.cli download EURUSD 2024-01-02 2024-01-03

# 2) Xem đã có dữ liệu khoảng nào
python -m tdsclone.cli coverage EURUSD

# 3) Build .fxt (+.hst, +.tdspread) với spread model "real"
python -m tdsclone.cli build EURUSD --period 1 \
    --from 2024-01-02 --to 2024-01-03 --spread real --out out

# 4) Soi header FXT để verify layout (so với .fxt do MT4 sinh)
python -m tdsclone.cli inspect-fxt out/EURUSD1_0.fxt
```

Sau `pip install -e .` có thể gọi gọn `tdsclone ...` và `tdsclone-gui`.

## GUI

```bash
pip install -e ".[gui]"
python -m tdsclone.gui.app      # hoặc: tdsclone-gui
```

4 tab: **Download** → **Build FXT** (form cấu hình SpreadModel động) → **Coverage**
→ **Log**. Tác vụ nặng chạy trong thread riêng nên giao diện không đơ.

## Spread models (Module 3.4)

| Tên | Ý nghĩa |
|-----|---------|
| `real` | dùng spread thật từ data (`ask-bid`), có `min_points`/`multiplier` |
| `fixed` | spread cố định N points |
| `random` | ngẫu nhiên trong `[min, max]` points (có seed) |
| `session` | theo phiên Á/Âu/Mỹ (giãn lúc thanh khoản thấp) |
| `news` | bọc model nền, giãn spread quanh rollover 22:00 UTC / mốc tin |

3 chiến lược áp spread (mục 3.4): **C1** bake vào FXT · **C2** EA `#import` DLL
(`.tdspread`) · **C3** hook runtime trong suốt (Module 5 + shared memory).

## Triển khai cho MT4 (Windows)

- File `.fxt` → `<terminal>\tester\history\`
- File `.hst` → `<terminal>\history\<server>\`
- Đường sạch khuyến nghị (C2): build `tdsclone.dll`, dùng EA
  [`mql4/TDSCloneTemplate.mq4`](mql4/TDSCloneTemplate.mq4), nạp `.tdspread`.
- Chi tiết build native: [`native/README.md`](native/README.md).

## Chạy test

```bash
python python/tests/test_pipeline.py      # không cần pytest
# hoặc:
pip install -e ".[dev]" && pytest
```

## Cấu trúc thư mục

```
tickdata/
├── pyproject.toml
├── python/tdsclone/
│   ├── model.py          # canonical tick (TickFrame)
│   ├── symbols.py        # bảng digits/point (Phụ lục A)
│   ├── download/         # Module 1 (dukascopy.py, histdata.py)
│   ├── store/            # Module 2 (tickstore.py)
│   ├── convert/          # Module 3 (fxt.py, hst.py, spread_model.py, spread_file.py)
│   ├── gui/app.py        # Module 6 (PySide6)
│   ├── cli.py            # Module 6 headless
│   ├── pipeline.py       # orchestrator dùng chung
│   └── ipc.py            # cầu shared-memory cho hook (C3)
├── python/tests/
├── native/               # Module 4 & 5 (C++), build trên Windows
├── ghidra_scripts/       # Module 5C find_candidates.py
└── mql4/TDSCloneTemplate.mq4
```

## Lộ trình (mục 8)

- **Phase 1–2** (đã hiện thực bằng Python, ~90% giá trị, 100% sạch): download →
  store → FXT/HST → variable spread qua `#import`.
- **Phase 3** (C++ trên Windows, fragile): injector + hook + sigscan cho variable
  spread *trong suốt* với EA. Chỉ làm nếu thật sự cần.
