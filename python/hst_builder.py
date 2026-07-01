"""
HST Builder — tao file history .hst (version 401, MT4 build 600+) tu tick store.

VAI TRO (quan trong): MT4 Strategy Tester sinh FXT tu HST. Neu KHONG co HST phu
khoang test -> "TestGenerator: no history data". Build HST phu khoang test ->
MT4 luon co nguon -> KHONG bao gio bao loi do nua.

Ket hop: HST (de MT4 khong loi) + FXT tick-accurate (de MT4 dung tick that) +
hook gia (tdshook) -> giong TDS.

Format v401:
  HEADER 148 byte: int version(401), char copyright[64], char symbol[12],
                   int period, int digits, int timesign, int last_sync,
                   char reserved[52]
  BAR 60 byte: int64 ctm, double O,H,L,C, int64 tick_volume, int32 spread,
               int64 real_volume
"""

import os
import struct
import datetime

from symbols_meta import resolve

HST_VERSION = 401
HDR = 148
_BAR = struct.Struct("<qddddqiq")   # 60 byte
assert _BAR.size == 60


def _pack_header(symbol, period_min, digits):
    buf = bytearray(HDR)
    struct.pack_into("<i", buf, 0, HST_VERSION)
    cp = b"TDS Clone (c) eareview-style"
    buf[4:4 + len(cp)] = cp
    sym = symbol.upper().encode("ascii")[:11]
    buf[68:68 + len(sym)] = sym
    struct.pack_into("<i", buf, 80, period_min)
    struct.pack_into("<i", buf, 84, digits)
    struct.pack_into("<i", buf, 88, int(datetime.datetime.now().timestamp()))  # timesign
    struct.pack_into("<i", buf, 92, 0)   # last_sync
    return bytes(buf)


def build_hst(ticks, symbol, period_min, out_path):
    """
    Build HST tu iterable tick (time_ms, bid, ask) -> bar OHLC theo BID.
    Stream tung tick (RAM thap). Ghi spread trung binh moi bar (points).
    """
    meta = resolve(symbol)
    digits = meta.digits
    point = meta.point
    period_sec = period_min * 60

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    tmp = out_path + ".tmp"

    bar_time = -1
    o = h = l = c = 0.0
    vol = 0
    spread_acc = 0.0
    nbar = 0

    with open(tmp, "wb") as f:
        f.write(_pack_header(symbol, period_min, digits))

        def flush():
            nonlocal nbar
            if bar_time < 0:
                return
            sp = int(round(spread_acc / vol)) if vol else 0
            f.write(_BAR.pack(bar_time, o, h, l, c, vol, sp, 0))
            nbar += 1

        for time_ms, bid, ask in ticks:
            ts = int(time_ms) // 1000
            bt = (ts // period_sec) * period_sec
            if bt != bar_time:
                flush()
                bar_time = bt
                o = h = l = c = bid
                vol = 0
                spread_acc = 0.0
            c = bid
            if bid > h: h = bid
            if bid < l: l = bid
            vol += 1
            spread_acc += (ask - bid) / point
        flush()

    os.replace(tmp, out_path)
    sz = os.path.getsize(out_path)
    print(f"[OK] HST: {nbar:,} bars  {sz/1024/1024:.1f} MB  -> {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Tim thu muc history/<server> cua MT4
# ---------------------------------------------------------------------------
_SPECIAL = {"default", "deleted", "downloads", "mailbox", "signals", "symbolsets"}

def find_mt4_history_dirs():
    """
    Tra list (terminal_dir, server_history_dir) cho moi terminal/server.
    server_history_dir = <terminal>\\history\\<server> (bo qua thu muc he thong).
    """
    appdata = os.environ.get("APPDATA", "")
    root = os.path.join(appdata, "MetaQuotes", "Terminal")
    if not os.path.isdir(root):
        return []
    out = []
    for term in os.listdir(root):
        hroot = os.path.join(root, term, "history")
        if not os.path.isdir(hroot):
            continue
        for srv in os.listdir(hroot):
            p = os.path.join(hroot, srv)
            if os.path.isdir(p) and srv.lower() not in _SPECIAL:
                out.append((os.path.join(root, term), p))
    return out


def deploy_hst(src_hst, symbol, period_min, server_dir=None):
    """Copy/ghi HST vao history/<server>/. Tra duong dan dest."""
    import shutil
    if server_dir is None:
        dirs = find_mt4_history_dirs()
        if not dirs:
            raise RuntimeError("Khong tim thay history/<server> cua MT4")
        server_dir = dirs[0][1]
    fname = f"{symbol.upper()}{period_min}.hst"
    dst = os.path.join(server_dir, fname)
    if os.path.abspath(src_hst) != os.path.abspath(dst):
        shutil.copy2(src_hst, dst)
    print(f"[OK] Deploy HST: {dst}")
    return dst


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import tick_store
    import argparse
    ap = argparse.ArgumentParser(description="HST builder cho MT4")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--period", type=int, default=1)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out = args.out or f"{args.symbol.upper()}{args.period}.hst"
    build_hst(tick_store.iter_all(args.symbol), args.symbol, args.period, out)
