"""
FXT ẢO (virtual) — giống cơ chế TDS: KHÔNG ghi file FXT 1.5GB ra đĩa.

Thay vào đó:
  1. Ghi 1 SPARSE placeholder FXT: logical size = 728 + N*56 (MT4 thấy đủ lớn) nhưng
     ~0 byte vật lý. Header 728B ghi thật (MT4 validate + trick read-only 99.9%).
     Vùng record là "thưa" (đọc ra toàn 0).
  2. Ghi config nhỏ data/active.fxtv: header + nguồn tick (.bin) + tz + range.
  3. tdshook.dll hook ReadFile: khi MT4 đọc vùng record của FXT (đọc ra 0), hook
     GHI ĐÈ bằng record THẬT sinh on-the-fly từ .bin (giống TDS feed từ .bfc).

Nhờ sparse file: GetFileSize/SetFilePointer/EOF do OS lo (không cần hook) — chỉ hook
ReadFile. An toàn hơn nhiều.

Config binary (little-endian) — phải KHỚP parser C++ (fxt_virtual.cpp):
  'FXTV'(4) | u32 ver=1 | u32 header_len=728 | u8[728] header |
  i32 period_sec | i32 gmt_offset | u32 dst_count | {i64 start,i64 end}*dst_count |
  i64 from_ms | i64 to_ms | u64 total_records | f64 point |
  u32 nbin | {u32 pathlen, char[pathlen] utf8}*nbin
"""

import os
import struct
import datetime
import ctypes
from ctypes import wintypes

import numpy as np
import tick_store
import symbols_meta
import fxt_builder

MAGIC = b'FXTV'
HEADER_SIZE = 728
RECORD_SIZE = 56

# --------------------------------------------------------------------------
# Sparse file (Windows): logical size lớn, vật lý ~0. GetFileSize tra ra size ảo.
# --------------------------------------------------------------------------
FSCTL_SET_SPARSE = 0x000900C4
GENERIC_WRITE = 0x40000000
CREATE_ALWAYS = 2
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_BEGIN = 0
INVALID_HANDLE = ctypes.c_void_p(-1).value


def _make_sparse_placeholder(path, header_bytes, total_size):
    """Tao sparse FXT: ghi header that (728B), dat logical size = total_size, ~0 vat ly."""
    k = ctypes.windll.kernel32
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    if os.path.exists(path):
        fxt_builder._set_readonly(path, False)
    h = k.CreateFileW(path, GENERIC_WRITE, 0, None, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
    if h == INVALID_HANDLE or h is None:
        raise OSError(f'CreateFileW placeholder loi: {ctypes.get_last_error()}')
    try:
        br = wintypes.DWORD(0)
        # Set SPARSE
        k.DeviceIoControl(h, FSCTL_SET_SPARSE, None, 0, None, 0, ctypes.byref(br), None)
        # Ghi header that
        buf = (ctypes.c_char * len(header_bytes)).from_buffer_copy(header_bytes)
        k.WriteFile(h, buf, len(header_bytes), ctypes.byref(br), None)
        # Dat logical size = total_size
        k.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong,
                                       ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
        k.SetFilePointerEx(h, ctypes.c_longlong(total_size), None, FILE_BEGIN)
        if not k.SetEndOfFile(h):
            raise OSError(f'SetEndOfFile loi: {ctypes.get_last_error()}')
    finally:
        k.CloseHandle(h)
    fxt_builder._set_readonly(path, True)   # read-only -> MT4 dung real ticks 99.9%


# --------------------------------------------------------------------------
def _dst_intervals(y0, y1, dst):
    """Cac khoang [start,end) UTC-sec ma DST active (+3600). Khop tz_shift_sec Python."""
    ivs = []
    if dst not in (1, 2):
        return ivs
    import calendar
    for y in range(y0, y1 + 1):
        if dst == 1:  # US
            s = fxt_builder._nth_sunday(y, 3, 2)
            e = fxt_builder._nth_sunday(y, 11, 1)
        else:         # EU: CN cuoi thang 3 -> CN cuoi thang 10
            def last_sun(mo):
                last = calendar.monthrange(y, mo)[1]
                d = datetime.date(y, mo, last)
                lsd = last - ((d.weekday() - 6) % 7)
                return int(datetime.datetime(y, mo, lsd, tzinfo=datetime.timezone.utc).timestamp())
            s, e = last_sun(3), last_sun(10)
        ivs.append((s, e))
    return ivs


def _months_in_range(symbol, from_ms, to_ms):
    """
    Danh sach path .bin cua cac thang giao [from_ms,to_ms) cho native MMAP.
    Thang nen (.tkz) duoc BUNG ra VUNG TAM (data/_materialized), store GIU NGUYEN nen
    -> backtest KHONG lam phinh store, KHONG can nen lai sau backtest (giong TDS).
    """
    return tick_store.materialize_paths(symbol, from_ms, to_ms)


def write_virtual(symbol, period_min, cfg_path, placeholder_fxt,
                  from_ms, to_ms, gmt_offset=0, dst=0,
                  swap_long=None, swap_short=None, server_name=None):
    """
    Dung FXT ao: sparse placeholder + config. KHONG ghi 1.5GB record.
    Tra dict thong tin (total, bars, size).
    """
    sym = symbol.upper()
    meta = symbols_meta.resolve(sym)
    point = meta.point
    period_sec = period_min * 60

    # *** TOC DO (2026-07): metadata header lay tu daymeta (cache SQLite) — CONG theo
    # ngay O(so ngay), KHONG giai nen toan range moi Start. Ket qua (total/num_bars/
    # from_ts/to_ts) khop Y HET vong numpy cu (loc cuoi tuan gio server + bucket period;
    # bucket <=D1 khong vat qua ranh ngay -> sum per-day = tong distinct bucket). ***
    total, num_bars, from_ts, to_ts = tick_store.range_meta(
        sym, from_ms, to_ms, period_min, gmt_offset, dst)
    if total == 0:
        raise ValueError(f'{sym}: khong co tick trong khoang (sau loc cuoi tuan)')
    # y0..y1 cho _dst_intervals: dung nam UTC cua from/to (superset khoang tick) -> du.
    y0 = datetime.datetime.utcfromtimestamp(from_ms / 1000).year
    y1 = datetime.datetime.utcfromtimestamp(max(from_ms, to_ms - 1) / 1000).year

    # Header (dung _pack_header giong build_fxt -> byte-identical)
    if server_name is None:
        server_name = fxt_builder.detect_server_name()
    if swap_long is None or swap_short is None:
        bs = fxt_builder._broker_swap(sym)
        d = fxt_builder.SWAP_DEFAULTS.get(sym.split('.')[0], (0.0, 0.0, 0))
        if swap_long is None:  swap_long = bs[0] if bs else d[0]
        if swap_short is None: swap_short = bs[1] if bs else d[1]
    header = fxt_builder._pack_header(sym, period_min, from_ts, to_ts, num_bars, total,
                                      server_name=server_name, swap_long=swap_long,
                                      swap_short=swap_short, swap_type=0)
    assert len(header) == HEADER_SIZE

    total_size = HEADER_SIZE + total * RECORD_SIZE
    _make_sparse_placeholder(placeholder_fxt, header, total_size)

    # Config cho hook C++ — VER 2 (windowed .tkd giong TDS): liet ke day .tkd + bounds,
    # KHONG materialize .bin (bo 11GB scratch). Native giai nen theo ngay (LRU) -> chay
    # 10+ nam trong MT4 32-bit (khong mmap toan bo).
    dst_iv = _dst_intervals(y0, y1, dst)
    days = tick_store.day_paths_meta(sym, from_ms, to_ms)
    with open(cfg_path, 'wb') as f:
        f.write(MAGIC)
        f.write(struct.pack('<II', 2, HEADER_SIZE))      # ver=2
        f.write(header)
        f.write(struct.pack('<ii', period_sec, int(gmt_offset)))
        f.write(struct.pack('<I', len(dst_iv)))
        for s, e in dst_iv:
            f.write(struct.pack('<qq', s, e))
        f.write(struct.pack('<qq', int(from_ms), int(to_ms)))
        f.write(struct.pack('<Q', total))
        f.write(struct.pack('<d', point))
        # ten file placeholder (basename) -> hook C++ nhan dien handle FXT ao
        phname = os.path.basename(placeholder_fxt).encode('utf-8')
        f.write(struct.pack('<I', len(phname)))
        f.write(phname)
        # nday | { pathlen, path_utf8, first_ms(i64), last_ms(i64), count(u32) }*nday
        f.write(struct.pack('<I', len(days)))
        for (p, fms, lms, cnt) in days:
            pb = p.encode('utf-8')
            f.write(struct.pack('<I', len(pb)))
            f.write(pb)
            f.write(struct.pack('<qqI', fms, lms, cnt))

    return dict(total=total, bars=num_bars, size=total_size,
                nbin=len(days), placeholder=placeholder_fxt, cfg=cfg_path)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='FXT ao (sparse + config) giong TDS')
    ap.add_argument('--symbol', required=True)
    ap.add_argument('--period', type=int, default=240)
    ap.add_argument('--from', dest='dfrom', required=True)
    ap.add_argument('--to', dest='dto', required=True)
    ap.add_argument('--gmt', type=int, default=2)
    ap.add_argument('--dst', type=int, default=1)
    a = ap.parse_args()
    d0 = datetime.datetime.fromisoformat(a.dfrom).replace(tzinfo=datetime.timezone.utc)
    d1 = (datetime.datetime.fromisoformat(a.dto).replace(tzinfo=datetime.timezone.utc)
          + datetime.timedelta(days=1))
    from_ms, to_ms = int(d0.timestamp()*1000), int(d1.timestamp()*1000)
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = os.path.join(proj, 'data', 'active.fxtv')
    dirs = fxt_builder.find_mt4_tester_dirs()
    ph = os.path.join(dirs[0], f'{a.symbol.upper()}{a.period}_0.fxt') if dirs else \
         os.path.join(proj, 'data', 'fxt', f'{a.symbol.upper()}{a.period}_0.fxt')
    info = write_virtual(a.symbol, a.period, cfg, ph, from_ms, to_ms,
                         gmt_offset=a.gmt, dst=a.dst)
    print(f'[OK] FXT ao: {info["total"]:,} records, {info["bars"]:,} bars, '
          f'logical {info["size"]/1024/1024:.0f} MB (sparse ~0 vat ly)')
    print(f'     placeholder: {ph}')
    print(f'     config: {cfg} ({info["nbin"]} bin files)')
