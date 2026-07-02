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
    Danh sach path file NGAY .tkd giao [from_ms,to_ms) cho native (fxt_virtual.cpp
    giai doan B) doc thang tung ngay (giai nen puff, windowed) — KHONG scratch, KHONG
    lam phinh kho. (Ten ham giu nguyen de tuong thich; thuc chat tra path ngay .tkd.)
    """
    return tick_store.day_paths(symbol, from_ms, to_ms)


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

    # Quet tick (numpy) chi de tinh metadata header (num_bars, total, from/to shifted).
    a = tick_store.load_range_np(sym, from_ms, to_ms)
    if a is None or len(a) == 0:
        raise ValueError(f'{sym}: khong co tick trong khoang')
    tsec = (a['t'] // 1000).astype('int64')
    if gmt_offset or dst:
        tsec = tsec + fxt_builder._tz_shift_vec(tsec, gmt_offset, dst)
    # Loc cuoi tuan (server time) — PHAI khop y het C++ NextTick (fxt_virtual.cpp):
    # bo tick Sat(6)/Sun(0) de khong sinh bar/lenh cuoi tuan (lich phien broker, giong TDS).
    wd = ((tsec // 86400) + 4) % 7          # 0=Sun .. 6=Sat
    keep = (wd != 0) & (wd != 6)
    tsec = tsec[keep]
    if len(tsec) == 0:
        raise ValueError(f'{sym}: khong con tick sau khi loc cuoi tuan')
    period_arr = (tsec // period_sec) * period_sec
    num_bars = int((np.diff(period_arr) != 0).sum()) + 1
    total = int(len(tsec))
    from_ts, to_ts = int(tsec[0]), int(tsec[-1])
    y0 = datetime.datetime.utcfromtimestamp(int(a['t'][0] // 1000)).year
    y1 = datetime.datetime.utcfromtimestamp(int(a['t'][-1] // 1000)).year
    del a, tsec, period_arr

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

    # Config cho hook C++
    dst_iv = _dst_intervals(y0, y1, dst)
    paths = _months_in_range(sym, from_ms, to_ms)
    with open(cfg_path, 'wb') as f:
        f.write(MAGIC)
        f.write(struct.pack('<II', 1, HEADER_SIZE))
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
        f.write(struct.pack('<I', len(paths)))
        for p in paths:
            pb = p.encode('utf-8')
            f.write(struct.pack('<I', len(pb)))
            f.write(pb)

    return dict(total=total, bars=num_bars, size=total_size,
                nbin=len(paths), placeholder=placeholder_fxt, cfg=cfg_path)


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
