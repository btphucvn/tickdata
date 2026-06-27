"""
Cầu IPC orchestrator (Module 6) ↔ hook DLL (Module 5B) — chiến lược C3.

Hook ``tdshook.dll`` đọc bảng spread từ một SHARED MEMORY có tên
``Local\\TDSClone_SpreadShm``. Module này (Python, chạy phía orchestrator) GHI
bảng đó để hook tra cứu runtime.

Layout (khớp ``native/hook/tdshook.cpp`` -> struct SpreadShmHeader/Record):
    magic   : uint32 = 0x53534454  (b"TDSS" little-endian)
    count   : uint32
    records : count * { int32 unixTime ; float points }

⚠️ CHỈ CÓ NGHĨA TRÊN WINDOWS: named shared memory dạng ``Local\\...`` là khái niệm
Windows. Trên WSL/Linux module vẫn import được nhưng :func:`publish_spread_shm`
sẽ báo lỗi rõ ràng. Phần hook chỉ chạy trên Windows nên đây là đúng kỳ vọng.
"""

from __future__ import annotations

import struct
import sys

from tdsclone.convert.spread_model import SpreadModel
from tdsclone.model import TickFrame, US_PER_SEC
from tdsclone.symbols import get_symbol_spec

SHM_NAME = "TDSClone_SpreadShm"   # mmap tagname (Windows). Hook mở "Local\\<name>".
_MAGIC = 0x53534454               # 'TDSS'
_HEADER = struct.Struct("<II")    # magic, count
_REC = struct.Struct("<if")       # int32 unixTime, float points


def build_shm_bytes(symbol: str, ticks: TickFrame, spread_model: SpreadModel,
                    dedup_per_second: bool = True) -> bytes:
    """Dựng nội dung shared-memory (bytes) từ tick + model — test được mọi nền tảng."""
    spec = get_symbol_spec(symbol)
    point = spec.point
    recs: list[tuple[int, float]] = []
    last = -1
    for i in range(len(ticks)):
        sec = ticks.ts[i] // US_PER_SEC
        if dedup_per_second and sec == last:
            continue
        last = sec
        sp = spread_model.spread_points(ticks.ts[i], ticks.bid[i], ticks.ask[i], point)
        recs.append((int(sec), float(sp)))

    buf = bytearray(_HEADER.pack(_MAGIC, len(recs)))
    for sec, sp in recs:
        buf += _REC.pack(sec, sp)
    return bytes(buf)


def publish_spread_shm(symbol: str, ticks: TickFrame, spread_model: SpreadModel):
    """
    Ghi bảng spread vào named shared memory cho hook DLL đọc (Windows-only).

    Trả về đối tượng mmap (giữ tham chiếu để shared memory còn sống — đóng nó sẽ
    huỷ mapping). Gọi từ orchestrator TRƯỚC khi inject hook hoặc chạy backtest.
    """
    if not sys.platform.startswith("win"):
        raise RuntimeError(
            "publish_spread_shm chỉ chạy trên Windows (named shared memory). "
            "Trên WSL/Linux hãy dùng Module 4 (#import) hoặc build .tdspread file."
        )
    import mmap

    data = build_shm_bytes(symbol, ticks, spread_model)
    # tagname -> Windows tạo "Local\\TDSClone_SpreadShm"; hook mở đúng tên này.
    mm = mmap.mmap(-1, len(data), tagname=SHM_NAME, access=mmap.ACCESS_WRITE)
    mm.write(data)
    mm.flush()
    return mm  # caller giữ tham chiếu cho tới khi backtest xong
