"""
TDS Clone orchestrator — tao shared memory voi spread data thuc tu Dukascopy.

3 che do:
  Mode 0a (flat spread):      chi override spread co dinh.
  Mode 0b (real spread):      tinh spread (ask-bid)/point tung tick, phuc vu qua hook.
  Mode 1  (full tick inject): inject ca bid + ask thuc (dung khi KHONG co FXT).

Khuyen dung ket hop: deploy FXT (bid thuc) + orchestrator Mode 0b (spread thuc)
-> EA nhan bid thuc + ask thuc, hoan toan giong TDS.

Usage:
  # Spread thuc tu Dukascopy (ket hop voi FXT da deploy):
  python orchestrator.py --spread-from-ticks ticks.bin --point 0.00001

  # Spread co dinh:
  python orchestrator.py --spread 2.0 --point 0.00001

  # Tick mode (inject ca bid+ask, dung khi KHONG co FXT):
  python orchestrator.py --ticks ticks.bin --point 0.00001

Shared memory name: "Local\\TDSClone_SpreadShm"

Layout (mode 0):
  uint32 magic='TDSS', uint32 count, double point, [int32 time, float spread_pts] x N

Layout (mode 1):
  uint32 magic='TDST', uint32 mode=1, double point, uint64 count,
  [int64 time_ms, double bid, double ask] x N
"""

import struct
import ctypes
import ctypes.wintypes
import argparse
import sys
import os
import time
import datetime

SHM_NAME_SPREAD = "Local\\TDSClone_SpreadShm"
MAGIC_SPREAD = 0x53534454   # 'TDSS' - spread mode
MAGIC_TICK   = 0x54534454   # 'TDST' - tick mode

_k32 = ctypes.windll.kernel32
_k32.CreateFileMappingW.restype = ctypes.wintypes.HANDLE
_k32.MapViewOfFile.restype      = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes   = [ctypes.c_void_p]
_k32.CloseHandle.argtypes       = [ctypes.wintypes.HANDLE]

PAGE_READWRITE = 0x04
FILE_MAP_ALL   = 0xF001F


# ---------------------------------------------------------------------------
# Mode 0a: spread co dinh
# ---------------------------------------------------------------------------
def build_spread_shm(spread_pts, point):
    now = int(time.time())
    span = 5 * 365 * 86400
    records = [(now - span, float(spread_pts)), (now + span, float(spread_pts))]
    count = len(records)
    data  = struct.pack("<IId", MAGIC_SPREAD, count, point)
    data += b"".join(struct.pack("<if", t, s) for t, s in records)
    return data


# ---------------------------------------------------------------------------
# Mode 0b: spread thuc tu Dukascopy (ask-bid)/point cho tung tick
# ---------------------------------------------------------------------------
def build_spread_from_ticks(tick_bin_path, point):
    """
    Doc file .bin tu download_ticks.py, tinh spread = (ask-bid)/point tung tick.
    Luu thanh series [int32 time_sec, float spread_pts] de hook tra cuu theo thoi gian.
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))
    from download_ticks import load_ticks

    print(f"[*] Doc tick: {tick_bin_path}")
    ticks = load_ticks(tick_bin_path)
    print(f"[*] {len(ticks):,} ticks -> tinh spread thuc")

    records = []
    for time_ms, bid, ask in ticks:
        sp = (ask - bid) / point
        if sp > 0:
            records.append((int(time_ms) // 1000, float(sp)))

    # Them record dau/cuoi de tranh out-of-range
    if records:
        t0, s0 = records[0]
        t1, s1 = records[-1]
        records.insert(0, (t0 - 86400 * 30, s0))
        records.append((t1 + 86400 * 30, s1))

    count = len(records)
    data  = struct.pack("<IId", MAGIC_SPREAD, count, point)
    data += b"".join(struct.pack("<if", t, s) for t, s in records)

    # Thong ke spread
    spreads = [s for _, s in records[1:-1]]
    avg_sp = sum(spreads) / len(spreads) if spreads else 0
    print(f"[*] Spread trung binh: {avg_sp:.2f} pts  (min={min(spreads):.1f}  max={max(spreads):.1f})")

    return data


# ---------------------------------------------------------------------------
# Mode 1: tick mode — load file nhi phan do download_ticks.py tao
# ---------------------------------------------------------------------------
def load_tick_file(path):
    import os
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"TDST":
            raise ValueError(f"{path}: khong phai file TDST")
        version, = struct.unpack("<I", f.read(4))
        count,   = struct.unpack("<Q", f.read(8))
        raw = f.read()   # phan con lai: N x (int64 + double + double) = N x 24
    return count, raw    # raw la bytes chua cac tick


def build_tick_shm(tick_path, point):
    count, tick_bytes = load_tick_file(tick_path)
    # Header: magic(4) + mode(4) + point(8) + count(8) = 24 bytes
    header = struct.pack("<IIdQ", MAGIC_TICK, 1, point, count)
    return header + tick_bytes


# ---------------------------------------------------------------------------
# Ghi shared memory (Windows named shared memory)
# ---------------------------------------------------------------------------
def write_shm(data, name=SHM_NAME_SPREAD):
    size = len(data)
    h = _k32.CreateFileMappingW(
        ctypes.wintypes.HANDLE(-1), None, PAGE_READWRITE, 0, size, name)
    if not h:
        raise RuntimeError(f"CreateFileMappingW failed: {ctypes.GetLastError()}")

    ptr = _k32.MapViewOfFile(h, FILE_MAP_ALL, 0, 0, size)
    if not ptr:
        _k32.CloseHandle(h)
        raise RuntimeError(f"MapViewOfFile failed: {ctypes.GetLastError()}")

    ctypes.memmove(ptr, data, size)
    _k32.UnmapViewOfFile(ptr)
    return h


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="TDS Clone orchestrator")
    ap.add_argument("--symbol",  default="EURUSD")
    ap.add_argument("--spread",  type=float, default=2.0,
                    help="Spread co dinh (points) - mode 0a")
    ap.add_argument("--point",   type=float, default=0.00001,
                    help="Pip size: 0.00001 (EURUSD), 0.001 (JPY), 0.01 (XAUUSD)")
    ap.add_argument("--ticks",   default="",
                    help="File .bin -> bat mode 1 (full tick inject, dung khi khong co FXT)")
    ap.add_argument("--spread-from-ticks", default="", dest="spread_from_ticks",
                    help="File .bin -> bat mode 0b (spread thuc Dukascopy, ket hop FXT)")
    args = ap.parse_args()

    if args.ticks:
        # ---- Mode 1: full tick injection ----
        print(f"[*] Tick mode: doc {args.ticks}")
        data = build_tick_shm(args.ticks, args.point)
        count, = struct.unpack_from("<Q", data, 16)
        size_mb = len(data) / 1024 / 1024
        h = write_shm(data)
        print(f"[OK] Shared memory: {count:,} ticks  ({size_mb:.1f} MB)")
        print(f"     Magic=TDST  Symbol={args.symbol}  point={args.point}")
        mode_str = "TICK MODE (bid+ask thuc)"

    elif args.spread_from_ticks:
        # ---- Mode 0b: spread thuc tu Dukascopy ----
        data = build_spread_from_ticks(args.spread_from_ticks, args.point)
        h = write_shm(data)
        count, = struct.unpack_from("<I", data, 4)
        size_mb = len(data) / 1024 / 1024
        print(f"[OK] Shared memory: {count:,} spread records  ({size_mb:.2f} MB)")
        mode_str = "SPREAD THUC (Dukascopy ask-bid)"

    else:
        # ---- Mode 0a: spread co dinh ----
        data = build_spread_shm(args.spread, args.point)
        h = write_shm(data)
        print(f"[*] Spread co dinh: {args.spread} pts  point={args.point}  ({args.symbol})")
        print(f"[OK] Shared memory: {len(data)} bytes")
        mode_str = f"SPREAD CO DINH ({args.spread} points)"

    print(f"\n[LIVE] {mode_str}")
    print("       Giu cua so nay mo trong khi backtest MT4.")
    print("       Nhan Ctrl+C de tat.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        _k32.CloseHandle(h)
        print("[*] Da dong shared memory.")


if __name__ == "__main__":
    main()
