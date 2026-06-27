"""
TDS-Clone — Tick Data Suite functional clone.

Gói Python này hiện thực Module 1/2/3/6 của kiến trúc trong
``TDS_CLONE_ARCHITECTURE.md``:

* :mod:`tdsclone.download`  — Module 1: tải tick-data (Dukascopy, HistData).
* :mod:`tdsclone.store`     — Module 2: Tick Store (chuẩn hoá + lưu trữ + query).
* :mod:`tdsclone.convert`  — Module 3: build ``.fxt`` / ``.hst`` cho MT4, SpreadModel.
* :mod:`tdsclone.gui`      — Module 6: giao diện PySide6 (orchestrator).
* :mod:`tdsclone.cli`      — orchestrator dòng lệnh (không cần GUI).

Triết lý phụ thuộc: phần lõi chỉ dùng standard library nên chạy được ngay trên
WSL/Linux/Windows mà không cần cài thêm gói nào. Các module native (4/5) là C++
và nằm ở thư mục ``native/`` — không build được ở WSL (xem mục 0.5 của kiến trúc).
"""

from __future__ import annotations

__version__ = "0.1.0"

# Re-export các lớp hay dùng để import gọn:  from tdsclone import TickStore, ...
from tdsclone.model import Tick, TickFrame  # noqa: E402
from tdsclone.symbols import SymbolSpec, get_symbol_spec  # noqa: E402

__all__ = [
    "__version__",
    "Tick",
    "TickFrame",
    "SymbolSpec",
    "get_symbol_spec",
]
