"""
Module 3 ↔ 4 cầu nối — Spread File (precomputed).

Module 4 (EA Helper DLL, chiến lược C2) cần một file tra cứu ``timestamp -> spread``
để hàm ``TDS_SpreadAt(unixTime)`` đọc O(log n). File này do phía Python sinh ra từ
:class:`~tdsclone.convert.spread_model.SpreadModel`.

ĐỊNH DẠNG FILE ``.tdspread`` (little-endian, khớp với ``native/ea_helper_dll``):
    magic   : 8 byte  = b"TDSSPRD1"
    count   : uint32  = số bản ghi
    records : count * { int32 unixTime ; float spreadPoints }   (12 byte? -> 8 byte)
              -> mỗi record = int32 (4) + float (4) = 8 byte, SẮP XẾP theo unixTime tăng.

DLL sẽ binary-search theo unixTime và trả spreadPoints của mốc gần nhất <= unixTime.
"""

from __future__ import annotations

import struct
from pathlib import Path

from tdsclone.convert.spread_model import SpreadModel
from tdsclone.model import TickFrame, US_PER_SEC
from tdsclone.symbols import get_symbol_spec

_MAGIC = b"TDSSPRD1"
_REC = struct.Struct("<if")   # int32 unixTime, float spreadPoints — 8 byte
assert _REC.size == 8


def export_spread_file(path: Path | str, symbol: str, ticks: TickFrame,
                       spread_model: SpreadModel, dedup_per_second: bool = True) -> Path:
    """
    Sinh file ``.tdspread`` từ tick + spread model.

    ``dedup_per_second=True``: chỉ lưu 1 bản ghi/giây (đủ phân giải cho EA gọi theo
    ``TimeCurrent()`` vốn là giây) -> file nhỏ hơn nhiều.
    """
    spec = get_symbol_spec(symbol)
    point = spec.point
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    records: list[tuple[int, float]] = []
    last_sec = -1
    for i in range(len(ticks)):
        ts_us = ticks.ts[i]
        sec = ts_us // US_PER_SEC
        if dedup_per_second and sec == last_sec:
            continue
        last_sec = sec
        sp = spread_model.spread_points(ts_us, ticks.bid[i], ticks.ask[i], point)
        records.append((int(sec), float(sp)))

    with open(path, "wb") as fh:
        fh.write(_MAGIC)
        fh.write(struct.pack("<I", len(records)))
        for sec, sp in records:
            fh.write(_REC.pack(sec, sp))
    return path


def read_spread_file(path: Path | str) -> list[tuple[int, float]]:
    """Đọc lại file .tdspread (dùng để test round-trip với DLL)."""
    raw = Path(path).read_bytes()
    if raw[:8] != _MAGIC:
        raise ValueError("Không phải file .tdspread hợp lệ")
    (count,) = struct.unpack_from("<I", raw, 8)
    off = 12
    out = []
    for _ in range(count):
        sec, sp = _REC.unpack_from(raw, off)
        out.append((sec, sp))
        off += _REC.size
    return out
