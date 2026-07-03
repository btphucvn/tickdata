"""
Ghi shared memory cho native hook (tdshook.dll) doc — phien ban TOAN DIEN,
doc settings per-symbol (settings_store) + tick store + ap cong thuc spread TDS.

3 vung shared memory (Windows named):
  Local\\TDSClone_SpreadShm    : spread/tick cho tick-hook (magic TDSS hoac TDST)
  Local\\TDSClone_SlippageShm  : tham so slippage cho order-hook (magic TSLP)

Tuong thich nguoc voi orchestrator.py (cung magic/layout cho vung Spread).
Service goi cac ham build_* + ShmHolder de giu vung nho song trong khi backtest.
"""

import os
import sys
import struct
import ctypes
import ctypes.wintypes

sys.path.insert(0, os.path.dirname(__file__))

import tick_store
import symbols_meta
import settings_store
from spread_slippage import pack_slippage_params

SHM_SPREAD   = "Local\\TDSClone_SpreadShm"
SHM_SLIPPAGE = "Local\\TDSClone_SlippageShm"

MAGIC_SPREAD = 0x53534454   # 'TDSS'
MAGIC_TICK   = 0x54534454   # 'TDST'

_k32 = ctypes.windll.kernel32
_k32.CreateFileMappingW.restype = ctypes.wintypes.HANDLE
_k32.MapViewOfFile.restype      = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes   = [ctypes.c_void_p]
_k32.CloseHandle.argtypes       = [ctypes.wintypes.HANDLE]

PAGE_READWRITE = 0x04
FILE_MAP_ALL   = 0xF001F


# ---------------------------------------------------------------------------
# Build cac vung du lieu
# ---------------------------------------------------------------------------
def build_spread_from_store(symbol, point=None, settings=None,
                            from_ms=None, to_ms=None, progress=None):
    """
    Doc tick store, tinh spread that (ask-bid)/point tung tick, AP CONG THUC TDS
    (clamp(real*mult+add,min,max)) tu settings, tra bytes vung Spread (magic TDSS).
    """
    sym = symbol.upper()
    meta = symbols_meta.resolve(sym)
    if point is None:
        point = meta.point
    if settings is None:
        settings = settings_store.load(sym)

    # Shift time giong FXT (GMT+2/DST) de hook tra cuu spread khop tick time MT4
    from fxt_builder import tz_shift_sec
    gmt_offset, dst = int(settings.gmt_offset), int(settings.dst)
    apply_tz = (gmt_offset != 0 or dst != 0)

    records = []           # (time_sec, spread_pts sau cong thuc)
    n = 0
    src = (tick_store.iter_range(sym, from_ms, to_ms)
           if (from_ms is not None and to_ms is not None)
           else tick_store.iter_all(sym))
    for time_ms, bid, ask in src:
        real_pts = (ask - bid) / point
        sp = settings.spread_points_for(real_pts)
        if sp >= 0:
            ts = int(time_ms) // 1000
            if apply_tz:
                ts += tz_shift_sec(ts, gmt_offset, dst)
            records.append((ts, float(sp)))
        n += 1
        if progress and (n & 0x3FFFF) == 0:
            progress(n)

    if not records:
        raise ValueError(f"{sym}: store rong (chua tai data?)")

    # Bao quanh dau/cuoi de hook khong out-of-range
    t0, s0 = records[0]; t1, s1 = records[-1]
    records.insert(0, (t0 - 86400 * 30, s0))
    records.append((t1 + 86400 * 30, s1))

    count = len(records)
    data = struct.pack("<IId", MAGIC_SPREAD, count, point)
    data += b"".join(struct.pack("<if", t, s) for t, s in records)

    spreads = [s for _, s in records[1:-1]]
    avg = sum(spreads) / len(spreads)
    stats = dict(count=count, avg=avg, min=min(spreads), max=max(spreads), point=point)
    return data, stats


def write_spread_file(symbol, out_path, from_ms=None, to_ms=None, settings=None):
    """
    Ghi FILE spread that (cho hook doc truc tiep, KHONG qua SHM-process fragile).

    Format == SpreadHeader cua hook:
      uint32 magic='TDSS', uint32 count, double point, [int32 time, float spread]*N

    STREAM tung tick -> RAM thap (khong dung list 28M tuple). Time da SHIFT GMT+2/DST
    giong FXT -> hook tra cuu khop tick time MT4. Atomic: ghi .tmp roi rename.
    """
    from fxt_builder import tz_shift_sec
    sym = symbol.upper()
    meta = symbols_meta.resolve(sym)
    point = meta.point
    if settings is None:
        settings = settings_store.load(sym)
    gmt_offset, dst = int(settings.gmt_offset), int(settings.dst)
    apply_tz = (gmt_offset != 0 or dst != 0)

    src = (tick_store.iter_range(sym, from_ms, to_ms)
           if (from_ms is not None and to_ms is not None)
           else tick_store.iter_all(sym))

    tmp = out_path + ".tmp"
    count = 0
    first_ts = first_sp = None
    last_ts = last_sp = None
    sum_sp = 0.0
    with open(tmp, "wb") as f:
        f.write(struct.pack("<IId", MAGIC_SPREAD, 0, point))  # count placeholder
        for time_ms, bid, ask in src:
            real_pts = (ask - bid) / point
            sp = settings.spread_points_for(real_pts)
            if sp < 0:
                continue
            ts = int(time_ms) // 1000
            if apply_tz:
                ts += tz_shift_sec(ts, gmt_offset, dst)
            if first_ts is None:
                first_ts, first_sp = ts, sp
                # record bao truoc 30 ngay (tranh out-of-range)
                f.write(struct.pack("<if", ts - 86400 * 30, float(sp)))
                count += 1
            f.write(struct.pack("<if", ts, float(sp)))
            count += 1
            last_ts, last_sp = ts, sp
            sum_sp += sp
        if last_ts is not None:
            f.write(struct.pack("<if", last_ts + 86400 * 30, float(last_sp)))
            count += 1
        f.seek(4)
        f.write(struct.pack("<I", count))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)   # atomic
    avg = (sum_sp / (count - 2)) if count > 2 else 0
    return dict(count=count, avg=avg, point=point)


def write_spread_file_fast(symbol, out_path, from_ms, to_ms, settings=None):
    """
    Nhu write_spread_file nhung VECTORIZED (numpy) — nhanh hon ~10-30x.
    Doc tick_store bulk, tinh spread + tz-shift vector, ghi 1 lan.
    """
    import numpy as np
    import tick_store
    from fxt_builder import _tz_shift_vec
    sym = symbol.upper()
    meta = symbols_meta.resolve(sym)
    point = meta.point
    if settings is None:
        settings = settings_store.load(sym)
    gmt, dst = int(settings.gmt_offset), int(settings.dst)

    a = tick_store.load_range_np(sym, from_ms, to_ms)
    if a is None or len(a) == 0:
        raise ValueError(f'{sym}: store rong (chua tai data?)')

    real = (a['ask'] - a['bid']) / point                       # spread thuc (points)
    if getattr(settings, 'use_variable_spread', True):
        sp = real * settings.spread_multiplier + settings.spread_addition
        if settings.min_spread > 0: sp = np.maximum(sp, float(settings.min_spread))
        if settings.max_spread > 0: sp = np.minimum(sp, float(settings.max_spread))
        sp = np.maximum(sp, 0.0)
    else:
        sp = np.full(len(a), float(settings.spread_addition))

    ts = (a['t'] // 1000).astype('int64')
    if gmt or dst:
        ts = ts + _tz_shift_vec(ts, gmt, dst)
    ts = ts.astype('<i4'); sp = sp.astype('<f4')

    n = len(ts)
    rec = np.empty(n + 2, dtype=[('t', '<i4'), ('sp', '<f4')])   # bao 2 record ±30 ngay
    rec['t'][1:n+1] = ts;      rec['sp'][1:n+1] = sp
    rec['t'][0]   = int(ts[0])  - 86400 * 30; rec['sp'][0]   = sp[0]
    rec['t'][n+1] = int(ts[-1]) + 86400 * 30; rec['sp'][n+1] = sp[-1]
    count = n + 2

    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<IId", MAGIC_SPREAD, count, point))
        rec.tofile(f)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, out_path)
    return dict(count=count, avg=float(sp.mean()), point=point)


def write_spread_stub(symbol, out_path, settings=None):
    """
    Ghi active.spread TI HON (chi header TDSS + 2 record bien), KHONG nap/giai nen data.

    *** TOC DO (2026-07) ***: o mode variable-spread ('TDSS'), hook (tdshook.cpp)
    KHONG doc than file — LookupSpread la DEAD CODE; spread that lay tu
    vfxt::RealSpreadPrice() doc thang .bin on-demand (khop tick bang bid, dung ca diem
    gap). Hook chi can header (magic 'TDSS' -> chon mode) + file map duoc (OpenShm).
    Vi vay ghi 23.7M record moi range la LANG PHI -> ghi stub. KET QUA BACKTEST Y HET
    (than file khong duoc doc). Neu can lai full body (vd debug LookupSpread) dung
    write_spread_file_fast.
    """
    sym = symbol.upper()
    point = symbols_meta.resolve(sym).point
    # 2 record bien phu toan mien thoi gian (neu co cong cu nao do doc -> tra 0).
    rec = struct.pack("<ifif", 0, 0.0, 0x7FFFFFFF, 0.0)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<IId", MAGIC_SPREAD, 2, point))
        f.write(rec)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, out_path)
    return dict(count=2, avg=0.0, point=point, stub=True)


def build_tick_from_store(symbol, point=None, from_ms=None, to_ms=None):
    """
    Vung Tick (magic TDST): nhet bid+ask THAT — dung khi KHONG deploy FXT.
    Layout: magic, mode=1, point(double), count(uint64), [int64 ms, double bid, double ask]*N
    """
    sym = symbol.upper()
    meta = symbols_meta.resolve(sym)
    if point is None:
        point = meta.point
    src = (tick_store.iter_range(sym, from_ms, to_ms)
           if (from_ms is not None and to_ms is not None)
           else tick_store.iter_all(sym))
    body = bytearray()
    count = 0
    for time_ms, bid, ask in src:
        body += struct.pack("<qdd", int(time_ms), float(bid), float(ask))
        count += 1
    if count == 0:
        raise ValueError(f"{sym}: store rong")
    header = struct.pack("<IIdQ", MAGIC_TICK, 1, point, count)
    return bytes(header) + bytes(body), dict(count=count, point=point)


def build_slippage(symbol=None, settings=None, seed=12345):
    """Block tham so slippage (magic TSLP) cho order-hook."""
    if settings is None:
        settings = settings_store.load((symbol or "EURUSD").upper())
    return pack_slippage_params(settings, seed=seed)


# ---------------------------------------------------------------------------
# Giu shared memory song (RAII): tao -> map -> copy -> giu handle
# ---------------------------------------------------------------------------
class ShmHolder:
    """Giu 1 vung shared memory song. Goi close() khi xong."""

    def __init__(self):
        self._handles = []

    def publish(self, name, data):
        size = len(data)
        h = _k32.CreateFileMappingW(
            ctypes.wintypes.HANDLE(-1), None, PAGE_READWRITE, 0, size, name)
        if not h:
            raise RuntimeError(f"CreateFileMappingW({name}) loi: {ctypes.GetLastError()}")
        ptr = _k32.MapViewOfFile(h, FILE_MAP_ALL, 0, 0, size)
        if not ptr:
            _k32.CloseHandle(h)
            raise RuntimeError(f"MapViewOfFile({name}) loi: {ctypes.GetLastError()}")
        ctypes.memmove(ptr, data, size)
        _k32.UnmapViewOfFile(ptr)
        # GIU handle song -> vung nho ton tai. (Khong unmap handle.)
        self._handles.append((name, h))
        return size

    def close(self):
        for _, h in self._handles:
            _k32.CloseHandle(h)
        self._handles.clear()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()


# ---------------------------------------------------------------------------
# Tien ich cap cao: publish day du cho 1 symbol (spread + slippage)
# ---------------------------------------------------------------------------
def publish_for_symbol(symbol, mode="spread", from_ms=None, to_ms=None,
                       holder=None, progress=None):
    """
    Publish shared memory cho 1 symbol theo settings hien hanh.
      mode="spread": deploy FXT + override spread bien dong (khuyen dung).
      mode="tick"  : nhet ca bid+ask that (khi khong co FXT).
    Tra (holder, info).
    """
    sym = symbol.upper()
    settings = settings_store.load(sym)
    holder = holder or ShmHolder()

    if mode == "tick":
        data, info = build_tick_from_store(sym, from_ms=from_ms, to_ms=to_ms)
    else:
        data, info = build_spread_from_store(sym, settings=settings,
                                             from_ms=from_ms, to_ms=to_ms,
                                             progress=progress)
    holder.publish(SHM_SPREAD, data)

    slip = build_slippage(symbol=sym, settings=settings)
    holder.publish(SHM_SLIPPAGE, slip)

    info["mode"] = mode
    info["slippage_enabled"] = settings.slippage_enabled
    return holder, info


if __name__ == "__main__":
    import argparse, time, datetime
    ap = argparse.ArgumentParser(description="TDS Clone — publish spread that vao SHM")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--mode", choices=["spread", "tick"], default="spread")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to",   dest="date_to",   default=None)
    args = ap.parse_args()

    from_ms = to_ms = None
    if args.date_from and args.date_to:
        d0 = datetime.datetime.fromisoformat(args.date_from).replace(tzinfo=datetime.timezone.utc)
        d1 = (datetime.datetime.fromisoformat(args.date_to).replace(tzinfo=datetime.timezone.utc)
              + datetime.timedelta(days=1))
        from_ms = int(d0.timestamp() * 1000)
        to_ms   = int(d1.timestamp() * 1000)

    print(f"[*] Build spread that {args.symbol} {args.date_from}..{args.date_to} ...")
    holder, info = publish_for_symbol(args.symbol, mode=args.mode,
                                      from_ms=from_ms, to_ms=to_ms)
    print(f"[OK] Published spread that: {info}")
    print("    Hook se ap ask = bid + spread that. Giu tien trinh nay trong khi backtest.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        holder.close()
